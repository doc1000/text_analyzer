## \app\helpers.py

from typing import List, Optional, Literal
import os
from openai import OpenAI
import json
import urllib.request
import urllib.error
from sqlalchemy.orm import Session
from sqlalchemy import func
from .db import get_db
from .config import PREFERENCES
from uuid import UUID
from pydantic import BaseModel
from datetime import datetime
import numpy as np
import ast
from .models import Document, get_or_create_embedding_class

MAX_CHARS_PER_CHUNK = 1024  # tune this as you like
MAX_CHARS_PER_SENTENCE = 170  # typical sentence is 75-100, academic 150
OLLAMA_EMBED_CHAR_LIMIT = int(os.getenv("OLLAMA_EMBED_CHAR_LIMIT", "500"))

## BaseModel Classes - could be moved
## DocumentOut, QueryRequest, ChunkHit, QueryResponse
class DocumentOut(BaseModel):
    id: UUID
    url: str
    title: str | None
    captured_at: datetime

    class Config:
        #orm_mode = True
        from_attributes = True
        #model_config = ConfigDict(from_attributes=True)


class QueryRequest(BaseModel):
    query: str
    top_k: int = 15
    with_answer: bool = True
    doc_ids: Optional[List[str]] = None


class ChunkHit(BaseModel):
    document_id: str
    document_title: str | None
    url: str
    chunk_index: int
    chunk_text: str
    similarity: float
    sent_text: str | None = None  # Sentence text when using sentence embeddings
    sent_index: int | None = None  # Sentence index within chunk when using sentence embeddings


class QueryResponse(BaseModel):
    answer: str | None
    hits: List[ChunkHit]


def split_long_sentence(sent, max_tokens=MAX_CHARS_PER_CHUNK):
    """Split a long sentence into smaller chunks."""
    if len(sent) <= max_tokens:
        return [sent]

    chunks = []
    offset = 0
    while offset < len(sent):
        chunk = sent[offset: offset + max_tokens]
        chunks.append(chunk)
        offset += max_tokens
    return chunks


def iter_sentences(text: str):
    """
    Iterate over sentences using syntok (fast, no ML model required).
    Replaces spaCy sentence segmentation for much faster startup.
    """
    from syntok import segmenter
    
    for paragraph in segmenter.process(text):
        for sentence in paragraph:
            yield "".join(token.value for token in sentence).strip()


def chunk_text(text: str, max_char: int = MAX_CHARS_PER_CHUNK) -> List[str]:
    """
    Sentence-based chunker using syntok for segmentation.
    Groups sentences up to ~MAX_CHARS_PER_CHUNK.
    """
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0

    for s in iter_sentences(text):
        if not s:
            continue

        # Handle sentences longer than max_char
        if len(s) > max_char:
            for sub in split_long_sentence(s, max_char):
                if current_len + len(sub) > max_char and current:
                    chunks.append(" ".join(current))
                    current = [sub]
                    current_len = len(sub)
                else:
                    current.append(sub)
                    current_len += len(sub) + 1
            continue

        if current_len + len(s) > max_char and current:
            chunks.append(" ".join(current))
            current = [s]
            current_len = len(s)
        else:
            current.append(s)
            current_len += len(s) + 1

    if current:
        chunks.append(" ".join(current))

    return chunks

# Global OpenAI client - can be updated dynamically
_client_instance = None

def get_openai_client():
    """Get or create OpenAI client with current API key."""
    global _client_instance
    api_key = os.getenv("OPENAI_API_KEY")
    if _client_instance is None or (hasattr(_client_instance, 'api_key') and _client_instance.api_key != api_key):
        _client_instance = OpenAI(api_key=api_key)
    return _client_instance

def update_openai_client(api_key: str):
    """Update the OpenAI client with a new API key."""
    global _client_instance
    os.environ["OPENAI_API_KEY"] = api_key
    _client_instance = OpenAI(api_key=api_key)

# Initialize client
client = get_openai_client()

def _openai_chat(prompt: str) -> str:
    model_name = PREFERENCES.models.llm_model
    
    # Ensure we're using a valid OpenAI model (not an Ollama model name)
    # Valid OpenAI models start with "gpt-" or "o1-"
    openai_models = ["gpt-4.1-nano", "gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini", "gpt-4o"]
    if model_name not in openai_models:
        # Fallback to a default OpenAI model if llm_model is set to an Ollama model
        model_name = "gpt-4o-mini"
    
    resp = get_openai_client().chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content.strip()



ollama_chat_defaults = {
    "temperature": 0.6,
    "top_p": 0.9,
    #"num_predict": 256
  }

def _ollama_chat(prompt: str, options: dict=ollama_chat_defaults, model: str = None) -> str:
    base = PREFERENCES.models.ollama.base_url.rstrip("/")
    # Use provided model or default to chat_model
    if model is None:
        model = PREFERENCES.models.ollama.chat_model
    url = f"{base}/api/chat"

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        #"options": options
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    # Ollama returns message content under data["message"]["content"]
    return (data.get("message", {}) or {}).get("content", "").strip()


def _post_json(url: str, payload: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _clean_embed_input(text: str) -> str:
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    # remove null bytes (common DB artifact)
    text = text.replace("\x00", "")
    # trim whitespace
    text = text.strip()
    # cap size to avoid 400 from oversized inputs
    if len(text) > OLLAMA_EMBED_CHAR_LIMIT:
        text = text[:OLLAMA_EMBED_CHAR_LIMIT]
    return text

def _ollama_embed(text: str) -> list[float]:
    base = PREFERENCES.models.ollama.base_url.rstrip("/")
    model = PREFERENCES.models.ollama.embed_model

    cleaned = _clean_embed_input(text)

    url = f"{base}/api/embed"
    payload = {"model": model, "input": cleaned}

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(f"Ollama embed HTTP {e.code}: {e.reason}. Body: {body[:500]}") from e

    # Response commonly includes "embeddings": [[...]]
    vec = None
    if "embeddings" in data and data["embeddings"]:
        vec = data["embeddings"][0]
    elif "embedding" in data:
        vec = data["embedding"]

    if not isinstance(vec, list) or not vec:
        raise RuntimeError(f"Unexpected Ollama embed response: {data}")

    return vec


def _answer_from_hits(query: str, hits: List[ChunkHit]) -> str:
    # Calculate prompt template size (everything except context)
    prompt_template = (
        "You are a helpful assistant. Using ONLY the context below, "
        "Only respond with the answer to the question, do not include any other text.\n"
        "answer the user's question concisely.\n\n"
        f"Question: {query}\n\n"
        "Context:\n"
    )
    template_size = len(prompt_template)
    
    # Get max context size from config (leaves room for prompt template)
    max_context_chars = PREFERENCES.query.max_context_chars
    max_prompt_chars = PREFERENCES.query.max_prompt_chars
    
    # Group hits by chunk (document_id + chunk_index) and sort sentences within each chunk by sent_index
    from collections import defaultdict
    chunk_groups = defaultdict(list)
    
    for h in hits:
        # Use (document_id, chunk_index) as the grouping key
        chunk_key = (h.document_id, h.chunk_index)
        chunk_groups[chunk_key].append(h)
    
    # Sort sentences within each chunk by sent_index (sequential order)
    # Also track the highest similarity for each chunk to preserve ordering
    grouped_hits = []
    for chunk_key, chunk_hits in chunk_groups.items():
        # Sort by sent_index if available, otherwise keep original order
        sorted_chunk_hits = sorted(
            chunk_hits,
            key=lambda x: x.sent_index if x.sent_index is not None else float('inf')
        )
        # Get highest similarity in this chunk (for ordering chunks)
        max_similarity = max(h.similarity for h in sorted_chunk_hits)
        grouped_hits.append((chunk_key, sorted_chunk_hits, max_similarity))
    
    # Sort chunks by highest similarity (preserve relevance ordering)
    grouped_hits.sort(key=lambda x: x[2], reverse=True)
    
    # Build context incrementally, prioritizing higher similarity chunks (they come first)
    context_parts = []
    current_context_size = 0
    source_num = 0
    
    for chunk_key, chunk_hits, _ in grouped_hits:
        # Get document info from first hit in chunk (all hits in chunk have same doc info)
        first_hit = chunk_hits[0]
        source_num += 1
        hit_prefix = f"Source {source_num} ({first_hit.url}):\n"
        
        # Combine sentences from same chunk in sequential order, removing duplicates
        if all(h.sent_text for h in chunk_hits):
            # All hits have sentence text - combine them in order, removing duplicates by text content
            seen_sentence_texts = set()
            unique_sentence_texts = []
            for h in chunk_hits:
                # Deduplicate by normalized sentence text content
                sent_text_normalized = (h.sent_text or "").strip()
                if sent_text_normalized and sent_text_normalized not in seen_sentence_texts:
                    seen_sentence_texts.add(sent_text_normalized)
                    unique_sentence_texts.append(h.sent_text)
            hit_text = " ".join(unique_sentence_texts)
        elif chunk_hits[0].sent_text:
            # Some have sentence text - use first sentence text
            hit_text = chunk_hits[0].sent_text
        else:
            # Fallback to chunk text
            hit_text = first_hit.chunk_text
        
        # Calculate size if we add this grouped hit
        hit_size = len(hit_prefix) + len(hit_text)
        separator_size = len("\n\n") if context_parts else 0
        total_size_if_added = current_context_size + separator_size + hit_size
        
        # Check if adding this hit would exceed the limit
        if total_size_if_added > max_context_chars:
            # Try truncating this hit to fit
            available_space = max_context_chars - current_context_size - separator_size - len(hit_prefix)
            if available_space > 50:  # Only add if we have meaningful space (at least 50 chars)
                truncated_text = hit_text[:available_space] + "..."
                context_parts.append(f"{hit_prefix}{truncated_text}")
            # Stop here - we've filled the context budget
            break
        else:
            context_parts.append(f"{hit_prefix}{hit_text}")
            current_context_size = total_size_if_added
    
    # Join context parts
    context = "\n\n".join(context_parts)
    
    # Build final prompt
    prompt = prompt_template + context
    
    # Final safety check: truncate entire prompt if it somehow exceeds max
    if len(prompt) > max_prompt_chars:
        # Truncate context portion to fit
        available_for_context = max_prompt_chars - template_size
        if available_for_context > 0:
            context = context[:available_for_context] + "..."
            prompt = prompt_template + context
        else:
            # Even template is too large (shouldn't happen), use minimal prompt
            prompt = f"Question: {query}\n\nAnswer based on the provided context."

    chat_provider = getattr(PREFERENCES.models, "chat_provider", "ollama")

    if chat_provider == "ollama":
        text = _ollama_chat(prompt)
    else:
        text = _openai_chat(prompt)
    return text


# ---------- Summary Generation Functions ----------

def generate_chunk_summary(chunk_text: str) -> str:
    """
    Generate a concise summary for a single chunk of text using LLM.
    
    Args:
        chunk_text: The text chunk to summarize
    
    Returns:
        Summary string (truncated to max_chunk_summary_chars)
    """
    cfg = PREFERENCES.summary
    max_chars = cfg.max_chunk_summary_chars
    
    # Skip if chunk is already very short
    if len(chunk_text) < 100:
        return chunk_text
    
    prompt = (
        "Summarize the following text in 2-3 sentences. "
        "Preserve key facts, terminology, and important details. "
        "Be concise but comprehensive.\n\n"
        f"Text:\n{chunk_text[:1500]}\n\n"
        "Summary:"
    )
    
    topic_provider = getattr(PREFERENCES.models, "topic_provider", "ollama")
    
    try:
        if topic_provider == "ollama":
            # Use topic_model for faster summarization
            topic_model = PREFERENCES.models.ollama.topic_model
            summary = _ollama_chat(prompt, model=topic_model)
        else:
            summary = _openai_chat(prompt)
        
        # Truncate if needed
        summary = summary.strip()
        if len(summary) > max_chars:
            summary = summary[:max_chars-3] + "..."
        
        return summary
    
    except Exception as e:
        print(f"[WARN] Chunk summary generation failed: {e}")
        # Fallback: return truncated original text
        return chunk_text[:max_chars-3] + "..." if len(chunk_text) > max_chars else chunk_text


def generate_chunk_summaries_batch(
    chunk_texts: List[str], 
    max_workers: int = None,
    parallel: bool = True
) -> List[str]:
    """
    Generate summaries for multiple chunks using parallel processing.
    
    Args:
        chunk_texts: List of text chunks to summarize
        max_workers: Number of parallel workers (default: 4 for Ollama, 10 for OpenAI)
        parallel: If False, process sequentially (useful for debugging)
    
    Returns:
        List of summary strings (same order as input)
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    if not chunk_texts:
        return []
    
    # Determine default workers based on provider
    if max_workers is None:
        topic_provider = getattr(PREFERENCES.models, "topic_provider", "ollama")
        # Ollama typically runs locally, so fewer workers to avoid overload
        # OpenAI can handle more concurrent requests
        max_workers = 4 if topic_provider == "ollama" else 10
    
    max_chars = PREFERENCES.summary.max_chunk_summary_chars
    
    def _generate_with_fallback(idx_and_text):
        """Generate summary with fallback to truncated text."""
        idx, chunk_text = idx_and_text
        try:
            summary = generate_chunk_summary(chunk_text)
            return idx, summary, None
        except Exception as e:
            # Fallback to truncated text
            fallback = chunk_text[:max_chars-3] + "..." if len(chunk_text) > max_chars else chunk_text
            return idx, fallback, str(e)
    
    # Sequential processing
    if not parallel or len(chunk_texts) <= 2:
        summaries = []
        for i, chunk_text in enumerate(chunk_texts):
            _, summary, error = _generate_with_fallback((i, chunk_text))
            if error:
                print(f"[WARN] Failed to summarize chunk {i}: {error}")
            summaries.append(summary)
            if (i + 1) % 5 == 0:
                print(f"  Generated {i + 1}/{len(chunk_texts)} chunk summaries")
        return summaries
    
    # Parallel processing
    print(f"  Processing {len(chunk_texts)} chunks with {max_workers} workers...")
    
    # Initialize results list with None placeholders
    summaries = [None] * len(chunk_texts)
    completed = 0
    errors = 0
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        futures = {
            executor.submit(_generate_with_fallback, (i, text)): i 
            for i, text in enumerate(chunk_texts)
        }
        
        # Process as they complete
        for future in as_completed(futures):
            try:
                idx, summary, error = future.result()
                summaries[idx] = summary
                completed += 1
                
                if error:
                    errors += 1
                    print(f"  [WARN] Chunk {idx} fallback: {error[:50]}...")
                
                if completed % 10 == 0 or completed == len(chunk_texts):
                    print(f"  Progress: {completed}/{len(chunk_texts)} chunks ({errors} fallbacks)")
                    
            except Exception as e:
                # This shouldn't happen since _generate_with_fallback catches errors
                idx = futures[future]
                summaries[idx] = chunk_texts[idx][:max_chars-3] + "..."
                errors += 1
                print(f"  [ERROR] Chunk {idx}: {e}")
    
    print(f"  ✓ Completed {len(chunk_texts)} chunk summaries ({errors} fallbacks)")
    return summaries


def generate_document_summary(chunk_summaries: List[str], title: str = None) -> str:
    """
    Generate a document-level summary by synthesizing chunk summaries.
    
    Args:
        chunk_summaries: List of chunk summary strings
        title: Optional document title for context
    
    Returns:
        Document summary string
    """
    cfg = PREFERENCES.summary
    max_chars = cfg.max_doc_summary_chars
    max_chunks = cfg.max_chunks_for_doc_summary
    
    if not chunk_summaries:
        return ""
    
    # Limit number of chunk summaries to include
    summaries_to_use = chunk_summaries[:max_chunks]
    
    # Build context from chunk summaries
    context = "\n".join(f"- {s}" for s in summaries_to_use)
    
    # Truncate context if too long (reserve space for prompt template)
    if len(context) > 2000:
        context = context[:2000] + "..."
    
    title_context = f"Document title: {title}\n\n" if title else ""
    
    prompt = (
        "Synthesize the following section summaries into a cohesive 3-5 sentence summary "
        "of the entire document. Capture the main themes, key points, and important details.\n\n"
        f"{title_context}"
        f"Section summaries:\n{context}\n\n"
        "Document summary:"
    )
    
    topic_provider = getattr(PREFERENCES.models, "topic_provider", "ollama")
    
    try:
        if topic_provider == "ollama":
            topic_model = PREFERENCES.models.ollama.topic_model
            summary = _ollama_chat(prompt, model=topic_model)
        else:
            summary = _openai_chat(prompt)
        
        # Truncate if needed
        summary = summary.strip()
        if len(summary) > max_chars:
            summary = summary[:max_chars-3] + "..."
        
        return summary
    
    except Exception as e:
        print(f"[WARN] Document summary generation failed: {e}")
        # Fallback: concatenate first few chunk summaries
        fallback = " ".join(summaries_to_use[:3])
        return fallback[:max_chars-3] + "..." if len(fallback) > max_chars else fallback


def generate_cluster_summary(doc_summaries: List[str], doc_titles: List[str] = None) -> str:
    """
    Generate a summary for a cluster of documents based on their summaries.
    
    This provides richer context for topic naming than just titles/snippets.
    
    Args:
        doc_summaries: List of document summary strings
        doc_titles: Optional list of document titles
    
    Returns:
        Cluster summary string suitable for topic naming
    """
    if not doc_summaries:
        return ""
    
    # Build context from document summaries
    context_parts = []
    for i, summary in enumerate(doc_summaries[:10]):  # Limit to 10 docs
        if doc_titles and i < len(doc_titles) and doc_titles[i]:
            context_parts.append(f"Doc {i+1} ({doc_titles[i][:50]}): {summary}")
        else:
            context_parts.append(f"Doc {i+1}: {summary}")
    
    context = "\n".join(context_parts)
    
    # Truncate if too long
    if len(context) > 2500:
        context = context[:2500] + "..."
    
    prompt = (
        "These documents are grouped together in a cluster. "
        "Describe the common theme or pattern that connects them in 2-3 sentences. "
        "Focus on what makes these documents similar - the shared topic, subject matter, or purpose.\n\n"
        f"Document summaries:\n{context}\n\n"
        "Common theme:"
    )
    
    topic_provider = getattr(PREFERENCES.models, "topic_provider", "ollama")
    
    try:
        if topic_provider == "ollama":
            topic_model = PREFERENCES.models.ollama.topic_model
            summary = _ollama_chat(prompt, model=topic_model)
        else:
            summary = _openai_chat(prompt)
        
        return summary.strip()
    
    except Exception as e:
        print(f"[WARN] Cluster summary generation failed: {e}")
        return ""


def _generate_summary_from_text(text: str, title: str = None) -> str:
    """
    Generate a summary directly from raw text (when chunk summaries aren't available).
    
    Args:
        text: The raw text to summarize
        title: Optional document title for context
    
    Returns:
        Summary string
    """
    cfg = PREFERENCES.summary
    max_chars = cfg.max_doc_summary_chars
    
    if not text or not text.strip():
        return ""
    
    title_context = f"Document title: {title}\n\n" if title else ""
    
    prompt = (
        "Summarize the following text in 3-5 sentences. "
        "Capture the main topic, key points, and important details.\n\n"
        f"{title_context}"
        f"Text:\n{text}\n\n"
        "Summary:"
    )
    
    topic_provider = getattr(PREFERENCES.models, "topic_provider", "ollama")
    
    try:
        if topic_provider == "ollama":
            topic_model = PREFERENCES.models.ollama.topic_model
            summary = _ollama_chat(prompt, model=topic_model)
        else:
            summary = _openai_chat(prompt)
        
        summary = summary.strip()
        if len(summary) > max_chars:
            summary = summary[:max_chars-3] + "..."
        
        return summary
    
    except Exception as e:
        print(f"[WARN] Text summary generation failed: {e}")
        # Fallback to truncated text
        return text[:max_chars-3] + "..." if len(text) > max_chars else text


def normalize_embedding(emb):
    # Already a numpy array
    if isinstance(emb, np.ndarray):
        return emb

    # Already a (Python) list/tuple of numbers
    if isinstance(emb, (list, tuple)):
        return np.array(emb, dtype=float)

    # String that needs parsing
    if isinstance(emb, str):
        parsed = ast.literal_eval(emb)
        return np.array(parsed, dtype=float)

    raise TypeError(f"Unexpected embedding type: {type(emb)}")

def get_embedding(text: str) -> List[float]:
    """
    Get a single embedding vector for a text using the configured provider.
    Returns L2-normalized embeddings for consistent cosine similarity calculations.
    
    Supports Ollama, OpenAI, and HuggingFace providers.
    """
    embedding_provider = getattr(PREFERENCES.models, "embedding_provider", "ollama")
    
    if embedding_provider == "ollama":
        vec = _ollama_embed(text)
    elif embedding_provider == "huggingface":
        # Use batch function for single text (HuggingFace API is batch-oriented)
        results = _huggingface_embed_batch([text])
        return results[0] if results else []
    else:
        # OpenAI embeddings
        model_name = PREFERENCES.models.embedding_model
        resp = get_openai_client().embeddings.create(model=model_name, input=text)
        vec = resp.data[0].embedding
    
    # Ensure consistent array format
    vec = normalize_embedding(vec)
    
    # L2-normalize for proper cosine similarity (dot product = cosine sim when normalized)
    # OpenAI embeddings are already normalized, but Ollama may not be
    # This ensures consistency across all providers
    from .mmr import l2_normalize_vector
    vec = l2_normalize_vector(vec)
    
    return vec.tolist()


def _ollama_embed_batch(texts: List[str]) -> List[List[float]]:
    """
    Get embeddings for multiple texts from Ollama in a single API call.
    
    Ollama API supports batch via "input" as list of strings.
    
    Args:
        texts: List of text strings to embed
    
    Returns:
        List of embedding vectors (same order as input)
    """
    if not texts:
        return []
    
    base = PREFERENCES.models.ollama.base_url.rstrip("/")
    model = PREFERENCES.models.ollama.embed_model
    
    # Clean all inputs
    cleaned_texts = [_clean_embed_input(t) for t in texts]
    
    url = f"{base}/api/embed"
    payload = {
        "model": model,
        "input": cleaned_texts  # List of strings
    }
    
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(f"Ollama batch embed HTTP {e.code}: {e.reason}. Body: {body[:500]}") from e
    
    # Ollama returns "embeddings": [[...], [...], ...] for batch input
    if "embeddings" in data and isinstance(data["embeddings"], list):
        embeddings_list = data["embeddings"]
    elif "embedding" in data:
        # Fallback: single embedding wrapped (shouldn't happen with batch, but handle gracefully)
        embeddings_list = [data["embedding"]]
    else:
        raise RuntimeError(f"Unexpected Ollama batch response: {data}")
    
    # Verify we got the right number of embeddings
    if len(embeddings_list) != len(texts):
        raise RuntimeError(
            f"Ollama batch returned {len(embeddings_list)} embeddings for {len(texts)} texts"
        )
    
    # Normalize all embeddings
    from .mmr import l2_normalize_vector
    normalized = []
    for vec in embeddings_list:
        vec = normalize_embedding(vec)
        vec = l2_normalize_vector(vec)
        normalized.append(vec.tolist())
    
    return normalized


def _huggingface_embed_batch(texts: List[str]) -> List[List[float]]:
    """
    Get embeddings for multiple texts from HuggingFace Inference API.
    
    Uses the feature-extraction pipeline which returns embeddings.
    HuggingFace API accepts batch requests.
    
    Args:
        texts: List of text strings to embed
    
    Returns:
        List of embedding vectors (same order as input)
    """
    if not texts:
        return []
    
    hf_config = PREFERENCES.models.huggingface
    api_token = hf_config.api_token
    model = hf_config.embed_model
    base_url = hf_config.embed_url.rstrip("/")
    
    if not api_token:
        raise RuntimeError(
            "HuggingFace API token not configured. "
            "Set HUGGINGFACE_API_TOKEN environment variable."
        )
    
    # Clean all inputs
    cleaned_texts = [_clean_embed_input(t) for t in texts]
    
    # HuggingFace Inference API endpoint
    # Supports:
    # 1. Dedicated endpoint (URL is complete): https://xxxxx.aws.endpoints.huggingface.cloud
    # 2. Shared API with {model} placeholder: https://router.huggingface.co/.../models/{model}
    # 3. Shared API base URL: https://router.huggingface.co/.../models (we append /{model})
    if "{model}" in base_url:
        url = base_url.format(model=model)
    elif "endpoints.huggingface.cloud" in base_url or not base_url.endswith("/models"):
        # Dedicated endpoint - use URL as-is
        url = base_url
    else:
        # Shared API - append model name
        url = f"{base_url}/{model}"
    
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json"
    }
    
    # HuggingFace accepts {"inputs": "..."} or {"inputs": ["...", "..."]} for batch.
    # wait_for_model helps avoid 503s during cold starts.
    payload = {"inputs": cleaned_texts, "options": {"wait_for_model": True}}
    
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(f"HuggingFace embed HTTP {e.code}: {e.reason}. Body: {body[:500]}") from e
    
    # HuggingFace returns list of embeddings directly for sentence-transformers
    # Each embedding may be a list of floats, or for some models, list of token embeddings
    embeddings_list = []
    
    for item in data:
        if isinstance(item, list):
            if item and isinstance(item[0], list):
                # Token-level embeddings - mean pool to get sentence embedding
                import numpy as np
                arr = np.array(item)
                pooled = arr.mean(axis=0).tolist()
                embeddings_list.append(pooled)
            else:
                # Direct sentence embedding
                embeddings_list.append(item)
        else:
            raise RuntimeError(f"Unexpected HuggingFace response format: {type(item)}")
    
    # Verify we got the right number of embeddings
    if len(embeddings_list) != len(texts):
        raise RuntimeError(
            f"HuggingFace returned {len(embeddings_list)} embeddings for {len(texts)} texts"
        )
    
    # Normalize all embeddings
    from .mmr import l2_normalize_vector
    normalized = []
    for vec in embeddings_list:
        vec = normalize_embedding(vec)
        vec = l2_normalize_vector(vec)
        normalized.append(vec.tolist())
    
    return normalized


def get_embeddings_batch(texts: List[str]) -> List[List[float]]:
    """
    Get embeddings for multiple texts in a single API call.
    
    This is much faster than calling get_embedding() multiple times.
    Supports Ollama, OpenAI, and HuggingFace providers.
    
    Args:
        texts: List of text strings to embed
    
    Returns:
        List of embedding vectors (same order as input)
    
    Example:
        >>> texts = ["Hello world", "How are you?", "Goodbye"]
        >>> embeddings = get_embeddings_batch(texts)
        >>> len(embeddings) == len(texts)  # True
    """
    if not texts:
        return []
    
    embedding_provider = getattr(PREFERENCES.models, "embedding_provider", "ollama")
    
    if embedding_provider == "ollama":
        return _ollama_embed_batch(texts)
    
    if embedding_provider == "huggingface":
        return _huggingface_embed_batch(texts)
    
    # OpenAI batch embedding
    model_name = PREFERENCES.models.embedding_model
    resp = get_openai_client().embeddings.create(
        model=model_name,
        input=texts  # OpenAI accepts list of strings
    )
    
    # Extract embeddings and normalize
    embeddings = []
    from .mmr import l2_normalize_vector
    
    # Verify we got the right number of embeddings
    if len(resp.data) != len(texts):
        raise RuntimeError(
            f"OpenAI returned {len(resp.data)} embeddings for {len(texts)} texts"
        )
    
    for item in resp.data:
        vec = normalize_embedding(item.embedding)
        vec = l2_normalize_vector(vec)
        embeddings.append(vec.tolist())
    
    return embeddings

# Table definitions moved to app/db.py to avoid circular dependencies
# Import them from there
from .db import EMBED_TABLE, SENTENCE_TABLE, DOCUMENT_TABLE, EMBED_DIM, EMBED_MODEL

def fill_empty_embed_docs(embed_type: Literal["chunk", "sent"] = "chunk", batch_size: int = None):
    """
    Fill missing embeddings for documents/chunks using batch processing.
    
    Args:
        embed_type: "chunk" for document chunks, "sent" for sentence embeddings
        batch_size: Number of texts to embed per API call (defaults to config)
    """
    if batch_size is None:
        batch_size = PREFERENCES.embedding.batch_size
    
    # Determine parent/child table relationships
    if embed_type == "chunk":
        target_table = EMBED_TABLE
        parent_table = Document
        parent_id = target_table.document_id
        parent_text = parent_table.full_text
    else:  # sent
        target_table = SENTENCE_TABLE
        parent_table = EMBED_TABLE
        parent_id = target_table.chunk_id
        parent_text = parent_table.chunk_text
    
    db_gen = get_db()
    db: Session = next(db_gen)
    
    try:
        # Count total items needing embedding for progress tracking
        subq = (
            db.query(target_table.id)
            .filter(parent_id == parent_table.id)
            .exists()
        )
        total_count_query = (
            db.query(func.count(parent_table.id))
            .filter(~subq)
        )
        total_count = total_count_query.scalar() or 0
        
        print(f"\n{'='*60}")
        print(f"Filling empty {embed_type} embeddings")
        print(f"Total items to process: {total_count}")
        print(f"Batch size: {batch_size}")
        print(f"{'='*60}\n")
        
        # Query for items without embeddings
        query = (
            db.query(parent_table.id, parent_text)
            .filter(~subq)  # ← find NULL embeddings
            .limit(100)  # Process in batches of 100 parent items
        )
        
        processed_count = 0
        while True:
            rows = query.all()
            if not rows:
                break
            
            print(f"Processing batch of {len(rows)} items...")
            
            for i, doc_row in enumerate(rows):
                try:
                    # Create a simple object with id and text
                    class DocProxy:
                        def __init__(self, id_val, text_val):
                            self.id = id_val
                            if embed_type == "chunk":
                                self.full_text = text_val
                            else:
                                self.chunk_text = text_val
                    
                    doc = DocProxy(doc_row[0], doc_row[1])
                    
                    # Embed using batch processing
                    success = embed_doc_chunks(doc, chunk_type=embed_type, batch_size=batch_size)
                    processed_count += 1
                    
                    if processed_count % 10 == 0:
                        print(f"  Progress: {processed_count}/{total_count} items processed")
                        
                except Exception as e:
                    print(f"[ERROR] Failed to process item {doc_row[0]}: {e}")
                    continue
        
        print(f"\n{'='*60}")
        print(f"Completed: {processed_count} items processed")
        print(f"{'='*60}\n")
        
    finally:
        try:
            next(db_gen)
        except StopIteration:
            pass
                

def embed_doc_chunks(
    doc: Document | type[EMBED_TABLE],
    chunk_type: Literal["chunk", "sent"] = "chunk",
    batch_size: int = None,
    generate_summaries: bool = None
) -> int:
    """
    Embed chunks/sentences using batch processing for much better performance.
    
    When processing chunks (chunk_type="chunk"), this function will:
    1. Create chunk embeddings
    2. Generate chunk summaries (if enabled in config)
    3. Create sentence embeddings for each chunk
    4. Create a document-level record with average embedding and aggregated summary
    
    Args:
        doc: Document or chunk to embed
        chunk_type: "chunk" or "sent"
        batch_size: Number of texts to embed per API call (defaults to config)
        generate_summaries: Override config setting for summary generation
    
    Returns:
        Number of chunks successfully embedded
    """
    if batch_size is None:
        batch_size = PREFERENCES.embedding.batch_size
    
    # Determine if we should generate summaries
    summary_cfg = PREFERENCES.summary
    if generate_summaries is None:
        generate_summaries = (
            chunk_type == "chunk" and summary_cfg.generate_chunk_summaries
        )
    
    db_gen = get_db()
    db: Session = next(db_gen)
    
    try:
        # 1. Chunk the text
        if chunk_type == "chunk":
            max_char = MAX_CHARS_PER_CHUNK
            # Prefer extracted_text for NLP (cleaner), fall back to captured_text, then full_text
            text_input = getattr(doc, 'extracted_text', None) or getattr(doc, 'captured_text', None) or doc.full_text
        else:
            max_char = MAX_CHARS_PER_SENTENCE
            text_input = doc.chunk_text
        
        chunks = chunk_text(text_input, max_char=max_char)
        
        if not chunks:
            return 0
        
        # 2. Batch embed all chunks
        print(f"Embedding {len(chunks)} {chunk_type}s in batches of {batch_size}...")
        
        all_embeddings = []
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            batch_num = i // batch_size + 1
            try:
                batch_embeddings = get_embeddings_batch(batch)
                all_embeddings.extend(batch_embeddings)
                print(f"  ✓ Batch {batch_num}: {len(batch)} {chunk_type}s embedded")
            except Exception as e:
                print(f"  [WARN] Batch {batch_num} failed: {e}")
                # Fill with None for failed batches
                all_embeddings.extend([None] * len(batch))
        
        # 3. Generate chunk summaries if enabled (only for chunk type)
        chunk_summaries = []
        if generate_summaries and chunk_type == "chunk":
            print(f"Generating summaries for {len(chunks)} chunks...")
            chunk_summaries = generate_chunk_summaries_batch(chunks)
        
        # 4. Prepare bulk insert data
        objects_to_insert = []
        valid_embeddings = []  # Track valid embeddings for document-level averaging
        
        for idx, (chunk_text_value, embedding) in enumerate(zip(chunks, all_embeddings)):
            if embedding is None:
                print(f"  [WARN] Skipping {chunk_type} {idx} (embedding failed)")
                continue
            
            valid_embeddings.append(embedding)
            
            if chunk_type == "chunk":
                obj_data = {
                    "document_id": doc.id,
                    "chunk_index": idx,
                    "chunk_text": chunk_text_value,
                    "embedding": embedding
                }
                # Add summary if available
                if chunk_summaries and idx < len(chunk_summaries):
                    obj_data["summary_text"] = chunk_summaries[idx]
                objects_to_insert.append(obj_data)
            else:
                obj_data = {
                    "chunk_id": doc.id,
                    "sent_index": idx,
                    "sent_text": chunk_text_value,
                    "embedding": embedding
                }
                objects_to_insert.append(obj_data)
        
        # 5. Bulk insert
        success_count = 0
        if objects_to_insert:
            try:
                # Create ORM objects for bulk insert
                orm_objects = []
                for obj_data in objects_to_insert:
                    if chunk_type == "chunk":
                        obj = EMBED_TABLE(**obj_data)
                    else:
                        obj = SENTENCE_TABLE(**obj_data)
                    orm_objects.append(obj)
                
                # Bulk save objects (faster than individual commits)
                db.bulk_save_objects(orm_objects)
                db.commit()
                success_count = len(orm_objects)
                print(f"✓ Inserted {success_count}/{len(chunks)} {chunk_type}s")
            except Exception as e:
                db.rollback()
                print(f"[ERROR] Bulk insert failed: {e}")
                # Fallback: try individual inserts
                print("  Falling back to individual inserts...")
                for obj_data in objects_to_insert:
                    try:
                        if chunk_type == "chunk":
                            obj = EMBED_TABLE(**obj_data)
                        else:
                            obj = SENTENCE_TABLE(**obj_data)
                        db.add(obj)
                        db.commit()
                        success_count += 1
                    except Exception as e2:
                        db.rollback()
                        print(f"  [WARN] Failed to insert {chunk_type} {obj_data.get('chunk_index', obj_data.get('sent_index'))}: {e2}")
        
        # 6. Recursively embed sentences for chunks
        if chunk_type == "chunk" and success_count > 0:
            # Get inserted chunks and embed their sentences
            inserted_chunks = (
                db.query(EMBED_TABLE)
                .filter(EMBED_TABLE.document_id == doc.id)
                .order_by(EMBED_TABLE.chunk_index)
                .all()
            )
            
            for chunk in inserted_chunks:
                embed_doc_chunks(chunk, chunk_type="sent", batch_size=batch_size)
            
            # 7. Create document-level record with average embedding and summary
            _create_document_embedding_record(
                db=db,
                doc=doc,
                chunk_embeddings=valid_embeddings,
                chunk_summaries=chunk_summaries
            )
        
        return success_count
        
    except Exception as e:
        db.rollback()
        print(f"[ERROR] Failed to embed {chunk_type}s for doc {doc.id}: {e}")
        raise
    finally:
        try:
            next(db_gen)
        except StopIteration:
            pass


def _create_document_embedding_record(
    db: Session,
    doc: Document,
    chunk_embeddings: List[List[float]],
    chunk_summaries: List[str]
) -> bool:
    """
    Create a document-level embedding record with average embedding and aggregated summary.
    
    Args:
        db: Database session
        doc: Document being processed
        chunk_embeddings: List of chunk embedding vectors
        chunk_summaries: List of chunk summary strings
    
    Returns:
        True if record created successfully, False otherwise
    """
    if not chunk_embeddings:
        return False
    
    try:
        # Compute average embedding
        embeddings_array = np.array(chunk_embeddings)
        avg_embedding = np.mean(embeddings_array, axis=0)
        
        # L2-normalize the average embedding
        from .mmr import l2_normalize_vector
        avg_embedding = l2_normalize_vector(avg_embedding)
        
        # Generate document summary from chunk summaries
        doc_summary = None
        if chunk_summaries and PREFERENCES.summary.generate_document_summaries:
            # Get document title if available
            doc_title = getattr(doc, 'title', None)
            doc_summary = generate_document_summary(chunk_summaries, title=doc_title)
        
        # Check if document record already exists
        existing = (
            db.query(DOCUMENT_TABLE)
            .filter(DOCUMENT_TABLE.document_id == doc.id)
            .first()
        )
        
        if existing:
            # Update existing record
            existing.embedding = avg_embedding.tolist()
            if doc_summary:
                existing.summary_text = doc_summary
            db.commit()
            print(f"✓ Updated document embedding record for {doc.id}")
        else:
            # Create new record
            doc_record = DOCUMENT_TABLE(
                document_id=doc.id,
                summary_text=doc_summary,
                embedding=avg_embedding.tolist()
            )
            db.add(doc_record)
            db.commit()
            print(f"✓ Created document embedding record for {doc.id}")
        
        return True
        
    except Exception as e:
        db.rollback()
        print(f"[WARN] Failed to create document embedding record: {e}")
        return False

def backfill_chunk_summaries(batch_size: int = 50, max_workers: int = None) -> dict:
    """
    Backfill summaries for chunks that don't have them using parallel batch processing.
    
    Args:
        batch_size: Number of chunks to process in each batch (default: 50)
        max_workers: Number of parallel workers (default: auto-detect based on provider)
    
    Returns:
        Dict with statistics: {total: int, updated: int, skipped: int, errors: int}
    """
    db_gen = get_db()
    db: Session = next(db_gen)
    
    stats = {"total": 0, "updated": 0, "skipped": 0, "errors": 0}
    
    try:
        # Find chunks without summaries - just get IDs and text
        chunks_without_summaries = (
            db.query(EMBED_TABLE.id, EMBED_TABLE.chunk_text)
            .filter(EMBED_TABLE.summary_text == None)
            .filter(EMBED_TABLE.chunk_text != None)
            .all()
        )
        
        stats["total"] = len(chunks_without_summaries)
        
        print(f"\n{'='*60}")
        print(f"Backfilling chunk summaries (parallel batch processing)")
        print(f"Chunks to process: {stats['total']}")
        print(f"Batch size: {batch_size}")
        print(f"{'='*60}\n")
        
        if not chunks_without_summaries:
            print("No chunks need summary backfill.")
            return stats
        
        # Process in batches
        chunk_data = [(c[0], c[1]) for c in chunks_without_summaries]  # (id, text) tuples
        
        for batch_start in range(0, len(chunk_data), batch_size):
            batch = chunk_data[batch_start:batch_start + batch_size]
            batch_ids = [c[0] for c in batch]
            batch_texts = [c[1] for c in batch]
            
            batch_num = batch_start // batch_size + 1
            total_batches = (len(chunk_data) + batch_size - 1) // batch_size
            print(f"\nBatch {batch_num}/{total_batches}: Processing {len(batch)} chunks...")
            
            # Generate summaries in parallel
            summaries = generate_chunk_summaries_batch(
                batch_texts, 
                max_workers=max_workers,
                parallel=True
            )
            
            # Update database with generated summaries
            for chunk_id, summary in zip(batch_ids, summaries):
                try:
                    if summary:
                        db.query(EMBED_TABLE).filter(EMBED_TABLE.id == chunk_id).update(
                            {"summary_text": summary},
                            synchronize_session="fetch"
                        )
                        stats["updated"] += 1
                    else:
                        stats["skipped"] += 1
                except Exception as e:
                    print(f"  [ERROR] Updating chunk {chunk_id}: {e}")
                    stats["errors"] += 1
            
            # Commit after each batch
            try:
                db.commit()
                print(f"  ✓ Batch {batch_num} committed: {stats['updated']} total updated")
            except Exception as e:
                db.rollback()
                print(f"  [ERROR] Batch commit failed: {e}")
                stats["errors"] += len(batch)
        
        print(f"\n{'='*60}")
        print(f"Chunk summary backfill complete: {stats}")
        print(f"{'='*60}\n")
        
        return stats
        
    finally:
        try:
            next(db_gen)
        except StopIteration:
            pass


def backfill_document_embeddings(generate_summaries: bool = False) -> dict:
    """
    Comprehensive backfill for document-level embeddings and summaries.
    
    This function:
    1. If generate_summaries=True, first backfills chunk summaries for chunks that don't have them
    2. Creates DOCUMENT_TABLE records for documents that don't have them
    3. If generate_summaries=True, updates existing DOCUMENT_TABLE records with null summary_text
    
    Args:
        generate_summaries: If True, generate chunk and document summaries via LLM
    
    Returns:
        Dict with statistics
    """
    from .models import Document
    from .mmr import l2_normalize_vector
    
    db_gen = get_db()
    db: Session = next(db_gen)
    
    stats = {
        "chunk_summaries": {"total": 0, "updated": 0, "skipped": 0, "errors": 0},
        "doc_embeddings": {"processed": 0, "created": 0, "skipped": 0, "errors": 0},
        "doc_summaries": {"processed": 0, "updated": 0, "skipped": 0, "errors": 0}
    }
    
    try:
        # ===== PHASE 1: Backfill chunk summaries (if generating summaries) =====
        if generate_summaries:
            print("\n" + "="*60)
            print("PHASE 1: Backfilling chunk summaries (parallel batch)")
            print("="*60)
            
            # Find chunks without summaries - get IDs and text
            chunks_without_summaries = (
                db.query(EMBED_TABLE.id, EMBED_TABLE.chunk_text)
                .filter(EMBED_TABLE.summary_text == None)
                .filter(EMBED_TABLE.chunk_text != None)
                .all()
            )
            
            chunk_data = [(c[0], c[1]) for c in chunks_without_summaries]
            stats["chunk_summaries"]["total"] = len(chunk_data)
            
            print(f"Chunks needing summaries: {len(chunk_data)}")
            
            if chunk_data:
                # Process in batches of 50
                batch_size = 50
                for batch_start in range(0, len(chunk_data), batch_size):
                    batch = chunk_data[batch_start:batch_start + batch_size]
                    batch_ids = [c[0] for c in batch]
                    batch_texts = [c[1] for c in batch]
                    
                    batch_num = batch_start // batch_size + 1
                    total_batches = (len(chunk_data) + batch_size - 1) // batch_size
                    print(f"\n  Batch {batch_num}/{total_batches}: Processing {len(batch)} chunks...")
                    
                    # Generate summaries in parallel
                    summaries = generate_chunk_summaries_batch(batch_texts, parallel=True)
                    
                    # Update database with generated summaries
                    for chunk_id, summary in zip(batch_ids, summaries):
                        try:
                            if summary:
                                db.query(EMBED_TABLE).filter(EMBED_TABLE.id == chunk_id).update(
                                    {"summary_text": summary},
                                    synchronize_session="fetch"
                                )
                                stats["chunk_summaries"]["updated"] += 1
                            else:
                                stats["chunk_summaries"]["skipped"] += 1
                        except Exception as e:
                            print(f"  [ERROR] Updating chunk {chunk_id}: {e}")
                            stats["chunk_summaries"]["errors"] += 1
                    
                    # Commit after each batch
                    try:
                        db.commit()
                    except Exception as e:
                        db.rollback()
                        print(f"  [ERROR] Batch commit failed: {e}")
            
            print(f"✓ Chunk summaries complete: {stats['chunk_summaries']}")
        
        # ===== PHASE 2: Create document embeddings for docs that don't have them =====
        print("\n" + "="*60)
        print("PHASE 2: Creating document embeddings")
        print("="*60)
        
        # Find documents without document embeddings
        existing_doc_ids = db.query(DOCUMENT_TABLE.document_id).subquery()
        
        docs_without_embeddings = (
            db.query(Document)
            .filter(~Document.id.in_(db.query(existing_doc_ids)))
            .all()
        )
        
        print(f"Documents needing embeddings: {len(docs_without_embeddings)}")
        
        for i, doc in enumerate(docs_without_embeddings):
            stats["doc_embeddings"]["processed"] += 1
            
            try:
                # Get chunk embeddings for this document
                chunks = (
                    db.query(EMBED_TABLE)
                    .filter(EMBED_TABLE.document_id == doc.id)
                    .filter(EMBED_TABLE.embedding != None)
                    .all()
                )
                
                if not chunks:
                    print(f"  [SKIP] Doc {doc.id}: No chunk embeddings")
                    stats["doc_embeddings"]["skipped"] += 1
                    continue
                
                # Compute average embedding
                chunk_embeddings = [normalize_embedding(c.embedding) for c in chunks]
                embeddings_array = np.array(chunk_embeddings)
                avg_embedding = np.mean(embeddings_array, axis=0)
                avg_embedding = l2_normalize_vector(avg_embedding)
                
                # Generate document summary if enabled
                doc_summary = None
                if generate_summaries:
                    chunk_summaries = [c.summary_text for c in chunks if c.summary_text]
                    
                    if chunk_summaries:
                        doc_summary = generate_document_summary(chunk_summaries, title=doc.title)
                        if doc_summary:
                            print(f"  ✓ Generated doc summary: {doc.title[:40] if doc.title else 'untitled'}...")
                    else:
                        # Fallback: generate from raw text
                        chunk_texts = [c.chunk_text for c in chunks if c.chunk_text]
                        if chunk_texts:
                            max_chunks = PREFERENCES.summary.max_chunks_for_doc_summary
                            combined_text = "\n\n".join(chunk_texts[:max_chunks])
                            if len(combined_text) > 8000:
                                combined_text = combined_text[:8000] + "..."
                            doc_summary = _generate_summary_from_text(combined_text, title=doc.title)
                            if doc_summary:
                                print(f"  ✓ Generated doc summary (from text): {doc.title[:40] if doc.title else 'untitled'}...")
                
                # Create document record
                doc_record = DOCUMENT_TABLE(
                    document_id=doc.id,
                    summary_text=doc_summary,
                    embedding=avg_embedding.tolist()
                )
                db.add(doc_record)
                db.commit()
                
                stats["doc_embeddings"]["created"] += 1
                
                if (i + 1) % 10 == 0:
                    print(f"  Doc progress: {i + 1}/{len(docs_without_embeddings)} ({stats['doc_embeddings']['created']} created)")
                    
            except Exception as e:
                db.rollback()
                print(f"  [ERROR] Doc {doc.id}: {e}")
                stats["doc_embeddings"]["errors"] += 1
        
        print(f"✓ Document embeddings complete: {stats['doc_embeddings']}")
        
        # ===== PHASE 3: Update existing doc records with null summary_text =====
        if generate_summaries:
            print("\n" + "="*60)
            print("PHASE 3: Updating document summaries")
            print("="*60)
            
            # Find doc records with null summary
            docs_needing_summary = (
                db.query(DOCUMENT_TABLE.id, DOCUMENT_TABLE.document_id)
                .filter(DOCUMENT_TABLE.summary_text == None)
                .all()
            )
            
            print(f"Document records needing summaries: {len(docs_needing_summary)}")
            
            for i, (record_id, document_id) in enumerate(docs_needing_summary):
                stats["doc_summaries"]["processed"] += 1
                
                try:
                    # Get the parent document
                    doc = db.query(Document).filter(Document.id == document_id).first()
                    if not doc:
                        stats["doc_summaries"]["skipped"] += 1
                        continue
                    
                    # Get chunks with summaries
                    chunks = (
                        db.query(EMBED_TABLE)
                        .filter(EMBED_TABLE.document_id == document_id)
                        .all()
                    )
                    
                    chunk_summaries = [c.summary_text for c in chunks if c.summary_text]
                    
                    doc_summary = None
                    if chunk_summaries:
                        doc_summary = generate_document_summary(chunk_summaries, title=doc.title)
                    else:
                        # Fallback to raw text
                        chunk_texts = [c.chunk_text for c in chunks if c.chunk_text]
                        if chunk_texts:
                            max_chunks = PREFERENCES.summary.max_chunks_for_doc_summary
                            combined_text = "\n\n".join(chunk_texts[:max_chunks])
                            if len(combined_text) > 8000:
                                combined_text = combined_text[:8000] + "..."
                            doc_summary = _generate_summary_from_text(combined_text, title=doc.title)
                    
                    if doc_summary:
                        db.query(DOCUMENT_TABLE).filter(DOCUMENT_TABLE.id == record_id).update(
                            {"summary_text": doc_summary},
                            synchronize_session="fetch"
                        )
                        db.commit()
                        stats["doc_summaries"]["updated"] += 1
                        print(f"  ✓ Updated: {doc.title[:40] if doc.title else 'untitled'}...")
                    else:
                        stats["doc_summaries"]["skipped"] += 1
                    
                    if (i + 1) % 10 == 0:
                        print(f"  Summary progress: {i + 1}/{len(docs_needing_summary)} ({stats['doc_summaries']['updated']} updated)")
                        
                except Exception as e:
                    db.rollback()
                    print(f"  [ERROR] Doc record {record_id}: {e}")
                    stats["doc_summaries"]["errors"] += 1
            
            print(f"✓ Document summaries complete: {stats['doc_summaries']}")
        
        # ===== Final Summary =====
        print("\n" + "="*60)
        print("BACKFILL COMPLETE")
        print("="*60)
        print(f"Chunk summaries: {stats['chunk_summaries']}")
        print(f"Doc embeddings:  {stats['doc_embeddings']}")
        print(f"Doc summaries:   {stats['doc_summaries']}")
        print("="*60 + "\n")
        
        return stats
        
    finally:
        try:
            next(db_gen)
        except StopIteration:
            pass


def new_embedding_model_example():
    # Example: after containers are up and DB is reachable
    from .db import engine
    from .models import get_or_create_embedding_class

    meta = get_or_create_embedding_class(
        model_name="bge-small-en",
        version="v1",
        dim=512,
        db = get_db
    )

    print("Created/registered embedding table at:", meta.table_location)







