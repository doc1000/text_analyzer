## \app\helpers.py

from typing import List, Optional, Literal
import spacy
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
import tempfile
import requests
import pdfplumber
nlp = spacy.load("en_core_web_sm")
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
    if len(sent) <= max_tokens:
        return [sent]

    chunks = []
    offset = 0
    while offset < len(sent):
        chunk = sent[offset: offset + max_tokens]  # token-based slice
        chunks.append(chunk)
        offset += max_tokens
    return chunks

def split_doc_sentences(doc, max_tokens=MAX_CHARS_PER_CHUNK):
    for sent in doc.sents:
        s = sent.text.strip()
        if len(s) <= max_tokens:
            yield s
        else:
            # yield sub-spans of the long sentence
            yield from split_long_sentence(s, max_tokens)


def chunk_text(text: str, max_char: int = MAX_CHARS_PER_CHUNK) -> List[str]:
    """
    Very simple sentence-based chunker: walks spaCy sentences
    and groups them up to ~MAX_CHARS_PER_CHUNK.
    """
    doc = nlp(text)
    doc = list(split_doc_sentences(doc, max_tokens=max_char))
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0

    for s in doc: #.sents:
        #s = sent.text.strip()

        if not s:
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
    """Get or create OpenAI client with current API key.
    
    Only creates client if API key is available. Returns None if no key is set.
    """
    global _client_instance
    api_key = os.getenv("OPENAI_API_KEY")
    
    # Don't create client if no API key is set
    if not api_key or api_key.strip() == "":
        _client_instance = None
        return None
    
    # Create or update client if key changed
    if _client_instance is None or (hasattr(_client_instance, 'api_key') and _client_instance.api_key != api_key):
        _client_instance = OpenAI(api_key=api_key)
    return _client_instance

def update_openai_client(api_key: str):
    """Update the OpenAI client with a new API key."""
    global _client_instance
    if api_key and api_key.strip():
        os.environ["OPENAI_API_KEY"] = api_key
        _client_instance = OpenAI(api_key=api_key)
    else:
        # Clear the client if empty key provided
        os.environ.pop("OPENAI_API_KEY", None)
        _client_instance = None

def _openai_chat(prompt: str) -> str:
    client = get_openai_client()
    if client is None:
        raise RuntimeError("OpenAI API key not set. Please configure it in settings or set OPENAI_API_KEY environment variable.")
    
    model_name = PREFERENCES.models.llm_model
    
    # Ensure we're using a valid OpenAI model (not an Ollama model name)
    # Valid OpenAI models start with "gpt-" or "o1-"
    openai_models = ["gpt-4.1-nano", "gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini", "gpt-4o"]
    if model_name not in openai_models:
        # Fallback to a default OpenAI model if llm_model is set to an Ollama model
        model_name = "gpt-4o-mini"
    
    resp = client.chat.completions.create(
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
    
    # Get max context size from config based on chat provider
    # OpenAI gets longer context limits than Ollama
    chat_provider = getattr(PREFERENCES.models, "chat_provider", "ollama")
    max_context_chars = PREFERENCES.query.get_max_context_chars(chat_provider)
    max_prompt_chars = PREFERENCES.query.get_max_prompt_chars(chat_provider)
    
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

    if chat_provider == "ollama":
        text = _ollama_chat(prompt)
    else:
        text = _openai_chat(prompt)
    return text

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
    """
    embedding_provider = getattr(PREFERENCES.models, "embedding_provider", "ollama")
    if embedding_provider == "ollama":
        vec = _ollama_embed(text)
    else:
        # OpenAI embeddings
        client = get_openai_client()
        if client is None:
            raise RuntimeError("OpenAI API key not set. Please configure it in settings or set OPENAI_API_KEY environment variable.")
        model_name = PREFERENCES.models.embedding_model
        resp = client.embeddings.create(model=model_name, input=text)
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


def get_embeddings_batch(texts: List[str]) -> List[List[float]]:
    """
    Get embeddings for multiple texts in a single API call.
    
    This is much faster than calling get_embedding() multiple times.
    Supports both OpenAI and Ollama providers.
    
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
    
    # OpenAI batch embedding
    client = get_openai_client()
    if client is None:
        raise RuntimeError("OpenAI API key not set. Please configure it in settings or set OPENAI_API_KEY environment variable.")
    model_name = PREFERENCES.models.embedding_model
    resp = client.embeddings.create(
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
from .db import EMBED_TABLE, SENTENCE_TABLE, EMBED_DIM, EMBED_MODEL

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


def parse_pdf_from_url(pdf_url: str, max_pages: Optional[int] = None) -> str:
    """
    Download and parse text from a PDF URL.
    
    Args:
        pdf_url: URL of the PDF to download and parse
        max_pages: Maximum number of pages to parse (None = all pages)
    
    Returns:
        Extracted text from the PDF
    """
    try:
        print(f"[INFO] Downloading PDF from {pdf_url}")
        
        # Download PDF with timeout and user agent
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(pdf_url, headers=headers, timeout=30, stream=True)
        response.raise_for_status()
        
        # Check content type
        content_type = response.headers.get('content-type', '').lower()
        if 'pdf' not in content_type and not pdf_url.lower().endswith('.pdf'):
            print(f"[WARN] URL may not be a PDF: {content_type}")
        
        # Save to temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
            tmp_file.write(response.content)
            tmp_path = tmp_file.name
        
        try:
            # Parse PDF
            print(f"[INFO] Parsing PDF from {pdf_url}")
            text_parts = []
            
            with pdfplumber.open(tmp_path) as pdf:
                total_pages = len(pdf.pages)
                pages_to_parse = min(total_pages, max_pages) if max_pages else total_pages
                
                print(f"[INFO] PDF has {total_pages} pages, parsing {pages_to_parse} pages")
                
                for i, page in enumerate(pdf.pages[:pages_to_parse]):
                    try:
                        page_text = page.extract_text()
                        if page_text:
                            text_parts.append(page_text)
                    except Exception as e:
                        print(f"[WARN] Failed to extract text from page {i+1}: {e}")
                        continue
            
            extracted_text = "\n\n".join(text_parts)
            print(f"[INFO] Extracted {len(extracted_text)} characters from PDF")
            
            return extracted_text
            
        finally:
            # Clean up temporary file
            try:
                os.unlink(tmp_path)
            except Exception as e:
                print(f"[WARN] Failed to delete temp file {tmp_path}: {e}")
                
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Failed to download PDF from {pdf_url}: {e}")
        return ""
    except Exception as e:
        print(f"[ERROR] Failed to parse PDF from {pdf_url}: {e}")
        import traceback
        traceback.print_exc()
        return ""


def parse_pdfs_from_urls(pdf_urls: List[str], max_pages_per_pdf: Optional[int] = None) -> str:
    """
    Parse multiple PDFs from URLs and combine their text.
    
    Args:
        pdf_urls: List of PDF URLs to parse
        max_pages_per_pdf: Maximum number of pages to parse per PDF
    
    Returns:
        Combined text from all PDFs
    """
    if not pdf_urls:
        return ""
    
    all_texts = []
    for i, pdf_url in enumerate(pdf_urls, 1):
        print(f"[INFO] Parsing PDF {i}/{len(pdf_urls)}: {pdf_url}")
        pdf_text = parse_pdf_from_url(pdf_url, max_pages=max_pages_per_pdf)
        if pdf_text:
            # Add separator between PDFs
            if all_texts:
                all_texts.append(f"\n\n--- PDF {i} ({pdf_url}) ---\n\n")
            all_texts.append(pdf_text)
        else:
            print(f"[WARN] No text extracted from PDF {i}: {pdf_url}")
    
    return "\n\n".join(all_texts)
                

def embed_doc_chunks(
    doc: Document | type[EMBED_TABLE],
    chunk_type: Literal["chunk", "sent"] = "chunk",
    batch_size: int = None
) -> int:
    """
    Embed chunks/sentences using batch processing for much better performance.
    
    Args:
        doc: Document or chunk to embed
        chunk_type: "chunk" or "sent"
        batch_size: Number of texts to embed per API call (defaults to config)
    
    Returns:
        Number of chunks successfully embedded
    """
    if batch_size is None:
        batch_size = PREFERENCES.embedding.batch_size
    
    db_gen = get_db()
    db: Session = next(db_gen)
    
    try:
        # 1. Chunk the text
        if chunk_type == "chunk":
            max_char = MAX_CHARS_PER_CHUNK
            text_input = doc.full_text
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
        
        # 3. Prepare bulk insert data
        objects_to_insert = []
        
        for idx, (chunk_text_value, embedding) in enumerate(zip(chunks, all_embeddings)):
            if embedding is None:
                print(f"  [WARN] Skipping {chunk_type} {idx} (embedding failed)")
                continue
            
            if chunk_type == "chunk":
                obj_data = {
                    "document_id": doc.id,
                    "chunk_index": idx,
                    "chunk_text": chunk_text_value,
                    "embedding": embedding
                }
                objects_to_insert.append(obj_data)
            else:
                obj_data = {
                    "chunk_id": doc.id,
                    "sent_index": idx,
                    "sent_text": chunk_text_value,
                    "embedding": embedding
                }
                objects_to_insert.append(obj_data)
        
        # 4. Bulk insert
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
        
        # 5. Recursively embed sentences for chunks
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







