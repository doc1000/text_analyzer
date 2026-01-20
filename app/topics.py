# app/topics.py

import os
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional
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
    db.commit()
    db.refresh(topic_record)
    
    return topic_record.id


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
        return None
    
    # Find best match using cosine similarity
    best_match = None
    best_similarity = similarity_threshold
    
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

def compute_hierarchical_topics(
    db: Session,
    days: int = 30,
) -> dict:
    """
    Compute and save topics at all hierarchy levels using agglomerative clustering.
    
    This creates topics at 3 levels:
    - Level 0: Fine-grained topics (cosine sim >= 0.85, distance <= 0.15)
    - Level 1: Topics (cosine sim >= 0.75, distance <= 0.25)
    - Level 2: Super-topics (cosine sim >= 0.60, distance <= 0.40)
    
    Topics at each level are linked to their parent at the next level up.
    
    Returns:
        Dict with statistics and created topic info
    """
    from .mmr import compute_centroid
    
    cfg = PREFERENCES.clustering
    agglom_cfg = PREFERENCES.agglomerative
    
    # 1) Select recent documents
    cutoff = datetime.utcnow() - timedelta(days=days)
    docs = (
        db.query(Document)
        .filter(Document.captured_at >= cutoff)
        .order_by(Document.captured_at.desc())
        .all()
    )
    
    if not docs:
        return {"status": "no_documents", "message": "No documents found in time range"}
    
    print(f"\n{'='*60}")
    print(f"Computing hierarchical topics for {len(docs)} documents")
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
    
    # 8) Clear existing topics for clean reclustering
    try:
        deleted = db.query(TOPIC_TABLE).delete()
        db.commit()
        print(f"Cleared {deleted} existing topics")
    except Exception as e:
        db.rollback()
        print(f"[WARN] Could not clear existing topics: {e}")
    
    # 9) Create topics at each level, starting from coarsest (level 2) down to finest (level 0)
    # This ensures parent topics exist before children reference them
    
    # Map: (level, cluster_label) -> topic_db_id
    topic_id_map: Dict[Tuple[int, int], UUID] = {}
    
    # Map: doc_id -> Document object
    doc_by_id = {d.id: d for d in docs_with_embeddings}
    
    created_topics = {level: 0 for level in range(len(agglom_cfg.level_thresholds))}
    
    # Process levels from coarsest to finest
    for level in reversed(range(len(agglom_cfg.level_thresholds))):
        level_labels = labels_by_level[level]
        level_structure = structure[level]
        
        print(f"\nCreating Level {level} topics ({len(level_structure)} clusters)...")
        
        for cluster_label, doc_ids_in_cluster in level_structure.items():
            # Get documents in this cluster
            docs_in_cluster = [doc_by_id[did] for did in doc_ids_in_cluster if did in doc_by_id]
            
            if not docs_in_cluster:
                continue
            
            # Compute centroid
            cluster_embeddings = [doc_embeddings[d.id] for d in docs_in_cluster]
            centroid = compute_centroid(cluster_embeddings, normalize=True)
            
            # Find parent topic (for levels 0 and 1)
            parent_id = None
            if level < len(agglom_cfg.level_thresholds) - 1:
                # Get parent cluster label from hierarchy
                parent_cluster_label = hierarchy.get(level, {}).get(cluster_label)
                if parent_cluster_label:
                    parent_id = topic_id_map.get((level + 1, parent_cluster_label))
            
            # Generate title and summary
            title, summary = _generate_title_and_summary(
                docs_in_cluster, 
                db=db, 
                doc_embeddings=doc_embeddings, 
                doc_summaries=doc_summaries
            )
            
            # Add level indicator to title for clarity
            level_names = {0: "Fine", 1: "Topic", 2: "Category"}
            level_prefix = level_names.get(level, f"L{level}")
            
            # Save topic to database
            topic_db_id = _save_topic_to_db(
                db=db,
                title=title,
                centroid=centroid,
                document_count=len(docs_in_cluster),
                summary=summary,
                parent_id=parent_id,
                level_index=level
            )
            
            # Store mapping
            topic_id_map[(level, cluster_label)] = topic_db_id
            created_topics[level] += 1
            
            print(f"  [{level_prefix}] {title[:50]}... ({len(docs_in_cluster)} docs)")
        
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
                doc.assigned_topic_id = topic_db_id
                doc.assigned_topic_title = topic_title
                assignments += 1
    
    db.commit()
    print(f"Assigned {assignments} documents to topics")
    
    # Clear cache
    clear_topics_cache()
    
    # Return summary
    result = {
        "status": "ok",
        "documents_processed": len(docs_with_embeddings),
        "topics_created": created_topics,
        "total_topics": sum(created_topics.values()),
        "documents_assigned": assignments,
        "level_stats": stats
    }
    
    print(f"\n{'='*60}")
    print(f"Hierarchical topic computation complete")
    print(f"Created: {created_topics}")
    print(f"{'='*60}\n")
    
    return result


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
    
    # 3b) Fetch document summaries for improved topic naming
    doc_ids_for_summaries = [d.id for d in canonical_docs]
    doc_summaries = get_document_summaries(db, doc_ids_for_summaries)
    print(f"[Topics] Fetched {len(doc_summaries)} document summaries")

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
                    title, summary = _generate_title_and_summary(docs_list, db=db, doc_embeddings=doc_embeddings, doc_summaries=doc_summaries)
                    
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
                similarity_threshold=PREFERENCES.topic_persistence.similarity_threshold_topic,
                level_index=0
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


def build_hierarchical_topics_for_d3(db: Session, days: int = 30) -> dict:
    """
    Build a D3-friendly hierarchy from TOPIC_TABLE with 3 levels:
    
    root -> Level 2 (Categories) -> Level 1 (Topics) -> Level 0 (Fine) -> Documents
    
    Uses parent_id relationships in TOPIC_TABLE to build the tree.
    """
    from datetime import timedelta
    
    cutoff = datetime.utcnow() - timedelta(days=days)
    
    # Get all topics ordered by level (coarsest first)
    all_topics = (
        db.query(TOPIC_TABLE)
        .order_by(TOPIC_TABLE.level_index.desc())
        .all()
    )
    
    # Get documents in the time range with topic assignments
    docs_with_topics = (
        db.query(Document)
        .filter(Document.captured_at >= cutoff)
        .filter(Document.assigned_topic_id != None)
        .all()
    )
    
    # Group documents by their assigned topic (Level 0)
    docs_by_topic: Dict[UUID, List[Document]] = {}
    for doc in docs_with_topics:
        docs_by_topic.setdefault(doc.assigned_topic_id, []).append(doc)
    
    # Build topic lookup by ID
    topic_by_id = {t.id: t for t in all_topics}
    
    # Build children lookup (parent_id -> list of child topics)
    children_by_parent: Dict[UUID, List] = {}
    level_2_topics = []  # Root level topics (categories)
    
    for topic in all_topics:
        if topic.parent_id:
            children_by_parent.setdefault(topic.parent_id, []).append(topic)
        elif topic.level_index == 2:
            level_2_topics.append(topic)
    
    def build_topic_node(topic, level_name: str) -> dict:
        """Recursively build a topic node with its children."""
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
        
        # Get child topics
        child_topics = children_by_parent.get(topic.id, [])
        topic_children = []
        
        child_level_names = {2: "Topic", 1: "Fine", 0: ""}
        for child in child_topics:
            child_node = build_topic_node(child, child_level_names.get(child.level_index, ""))
            topic_children.append(child_node)
        
        # Combine children: topic children first, then documents
        all_children = topic_children + doc_children
        
        # Count total documents under this topic
        total_docs = len(doc_children)
        for child in topic_children:
            total_docs += child.get("doc_count", 0)
        
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
        if topic_node["children"] or topic_node.get("doc_count", 0) > 0:
            root["children"].append(topic_node)
    
    # Handle orphan topics (Level 1 or 0 without parents)
    # This can happen if clustering created topics without full hierarchy
    orphan_topics = [t for t in all_topics 
                     if t.parent_id is None 
                     and t.level_index < 2
                     and t.id not in [lt.id for lt in level_2_topics]]
    
    for topic in orphan_topics:
        level_names = {0: "Fine", 1: "Topic"}
        topic_node = build_topic_node(topic, level_names.get(topic.level_index, ""))
        if topic_node["children"] or topic_node.get("doc_count", 0) > 0:
            root["children"].append(topic_node)
    
    # Also include documents with no topic assignment as "Uncategorized"
    unassigned_docs = (
        db.query(Document)
        .filter(Document.captured_at >= cutoff)
        .filter(Document.assigned_topic_id == None)
        .all()
    )
    
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
