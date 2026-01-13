# app/topics.py

import os
from datetime import datetime, timedelta
from typing import List, Dict, Tuple
from uuid import UUID
import numpy as np
from sqlalchemy.orm import Session
from sqlalchemy import func
from sklearn.metrics.pairwise import cosine_similarity
import umap
from sklearn.decomposition import PCA
from sklearn.cluster import KMeans
from openai import OpenAI
from urllib.parse import urlsplit, urlunsplit, parse_qsl
from .config import PREFERENCES
from .models import Document, get_or_create_embedding_class
from .db import get_db
from .schemas import (         # whatever pydantic models you use
    Topic,
    Subtopic,
    TopicDoc,
    TopicsResponse,
)
from .helpers import _openai_chat, _ollama_chat, get_embedding, get_openai_client, update_openai_client
from .db import EMBED_TABLE, TOPIC_TABLE
# Simple in-memory cache for topics per (days, max_captured_at)
_topics_cache: dict[tuple[int, datetime | None], TopicsResponse] = {}



# ---------- Dimensionality reduction ----------
def reduce_embeddings(X: np.ndarray) -> np.ndarray:
    """
    Reduce embeddings according to preferences:
    - 'pca': PCA with bounded components
    - 'umap': UMAP with sane bounds and random init
    - 'none': return X unchanged
    """
    cfg = PREFERENCES.clustering
    n_docs, dim = X.shape

    if cfg.dim_reducer == "none":
        return X

    # Decide how many components we can reasonably take
    max_components = max(2, cfg.max_components)
    n_components = min(max_components, dim, n_docs)

    # If we can't get at least 2 components, just return X as-is
    if n_components < 2:
        return X

    if cfg.dim_reducer == "pca":
        pca = PCA(
            n_components=n_components,
            random_state=cfg.random_state,
        )
        return pca.fit_transform(X)

    if cfg.dim_reducer == "umap":
        const_neighbors = max(2, min(cfg.max_neighbors, n_docs - 1))

        reducer = umap.UMAP(
            n_neighbors=const_neighbors,
            min_dist=0.1,
            n_components=n_components,
            metric="cosine",
            random_state=cfg.random_state,
            init="random",  # avoids spectral init / eigsh issues
        )
        return reducer.fit_transform(X)

    # Fallback if someone puts an unexpected value in config
    return X

# ---------- Clustering ----------
def _choose_k(num_docs: int, k_min: int, k_max: int) -> int:
    # Simple heuristic: sqrt-like scaling with clamps
    #base = int(np.sqrt(num_docs / 2))
    base = int(np.sqrt(num_docs * 2)) #prev clamp was too restrictive, putting together docs not that similar
    return max(k_min, min(k_max, base))


def _cluster_kmeans(X: np.ndarray) -> np.ndarray:
    """
    KMeans clustering: return labels array of shape (n_samples,)
    """
    cfg = PREFERENCES.clustering
    n_docs = X.shape[0]
    k_topics = _choose_k(n_docs, cfg.k_topics_min, cfg.k_topics_max)

    kmeans = KMeans(
        n_clusters=k_topics,
        random_state=cfg.random_state,
        n_init="auto",
    )
    return kmeans.fit_predict(X)


def cluster_embeddings(X: np.ndarray) -> np.ndarray:
    """
    Main clustering entry point. Uses preferences to decide:
    - whether to run UMAP first for clustering
    - which clustering algorithm to use (currently KMeans).
    """
    cfg = PREFERENCES.clustering

    # Decide which space to cluster in
    if cfg.use_reducer_for_clustering:
        X_cluster = reduce_embeddings(X)
    else:
        X_cluster = X

    if cfg.cluster_algo == "kmeans":
        return _cluster_kmeans(X_cluster)

    # Fallback / future expansion
    raise ValueError(f"Unsupported cluster_algo: {cfg.cluster_algo}")

def _get_sentence_embeddings_for_docs(
    db: Session,
    doc_ids: List[UUID],
) -> Tuple[List[str], List[np.ndarray]]:
    """
    Fetch all sentence embeddings for a set of documents.
    
    Returns:
        sentences: List of sentence texts
        embeddings: List of sentence embedding vectors
    """
    from .db import SENTENCE_TABLE
    
    # Query all sentences for these documents via their chunks
    # SENTENCE_TABLE has chunk_id -> EMBED_TABLE has document_id
    chunk_ids_query = (
        db.query(EMBED_TABLE.id)
        .filter(EMBED_TABLE.document_id.in_(doc_ids))
        #.subquery()
    )
    
    sentences_rows = (
        db.query(SENTENCE_TABLE.sent_text, SENTENCE_TABLE.embedding)
        .filter(SENTENCE_TABLE.chunk_id.in_(chunk_ids_query))
        .filter(SENTENCE_TABLE.embedding != None)
        .all()
    )
    
    if not sentences_rows:
        return [], []
    
    sentences = []
    embeddings = []
    
    for sent_text, sent_emb in sentences_rows:
        if sent_emb is None:
            continue
        vec = normalize_embedding(sent_emb)
        sentences.append(sent_text)
        embeddings.append(vec)
    
    return sentences, embeddings


def _generate_title_and_summary_mmr(
    db: Session,
    docs: List[Document],
    doc_embeddings: Dict[UUID, np.ndarray],
) -> Tuple[str, str]:
    """
    Use MMR-based sentence selection to generate a short title for a cluster.
    
    This approach:
    1. Fetches sentence embeddings for all documents in cluster
    2. Computes cluster centroid from document embeddings
    3. Uses MMR to select k representative sentences (high relevance, low redundancy)
    4. Sends compact prompt (~1000-1500 chars) to LLM
    5. Returns generated title and summary
    
    Args:
        db: Database session
        docs: Documents in the cluster
        doc_embeddings: Pre-computed document embeddings (from compute_document_embeddings)
    
    Returns:
        (title, summary) tuple
    """
    from .mmr import mmr_select, compute_centroid, l2_normalize
    
    cfg = PREFERENCES.mmr
    
    if not docs:
        return "Miscellaneous", "Mixed documents."
    
    # Limit number of docs to avoid overwhelming MMR
    if len(docs) > cfg.max_docs_to_process:
        docs = docs[:cfg.max_docs_to_process]
    
    doc_ids = [d.id for d in docs]
    
    # Get sentence embeddings for these documents
    sentences, sent_embeddings = _get_sentence_embeddings_for_docs(db, doc_ids)
    
    # Fallback: if no sentence embeddings, use old approach
    if len(sentences) < cfg.min_sentences_for_mmr:
        return _generate_title_and_summary_fallback(docs)
    
    # Compute cluster centroid from document embeddings
    doc_emb_list = [doc_embeddings[d.id] for d in docs if d.id in doc_embeddings]
    if not doc_emb_list:
        return _generate_title_and_summary_fallback(docs)
    
    centroid = compute_centroid(doc_emb_list, normalize=True)
    
    # Run MMR to select diverse, relevant sentences
    selected_indices = mmr_select(
        embeddings=sent_embeddings,
        query_embedding=centroid,
        k=cfg.k_sentences,
        lambda_=cfg.lambda_param,
        normalize=True
    )
    
    # Build context from selected sentences
    selected_sentences = [sentences[i] for i in selected_indices]
    context = "\n".join(f"- {s}" for s in selected_sentences)
    
    # Truncate if needed to respect max_prompt_chars
    if len(context) > cfg.max_prompt_chars:
        context = context[:cfg.max_prompt_chars] + "..."
    
    # Simplified prompt optimized for small models
    prompt = (
        "You are categorizing a cluster of documents.\n"
        "Below are the most representative sentences from this cluster.\n"
        "Create a SHORT topic title (3-6 words max) that captures the BROAD, HIGH-LEVEL theme connecting these documents.\n"
        "Focus on the overarching concept or pattern, NOT specific details or particulars.\n"
        "Think about what connects these documents at a conceptual level - what is the common thread?\n"
        "Examples: 'Machine Learning Research', 'Financial Planning', 'Health & Wellness', 'Product Development'\n\n"
        "REPRESENTATIVE SENTENCES:\n"
        f"{context}\n\n"
        "Respond ONLY with:\n"
        "TITLE: <your title>"
    )
    
    # Send to LLM - use topic_model for faster topic generation
    topic_provider = getattr(PREFERENCES.models, "topic_provider", "ollama")
    if topic_provider == "ollama":
        ollama_options = {
            "temperature": 0.6,
            "top_p": 0.8,
        }
        # Use topic_model for topic generation (faster latency)
        topic_model = PREFERENCES.models.ollama.topic_model
        text = _ollama_chat(prompt, ollama_options, model=topic_model)
    else:
        text = _openai_chat(prompt)
    
    # Parse response
    title = None
    summary = ""
    
    # Try to extract title from response
    for line in text.splitlines():
        line_upper = line.upper().strip()
        if line_upper.startswith("TITLE:"):
            parsed_title = line.split(":", 1)[1].strip()
            # Only accept non-empty titles that aren't placeholder text
            if parsed_title and parsed_title.lower() not in ["untitled topic", "untitled", "no title", ""]:
                title = parsed_title
        elif line_upper.startswith("SUMMARY:"):
            summary = line.split(":", 1)[1].strip() or summary
    
    # If no title found in structured format, try to extract from first line
    if not title:
        first_line = text.splitlines()[0].strip() if text.splitlines() else ""
        # If first line looks like a title (not too long, not placeholder)
        if first_line and len(first_line) < 200 and first_line.lower() not in ["untitled topic", "untitled", "no title"]:
            title = first_line
    
    # Fallback to document titles if LLM didn't provide a valid title
    # Always generate fallback title as backup
    fallback_title = _generate_title_from_document_titles(docs)
    
    if not title or title.lower().strip() in ["untitled topic", "untitled", "no title", ""]:
        title = fallback_title
    elif title.lower().strip() == fallback_title.lower().strip():
        # LLM just returned our fallback, use it directly
        title = fallback_title
    # else: use the LLM-generated title
    
    # Final safety check - ensure we never return "Untitled Topic" or empty
    title_lower = title.lower().strip() if title else ""
    if not title or title_lower in ["untitled topic", "untitled", "no title", ""]:
        title = fallback_title if fallback_title else f"Topic with {len(docs)} documents"
    
    if not summary:
        summary = "Summary not available."
    
    return title, summary


def _generate_title_from_document_titles(docs: List[Document], max_words: int = 6) -> str:
    """
    Generate a title from document titles as a final fallback.
    Creates a concise title from the first few document titles.
    Always returns a non-empty title string.
    
    Args:
        docs: List of documents
        max_words: Maximum words in the title
    
    Returns:
        A title string (never empty, never "Untitled Topic")
    """
    if not docs:
        return "Miscellaneous"
    
    # Collect non-empty titles
    titles = [d.title.strip() for d in docs if d.title and d.title.strip()]
    
    if not titles:
        # No titles available, use URL domains or generic
        domains = []
        for d in docs[:5]:
            if d.url:
                try:
                    domain = urlsplit(d.url).netloc
                    if domain and domain not in domains:
                        domains.append(domain)
                except:
                    pass
        
        if domains:
            return f"Documents from {', '.join(domains[:3])}"
        return f"Cluster of {len(docs)} documents"
    
    # If we have titles, create a concise combination
    if len(titles) == 1:
        # Single title - truncate if needed
        title = titles[0]
        if not title or title.lower() in ["untitled", "no title", ""]:
            # Even the single title is invalid, use domain fallback
            if docs[0].url:
                try:
                    domain = urlsplit(docs[0].url).netloc
                    if domain:
                        return f"Document from {domain}"
                except:
                    pass
            return f"Single document cluster"
        words = title.split()
        if len(words) > max_words:
            return " ".join(words[:max_words]) + "..."
        return title
    
    # Multiple titles - create a combined title
    # Filter out any invalid titles
    valid_titles = [t for t in titles if t.lower() not in ["untitled", "no title", ""]]
    
    if not valid_titles:
        # All titles were invalid, use domain fallback
        domains = []
        for d in docs[:3]:
            if d.url:
                try:
                    domain = urlsplit(d.url).netloc
                    if domain and domain not in domains:
                        domains.append(domain)
                except:
                    pass
        if domains:
            return f"Documents from {', '.join(domains[:2])}"
        return f"Cluster of {len(docs)} documents"
    
    # Use valid titles
    if len(valid_titles) <= 3:
        # Small number - just combine
        combined = " / ".join(valid_titles[:3])
        words = combined.split()
        if len(words) > max_words:
            return " ".join(words[:max_words]) + "..."
        return combined
    else:
        # Many titles - use first few with count
        first_titles = " / ".join(valid_titles[:2])
        words = first_titles.split()
        if len(words) > max_words - 3:
            words = words[:max_words - 3]
        return " ".join(words) + f" (+{len(valid_titles) - 2} more)"


def _generate_title_and_summary_fallback(docs: List[Document]) -> Tuple[str, str]:
    """
    Fallback title generation when MMR cannot be used (e.g., missing sentence embeddings).
    Uses the old approach: document titles + text snippets.
    """
    if not docs:
        return "Miscellaneous", "Mixed documents."
    
    # Take up to 10 docs, use titles + first snippet of full_text
    snippets = []
    for d in docs[:10]:
        title = d.title or "(no title)"
        body = (d.full_text or "")[:400].replace("\n", " ")
        snippets.append(f"Title: {title}\nSnippet: {body}")
    
    context = "\n\n".join(snippets)
    
    prompt = (
        "You are helping categorize a cluster of documents. "
        "Based on the titles and snippets below, create:\n"
        "A SHORT topic title (3-6 words max) that captures the BROAD, HIGH-LEVEL theme connecting these documents.\n"
        "Focus on the overarching concept or pattern that connects them, NOT specific details or particulars.\n"
        "Think about what connects these documents at a conceptual level - what is the common thread?\n"
        "Examples: 'Machine Learning Research', 'Financial Planning', 'Health & Wellness', 'Product Development'\n"
        "Do not return an exact copy of the document titles, but identify the broad theme.\n"
        "Respond in the format:\n"
        "TITLE: <short title>\n"
        f"DOCUMENTS:\n{context}"
    )
    
    topic_provider = getattr(PREFERENCES.models, "topic_provider", "ollama")
    ollama_title_options = {
        "temperature": 0.6,
        "top_p": 0.8,
    }
    if topic_provider == "ollama":
        # Use topic_model for topic generation (faster latency)
        topic_model = PREFERENCES.models.ollama.topic_model
        text = _ollama_chat(prompt, ollama_title_options, model=topic_model)
    else:
        text = _openai_chat(prompt)
    
    # Parse response
    title = None
    summary = ""
    
    # Try to extract title from response
    for line in text.splitlines():
        line_upper = line.upper().strip()
        if line_upper.startswith("TITLE:"):
            parsed_title = line.split(":", 1)[1].strip()
            # Only accept non-empty titles that aren't placeholder text
            if parsed_title and parsed_title.lower() not in ["untitled topic", "untitled", "no title", ""]:
                title = parsed_title
        elif line_upper.startswith("SUMMARY:"):
            summary = line.split(":", 1)[1].strip() or summary
    
    # If no title found in structured format, try to extract from first line
    if not title:
        first_line = text.splitlines()[0].strip() if text.splitlines() else ""
        # If first line looks like a title (not too long, not placeholder)
        if first_line and len(first_line) < 200 and first_line.lower() not in ["untitled topic", "untitled", "no title"]:
            title = first_line
    
    # Fallback to document titles if LLM didn't provide a valid title
    # Always generate fallback title as backup
    fallback_title = _generate_title_from_document_titles(docs)
    
    if not title or title.lower().strip() in ["untitled topic", "untitled", "no title", ""]:
        title = fallback_title
    elif title.lower().strip() == fallback_title.lower().strip():
        # LLM just returned our fallback, use it directly
        title = fallback_title
    # else: use the LLM-generated title
    
    # Final safety check - ensure we never return "Untitled Topic" or empty
    title_lower = title.lower().strip() if title else ""
    if not title or title_lower in ["untitled topic", "untitled", "no title", ""]:
        title = fallback_title if fallback_title else f"Topic with {len(docs)} documents"
    
    if not summary:
        summary = "Summary not available."
    
    return title, summary


def _generate_title_and_summary(
    docs: List[Document],
    db: Session = None,
    doc_embeddings: Dict[UUID, np.ndarray] = None,
) -> Tuple[str, str]:
    """
    Generate title and summary for a cluster of documents.
    
    Uses MMR-based sentence selection if db and doc_embeddings are provided,
    otherwise falls back to document snippet approach.
    
    Args:
        docs: Documents in the cluster
        db: Database session (optional, required for MMR)
        doc_embeddings: Pre-computed document embeddings (optional, required for MMR)
    
    Returns:
        (title, summary) tuple
    """
    # Try MMR approach if we have the required inputs
    if db is not None and doc_embeddings is not None:
        try:
            return _generate_title_and_summary_mmr(db, docs, doc_embeddings)
        except Exception as e:
            print(f"[WARN] MMR title generation failed, using fallback: {e}")
            return _generate_title_and_summary_fallback(docs)
    
    # Fallback approach
    return _generate_title_and_summary_fallback(docs)


# ---------- Topic Persistence ----------

def _save_topic_to_db(
    db: Session,
    title: str,
    centroid: np.ndarray,
    document_count: int,
    summary: str = None,
    parent_id: UUID = None,
    level_index: int = 0
) -> UUID:
    """
    Save a topic to the TOPIC_TABLE.
    
    Args:
        db: Database session
        title: Topic title
        centroid: Cluster centroid embedding
        document_count: Number of documents in cluster
        summary: Optional summary text
        parent_id: Parent topic ID (for subtopics)
        level_index: 0 for top-level, 1 for subtopics
    
    Returns:
        UUID of created topic
    """
    try:
        topic_record = TOPIC_TABLE(
            parent_id=parent_id,
            level_index=level_index,
            title_text=title,
            embedding=centroid.tolist(),
            document_count=document_count,
            summary_text=summary,
            match_count=0
        )
        
        db.add(topic_record)
        db.flush()  # Flush before commit to catch any errors early
        db.commit()
        db.refresh(topic_record)
        
        print(f"[DEBUG] Successfully saved topic '{title}' to database (ID: {topic_record.id})")
        return topic_record.id
    except Exception as e:
        db.rollback()
        print(f"[ERROR] Failed to save topic '{title}' to database: {e}")
        import traceback
        traceback.print_exc()
        raise


def _find_matching_topic(
    db: Session,
    centroid: np.ndarray,
    similarity_threshold: float = 0.85,
    level_index: int = 0
) -> Tuple[UUID, str, str] | None:
    """
    Find an existing topic with similar centroid.
    
    Args:
        db: Database session
        centroid: Cluster centroid to match against
        similarity_threshold: Minimum similarity to consider a match
        level_index: Topic level to search (0=topics, 1=subtopics)
    
    Returns:
        (topic_id, title, summary) if match found, else None
    """
    cfg = PREFERENCES.topic_persistence
    
    # Query topics at the same level, limit for performance
    existing_topics = (
        db.query(TOPIC_TABLE)
        .filter(TOPIC_TABLE.level_index == level_index)
        .order_by(TOPIC_TABLE.created_at.desc())
        .limit(cfg.max_existing_topics_to_check)
        .all()
    )
    
    if not existing_topics:
        print(f"[DEBUG] _find_matching_topic: No existing topics at level {level_index}")
        return None
    
    print(f"[DEBUG] _find_matching_topic: Checking {len(existing_topics)} existing topics (threshold: {similarity_threshold})")
    
    # Find best match using cosine similarity
    best_match = None
    best_similarity = similarity_threshold
    actual_best_similarity = 0.0  # Track the actual best even if below threshold
    
    for topic in existing_topics:
        topic_emb = normalize_embedding(topic.embedding)
        similarity = float(cosine_similarity(
            centroid.reshape(1, -1),
            topic_emb.reshape(1, -1)
        )[0, 0])
        
        if similarity > actual_best_similarity:
            actual_best_similarity = similarity
        
        if similarity > best_similarity:
            best_similarity = similarity
            best_match = topic
    
    if best_match:
        # Update match statistics
        best_match.last_matched_at = datetime.utcnow()
        best_match.match_count = (best_match.match_count or 0) + 1
        db.commit()
        
        print(f"[DEBUG] _find_matching_topic: Found match '{best_match.title_text}' (similarity: {best_similarity:.4f})")
        return (best_match.id, best_match.title_text, best_match.summary_text or "")
    
    print(f"[DEBUG] _find_matching_topic: No match above threshold (best similarity: {actual_best_similarity:.4f})")
    return None


def assign_document_to_best_topic(
    db: Session,
    doc: Document,
    similarity_threshold: float = 0.75
) -> bool:
    """
    Assign a document to the best matching existing topic based on semantic similarity.
    
    Args:
        db: Database session
        doc: Document to assign
        similarity_threshold: Minimum similarity to assign (default 0.75)
    
    Returns:
        True if document was assigned to a topic, False otherwise
    """
    try:
        # Skip if document already has a topic assignment
        if doc.assigned_topic_id:
            return True
        
        # Compute document embedding
        doc_embeddings = compute_document_embeddings(db, [doc])
        if doc.id not in doc_embeddings:
            print(f"[Topic Assignment] Document {doc.id} has no embeddings, skipping topic assignment")
            return False
        
        doc_embedding = doc_embeddings[doc.id]
        
        # Find best matching topic
        match_result = _find_matching_topic(
            db=db,
            centroid=doc_embedding,
            similarity_threshold=similarity_threshold,
            level_index=0  # Top-level topics only
        )
        
        if match_result:
            topic_id, topic_title, topic_summary = match_result
            # Assign document to topic
            doc.assigned_topic_id = topic_id
            doc.assigned_topic_title = topic_title
            db.commit()
            print(f"[Topic Assignment] Assigned document {doc.id} to topic: {topic_title}")
            return True
        else:
            print(f"[Topic Assignment] No matching topic found for document {doc.id} (threshold: {similarity_threshold})")
            return False
            
    except Exception as e:
        print(f"[Topic Assignment] Error assigning document {doc.id} to topic: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
        return False


# ---------- URL canonicalization ----------

def canonicalize_url(url: str) -> str:
    parts = urlsplit(url)
    # Drop utm_* and similar tracking params
    query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    clean_pairs = [
        (k, v) for (k, v) in query_pairs
        if not k.lower().startswith("utm_")
    ]
    clean_query = "&".join(f"{k}={v}" for k, v in clean_pairs)
    # Normalize (you can add more rules if you like)
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), clean_query, parts.fragment))


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



# ---------- Embeddings aggregation ----------
import ast
def compute_document_embeddings(
    db: Session,
    docs: List[Document],
) -> Dict[UUID, np.ndarray]:
    """
    Compute a single embedding per document by averaging its chunk embeddings.
    Returns a dict: {document_id: np.ndarray(dim)}.
    Skips docs that have no chunk embeddings.
    """
    if not docs:
        return {}

    doc_ids = [d.id for d in docs]
    emb_dim = len(get_embedding("dimension probe"))

    chunks = (
        db.query(EMBED_TABLE)
        .filter(EMBED_TABLE.document_id.in_(doc_ids))
        .filter(EMBED_TABLE.embedding != None)  # only chunks with embeddings
        .all()
    )
    
    by_doc: Dict[UUID, List[np.ndarray]] = {}
    for ch in chunks:
        # pgvector returns something list-like; convert to numpy array
        vec = normalize_embedding(ch.embedding)
        #lst = ast.literal_eval(ch.embedding)
        #vec = np.array(lst, dtype=np.float32)
        by_doc.setdefault(ch.document_id, []).append(vec)

    doc_embeds: Dict[UUID, np.ndarray] = {}
    for d in docs:
        vecs = by_doc.get(d.id)
        if not vecs:
            continue
        arr = np.stack(vecs, axis=0)
        mean_vec = arr.mean(axis=0)
        doc_embeds[d.id] = mean_vec

    return doc_embeds


# ---------- Semantic dedupe (keep latest) ----------

def dedupe_documents_semantic(
    docs: List[Document],
    embeddings: Dict[UUID, np.ndarray],
    similarity_threshold: float = 0.92,
) -> List[Document]:
    """
    Given documents + their embeddings, remove semantic duplicates.
    Keeps the latest captured_at document among duplicates.
    Returns the list of documents to keep.
    """
    # Only consider docs that have embeddings
    docs = [d for d in docs if d.id in embeddings]

    if len(docs) <= 1:
        return docs

    # Sort by captured_at descending (latest first)
    docs_sorted = sorted(docs, key=lambda d: d.captured_at or datetime.min, reverse=True)

    kept_ids: List[UUID] = []
    duplicate_ids: set[UUID] = set()

    # Pre-cache embeddings in same order
    emb_by_id = embeddings

    for i, d_i in enumerate(docs_sorted):
        if d_i.id in duplicate_ids:
            continue

        kept_ids.append(d_i.id)
        v_i = emb_by_id[d_i.id].reshape(1, -1)

        # Compare to all later docs in the list (older ones)
        for j in range(i + 1, len(docs_sorted)):
            d_j = docs_sorted[j]
            if d_j.id in duplicate_ids:
                continue

            v_j = emb_by_id[d_j.id].reshape(1, -1)
            sim = float(cosine_similarity(v_i, v_j)[0, 0])

            if sim >= similarity_threshold:
                duplicate_ids.add(d_j.id)

    kept_docs = [d for d in docs_sorted if d.id in kept_ids]
    return kept_docs


# ---------- High-level topics computation ----------

def compute_topics(
    db: Session,
    days: int = 30,
    min_cluster_size: int = 5,
    min_docs_for_clustering: int = 3,
) -> TopicsResponse:
    """
    Main entry point to compute topics for the last `days` days.
    """
    cfg = PREFERENCES.clustering
    min_docs_for_clustering = cfg.min_docs_for_clustering

    # 1) Select recent documents
    cutoff = datetime.utcnow() - timedelta(days=days)
    docs = (
        db.query(Document)
        .filter(Document.captured_at >= cutoff)
        .order_by(Document.captured_at.desc())
        .all()
    )

    if not docs:
        return TopicsResponse(time_range_days=days, topics=[])

    # 2) Deduplicate by canonical URL (keep latest for each canonical_url)
    canonical_groups: Dict[str, List[Document]] = {}
    for d in docs:
        cu = canonicalize_url(d.url or "")
        canonical_groups.setdefault(cu, []).append(d)

    canonical_docs: List[Document] = []
    for cu, group in canonical_groups.items():
        # docs were already ordered desc by captured_at
        group_sorted = sorted(group, key=lambda d: d.captured_at or datetime.min, reverse=True)
        canonical_docs.append(group_sorted[0])  # keep latest

    # 3) Compute document embeddings for canonical docs
    doc_embeddings = compute_document_embeddings(db, canonical_docs)

    # 4) Semantic dedupe (keeps latest among high-similarity docs)
    deduped_docs = dedupe_documents_semantic(canonical_docs, doc_embeddings)

    # 4b) Separate documents with existing topic assignments from unassigned
    # Pre-assigned documents will be grouped by their existing topic
    pre_assigned_groups: Dict[UUID, List[Document]] = {}
    unassigned_docs: List[Document] = []
    
    for d in deduped_docs:
        if d.assigned_topic_id:
            pre_assigned_groups.setdefault(d.assigned_topic_id, []).append(d)
        else:
            unassigned_docs.append(d)
    
    print(f"[Topics] {len(pre_assigned_groups)} existing topic groups, {len(unassigned_docs)} unassigned docs")

    # ---------- Check if we should force reclustering ----------
    # Force reclustering if number of pre-assigned groups exceeds k_topics_recluster
    # Use /topics/recluster endpoint to manually force fresh clustering
    total_pre_assigned_docs = sum(len(docs) for docs in pre_assigned_groups.values())
    should_recluster = len(pre_assigned_groups) > cfg.k_topics_recluster
    
    topics: List[Topic] = []
    topic_idx_counter = 0
    
    if should_recluster:
        print(f"[Topics] Forcing recluster: {len(pre_assigned_groups)} pre-assigned groups, {total_pre_assigned_docs} total docs (threshold: {cfg.k_topics_recluster})")
        # Collect all documents from pre-assigned groups and add them to unassigned_docs
        # Also clear their topic assignments so they get fresh assignments
        docs_to_recluster = []
        for topic_db_id, docs_in_group in pre_assigned_groups.items():
            for doc in docs_in_group:
                doc.assigned_topic_id = None
                doc.assigned_topic_title = None
            docs_to_recluster.extend(docs_in_group)
        unassigned_docs.extend(docs_to_recluster)
        # Commit the cleared assignments
        try:
            db.commit()
            print(f"[Topics] Cleared topic assignments for {len(docs_to_recluster)} documents")
        except Exception as e:
            db.rollback()
            print(f"[WARN] Failed to clear topic assignments: {e}")
    else:
        # Keep pre-assigned groups - create topics from them
        # Note: these will be simple single-subtopic topics, subtopics will be built later if needed
        print(f"[Topics] Keeping {len(pre_assigned_groups)} pre-assigned topic groups")
        for topic_db_id, docs_in_group in pre_assigned_groups.items():
            topic_title = docs_in_group[0].assigned_topic_title or "Assigned Topic"
            topic_id = f"T{topic_idx_counter}"
            topic_idx_counter += 1
            
            docs_out = [TopicDoc.model_validate(d) for d in docs_in_group]
            
            # Create a simple single-subtopic structure for pre-assigned groups
            topic = Topic(
                topic_id=topic_id,
                title=topic_title,
                summary=None,
                documents_count=len(docs_out),
                subtopics=[
                    Subtopic(
                        subtopic_id=f"{topic_id}-S0",
                        title=topic_title,
                        summary=None,
                        documents=docs_out,
                    )
                ],
            )
            topics.append(topic)
            print(f"♻ Reusing pre-assigned topic: {topic_title} ({len(docs_out)} docs)")
    
    # ---------- Cluster unassigned documents ----------
    # Filter unassigned docs to those with embeddings
    unassigned_docs = [d for d in unassigned_docs if d.id in doc_embeddings]
    
    if len(unassigned_docs) < min_docs_for_clustering:
        # Not enough unassigned docs to cluster - put them all in one topic
        if unassigned_docs:
            topic_docs = [TopicDoc.model_validate(d) for d in unassigned_docs]
            title, summary = _generate_title_and_summary(unassigned_docs, db=db, doc_embeddings=doc_embeddings)
            
            # Try to persist topic if persistence is enabled
            topic_db_id = None
            if PREFERENCES.topic_persistence.persist_topics:
                # Compute centroid for this small group
                from .mmr import compute_centroid
                doc_emb_list = [doc_embeddings[d.id] for d in unassigned_docs if d.id in doc_embeddings]
                centroid = compute_centroid(doc_emb_list, normalize=True) if doc_emb_list else None
                
                if centroid is not None:
                    # Check for existing match
                    existing_match = _find_matching_topic(
                        db, centroid,
                        similarity_threshold=PREFERENCES.topic_persistence.similarity_threshold_topic,
                        level_index=0
                    )
                    
                    if existing_match:
                        topic_db_id, title, summary = existing_match
                        print(f"♻ Reusing existing topic for small group: {title}")
                    else:
                        # Save new topic
                        try:
                            topic_db_id = _save_topic_to_db(
                                db=db,
                                title=title,
                                centroid=centroid,
                                document_count=len(unassigned_docs),
                                summary=summary,
                                level_index=0
                            )
                            print(f"✓ Created new topic for small group: {title} (ID: {topic_db_id})")
                        except Exception as e:
                            print(f"[ERROR] Failed to save topic for small group: {e}")
                            topic_db_id = None
            
            # Assign documents to topic
            for doc in unassigned_docs:
                doc.assigned_topic_title = title
                if topic_db_id:
                    doc.assigned_topic_id = topic_db_id
            
            try:
                db.flush()
                db.commit()
                for doc in unassigned_docs:
                    db.refresh(doc)
                print(f"[Topics] Assigned {len(unassigned_docs)} documents to topic: {title}")
            except Exception as e:
                db.rollback()
                print(f"[WARN] Failed to save topic assignments for small group: {e}")
                import traceback
                traceback.print_exc()
            
            single_topic = Topic(
                topic_id=f"T{topic_idx_counter}",
                title=title,
                summary=summary,
                documents_count=len(topic_docs),
                subtopics=[
                    Subtopic(
                        subtopic_id=f"T{topic_idx_counter}-S0",
                        title=title,
                        summary=summary,
                        documents=topic_docs,
                    )
                ],
            )
            topics.append(single_topic)
        
        # Return early if we have topics from pre-assigned groups
        if topics:
            return TopicsResponse(time_range_days=days, topics=topics)
        else:
            # No topics at all - return empty
            return TopicsResponse(time_range_days=days, topics=[])

    # Build matrix of embeddings for unassigned docs only
    X = np.stack([doc_embeddings[d.id] for d in unassigned_docs], axis=0)
    n_docs = X.shape[0]

 # 2) For visualization, always reduce to 2D/low-D (UMAP or none)
    X_vis = reduce_embeddings(X)   # uses cfg.dim_reducer; can be X unchanged
    # You'll use X_vis later to place document points if you want per-doc coordinates.

    # 3) For clustering, maybe use the same reduced space, maybe not
    labels = cluster_embeddings(X)

    cluster_labels = sorted(set(labels))  # e.g. [0,1,2,...]
    # Map from original index to doc (for unassigned docs only)
    idx_to_doc = {i: d for i, d in enumerate(unassigned_docs)}

    # Helper to build subtopics via a second-level KMeans
    def build_subtopics(topic_docs_indices: List[int], parent_topic_id: str, parent_title: str, parent_db_id: UUID = None) -> List[Subtopic]:
        num_topic_docs = len(topic_docs_indices)
        if num_topic_docs < min_docs_for_clustering:
            # Small topic: single subtopic with all docs
            docs_list = [idx_to_doc[i] for i in topic_docs_indices]
            docs_out = [TopicDoc.model_validate(d) for d in docs_list]
            return [
                Subtopic(
                    subtopic_id=f"{parent_topic_id}-S0",
                    title=parent_title,
                    summary=None,
                    documents=docs_out,
                )
            ]

        # Second-level KMeans clustering on reduced vectors for docs in this topic
        cfg = PREFERENCES.clustering
        X_topic = np.stack([X[i] for i in topic_docs_indices], axis=0)
        # Choose a small k for subtopics, e.g. 2–4
        def choose_k_sub(n: int) -> int:
            return max(
                cfg.k_sub_min,
                min(cfg.k_sub_max, int(np.sqrt(n )) or cfg.k_sub_min), #n/3 too restrictive
            )

        k_sub = choose_k_sub(num_topic_docs)
        sub_kmeans = KMeans(
            n_clusters=k_sub,
            random_state=cfg.random_state,
            n_init="auto",
        )
        sub_labels = sub_kmeans.fit_predict(X_topic)  # values 0..k_sub-1

        subtopics: List[Subtopic] = []
        for s_label in sorted(set(sub_labels)):
            sub_indices = [topic_docs_indices[i] for i, sl in enumerate(sub_labels) if sl == s_label]
            docs_list = [idx_to_doc[i] for i in sub_indices]
            docs_out = [TopicDoc.model_validate(d) for d in docs_list]
            
            # Compute subtopic centroid
            from .mmr import compute_centroid
            sub_doc_emb_list = [doc_embeddings[d.id] for d in docs_list if d.id in doc_embeddings]
            sub_centroid = compute_centroid(sub_doc_emb_list, normalize=True) if sub_doc_emb_list else None
            
            # Check for existing subtopic match (if persistence enabled and parent exists)
            if PREFERENCES.topic_persistence.persist_subtopics and sub_centroid is not None and parent_db_id:
                existing_sub = _find_matching_topic(
                    db, sub_centroid,
                    similarity_threshold=PREFERENCES.topic_persistence.similarity_threshold_subtopic,
                    level_index=1
                )
                
                if existing_sub:
                    sub_db_id, title, summary = existing_sub
                else:
                    title, summary = _generate_title_and_summary(docs_list, db=db, doc_embeddings=doc_embeddings)
                    
                    # Save subtopic with parent_id
                    sub_db_id = _save_topic_to_db(
                        db=db,
                        title=title,
                        centroid=sub_centroid,
                        document_count=len(docs_list),
                        summary=summary,
                        parent_id=parent_db_id,
                        level_index=1
                    )
            else:
                # Persistence disabled or no parent - just generate title
                title, summary = _generate_title_and_summary(docs_list, db=db, doc_embeddings=doc_embeddings)

            subtopic = Subtopic(
                subtopic_id=f"{parent_topic_id}-S{s_label}",
                title=title,
                summary=summary,
                documents=docs_out,
            )
            subtopics.append(subtopic)

        if not subtopics:
            # Fallback
            docs_list = [idx_to_doc[i] for i in topic_docs_indices]
            docs_out = [TopicDoc.model_validate(d) for d in docs_list]
            subtopics.append(
                Subtopic(
                    subtopic_id=f"{parent_topic_id}-S0",
                    title=parent_title,
                    summary=None,
                    documents=docs_out,
                )
            )

        return subtopics

    # 7) Build topic objects and persist them (for newly clustered unassigned docs)
    for idx, cluster_label in enumerate(cluster_labels):
        topic_id = f"T{topic_idx_counter + idx}"

        topic_doc_indices = [i for i, lbl in enumerate(labels) if lbl == cluster_label]
        docs_list = [idx_to_doc[i] for i in topic_doc_indices]

        # Compute centroid for this cluster
        from .mmr import compute_centroid
        doc_emb_list = [doc_embeddings[d.id] for d in docs_list if d.id in doc_embeddings]
        centroid = compute_centroid(doc_emb_list, normalize=True) if doc_emb_list else None
        
        topic_db_id = None
        
        # Debug logging
        if not PREFERENCES.topic_persistence.persist_topics:
            print(f"[DEBUG] Topic persistence is DISABLED - topics will not be saved to database")
        if centroid is None:
            print(f"[DEBUG] No centroid computed for topic cluster (doc_emb_list length: {len(doc_emb_list) if doc_emb_list else 0})")
        
        # Check if we have an existing topic that matches (if persistence enabled and centroid available)
        if PREFERENCES.topic_persistence.persist_topics and centroid is not None:
            existing_match = _find_matching_topic(
                db, centroid,
                similarity_threshold=PREFERENCES.topic_persistence.similarity_threshold_topic,
                level_index=0
            )
            
            if existing_match:
                # Reuse existing topic
                topic_db_id, topic_title, topic_summary = existing_match
                print(f"♻ Reusing existing topic: {topic_title}")
            else:
                # Generate new title & summary
                topic_title, topic_summary = _generate_title_and_summary(docs_list, db=db, doc_embeddings=doc_embeddings)
                
                # Save to database
                try:
                    topic_db_id = _save_topic_to_db(
                        db=db,
                        title=topic_title,
                        centroid=centroid,
                        document_count=len(docs_list),
                        summary=topic_summary,
                        level_index=0
                    )
                    print(f"✓ Created new topic: {topic_title} (ID: {topic_db_id})")
                except Exception as e:
                    print(f"[ERROR] Failed to save topic '{topic_title}' to database: {e}")
                    import traceback
                    traceback.print_exc()
                    # Continue without topic_db_id - topic won't be persisted
                    topic_db_id = None
        else:
            # Persistence disabled or no centroid - just generate title
            topic_title, topic_summary = _generate_title_and_summary(docs_list, db=db, doc_embeddings=doc_embeddings)

        # Build and persist subtopics
        subtopics = build_subtopics(topic_doc_indices, topic_id, topic_title, topic_db_id)

        # Save topic assignment to each document in this cluster
        # Always assign documents to topics, even if topic_db_id is None (persistence disabled)
        # This ensures documents show up with their topic assignments in the UI
        for doc in docs_list:
            doc.assigned_topic_title = topic_title
            if topic_db_id:
                doc.assigned_topic_id = topic_db_id
            # If topic_db_id is None, leave assigned_topic_id as None but still set the title
        
        try:
            db.flush()  # Flush changes before commit
            db.commit()
            # Refresh documents to ensure changes are persisted
            for doc in docs_list:
                db.refresh(doc)
            print(f"[Topics] Assigned {len(docs_list)} documents to topic: {topic_title}")
        except Exception as e:
            db.rollback()
            print(f"[WARN] Failed to save topic assignments: {e}")
            import traceback
            traceback.print_exc()

        topic = Topic(
            topic_id=topic_id,
            title=topic_title,
            summary=topic_summary,
            documents_count=len(docs_list),
            subtopics=subtopics,
        )
        topics.append(topic)

    return TopicsResponse(time_range_days=days, topics=topics)

def get_topics_with_cache(db: Session, days: int = 30) -> TopicsResponse:
    """
    Lightweight in-process cache for topics:
    - Keyed by (days, max_captured_at)
    - If no new documents since last compute, reuse cached TopicsResponse
    """

    # 1) Figure out the most recent document timestamp
    max_captured_at = db.query(func.max(Document.captured_at)).scalar()
    cache_key = (days, max_captured_at)

    # 2) Return cached if we have it
    cached = _topics_cache.get(cache_key)
    if cached is not None:
        return cached

    # 3) Otherwise compute and store
    topics_resp = compute_topics(db, days=days)

    # Optional: you can clear old entries if you want to keep cache tiny
    # For now we just store and let Python manage the small dict.
    _topics_cache[cache_key] = topics_resp

    return topics_resp

def clear_topics_cache():
    _topics_cache.clear()
# ---------- convert TopicResponse into D3-friendly hierarchy ----------

def build_topics_hierarchy(resp: TopicsResponse) -> dict:
    """
    Convert TopicsResponse into a D3-friendly hierarchy:
    root -> topics -> subtopics -> documents
    """
    root = {
        "name": "root",
        "time_range_days": resp.time_range_days,
        "children": []
    }

    for topic in resp.topics:
        topic_node = {
            "name": f"{topic.title} ({topic.documents_count})",
            "topic_id": topic.topic_id,
            "summary": topic.summary,
            "children": []
        }

        for sub in topic.subtopics:
            sub_node = {
                "name": f"{sub.title} ({len(sub.documents)})",
                "subtopic_id": sub.subtopic_id,
                "summary": sub.summary,
                "children": []
            }

            for doc in sub.documents:
                doc_node = {
                    "name": doc.title or "(no title)",
                    "doc_id": str(doc.id),
                    "url": doc.url,
                    "captured_at": doc.captured_at.isoformat(),
                    # D3 circle packing will use this as bubble size
                    "size": 1
                }
                sub_node["children"].append(doc_node)

            topic_node["children"].append(sub_node)

        root["children"].append(topic_node)

    return root


def clear_all_topic_assignments(db: Session) -> int:
    """
    Clear all topic assignments from documents to force fresh reclustering.
    
    Args:
        db: Database session
    
    Returns:
        Number of documents cleared
    """
    try:
        # Get count of documents with topic assignments
        docs_with_topics = (
            db.query(Document)
            .filter(Document.assigned_topic_id != None)
            .all()
        )
        
        count = len(docs_with_topics)
        
        if count > 0:
            # Clear all topic assignments
            for doc in docs_with_topics:
                doc.assigned_topic_id = None
                doc.assigned_topic_title = None
            
            db.commit()
            print(f"[Topics] Cleared topic assignments for {count} documents")
        
        # Also clear the topics cache
        clear_topics_cache()
        
        return count
    except Exception as e:
        db.rollback()
        print(f"[ERROR] Failed to clear topic assignments: {e}")
        import traceback
        traceback.print_exc()
        return 0
