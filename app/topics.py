# app/topics.py

import os
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional
from uuid import UUID
import numpy as np
from sqlalchemy.orm import Session
from sqlalchemy import func
from openai import OpenAI

# Lazy-load heavy ML libraries (sklearn ~10s, umap ~8s import time)
_cosine_similarity_fn = None
_umap_module = None
_pca_class = None
_kmeans_class = None

def get_cosine_similarity():
    """Lazy-load sklearn cosine_similarity."""
    global _cosine_similarity_fn
    if _cosine_similarity_fn is None:
        from sklearn.metrics.pairwise import cosine_similarity
        _cosine_similarity_fn = cosine_similarity
    return _cosine_similarity_fn

def get_umap():
    """Lazy-load umap module."""
    global _umap_module
    if _umap_module is None:
        import umap
        _umap_module = umap
    return _umap_module

def get_pca():
    """Lazy-load sklearn PCA class."""
    global _pca_class
    if _pca_class is None:
        from sklearn.decomposition import PCA
        _pca_class = PCA
    return _pca_class

def get_kmeans():
    """Lazy-load sklearn KMeans class."""
    global _kmeans_class
    if _kmeans_class is None:
        from sklearn.cluster import KMeans
        _kmeans_class = KMeans
    return _kmeans_class
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
from .helpers import (
    _openai_chat, _ollama_chat, get_embedding, get_openai_client, 
    update_openai_client, generate_cluster_summary
)
from .db import EMBED_TABLE, TOPIC_TABLE, DOCUMENT_TABLE
from .agglomerative import (
    cluster_embeddings_hierarchical,
    compute_cluster_centroids,
    get_cluster_stats,
    get_cluster_hierarchy,
    get_nested_cluster_structure,
)
# Simple in-memory cache for topics per (days, max_captured_at, vault_ids_tuple)
_topics_cache: dict[tuple[int, datetime | None, tuple | None], TopicsResponse] = {}



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
        PCA = get_pca()
        pca = PCA(
            n_components=n_components,
            random_state=cfg.random_state,
        )
        return pca.fit_transform(X)

    if cfg.dim_reducer == "umap":
        const_neighbors = max(2, min(cfg.max_neighbors, n_docs - 1))

        umap_mod = get_umap()
        reducer = umap_mod.UMAP(
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

    KMeans = get_kmeans()
    kmeans = KMeans(
        n_clusters=k_topics,
        random_state=cfg.random_state,
        n_init="auto",
    )
    return kmeans.fit_predict(X)


def cluster_embeddings(X: np.ndarray, doc_ids: List[UUID] = None) -> np.ndarray:
    """
    Main clustering entry point. Uses preferences to decide:
    - whether to run UMAP first for clustering
    - which clustering algorithm to use (KMeans or agglomerative).
    
    Args:
        X: Embedding matrix of shape (n_samples, dim)
        doc_ids: Optional list of document UUIDs for agglomerative clustering
    
    Returns:
        Cluster labels array of shape (n_samples,)
    """
    cfg = PREFERENCES.clustering
    agglom_cfg = PREFERENCES.agglomerative

    # Check if agglomerative clustering is enabled and configured
    if cfg.cluster_algo == "agglomerative" or agglom_cfg.enabled:
        # Use agglomerative clustering with cosine distance
        # Default to level 2 (topics level) for main topic assignments
        labels_by_level, structure, Z = cluster_embeddings_hierarchical(
            X, 
            doc_ids=doc_ids,
            thresholds=agglom_cfg.level_thresholds,
            linkage_method=agglom_cfg.linkage_method
        )
        # Use level 2 (topics) as the primary clustering level
        # Labels from fcluster are 1-indexed, convert to 0-indexed
        labels = labels_by_level.get(2, labels_by_level.get(1, labels_by_level[0]))
        return labels - 1  # Convert to 0-indexed

    # Decide which space to cluster in for KMeans
    if cfg.use_reducer_for_clustering:
        X_cluster = reduce_embeddings(X)
    else:
        X_cluster = X

    if cfg.cluster_algo == "kmeans":
        return _cluster_kmeans(X_cluster)

    # Fallback / future expansion
    raise ValueError(f"Unsupported cluster_algo: {cfg.cluster_algo}")


def cluster_embeddings_multilevel(
    X: np.ndarray,
    doc_ids: List[UUID] = None
) -> Tuple[Dict[int, np.ndarray], Dict[int, Dict[int, List]], np.ndarray]:
    """
    Perform hierarchical clustering and return all 4 levels.
    
    Args:
        X: Embedding matrix of shape (n_samples, dim)
        doc_ids: Optional list of document UUIDs
    
    Returns:
        Tuple of:
        - labels_by_level: {level_idx: cluster_labels_array}
        - structure: {level_idx: {cluster_label: [doc_ids_or_indices]}}
        - linkage_matrix: The scipy linkage matrix Z
    """
    agglom_cfg = PREFERENCES.agglomerative
    
    return cluster_embeddings_hierarchical(
        X,
        doc_ids=doc_ids,
        thresholds=agglom_cfg.level_thresholds,
        linkage_method=agglom_cfg.linkage_method
    )

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


def _generate_title_and_summary_from_doc_summaries(
    db: Session,
    docs: List[Document],
    doc_summaries: Dict[UUID, str] = None,
) -> Tuple[str, str]:
    """
    Generate title and summary for a cluster using document summaries.
    
    This approach uses pre-generated document summaries (from DOCUMENT_TABLE)
    to provide richer context for topic naming, addressing the issue of
    limited context cutting off included documents.
    
    Args:
        db: Database session
        docs: Documents in the cluster
        doc_summaries: Pre-fetched document summaries dict. If None, fetches from DB.
    
    Returns:
        (title, summary) tuple
    """
    if not docs:
        return "Miscellaneous", "Mixed documents."
    
    # Fetch document summaries if not provided
    if doc_summaries is None:
        doc_ids = [d.id for d in docs]
        doc_summaries = get_document_summaries(db, doc_ids)
    
    # Collect summaries for docs in this cluster
    cluster_summaries = []
    cluster_titles = []
    for d in docs[:15]:  # Limit to 15 docs for context
        if d.id in doc_summaries and doc_summaries[d.id]:
            cluster_summaries.append(doc_summaries[d.id])
        if d.title:
            cluster_titles.append(d.title)
    
    # If we have enough document summaries, use them for better context
    if len(cluster_summaries) >= 2:
        # Generate cluster summary first (for detailed context)
        cluster_summary = generate_cluster_summary(cluster_summaries, cluster_titles)
        
        # Build context from document summaries
        context_parts = []
        for i, summary in enumerate(cluster_summaries[:10]):
            doc_title = cluster_titles[i] if i < len(cluster_titles) else f"Doc {i+1}"
            context_parts.append(f"- {doc_title[:50]}: {summary[:200]}")
        
        context = "\n".join(context_parts)
        
        # Truncate if needed
        if len(context) > 2000:
            context = context[:2000] + "..."
        
        prompt = (
            "You are categorizing a cluster of documents.\n"
            "Below are summaries of documents in this cluster.\n"
            "Create a SHORT topic title (3-6 words max) that captures the BROAD, HIGH-LEVEL theme.\n"
            "Focus on the overarching concept, NOT specific details.\n\n"
            f"DOCUMENT SUMMARIES:\n{context}\n\n"
            "Respond ONLY with:\n"
            "TITLE: <your title>"
        )
        
        topic_provider = getattr(PREFERENCES.models, "topic_provider", "ollama")
        
        try:
            if topic_provider == "ollama":
                topic_model = PREFERENCES.models.ollama.topic_model
                text = _ollama_chat(prompt, model=topic_model)
            else:
                text = _openai_chat(prompt)
            
            # Parse response
            title = None
            for line in text.splitlines():
                line_upper = line.upper().strip()
                if line_upper.startswith("TITLE:"):
                    parsed_title = line.split(":", 1)[1].strip()
                    if parsed_title and parsed_title.lower() not in ["untitled topic", "untitled", "no title", ""]:
                        title = parsed_title
                        break
            
            if not title:
                first_line = text.splitlines()[0].strip() if text.splitlines() else ""
                if first_line and len(first_line) < 200:
                    title = first_line
            
            # Fallback to document titles
            if not title or title.lower().strip() in ["untitled topic", "untitled", "no title", ""]:
                title = _generate_title_from_document_titles(docs)
            
            return title, cluster_summary or "Summary not available."
            
        except Exception as e:
            print(f"[WARN] Document summary title generation failed: {e}")
    
    # Fallback: not enough document summaries
    return None, None


def _generate_title_and_summary(
    docs: List[Document],
    db: Session = None,
    doc_embeddings: Dict[UUID, np.ndarray] = None,
    doc_summaries: Dict[UUID, str] = None,
) -> Tuple[str, str]:
    """
    Generate title and summary for a cluster of documents.
    
    Priority order:
    1. Use document summaries (from DOCUMENT_TABLE) for richer context
    2. Use MMR-based sentence selection if db and doc_embeddings provided
    3. Fall back to document snippet approach
    
    Args:
        docs: Documents in the cluster
        db: Database session (optional, required for advanced approaches)
        doc_embeddings: Pre-computed document embeddings (optional, for MMR)
        doc_summaries: Pre-fetched document summaries (optional)
    
    Returns:
        (title, summary) tuple
    """
    # Try document summaries approach first (provides richest context)
    if db is not None and PREFERENCES.agglomerative.use_document_summaries:
        try:
            title, summary = _generate_title_and_summary_from_doc_summaries(db, docs, doc_summaries)
            if title:  # Successfully generated using doc summaries
                return title, summary
        except Exception as e:
            print(f"[WARN] Document summary title generation failed: {e}")
    
    # Try MMR approach if we have the required inputs
    if db is not None and doc_embeddings is not None:
        try:
            return _generate_title_and_summary_mmr(db, docs, doc_embeddings)
        except Exception as e:
            print(f"[WARN] MMR title generation failed, using fallback: {e}")
            return _generate_title_and_summary_fallback(docs)
    
    # Fallback approach
    return _generate_title_and_summary_fallback(docs)


# ---------- Topic Title Caching & Reuse ----------

def _cache_existing_topics(db: Session, vault_ids: List[UUID] = None) -> Dict[int, List[Tuple[np.ndarray, str, str]]]:
    """
    Cache existing topic embeddings and titles before reclustering.
    
    Args:
        db: Database session
        vault_ids: List of vault IDs to filter topics (for data isolation)
    
    Returns:
        Dict mapping level_index -> list of (embedding, title, summary) tuples
    """
    from collections import defaultdict
    
    # Query topics with vault filter
    query = db.query(TOPIC_TABLE)
    if vault_ids is not None and len(vault_ids) > 0:
        query = query.filter(TOPIC_TABLE.vault_id.in_(vault_ids))
    
    existing = query.all()
    
    cached_by_level: Dict[int, List[Tuple[np.ndarray, str, str]]] = defaultdict(list)
    
    for t in existing:
        if t.embedding is not None and t.title_text:
            emb = normalize_embedding(t.embedding)
            cached_by_level[t.level_index].append((emb, t.title_text, t.summary_text or ""))
    
    total_cached = sum(len(v) for v in cached_by_level.values())
    print(f"Cached {total_cached} existing topic titles for potential reuse")
    
    return dict(cached_by_level)


def _find_matching_title(
    centroid: np.ndarray,
    cached_topics: List[Tuple[np.ndarray, str, str]],
    min_similarity: float = 0.90
) -> Tuple[str, str] | None:
    """
    Find an existing topic title if centroid matches with high similarity.
    
    Args:
        centroid: New cluster centroid to match
        cached_topics: List of (embedding, title, summary) from existing topics
        min_similarity: Minimum cosine similarity to consider a match (default 0.90)
    
    Returns:
        (title, summary) if match found, else None
    """
    if not cached_topics:
        return None
    
    best_match = None
    best_sim = min_similarity
    cosine_similarity = get_cosine_similarity()
    
    for emb, title, summary in cached_topics:
        sim = float(cosine_similarity(
            centroid.reshape(1, -1),
            emb.reshape(1, -1)
        )[0, 0])
        if sim > best_sim:
            best_sim = sim
            best_match = (title, summary)
    
    return best_match


# ---------- Topic Persistence ----------

def _save_topic_to_db(
    db: Session,
    title: str,
    centroid: np.ndarray,
    document_count: int,
    vault_id: UUID,
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
        vault_id: Vault ID this topic belongs to (required for data isolation)
        summary: Optional summary text
        parent_id: Parent topic ID (for subtopics)
        level_index: 0 for top-level, 1 for subtopics
    
    Returns:
        UUID of created topic
    """
    topic_record = TOPIC_TABLE(
        vault_id=vault_id,
        parent_id=parent_id,
        level_index=level_index,
        title_text=title,
        embedding=centroid.tolist(),
        document_count=document_count,
        summary_text=summary,
        match_count=0
    )
    
    db.add(topic_record)
    db.commit()
    db.refresh(topic_record)
    
    return topic_record.id


def _find_matching_topic(
    db: Session,
    centroid: np.ndarray,
    min_similarity: float = 0.5,
    level_index: int = 0,
    exclude_stale: bool = True,
    vault_ids: List[UUID] = None
) -> Tuple[UUID, str, str] | None:
    """
    Find an existing topic with similar centroid.
    
    Args:
        db: Database session
        centroid: Cluster centroid to match against
        min_similarity: Minimum cosine SIMILARITY to consider a match (NOT distance)
        level_index: Topic level to search (0=fine, 1=topics, 2=categories)
        exclude_stale: If True, only match topics that are "active":
                      - Level 0: has at least one document assigned
                      - Level 1/2: has at least one child topic
        vault_ids: List of vault IDs to scope topic search (filters topics by vault)
    
    Returns:
        (topic_id, title, summary) if match found, else None
    """
    cfg = PREFERENCES.topic_persistence
    
    # Build query for topics at the same level
    query = db.query(TOPIC_TABLE).filter(TOPIC_TABLE.level_index == level_index)
    
    # Filter by vault_ids (CRITICAL for data isolation)
    if vault_ids is not None and len(vault_ids) > 0:
        query = query.filter(TOPIC_TABLE.vault_id.in_(vault_ids))
    elif vault_ids is not None and len(vault_ids) == 0:
        # User has no vault access - return no matches
        return None
    
    if exclude_stale:
        if level_index == 0:
            # Level 0: Only include topics that have at least one document assigned
            doc_query = (
                db.query(Document.assigned_topic_id)
                .filter(Document.assigned_topic_id != None)
            )
            # Filter by vault if vault_ids provided
            if vault_ids:
                doc_query = doc_query.filter(Document.vault_id.in_(vault_ids))
            topics_with_docs = doc_query.distinct().subquery()
            query = query.filter(TOPIC_TABLE.id.in_(topics_with_docs))
        else:
            # Level 1/2: Only include topics that have at least one child topic
            # Also need to filter child topics by vault
            child_query = db.query(TOPIC_TABLE.parent_id).filter(TOPIC_TABLE.parent_id != None)
            if vault_ids:
                child_query = child_query.filter(TOPIC_TABLE.vault_id.in_(vault_ids))
            topics_with_children = child_query.distinct().subquery()
            query = query.filter(TOPIC_TABLE.id.in_(topics_with_children))
    
    existing_topics = (
        query
        .order_by(TOPIC_TABLE.created_at.desc())
        .limit(cfg.max_existing_topics_to_check)
        .all()
    )
    
    if not existing_topics:
        return None
    
    # Find best match using cosine similarity
    best_match = None
    best_similarity = min_similarity
    cosine_similarity = get_cosine_similarity()
    
    for topic in existing_topics:
        topic_emb = normalize_embedding(topic.embedding)
        similarity = float(cosine_similarity(
            centroid.reshape(1, -1),
            topic_emb.reshape(1, -1)
        )[0, 0])
        
        if similarity > best_similarity:
            best_similarity = similarity
            best_match = topic
    
    if best_match:
        # Update match statistics
        best_match.last_matched_at = datetime.utcnow()
        best_match.match_count = (best_match.match_count or 0) + 1
        db.commit()
        
        return (best_match.id, best_match.title_text, best_match.summary_text or "")
    
    return None


def _assign_single_document_to_topic(
    db: Session,
    doc: Document,
    doc_embeddings: Dict[UUID, np.ndarray],
    vault_ids: List[UUID] = None
) -> dict:
    """
    Assign a single uncategorized document to the best matching existing topic.
    
    Used in incremental mode when there's only one document to process.
    
    Args:
        vault_ids: List of vault IDs to scope topic search (filters documents by vault)
    """
    from .mmr import compute_centroid
    
    agglom_cfg = PREFERENCES.agglomerative
    
    if doc.id not in doc_embeddings:
        return {"status": "no_embedding", "message": "Document has no embedding"}
    
    doc_embedding = doc_embeddings[doc.id]
    centroid = compute_centroid([doc_embedding], normalize=True)
    
    # Use level_0_distance converted to similarity for matching
    min_similarity = 1.0 - agglom_cfg.level_0_distance
    
    # Try to find a matching Level 0 topic (exclude stale topics)
    match = _find_matching_topic(
        db, 
        centroid, 
        min_similarity=min_similarity,
        level_index=0,
        exclude_stale=True,
        vault_ids=vault_ids
    )
    
    if match:
        topic_id, topic_title, _ = match
        doc.assigned_topic_id = topic_id
        doc.assigned_topic_title = topic_title
        doc.topic_manually_assigned = False  # Mark as auto-assigned
        db.commit()
        clear_topics_cache()
        
        return {
            "status": "ok",
            "mode": "incremental_single",
            "message": f"Assigned document to existing topic: {topic_title}",
            "documents_assigned": 1,
            "topics_created": {0: 0, 1: 0, 2: 0},
            "total_topics": 0
        }
    else:
        # No matching topic found - leave unassigned for now
        # (could create a new topic, but that requires more context)
        return {
            "status": "no_match",
            "mode": "incremental_single", 
            "message": "No matching topic found for single document. Run full recluster to create new topics.",
            "documents_assigned": 0,
            "topics_created": {0: 0, 1: 0, 2: 0},
            "total_topics": 0
        }


def _cleanup_empty_topics(
    db: Session,
    level0_ids: list,
    level1_ids: list,
    vault_ids: List[UUID] = None
) -> int:
    """
    Clean up topics that have no documents assigned after orphan reassignment.
    
    Args:
        db: Database session
        level0_ids: List of Level 0 topic IDs that may be empty
        level1_ids: List of Level 1 topic IDs that may be empty
        vault_ids: List of vault IDs to scope document queries (filters documents by vault)
    
    Returns:
        Total number of topics deleted
    """
    topics_deleted = 0
    
    # Delete Level 0 topics that now have no documents (in user's vaults)
    if level0_ids:
        # Find which ones are truly empty
        empty_level0 = []
        for tid in level0_ids:
            doc_query = db.query(Document).filter(Document.assigned_topic_id == tid)
            if vault_ids:
                doc_query = doc_query.filter(Document.vault_id.in_(vault_ids))
            has_docs = doc_query.first()
            if not has_docs:
                empty_level0.append(tid)
        
        if empty_level0:
            db.query(TOPIC_TABLE).filter(TOPIC_TABLE.id.in_(empty_level0)).delete(synchronize_session=False)
            topics_deleted += len(empty_level0)
            print(f"Deleted {len(empty_level0)} empty Level 0 topics")
    
    # Delete Level 1 topics that now have no Level 0 children
    if level1_ids:
        empty_level1 = []
        for tid in level1_ids:
            has_children = db.query(TOPIC_TABLE).filter(TOPIC_TABLE.parent_id == tid).first()
            if not has_children:
                empty_level1.append(tid)
        
        if empty_level1:
            db.query(TOPIC_TABLE).filter(TOPIC_TABLE.id.in_(empty_level1)).delete(synchronize_session=False)
            topics_deleted += len(empty_level1)
            print(f"Deleted {len(empty_level1)} empty Level 1 topics")
    
    if topics_deleted > 0:
        db.commit()
    
    return topics_deleted


def _incremental_topic_assignment(
    db: Session,
    agglom_cfg,
    clustering_cfg,
    vault_ids: list = None,
) -> dict:
    """
    Incremental topic assignment using a three-phase approach:
    
    Phase 0: Find "orphan" documents (sole member of a Level 1 topic) and 
             include them in reclustering to give them a chance to merge.
    
    Phase 1: For each uncategorized/orphan document, try to assign it to the best 
             matching existing topic (by centroid similarity).
    
    Phase 2: Any remaining unassigned documents, if count >= min_docs_for_clustering,
             cluster them together and create new topics.
    
    Args:
        db: Database session
        agglom_cfg: AgglomerativeConfig with distance thresholds
        clustering_cfg: ClusteringConfig with min_docs_for_clustering
        vault_ids: List of vault IDs to filter by. If None, processes all documents.
    
    Returns:
        Dict with assignment statistics
    """
    from .mmr import compute_centroid
    from collections import defaultdict
    
    # Note: Deduplication now happens at ingest time (see find_url_duplicate/find_semantic_duplicate)
    # Removed batch dedupe call here to reduce latency
    
    min_similarity = 1.0 - agglom_cfg.level_0_distance
    min_docs_for_clustering = clustering_cfg.min_docs_for_clustering
    
    print(f"\n{'='*60}")
    print(f"INCREMENTAL TOPIC ASSIGNMENT")
    print(f"min_similarity={min_similarity:.3f} (from level_0_distance={agglom_cfg.level_0_distance})")
    print(f"min_docs_for_clustering={min_docs_for_clustering}")
    print(f"{'='*60}")
    
    # ========== PHASE 0: Find orphan documents (sole member of Level 1 topic) ==========
    print(f"\n--- Phase 0: Finding orphan documents in singleton Level 1 topics ---")
    
    # A document is an "orphan" if its Level 1 topic has only 1 document total.
    # This means there are no related documents at any granularity level.
    
    # Get Level 0 topics with their parent_id (Level 1)
    level_0_topics = (
        db.query(TOPIC_TABLE.id, TOPIC_TABLE.parent_id)
        .filter(TOPIC_TABLE.level_index == 0)
        .all()
    )
    
    # Map Level 0 topic -> Level 1 parent
    level0_to_level1 = {t.id: t.parent_id for t in level_0_topics if t.parent_id}
    
    # Count documents per Level 1 topic
    docs_per_level1 = defaultdict(list)
    
    # Get all documents with topic assignments (filtered by vault)
    # EXCLUDE manually-assigned topics from orphan detection
    assigned_query = db.query(Document).filter(
        Document.assigned_topic_id != None,
        Document.topic_manually_assigned != True  # Skip manual assignments
    )
    if vault_ids is not None:
        assigned_query = assigned_query.filter(Document.vault_id.in_(vault_ids))
    assigned_docs = assigned_query.all()
    
    for doc in assigned_docs:
        level1_parent = level0_to_level1.get(doc.assigned_topic_id)
        if level1_parent:
            docs_per_level1[level1_parent].append(doc)
    
    # Find Level 1 topics with exactly 1 document (orphans)
    orphan_docs = []
    orphan_level1_ids = []
    orphan_level0_ids = []
    
    for level1_id, docs in docs_per_level1.items():
        if len(docs) == 1:
            orphan_docs.append(docs[0])
            orphan_level1_ids.append(level1_id)
            orphan_level0_ids.append(docs[0].assigned_topic_id)
    
    # Also check for documents in Level 0 topics that have NO Level 1 parent (truly orphaned)
    # EXCLUDE manually-assigned topics from orphan detection
    level0_ids_with_parents = set(level0_to_level1.keys())
    orphan_query = db.query(Document).filter(
        Document.assigned_topic_id != None,
        Document.topic_manually_assigned != True  # Skip manual assignments
    )
    if vault_ids is not None:
        orphan_query = orphan_query.filter(Document.vault_id.in_(vault_ids))
    if level0_ids_with_parents:
        orphan_query = orphan_query.filter(~Document.assigned_topic_id.in_(level0_ids_with_parents))
    orphan_no_parent = orphan_query.all()
    
    # Filter to only those actually in Level 0 topics (not some other assignment)
    level0_topic_ids = {t.id for t in level_0_topics}
    for doc in orphan_no_parent:
        if doc.assigned_topic_id in level0_topic_ids and doc not in orphan_docs:
            orphan_docs.append(doc)
            orphan_level0_ids.append(doc.assigned_topic_id)
    
    orphan_count = len(orphan_docs)
    
    if orphan_docs:
        print(f"Found {orphan_count} orphan documents:")
        print(f"  - {len(orphan_level1_ids)} in singleton Level 1 topics")
        print(f"  - {orphan_count - len(orphan_level1_ids)} in Level 0 topics without Level 1 parent")
        
        # Clear their topic assignments so they can be reassigned
        for doc in orphan_docs:
            doc.assigned_topic_id = None
            doc.assigned_topic_title = None
        db.commit()
        print(f"Cleared topic assignments for {orphan_count} orphan documents")
    else:
        print("No orphan documents found")
    
    # Get all uncategorized documents (now includes the cleared orphans)
    uncategorized_query = db.query(Document).filter(Document.assigned_topic_id == None)
    if vault_ids is not None:
        uncategorized_query = uncategorized_query.filter(Document.vault_id.in_(vault_ids))
    uncategorized_docs = uncategorized_query.order_by(Document.captured_at.desc()).all()
    
    new_uncategorized_count = len(uncategorized_docs) - orphan_count
    
    if not uncategorized_docs:
        # Clean up empty topics
        topics_deleted = _cleanup_empty_topics(db, orphan_level0_ids, orphan_level1_ids, vault_ids=vault_ids)
        
        return {
            "status": "ok",
            "mode": "incremental",
            "message": "No documents to process",
            "orphans_found": orphan_count,
            "phase1_assigned": 0,
            "phase2_assigned": 0,
            "topics_created": 0,
            "topics_deleted": topics_deleted,
            "documents_remaining_unassigned": 0
        }
    
    print(f"Total documents to process: {len(uncategorized_docs)} ({new_uncategorized_count} new + {orphan_count} orphans)")
    
    # Compute embeddings for uncategorized docs
    doc_embeddings = compute_document_embeddings(db, uncategorized_docs)
    docs_with_embeddings = [d for d in uncategorized_docs if d.id in doc_embeddings]
    
    if not docs_with_embeddings:
        return {
            "status": "ok",
            "mode": "incremental",
            "message": "No uncategorized documents have embeddings",
            "phase1_assigned": 0,
            "phase2_assigned": 0,
            "topics_created": 0,
            "documents_remaining_unassigned": len(uncategorized_docs)
        }
    
    print(f"Processing {len(docs_with_embeddings)} documents with embeddings")
    
    # ========== PHASE 1: Individual assignment to existing topics ==========
    # Try matching at all hierarchy levels (Level 0 -> Level 1 -> Level 2)
    # Each level has a different similarity threshold based on distance config
    print(f"\n--- Phase 1: Matching documents to existing topics (all levels) ---")
    
    # Calculate similarity thresholds for each level (similarity = 1 - distance)
    level_similarities = {
        0: 1.0 - agglom_cfg.level_0_distance,
        1: 1.0 - agglom_cfg.level_1_distance,
        2: 1.0 - agglom_cfg.level_2_distance,
    }
    print(f"Similarity thresholds: L0={level_similarities[0]:.3f}, L1={level_similarities[1]:.3f}, L2={level_similarities[2]:.3f}")
    
    phase1_assigned = 0
    phase1_level0_matched = 0
    phase1_level1_matched = 0
    phase1_level2_matched = 0
    unmatched_docs = []
    
    for doc in docs_with_embeddings:
        doc_embedding = doc_embeddings[doc.id]
        centroid = compute_centroid([doc_embedding], normalize=True)
        
        assigned = False
        
        # Try Level 0 first (finest granularity)
        match = _find_matching_topic(
            db,
            centroid,
            min_similarity=level_similarities[0],
            level_index=0,
            exclude_stale=True,
            vault_ids=vault_ids
        )
        
        if match:
            topic_id, topic_title, _ = match
            doc.assigned_topic_id = topic_id
            doc.assigned_topic_title = topic_title
            doc.topic_manually_assigned = False  # Mark as auto-assigned
            phase1_assigned += 1
            phase1_level0_matched += 1
            assigned = True
            print(f"  [L0 MATCH] '{doc.title[:35]}...' -> '{topic_title[:35]}...'")
        
        # Try Level 1 if no Level 0 match
        if not assigned:
            match = _find_matching_topic(
                db,
                centroid,
                min_similarity=level_similarities[1],
                level_index=1,
                exclude_stale=False,  # Level 1 topics don't have direct doc assignments
                vault_ids=vault_ids
            )
            
            if match:
                parent_topic_id, parent_topic_title, _ = match
                # Create a new Level 0 topic for this doc, attached to the Level 1 parent
                doc_title_short = (doc.title or "Untitled")[:50]
                new_topic_id = _save_topic_to_db(
                    db=db,
                    title=doc_title_short,
                    centroid=centroid,
                    document_count=1,
                    vault_id=doc.vault_id,
                    summary=None,
                    parent_id=parent_topic_id,
                    level_index=0
                )
                doc.assigned_topic_id = new_topic_id
                doc.assigned_topic_title = doc_title_short
                doc.topic_manually_assigned = False  # Mark as auto-assigned
                phase1_assigned += 1
                phase1_level1_matched += 1
                assigned = True
                print(f"  [L1 MATCH] '{doc.title[:35]}...' -> new L0 under '{parent_topic_title[:25]}...'")
        
        # Try Level 2 if no Level 1 match
        if not assigned:
            match = _find_matching_topic(
                db,
                centroid,
                min_similarity=level_similarities[2],
                level_index=2,
                exclude_stale=False,  # Level 2 topics don't have direct doc assignments
                vault_ids=vault_ids
            )
            
            if match:
                parent_topic_id, parent_topic_title, _ = match
                # Create a new Level 0 topic for this doc, attached to the Level 2 parent
                # (skipping Level 1 - it will be an orphan at Level 1)
                doc_title_short = (doc.title or "Untitled")[:50]
                new_topic_id = _save_topic_to_db(
                    db=db,
                    title=doc_title_short,
                    centroid=centroid,
                    document_count=1,
                    vault_id=doc.vault_id,
                    summary=None,
                    parent_id=parent_topic_id,  # Directly under Level 2
                    level_index=0
                )
                doc.assigned_topic_id = new_topic_id
                doc.assigned_topic_title = doc_title_short
                doc.topic_manually_assigned = False  # Mark as auto-assigned
                phase1_assigned += 1
                phase1_level2_matched += 1
                assigned = True
                print(f"  [L2 MATCH] '{doc.title[:35]}...' -> new L0 under '{parent_topic_title[:25]}...'")
        
        if not assigned:
            unmatched_docs.append(doc)
    
    db.commit()
    print(f"Phase 1 complete: {phase1_assigned} documents assigned")
    print(f"  - Level 0 matches: {phase1_level0_matched}")
    print(f"  - Level 1 matches (new L0 created): {phase1_level1_matched}")
    print(f"  - Level 2 matches (new L0 created): {phase1_level2_matched}")
    print(f"Remaining unmatched: {len(unmatched_docs)} documents")
    
    # ========== PHASE 2: Cluster remaining unmatched documents ==========
    phase2_assigned = 0
    topics_created = 0
    titles_reused = 0
    
    if len(unmatched_docs) >= min_docs_for_clustering:
        print(f"\n--- Phase 2: Clustering {len(unmatched_docs)} unmatched documents ---")
        
        # Cache existing Level 0 topics for title reuse
        cached_level0_topics = _cache_existing_topics(db).get(0, [])
        
        # Get summaries for unmatched docs
        unmatched_doc_ids = [d.id for d in unmatched_docs]
        doc_summaries = get_document_summaries(db, unmatched_doc_ids)
        
        # Build embedding matrix for unmatched docs
        X_full = np.stack([doc_embeddings[d.id] for d in unmatched_docs], axis=0)
        X = reduce_embeddings(X_full)
        
        # Cluster the unmatched documents
        labels_by_level, structure, Z = cluster_embeddings_hierarchical(
            X,
            doc_ids=unmatched_doc_ids,
            thresholds=agglom_cfg.level_thresholds,
            linkage_method=agglom_cfg.linkage_method
        )
        
        # Only create Level 0 topics for incremental mode
        level_0_structure = structure[0]
        print(f"Created {len(level_0_structure)} clusters from unmatched documents")
        
        doc_by_id = {d.id: d for d in unmatched_docs}
        
        for cluster_label, doc_ids_in_cluster in level_0_structure.items():
            docs_in_cluster = [doc_by_id[did] for did in doc_ids_in_cluster if did in doc_by_id]
            
            if not docs_in_cluster:
                continue
            
            # Compute centroid
            cluster_embeddings_list = [doc_embeddings[d.id] for d in docs_in_cluster]
            centroid = compute_centroid(cluster_embeddings_list, normalize=True)
            
            # Try to reuse existing topic title if centroid matches (90% similarity)
            existing_match = _find_matching_title(centroid, cached_level0_topics, min_similarity=0.90)
            
            if existing_match:
                title, summary = existing_match
                titles_reused += 1
                print(f"  [REUSED] '{title[:50]}...' ({len(docs_in_cluster)} docs)")
            else:
                # Generate title and summary for the new topic
                title, summary = _generate_title_and_summary(
                    docs_in_cluster,
                    db=db,
                    doc_embeddings=doc_embeddings,
                    doc_summaries=doc_summaries
                )
                print(f"  [NEW TOPIC] '{title[:50]}...' ({len(docs_in_cluster)} docs)")
            
            # Save new topic
            topic_db_id = _save_topic_to_db(
                db=db,
                title=title,
                centroid=centroid,
                document_count=len(docs_in_cluster),
                vault_id=docs_in_cluster[0].vault_id,
                summary=summary,
                parent_id=None,
                level_index=0
            )
            topics_created += 1
            
            # Assign documents to the new topic
            for doc in docs_in_cluster:
                doc.assigned_topic_id = topic_db_id
                doc.assigned_topic_title = title
                doc.topic_manually_assigned = False  # Mark as auto-assigned
                phase2_assigned += 1
        
        db.commit()
        print(f"Phase 2 complete: {phase2_assigned} documents assigned to {topics_created} new topics")
        print(f"  - Titles reused (90% match): {titles_reused}")
        print(f"  - New titles generated: {topics_created - titles_reused}")
    else:
        if len(unmatched_docs) > 0:
            print(f"\n--- Phase 2: Skipped (only {len(unmatched_docs)} unmatched docs, need {min_docs_for_clustering}) ---")
    
    # ========== CLEANUP: Remove empty topics from orphan processing ==========
    topics_deleted = _cleanup_empty_topics(db, orphan_level0_ids, orphan_level1_ids, vault_ids=vault_ids)
    
    # Clear cache
    clear_topics_cache()
    
    remaining_unassigned = len(unmatched_docs) - phase2_assigned
    
    result = {
        "status": "ok",
        "mode": "incremental",
        "orphans_found": orphan_count,
        "phase1_assigned": phase1_assigned,
        "phase1_level0_matched": phase1_level0_matched,
        "phase1_level1_matched": phase1_level1_matched,
        "phase1_level2_matched": phase1_level2_matched,
        "phase2_assigned": phase2_assigned,
        "topics_created": topics_created + phase1_level1_matched + phase1_level2_matched,  # Include L0 topics created for L1/L2 matches
        "phase2_titles_reused": titles_reused,
        "topics_deleted": topics_deleted,
        "documents_remaining_unassigned": remaining_unassigned
    }
    
    print(f"\n{'='*60}")
    print(f"Incremental assignment complete")
    print(f"Orphans processed: {orphan_count}")
    print(f"Phase 1 (matched to existing): {phase1_assigned}")
    print(f"  - L0 matches: {phase1_level0_matched}")
    print(f"  - L1 matches (new L0 created): {phase1_level1_matched}")
    print(f"  - L2 matches (new L0 created): {phase1_level2_matched}")
    print(f"Phase 2 (clustered): {topics_created} new topics, {phase2_assigned} docs assigned")
    if titles_reused > 0:
        print(f"  - Titles reused (saved {titles_reused} LLM calls)")
    print(f"Topics deleted: {topics_deleted}")
    print(f"Still unassigned: {remaining_unassigned}")
    print(f"{'='*60}\n")
    
    return result


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


def get_document_summaries(
    db: Session,
    doc_ids: List[UUID]
) -> Dict[UUID, str]:
    """
    Fetch document summaries from DOCUMENT_TABLE.
    
    Args:
        db: Database session
        doc_ids: List of document UUIDs
    
    Returns:
        Dict mapping document_id to summary_text (excludes docs without summaries)
    """
    if not doc_ids:
        return {}
    
    records = (
        db.query(DOCUMENT_TABLE.document_id, DOCUMENT_TABLE.summary_text)
        .filter(DOCUMENT_TABLE.document_id.in_(doc_ids))
        .filter(DOCUMENT_TABLE.summary_text != None)
        .all()
    )
    
    return {r.document_id: r.summary_text for r in records}


def get_document_embeddings_from_table(
    db: Session,
    doc_ids: List[UUID]
) -> Dict[UUID, np.ndarray]:
    """
    Fetch pre-computed document embeddings from DOCUMENT_TABLE.
    Falls back to computing from chunks if not available.
    
    Args:
        db: Database session
        doc_ids: List of document UUIDs
    
    Returns:
        Dict mapping document_id to embedding vector
    """
    if not doc_ids:
        return {}
    
    records = (
        db.query(DOCUMENT_TABLE.document_id, DOCUMENT_TABLE.embedding)
        .filter(DOCUMENT_TABLE.document_id.in_(doc_ids))
        .filter(DOCUMENT_TABLE.embedding != None)
        .all()
    )
    
    return {r.document_id: normalize_embedding(r.embedding) for r in records}


# ---------- Ingest-time dedupe (O(n) per document) ----------

def find_url_duplicate(
    db: Session,
    vault_id: UUID,
    url: str,
    has_extracted_text: bool,
) -> Optional[Document]:
    """
    Check if a document with the same canonical URL already exists in the vault.
    Only checks against full page captures (with extracted_text).
    
    Called BEFORE creating a new document to prevent duplicates.
    
    Args:
        db: Database session
        vault_id: The vault to check in
        url: The URL to check
        has_extracted_text: Whether the new doc is a full capture (vs note)
    
    Returns:
        The existing Document if duplicate found, None otherwise
    """
    # Notes are allowed to have duplicate URLs (user creates multiple notes from same page)
    if not has_extracted_text:
        return None
    
    canonical = canonicalize_url(url or "")
    if not canonical:
        return None
    
    # Check existing full captures in vault
    existing_docs = (
        db.query(Document)
        .filter(Document.vault_id == vault_id)
        .filter(Document.extracted_text != None)
        .all()
    )
    
    for doc in existing_docs:
        if canonicalize_url(doc.url or "") == canonical:
            print(f"[DEDUPE] URL duplicate found: {url} matches existing doc {doc.id}")
            return doc
    
    return None


def find_semantic_duplicate(
    db: Session,
    new_doc: Document,
    similarity_threshold: float = 0.92,
) -> Optional[Document]:
    """
    Check if a semantically similar document exists in the vault.
    Compares new doc's embedding against all existing doc embeddings. O(n) complexity.
    
    Called AFTER embedding a new document to catch content duplicates with different URLs.
    
    Args:
        db: Database session
        new_doc: The newly created document (must have embeddings)
        similarity_threshold: Cosine similarity threshold (default 0.92)
    
    Returns:
        The existing Document if semantic duplicate found, None otherwise
    """
    if not new_doc.vault_id or not new_doc.extracted_text:
        return None
    
    # Get new doc's embedding
    new_embedding = get_document_embeddings_from_table(db, [new_doc.id])
    if new_doc.id not in new_embedding:
        return None
    
    # Get all other full captures in vault
    other_docs = (
        db.query(Document)
        .filter(Document.vault_id == new_doc.vault_id)
        .filter(Document.id != new_doc.id)
        .filter(Document.extracted_text != None)
        .all()
    )
    
    if not other_docs:
        return None
    
    other_ids = [d.id for d in other_docs]
    other_embeddings = get_document_embeddings_from_table(db, other_ids)
    
    if not other_embeddings:
        return None
    
    new_emb = new_embedding[new_doc.id].reshape(1, -1)
    cosine_similarity = get_cosine_similarity()
    
    for doc in other_docs:
        if doc.id not in other_embeddings:
            continue
        other_emb = other_embeddings[doc.id].reshape(1, -1)
        sim = float(cosine_similarity(new_emb, other_emb)[0, 0])
        
        if sim >= similarity_threshold:
            print(f"[DEDUPE] Semantic duplicate found (sim={sim:.3f}): new doc {new_doc.id} matches existing doc {doc.id}")
            return doc
    
    return None


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
    cosine_similarity = get_cosine_similarity()

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


# ---------- Document deduplication (with DB deletion) ----------

def select_document_to_keep(group: List[Document]) -> Document:
    """
    Select the best document to keep from a group of duplicates.
    
    Priority:
    1. Has assigned topic (user has curated/engaged with it)
    2. Latest captured_at timestamp
    
    Args:
        group: List of duplicate documents
        
    Returns:
        The document to keep
    """
    if len(group) == 1:
        return group[0]
    
    # Priority 1: Documents with assigned topics
    with_topic = [d for d in group if d.assigned_topic_id is not None]
    if with_topic:
        # Among those with topics, keep the latest
        return max(with_topic, key=lambda d: d.captured_at or datetime.min)
    
    # Priority 2: Latest by captured_at
    return max(group, key=lambda d: d.captured_at or datetime.min)


def deduplicate_vault_documents(
    db: Session,
    vault_id: UUID,
    similarity_threshold: float = 0.92,
) -> dict:
    """
    Find and remove duplicate documents within a single vault.
    Called at the start of topic computation for each vault.
    
    Two-phase deduplication:
    1. URL duplicates: Group by canonical URL, keep best per group
    2. Semantic duplicates: Use embeddings to find similar content
    
    Args:
        db: Database session
        vault_id: The vault to deduplicate
        similarity_threshold: Cosine similarity threshold for semantic duplicates (default 0.92)
    
    Returns:
        {
            "vault_id": str,
            "url_duplicates_removed": int,
            "semantic_duplicates_removed": int,
            "documents_deleted": List[str]
        }
    """
    result = {
        "vault_id": str(vault_id),
        "url_duplicates_removed": 0,
        "semantic_duplicates_removed": 0,
        "documents_deleted": [],
    }
    
    # Fetch all documents in this vault
    docs = (
        db.query(Document)
        .filter(Document.vault_id == vault_id)
        .order_by(Document.captured_at.desc())
        .all()
    )
    
    if len(docs) <= 1:
        return result
    
    # Only deduplicate full page captures (have extracted_text).
    # Notes (no extracted_text) are intentionally excluded - users create multiple
    # notes from the same page and expect them all to be kept.
    # Full captures may be duplicated accidentally when users aren't sure if 
    # they've already captured a page.
    # NOTE: extracted_text is set by trafilatura during ingest for mode="page" captures.
    full_captures = [d for d in docs if d.extracted_text]
    notes = [d for d in docs if not d.extracted_text]
    
    if len(full_captures) <= 1:
        return result  # Nothing to deduplicate
    
    print(f"[DEDUPE] Vault {vault_id}: checking {len(full_captures)} full captures for duplicates (skipping {len(notes)} notes)")
    
    # Phase 1: URL-based deduplication (full captures only)
    canonical_groups: Dict[str, List[Document]] = {}
    for d in full_captures:
        cu = canonicalize_url(d.url or "")
        canonical_groups.setdefault(cu, []).append(d)
    
    url_duplicates_to_delete: List[UUID] = []
    docs_after_url_dedupe: List[Document] = []
    
    for cu, group in canonical_groups.items():
        if len(group) > 1:
            # Multiple docs with same canonical URL - keep the best one
            keeper = select_document_to_keep(group)
            docs_after_url_dedupe.append(keeper)
            for d in group:
                if d.id != keeper.id:
                    url_duplicates_to_delete.append(d.id)
                    print(f"[DEDUPE] URL duplicate: keeping {keeper.id} (topic={keeper.assigned_topic_id is not None}), deleting {d.id}")
        else:
            docs_after_url_dedupe.append(group[0])
    
    # Delete URL duplicates
    if url_duplicates_to_delete:
        db.query(Document).filter(Document.id.in_(url_duplicates_to_delete)).delete(synchronize_session=False)
        result["url_duplicates_removed"] = len(url_duplicates_to_delete)
        result["documents_deleted"].extend([str(uid) for uid in url_duplicates_to_delete])
    
    # Phase 2: Semantic deduplication (on remaining docs)
    if len(docs_after_url_dedupe) <= 1:
        db.commit()
        return result
    
    # Get embeddings for remaining documents
    doc_ids = [d.id for d in docs_after_url_dedupe]
    doc_embeddings = get_document_embeddings_from_table(db, doc_ids)
    
    # Only process docs that have embeddings
    docs_with_embeddings = [d for d in docs_after_url_dedupe if d.id in doc_embeddings]
    
    if len(docs_with_embeddings) <= 1:
        db.commit()
        return result
    
    # Find semantic duplicates using embedding similarity
    # Sort by priority: topic assignment first, then by captured_at
    def doc_priority(d: Document):
        has_topic = 1 if d.assigned_topic_id is not None else 0
        captured = d.captured_at or datetime.min
        return (has_topic, captured)
    
    docs_sorted = sorted(docs_with_embeddings, key=doc_priority, reverse=True)
    
    kept_ids: List[UUID] = []
    semantic_duplicates_to_delete: List[UUID] = []
    cosine_similarity = get_cosine_similarity()
    
    for i, d_i in enumerate(docs_sorted):
        if d_i.id in semantic_duplicates_to_delete:
            continue
        
        kept_ids.append(d_i.id)
        v_i = doc_embeddings[d_i.id].reshape(1, -1)
        
        # Compare to all later docs in the priority-sorted list
        for j in range(i + 1, len(docs_sorted)):
            d_j = docs_sorted[j]
            if d_j.id in semantic_duplicates_to_delete:
                continue
            
            v_j = doc_embeddings[d_j.id].reshape(1, -1)
            sim = float(cosine_similarity(v_i, v_j)[0, 0])
            
            if sim >= similarity_threshold:
                semantic_duplicates_to_delete.append(d_j.id)
                print(f"[DEDUPE] Semantic duplicate (sim={sim:.3f}): keeping {d_i.id}, deleting {d_j.id}")
    
    # Delete semantic duplicates
    if semantic_duplicates_to_delete:
        db.query(Document).filter(Document.id.in_(semantic_duplicates_to_delete)).delete(synchronize_session=False)
        result["semantic_duplicates_removed"] = len(semantic_duplicates_to_delete)
        result["documents_deleted"].extend([str(uid) for uid in semantic_duplicates_to_delete])
    
    db.commit()
    
    total_removed = result["url_duplicates_removed"] + result["semantic_duplicates_removed"]
    if total_removed > 0:
        print(f"[DEDUPE] Vault {vault_id}: removed {total_removed} duplicates ({result['url_duplicates_removed']} URL, {result['semantic_duplicates_removed']} semantic)")
    
    return result


# ---------- High-level topics computation ----------

def compute_hierarchical_topics(
    db: Session,
    days: int = 30,
    full_recluster: bool = False,
    vault_ids: list = None,
) -> dict:
    """
    Compute and save topics at all hierarchy levels using agglomerative clustering.
    
    This creates topics at 3 levels:
    - Level 0: Fine-grained topics (cosine sim >= 0.85, distance <= 0.15)
    - Level 1: Topics (cosine sim >= 0.75, distance <= 0.25)
    - Level 2: Super-topics (cosine sim >= 0.60, distance <= 0.40)
    
    Topics at each level are linked to their parent at the next level up.
    
    Args:
        db: Database session
        days: Number of days of documents to include
        full_recluster: If True, clear all topics and recluster everything.
                       If False, only process uncategorized documents.
        vault_ids: List of vault IDs to filter by. If None, processes all documents.
    
    Returns:
        Dict with statistics and created topic info
    """
    from .mmr import compute_centroid
    
    cfg = PREFERENCES.clustering
    agglom_cfg = PREFERENCES.agglomerative
    
    # Note: Deduplication now happens at ingest time (see find_url_duplicate/find_semantic_duplicate)
    # Removed batch dedupe call here to reduce latency
    
    # 1) Select documents based on mode
    cutoff = datetime.utcnow() - timedelta(days=days)
    
    if full_recluster:
        # Full recluster: get ALL documents in time range (filtered by vault)
        # BUT exclude documents with manually-assigned topics
        query = db.query(Document).filter(
            Document.captured_at >= cutoff,
            Document.topic_manually_assigned != True  # Exclude manual assignments
        )
        if vault_ids is not None:
            query = query.filter(Document.vault_id.in_(vault_ids))
        docs = query.order_by(Document.captured_at.desc()).all()
        
        # Count manually-assigned documents for logging
        manual_count_query = db.query(Document).filter(
            Document.captured_at >= cutoff,
            Document.topic_manually_assigned == True
        )
        if vault_ids is not None:
            manual_count_query = manual_count_query.filter(Document.vault_id.in_(vault_ids))
        manual_count = manual_count_query.count()
        
        mode_desc = f"FULL RECLUSTER (excluding {manual_count} manually-assigned docs)"
    else:
        # Incremental mode: two-phase approach
        # Phase 1: Try to assign each uncategorized doc to existing topics individually
        # Phase 2: Cluster any remaining unmatched docs if count >= min_docs_for_clustering
        return _incremental_topic_assignment(db, agglom_cfg, cfg, vault_ids=vault_ids)
    
    if not docs:
        return {"status": "no_documents", "message": "No documents found"}
    
    print(f"\n{'='*60}")
    print(f"topic vault ids: {vault_ids}")
    print(f"Computing hierarchical topics - {mode_desc}")
    print(f"Processing {len(docs)} documents")
    print(f"Thresholds: {agglom_cfg.level_thresholds}")
    print(f"{'='*60}")
    
    # 2) Deduplicate by canonical URL
    canonical_groups: Dict[str, List[Document]] = {}
    for d in docs:
        cu = canonicalize_url(d.url or "")
        canonical_groups.setdefault(cu, []).append(d)
    
    canonical_docs: List[Document] = []
    for cu, group in canonical_groups.items():
        group_sorted = sorted(group, key=lambda d: d.captured_at or datetime.min, reverse=True)
        canonical_docs.append(group_sorted[0])
    
    # 3) Compute document embeddings
    doc_embeddings = compute_document_embeddings(db, canonical_docs)
    
    # Filter to docs with embeddings
    docs_with_embeddings = [d for d in canonical_docs if d.id in doc_embeddings]
    
    if len(docs_with_embeddings) == 0:
        return {"status": "no_embeddings", "message": "No documents have embeddings"}
    
    if len(docs_with_embeddings) < 2:
        return {"status": "insufficient_documents", "message": f"Need at least 2 documents, got {len(docs_with_embeddings)}"}
    
    print(f"Processing {len(docs_with_embeddings)} documents with embeddings")
    
    # 4) Fetch document summaries
    doc_ids_ordered = [d.id for d in docs_with_embeddings]
    doc_summaries = get_document_summaries(db, doc_ids_ordered)
    print(f"Fetched {len(doc_summaries)} document summaries")
    
    # 5) Build embedding matrix
    X_full = np.stack([doc_embeddings[d.id] for d in docs_with_embeddings], axis=0)
    
    # 5b) Apply dimensionality reduction for clustering (per config settings)
    X = reduce_embeddings(X_full)
    print(f"Reduced embeddings from {X_full.shape[1]} to {X.shape[1]} dimensions for clustering")
    
    # 6) Perform hierarchical clustering on reduced embeddings
    labels_by_level, structure, Z = cluster_embeddings_hierarchical(
        X,
        doc_ids=doc_ids_ordered,
        thresholds=agglom_cfg.level_thresholds,
        linkage_method=agglom_cfg.linkage_method
    )
    
    # 7) Get cluster hierarchy (parent-child relationships)
    hierarchy = get_cluster_hierarchy(labels_by_level)
    
    # Print cluster stats
    stats = get_cluster_stats(labels_by_level)
    for level, level_stats in stats.items():
        print(f"  Level {level}: {level_stats['n_clusters']} clusters, avg size: {level_stats['avg_size']:.1f}")
    
    # 8) Cache existing topic titles for reuse, then clear topics
    cached_topics_by_level = _cache_existing_topics(db, vault_ids=vault_ids)
    
    # Only delete topics that don't have manually-assigned documents
    # Get topic IDs that have manually-assigned documents
    manually_assigned_topic_ids = (
        db.query(Document.assigned_topic_id)
        .filter(
            Document.assigned_topic_id != None,
            Document.topic_manually_assigned == True
        )
        .distinct()
        .all()
    )
    protected_topic_ids = {row[0] for row in manually_assigned_topic_ids}
    
    try:
        if protected_topic_ids:
            # Delete only topics that are not protected
            delete_query = db.query(TOPIC_TABLE).filter(
                ~TOPIC_TABLE.id.in_(protected_topic_ids)
            )
            # Also filter by vault_ids to only delete topics in user's vaults
            if vault_ids:
                delete_query = delete_query.filter(TOPIC_TABLE.vault_id.in_(vault_ids))
            deleted = delete_query.delete(synchronize_session=False)
            db.commit()
            print(f"Cleared {deleted} existing topics (preserved {len(protected_topic_ids)} topics with manual assignments)")
        else:
            # No protected topics, delete all topics in user's vaults
            delete_query = db.query(TOPIC_TABLE)
            if vault_ids:
                delete_query = delete_query.filter(TOPIC_TABLE.vault_id.in_(vault_ids))
            deleted = delete_query.delete()
            db.commit()
            print(f"Cleared {deleted} existing topics")
    except Exception as e:
        db.rollback()
        print(f"[WARN] Could not clear existing topics: {e}")
    
    # 9) Create topics at all levels (coarsest to finest)
    
    # Map: (level, cluster_label) -> topic_db_id
    topic_id_map: Dict[Tuple[int, int], UUID] = {}
    
    # Map: doc_id -> Document object
    doc_by_id = {d.id: d for d in docs_with_embeddings}
    
    created_topics = {level: 0 for level in range(len(agglom_cfg.level_thresholds))}
    reused_titles = {level: 0 for level in range(len(agglom_cfg.level_thresholds))}
    
    for level in reversed(range(len(agglom_cfg.level_thresholds))):
        level_labels = labels_by_level[level]
        level_structure = structure[level]
        
        print(f"\nCreating Level {level} topics ({len(level_structure)} clusters)...")
        
        for cluster_label, doc_ids_in_cluster in level_structure.items():
            # Get documents in this cluster
            docs_in_cluster = [doc_by_id[did] for did in doc_ids_in_cluster if did in doc_by_id]
            
            if not docs_in_cluster:
                continue
            
            # Determine vault_id for this topic from documents in the cluster
            # Use the vault of the first document (all docs should be from user's accessible vaults)
            cluster_vault_id = docs_in_cluster[0].vault_id
            
            # Sanity check: All documents should have the same vault_id in single-vault mode
            # (If multi-vault clustering is needed in the future, this logic would change)
            vault_ids_in_cluster = {d.vault_id for d in docs_in_cluster}
            if len(vault_ids_in_cluster) > 1:
                print(f"  [WARN] Cluster has documents from {len(vault_ids_in_cluster)} vaults, using first vault")
            
            # Compute centroid
            cluster_embeddings = [doc_embeddings[d.id] for d in docs_in_cluster]
            centroid = compute_centroid(cluster_embeddings, normalize=True)
            
            # Find parent topic (for levels 0 and 1)
            parent_id = None
            if level < len(agglom_cfg.level_thresholds) - 1:
                parent_cluster_label = hierarchy.get(level, {}).get(cluster_label)
                if parent_cluster_label:
                    parent_id = topic_id_map.get((level + 1, parent_cluster_label))
            
            # Try to reuse existing topic title if centroid matches (90% similarity)
            cached = cached_topics_by_level.get(level, [])
            existing_match = _find_matching_title(centroid, cached, min_similarity=0.90)
            
            level_names = {0: "Fine", 1: "Topic", 2: "Category"}
            level_prefix = level_names.get(level, f"L{level}")
            
            if existing_match:
                title, summary = existing_match
                reused_titles[level] += 1
                print(f"  [{level_prefix}] [REUSED] {title[:50]}... ({len(docs_in_cluster)} docs)")
            else:
                # Generate new title and summary via LLM
                title, summary = _generate_title_and_summary(
                    docs_in_cluster, 
                    db=db, 
                    doc_embeddings=doc_embeddings, 
                    doc_summaries=doc_summaries
                )
                print(f"  [{level_prefix}] [NEW] {title[:50]}... ({len(docs_in_cluster)} docs)")
            
            # Save topic to database with vault_id
            topic_db_id = _save_topic_to_db(
                db=db,
                title=title,
                centroid=centroid,
                document_count=len(docs_in_cluster),
                vault_id=cluster_vault_id,
                summary=summary,
                parent_id=parent_id,
                level_index=level
            )
            
            topic_id_map[(level, cluster_label)] = topic_db_id
            created_topics[level] += 1
        
        db.commit()
    
    # 10) Assign documents to their Level 0 (finest) topic
    print("\nAssigning documents to Level 0 topics...")
    level_0_structure = structure[0]
    assignments = 0
    
    for cluster_label, doc_ids_in_cluster in level_0_structure.items():
        topic_db_id = topic_id_map.get((0, cluster_label))
        if not topic_db_id:
            continue
        
        # Get topic title
        topic = db.query(TOPIC_TABLE).filter(TOPIC_TABLE.id == topic_db_id).first()
        topic_title = topic.title_text if topic else "Unknown"
        
        for doc_id in doc_ids_in_cluster:
            doc = doc_by_id.get(doc_id)
            if doc:
                # Skip if this document was manually assigned (should have been filtered out, but double-check)
                if doc.topic_manually_assigned == True:
                    print(f"  [SKIP] Document '{doc.title[:40]}...' has manual assignment, preserving")
                    continue
                    
                doc.assigned_topic_id = topic_db_id
                doc.assigned_topic_title = topic_title
                doc.topic_manually_assigned = False  # Mark as auto-assigned
                assignments += 1
    
    db.commit()
    print(f"Assigned {assignments} documents to topics")
    
    # Clear cache
    clear_topics_cache()
    
    # Return summary
    total_reused = sum(reused_titles.values())
    total_new = sum(created_topics.values()) - total_reused
    
    result = {
        "status": "ok",
        "mode": "full_recluster",
        "documents_processed": len(docs_with_embeddings),
        "topics_created": created_topics,
        "total_topics_created": sum(created_topics.values()),
        "titles_reused": reused_titles,
        "total_titles_reused": total_reused,
        "total_titles_generated": total_new,
        "documents_assigned": assignments,
        "level_stats": stats
    }
    
    print(f"\n{'='*60}")
    print(f"Full recluster complete")
    print(f"Topics created: {created_topics}")
    print(f"Titles reused (90% match): {reused_titles} (saved {total_reused} LLM calls)")
    print(f"New titles generated: {total_new}")
    print(f"{'='*60}\n")
    
    return result


def compute_topics(
    db: Session,
    days: int = 30,
    min_cluster_size: int = 5,
    min_docs_for_clustering: int = 3,
    vault_ids: list = None,
) -> TopicsResponse:
    """
    Main entry point to compute topics for the last `days` days.
    
    Args:
        db: Database session
        days: Number of days to look back
        min_cluster_size: Minimum cluster size for HDBSCAN
        min_docs_for_clustering: Minimum documents needed for clustering
        vault_ids: List of vault IDs to filter by. If None, returns all (auth disabled).
    """
    cfg = PREFERENCES.clustering
    min_docs_for_clustering = cfg.min_docs_for_clustering

    # 1) Select recent documents with vault filtering
    cutoff = datetime.utcnow() - timedelta(days=days)
    query = (
        db.query(Document)
        .filter(Document.captured_at >= cutoff)
        .order_by(Document.captured_at.desc())
    )
    if vault_ids is not None:
        query = query.filter(Document.vault_id.in_(vault_ids))
    docs = query.all()

    if not docs:
        return TopicsResponse(time_range_days=days, topics=[])

    # 2) Compute document embeddings
    doc_embeddings = compute_document_embeddings(db, docs)
    
    # 3) Fetch document summaries for improved topic naming
    doc_ids_for_summaries = [d.id for d in docs]
    doc_summaries = get_document_summaries(db, doc_ids_for_summaries)
    print(f"[Topics] Fetched {len(doc_summaries)} document summaries")

    # 4) Separate documents with existing topic assignments from unassigned
    # Pre-assigned documents will be grouped by their existing topic
    pre_assigned_groups: Dict[UUID, List[Document]] = {}
    unassigned_docs: List[Document] = []
    
    for d in docs:
        if d.assigned_topic_id:
            pre_assigned_groups.setdefault(d.assigned_topic_id, []).append(d)
        else:
            unassigned_docs.append(d)
    
    print(f"[Topics] {len(pre_assigned_groups)} existing topic groups, {len(unassigned_docs)} unassigned docs")

    # ---------- Process pre-assigned document groups first ----------
    topics: List[Topic] = []
    topic_idx_counter = 0
    
    # Create topics from pre-assigned groups (documents with existing topic assignments)
    for topic_db_id, docs_in_group in pre_assigned_groups.items():
        # Use the stored topic title from the first document
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
    
    # Check if number of pre-assigned topic groups exceeds k_topics_recluster
    # If so, re-cluster all pre-assigned documents instead of reusing their assignments
    if len(topics) > cfg.k_topics_recluster:
        print(f"[Topics] {len(topics)} pre-assigned topics exceeds k_topics_max ({cfg.k_topics_max}), re-clustering...")
        # Collect all documents from pre-assigned groups and add them to unassigned_docs
        docs_to_recluster = []
        for topic_db_id, docs_in_group in pre_assigned_groups.items():
            docs_to_recluster.extend(docs_in_group)
        unassigned_docs.extend(docs_to_recluster)
        # Clear the topics list since we're re-clustering
        topics = []
        topic_idx_counter = 0
    
    # ---------- Cluster unassigned documents ----------
    # Filter unassigned docs to those with embeddings
    unassigned_docs = [d for d in unassigned_docs if d.id in doc_embeddings]
    
    if len(unassigned_docs) < min_docs_for_clustering:
        # Not enough unassigned docs to cluster - put them all in one topic
        if unassigned_docs:
            topic_docs = [TopicDoc.model_validate(d) for d in unassigned_docs]
            title, summary = _generate_title_and_summary(unassigned_docs, db=db, doc_embeddings=doc_embeddings, doc_summaries=doc_summaries)
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
    
    # Get doc_ids in same order as X for agglomerative clustering
    doc_ids_ordered = [d.id for d in unassigned_docs]

    # 2) For visualization, always reduce to 2D/low-D (UMAP or none)
    X_vis = reduce_embeddings(X)   # uses cfg.dim_reducer; can be X unchanged
    # You'll use X_vis later to place document points if you want per-doc coordinates.

    # 3) For clustering, pass doc_ids for agglomerative clustering support
    labels = cluster_embeddings(X, doc_ids=doc_ids_ordered)

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
        KMeans = get_kmeans()
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
                    min_similarity=PREFERENCES.topic_persistence.similarity_threshold_subtopic,
                    level_index=1,
                    vault_ids=vault_ids
                )
                
                if existing_sub:
                    sub_db_id, title, summary = existing_sub
                else:
                    title, summary = _generate_title_and_summary(docs_list, db=db, doc_embeddings=doc_embeddings, doc_summaries=doc_summaries)
                    
                    # Save subtopic with parent_id
                    sub_db_id = _save_topic_to_db(
                        db=db,
                        title=title,
                        centroid=sub_centroid,
                        document_count=len(docs_list),
                        vault_id=docs_list[0].vault_id,
                        summary=summary,
                        parent_id=parent_db_id,
                        level_index=1
                    )
            else:
                # Persistence disabled or no parent - just generate title
                title, summary = _generate_title_and_summary(docs_list, db=db, doc_embeddings=doc_embeddings, doc_summaries=doc_summaries)

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
        
        # Check if we have an existing topic that matches (if persistence enabled and centroid available)
        if PREFERENCES.topic_persistence.persist_topics and centroid is not None:
            existing_match = _find_matching_topic(
                db, centroid,
                min_similarity=PREFERENCES.topic_persistence.similarity_threshold_topic,
                level_index=0,
                vault_ids=vault_ids
            )
            
            if existing_match:
                # Reuse existing topic
                topic_db_id, topic_title, topic_summary = existing_match
                print(f"♻ Reusing existing topic: {topic_title}")
            else:
                # Generate new title & summary using document summaries for richer context
                topic_title, topic_summary = _generate_title_and_summary(docs_list, db=db, doc_embeddings=doc_embeddings, doc_summaries=doc_summaries)
                
                # Save to database
                topic_db_id = _save_topic_to_db(
                    db=db,
                    title=topic_title,
                    centroid=centroid,
                    document_count=len(docs_list),
                    vault_id=docs_list[0].vault_id,
                    summary=topic_summary,
                    level_index=0
                )
                print(f"✓ Created new topic: {topic_title}")
        else:
            # Persistence disabled or no centroid - just generate title
            topic_title, topic_summary = _generate_title_and_summary(docs_list, db=db, doc_embeddings=doc_embeddings, doc_summaries=doc_summaries)

        # Build and persist subtopics
        subtopics = build_subtopics(topic_doc_indices, topic_id, topic_title, topic_db_id)

        # Save topic assignment to each document in this cluster
        if topic_db_id:
            for doc in docs_list:
                doc.assigned_topic_id = topic_db_id
                doc.assigned_topic_title = topic_title
                doc.topic_manually_assigned = False  # Mark as auto-assigned
            try:
                db.commit()
            except Exception as e:
                db.rollback()
                print(f"[WARN] Failed to save topic assignments: {e}")

        topic = Topic(
            topic_id=topic_id,
            title=topic_title,
            summary=topic_summary,
            documents_count=len(docs_list),
            subtopics=subtopics,
        )
        topics.append(topic)

    return TopicsResponse(time_range_days=days, topics=topics)

def get_topics_with_cache(
    db: Session, 
    days: int = 30,
    vault_ids: list = None
) -> TopicsResponse:
    """
    Lightweight in-process cache for topics:
    - Keyed by (days, max_captured_at, vault_ids)
    - If no new documents since last compute, reuse cached TopicsResponse
    
    Args:
        db: Database session
        days: Number of days to look back
        vault_ids: List of vault IDs to filter by. If None, returns all (auth disabled).
                   If empty list, returns empty result (user has no vault access).
    """
    # Handle case where user has no vault access
    if vault_ids is not None and len(vault_ids) == 0:
        return TopicsResponse(time_range_days=days, topics=[])

    # 1) Figure out the most recent document timestamp (for the user's vaults)
    max_query = db.query(func.max(Document.captured_at))
    if vault_ids is not None:
        max_query = max_query.filter(Document.vault_id.in_(vault_ids))
    max_captured_at = max_query.scalar()
    
    # Create hashable cache key including vault_ids
    vault_key = tuple(sorted(str(v) for v in vault_ids)) if vault_ids else None
    cache_key = (days, max_captured_at, vault_key)

    # 2) Return cached if we have it
    cached = _topics_cache.get(cache_key)
    if cached is not None:
        return cached

    # 3) Otherwise compute and store
    topics_resp = compute_topics(db, days=days, vault_ids=vault_ids)

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
    
    DEPRECATED: Use build_hierarchical_topics_for_d3 for the new 3-level hierarchy.
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


def build_hierarchical_topics_for_d3(
    db: Session, 
    days: int = 30,
    vault_ids: list = None
) -> dict:
    """
    Build a D3-friendly hierarchy from TOPIC_TABLE with 3 levels:
    
    root -> Level 2 (Categories) -> Level 1 (Topics) -> Level 0 (Fine) -> Documents
    
    Uses parent_id relationships in TOPIC_TABLE to build the tree.
    
    IMPORTANT: Only includes topics that have documents within the requested time window.
    Starts from documents, finds their topics, then builds up the ancestor chain.
    
    Args:
        db: Database session
        days: Number of days to look back for documents
        vault_ids: List of vault IDs to filter by. If None, returns all (auth disabled mode).
                   If empty list, returns empty result (user has no vault access).
    """
    from datetime import timedelta
    
    cutoff = datetime.utcnow() - timedelta(days=days)
    
    # Handle case where user has no vault access
    if vault_ids is not None and len(vault_ids) == 0:
        return {
            "name": "Topics",
            "time_range_days": days,
            "children": [],
            "type": "root"
        }
    
    # Build base query with vault filtering
    base_query = db.query(Document).filter(Document.captured_at >= cutoff)
    if vault_ids is not None:
        base_query = base_query.filter(Document.vault_id.in_(vault_ids))
    
    # Get documents in the time range with topic assignments
    docs_with_topics = base_query.filter(Document.assigned_topic_id != None).all()
    
    # Group documents by their assigned topic (Level 0)
    docs_by_topic: Dict[UUID, List[Document]] = {}
    for doc in docs_with_topics:
        docs_by_topic.setdefault(doc.assigned_topic_id, []).append(doc)
    
    # Get only the Level 0 topics that have documents in the time window
    relevant_topic_ids = set(docs_by_topic.keys())
    
    if not relevant_topic_ids:
        # No documents with topic assignments in this time range
        # Skip to unassigned docs below
        all_topics = []
        topic_by_id = {}
        children_by_parent = {}
        level_2_topics = []
    else:
        # Get all topics to build lookup tables - filter by vault
        query = db.query(TOPIC_TABLE).order_by(TOPIC_TABLE.level_index.desc())
        if vault_ids is not None and len(vault_ids) > 0:
            query = query.filter(TOPIC_TABLE.vault_id.in_(vault_ids))
        
        all_topics = query.all()
        
        # Build topic lookup by ID
        topic_by_id = {t.id: t for t in all_topics}
        
        # Walk up the hierarchy to find all ancestor topics that should be included
        # Start with Level 0 topics that have documents
        topics_to_include = set(relevant_topic_ids)
        
        for topic_id in list(relevant_topic_ids):
            # Walk up the parent chain
            current = topic_by_id.get(topic_id)
            while current and current.parent_id:
                topics_to_include.add(current.parent_id)
                current = topic_by_id.get(current.parent_id)
        
        # Build children lookup (parent_id -> list of child topics) - only for relevant topics
        children_by_parent: Dict[UUID, List] = {}
        level_2_topics = []  # Root level topics (categories)
        
        for topic in all_topics:
            if topic.id not in topics_to_include:
                continue  # Skip topics not in the relevant set
            
            if topic.parent_id and topic.parent_id in topics_to_include:
                children_by_parent.setdefault(topic.parent_id, []).append(topic)
            elif topic.level_index == 2:
                level_2_topics.append(topic)
    
    def build_topic_node(topic, level_name: str) -> dict | None:
        """Recursively build a topic node with its children.
        Returns None if the topic has no documents in the time window."""
        # Get direct document children (only for Level 0 topics)
        doc_children = []
        if topic.level_index == 0:
            docs = docs_by_topic.get(topic.id, [])
            for doc in docs:
                doc_children.append({
                    "name": doc.title or "(no title)",
                    "doc_id": str(doc.id),
                    "url": doc.url,
                    "captured_at": doc.captured_at.isoformat() if doc.captured_at else None,
                    "size": 1,
                    "type": "document"
                })
        
        # Get child topics (only ones in our relevant set)
        child_topics = children_by_parent.get(topic.id, [])
        topic_children = []
        
        child_level_names = {2: "Topic", 1: "Fine", 0: ""}
        for child in child_topics:
            child_node = build_topic_node(child, child_level_names.get(child.level_index, ""))
            # Only include children that have documents
            if child_node and child_node.get("doc_count", 0) > 0:
                topic_children.append(child_node)
        
        # Combine children: topic children first, then documents
        all_children = topic_children + doc_children
        
        # Count total documents under this topic
        total_docs = len(doc_children)
        for child in topic_children:
            total_docs += child.get("doc_count", 0)
        
        # Don't include topics with no documents
        if total_docs == 0:
            return None
        
        return {
            "name": f"{topic.title_text}" if topic.title_text else f"Cluster {topic.id}",
            "topic_id": str(topic.id),
            "level": topic.level_index,
            "level_name": level_name,
            "summary": topic.summary_text,
            "doc_count": total_docs,
            "children": all_children if all_children else None,
            "size": total_docs if not all_children else None,  # For leaf sizing
            "type": "topic"
        }
    
    # Build the root node
    root = {
        "name": "Topics",
        "time_range_days": days,
        "children": [],
        "type": "root"
    }
    
    # Add Level 2 topics as root children
    for topic in level_2_topics:
        topic_node = build_topic_node(topic, "Category")
        if topic_node and topic_node.get("doc_count", 0) > 0:
            root["children"].append(topic_node)
    
    # Handle orphan topics (Level 1 or 0 without parents that have documents)
    # This can happen if clustering created topics without full hierarchy
    if all_topics:
        orphan_topics = [t for t in all_topics 
                         if t.parent_id is None 
                         and t.level_index < 2
                         and t.id not in [lt.id for lt in level_2_topics]
                         and t.id in topics_to_include]
        
        for topic in orphan_topics:
            level_names = {0: "Fine", 1: "Topic"}
            topic_node = build_topic_node(topic, level_names.get(topic.level_index, ""))
            if topic_node and topic_node.get("doc_count", 0) > 0:
                root["children"].append(topic_node)
    
    # Also include documents with no topic assignment as "Uncategorized"
    unassigned_query = (
        db.query(Document)
        .filter(Document.captured_at >= cutoff)
        .filter(Document.assigned_topic_id == None)
    )
    if vault_ids is not None:
        unassigned_query = unassigned_query.filter(Document.vault_id.in_(vault_ids))
    unassigned_docs = unassigned_query.all()
    
    if unassigned_docs:
        uncategorized = {
            "name": f"Uncategorized ({len(unassigned_docs)})",
            "topic_id": "uncategorized",
            "level": -1,
            "level_name": "Uncategorized",
            "summary": "Documents without topic assignment",
            "doc_count": len(unassigned_docs),
            "children": [
                {
                    "name": doc.title or "(no title)",
                    "doc_id": str(doc.id),
                    "url": doc.url,
                    "captured_at": doc.captured_at.isoformat() if doc.captured_at else None,
                    "size": 1,
                    "type": "document"
                }
                for doc in unassigned_docs
            ],
            "type": "topic"
        }
        root["children"].append(uncategorized)
    
    return root
