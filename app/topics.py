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
from .helpers import _openai_chat, _ollama_chat, get_embedding, EMBED_TABLE

# ---------- OpenAI client ----------
_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
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

def _generate_title_and_summary(docs: List[Document]) -> Tuple[str, str]:
    """
    Use OpenAI to generate a short title and 2–3 sentence summary for a cluster.
    """
    #if True: #test to check speed
    if not docs:
        return "Miscellaneous", "Mixed documents."

    # Take up to 10 docs, use titles + first snippet of full_text
    snippets = []
    for d in docs[:10]:
        title = d.title or "(no title)"
        body = (d.full_text or "")[:400].replace("\n", " ")
        snippets.append(f"Title: {title}\nSnippet: {body}")

    context = "\n\n".join(snippets)

    prompt_old = (
        "You are helping categorize a cluster of documents. "
        "Based on the titles and snippets below, create:\n"
        "1) A SHORT topic title (max 5 words)\n"
        #"2) A concise 2-3 sentence summary of the main theme.\n\n"
        "Respond in the format:\n"
        "TITLE: <short title>\n"
        "SUMMARY: \n"#<summary text>\n\n"
        f"DOCUMENTS:\n{context}"
    )
    
    prompt = (
        "You are helping categorize a cluster of documents. "
        "Based on the titles and snippets below, create:\n"
        "1) A SHORT topic title (max 10 words)\n"
        "Respond in the format:\n"
        "TITLE: <short title>\n"
        f"DOCUMENTS:\n{context}"
    )

    model_provider = getattr(PREFERENCES.models, "provider", "openai")
    ollama_title_options = {
        "temperature": 0.3,
        "top_p": 0.8,
        "num_predict": 32
    }
    if model_provider == "ollama":
        text = _ollama_chat(prompt, ollama_title_options)
    else:
        text = _openai_chat(prompt)

    title = "Untitled Topic"
    summary = ""

    for line in text.splitlines():
        if line.upper().startswith("TITLE:"):
            title = line.split(":", 1)[1].strip() or title
        elif line.upper().startswith("SUMMARY:"):
            summary = line.split(":", 1)[1].strip() or summary

    if not summary:
        summary = "Summary not available."

    return title, summary


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
    print(f"embedding_dimensions: {emb_dim}")

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

    if len(deduped_docs) < min_docs_for_clustering:
        # Not enough docs to cluster — return a single topic
        topic_docs = [TopicDoc.model_validate(d) for d in deduped_docs]
        title, summary = _generate_title_and_summary(deduped_docs)
        single_topic = Topic(
            topic_id="T0",
            title=title,
            summary=summary,
            documents_count=len(topic_docs),
            subtopics=[
                Subtopic(
                    subtopic_id="T0-S0",
                    title=title,
                    summary=summary,
                    documents=topic_docs,
                )
            ],
        )
        return TopicsResponse(time_range_days=days, topics=[single_topic])

    # Build matrix of embeddings for deduped docs
    deduped_docs = [d for d in deduped_docs if d.id in doc_embeddings]
    if len(deduped_docs) < min_docs_for_clustering:
        # if embeddings filtered out too much
        topic_docs = [TopicDoc.model_validate(d) for d in deduped_docs]
        title, summary = _generate_title_and_summary(deduped_docs)
        single_topic = Topic(
            topic_id="T0",
            title=title,
            summary=summary,
            documents_count=len(topic_docs),
            subtopics=[
                Subtopic(
                    subtopic_id="T0-S0",
                    title=title,
                    summary=summary,
                    documents=topic_docs,
                )
            ],
        )
        return TopicsResponse(time_range_days=days, topics=[single_topic])
    # Build matrix of embeddings for deduped docs
    deduped_docs = [d for d in deduped_docs if d.id in doc_embeddings]
    if len(deduped_docs) < min_docs_for_clustering:
        # embeddings filtered out too much
        topic_docs = [TopicDoc.model_validate(d) for d in deduped_docs]
        title, summary = _generate_title_and_summary(deduped_docs)
        single_topic = Topic(
            topic_id="T0",
            title=title,
            summary=summary,
            documents_count=len(topic_docs),
            subtopics=[
                Subtopic(
                    subtopic_id="T0-S0",
                    title=title,
                    summary=summary,
                    documents=topic_docs,
                )
            ],
        )
        return TopicsResponse(time_range_days=days, topics=[single_topic])

    X = np.stack([doc_embeddings[d.id] for d in deduped_docs], axis=0)
    n_docs = X.shape[0]

    # ---------- small-N guard ----------
    # For very small N, skip UMAP/clustering and treat as a single topic.
    if n_docs < min_docs_for_clustering:
        topic_docs = [TopicDoc.model_validate(d) for d in deduped_docs]
        title, summary = _generate_title_and_summary(deduped_docs)
        single_topic = Topic(
            topic_id="T0",
            title=title,
            summary=summary,
            documents_count=len(topic_docs),
            subtopics=[
                Subtopic(
                    subtopic_id="T0-S0",
                    title=title,
                    summary=summary,
                    documents=topic_docs,
                )
            ],
        )
        return TopicsResponse(time_range_days=days, topics=[single_topic])


 # 2) For visualization, always reduce to 2D/low-D (UMAP or none)
    X_vis = reduce_embeddings(X)   # uses cfg.dim_reducer; can be X unchanged
    # You'll use X_vis later to place document points if you want per-doc coordinates.

    # 3) For clustering, maybe use the same reduced space, maybe not
    labels = cluster_embeddings(X)

    topic_labels = sorted(set(labels))  # e.g. [0,1,2,...]
    topics: List[Topic] = []
    # Map from original index to doc
    idx_to_doc = {i: d for i, d in enumerate(deduped_docs)}

    # Helper to build subtopics via a second-level KMeans
    def build_subtopics(topic_docs_indices: List[int], parent_topic_id: str, parent_title: str) -> List[Subtopic]:
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
            title, summary = _generate_title_and_summary(docs_list)

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

    # 7) Build topic objects
    for topic_idx, cluster_label in enumerate(topic_labels):
        topic_id = f"T{topic_idx}"

        topic_doc_indices = [i for i, lbl in enumerate(labels) if lbl == cluster_label]
        docs_list = [idx_to_doc[i] for i in topic_doc_indices]

        # Generate title & summary for this top-level topic
        topic_title, topic_summary = _generate_title_and_summary(docs_list)

        subtopics = build_subtopics(topic_doc_indices, topic_id, topic_title)

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
                    "score_info": doc.score_info,
                    "score_ai_slop": doc.score_ai_slop,
                    "captured_at": doc.captured_at.isoformat(),
                    # D3 circle packing will use this as bubble size
                    "size": 1
                }
                sub_node["children"].append(doc_node)

            topic_node["children"].append(sub_node)

        root["children"].append(topic_node)

    return root
