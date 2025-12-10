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
import hdbscan
from openai import OpenAI
from urllib.parse import urlsplit, urlunsplit, parse_qsl

from pydantic import BaseModel, Field

from .models import Document, Chunk

# ---------- Pydantic response models ----------

class TopicDoc(BaseModel):
    id: UUID
    title: str | None
    url: str
    score_info: float | None = None
    score_ai_slop: float | None = None
    captured_at: datetime

    class Config:
        from_attributes = True  # Pydantic v2


class Subtopic(BaseModel):
    subtopic_id: str
    title: str
    summary: str | None = None
    documents: List[TopicDoc]


class Topic(BaseModel):
    topic_id: str
    title: str
    summary: str | None = None
    documents_count: int
    subtopics: List[Subtopic]


class TopicsResponse(BaseModel):
    time_range_days: int = Field(..., description="Number of days used for the time window")
    topics: List[Topic]


# ---------- OpenAI client ----------

_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def _generate_title_and_summary(docs: List[Document]) -> Tuple[str, str]:
    """
    Use OpenAI to generate a short title and 2–3 sentence summary for a cluster.
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
        "1) A SHORT topic title (max 5 words)\n"
        "2) A concise 2-3 sentence summary of the main theme.\n\n"
        "Respond in the format:\n"
        "TITLE: <short title>\n"
        "SUMMARY: <summary text>\n\n"
        f"DOCUMENTS:\n{context}"
    )

    resp = _client.chat.completions.create(
        model="gpt-4.1-nano",
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.choices[0].message.content.strip()

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


# ---------- Embeddings aggregation ----------

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

    chunks = (
        db.query(Chunk)
        .filter(Chunk.document_id.in_(doc_ids))
        .filter(Chunk.embedding != None)  # only chunks with embeddings
        .all()
    )

    by_doc: Dict[UUID, List[np.ndarray]] = {}
    for ch in chunks:
        # pgvector returns something list-like; convert to numpy array
        vec = np.array(ch.embedding, dtype=np.float32)
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

    X = np.stack([doc_embeddings[d.id] for d in deduped_docs], axis=0)
    n_docs = X.shape[0]

    # ---------- NEW: handle small-N safely ----------
    # For very small N, skip UMAP/HDBSCAN and treat as a single topic.
    if n_docs < 6:
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

    # Make UMAP parameters respect dataset size
    n_neighbors = max(2, min(15, n_docs - 1))
    n_components = max(2, min(5, n_docs - 1))

    # 5) UMAP dimensionality reduction
    reducer = umap.UMAP(
        n_neighbors=n_neighbors,
        min_dist=0.1,
        n_components=n_components,
        metric="cosine",
        random_state=42,
    )
    X_reduced = reducer.fit_transform(X)


    # 6) Top-level clustering with HDBSCAN
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        metric="euclidean",
        cluster_selection_method="eom",
    )
    labels = clusterer.fit_predict(X_reduced)

    # Labels: -1 = noise
    unique_labels = sorted(set(labels))
    topic_labels = [l for l in unique_labels if l != -1]

    topics: List[Topic] = []

    # Map from original index to doc
    idx_to_doc = {i: d for i, d in enumerate(deduped_docs)}

    # Helper to build subtopics via a second-level HDBSCAN
    def build_subtopics(topic_docs_indices: List[int], parent_topic_id: str, parent_title: str) -> List[Subtopic]:
        if len(topic_docs_indices) < 5:
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

        # Second-level clustering on reduced vectors for docs in this topic
        X_topic = np.stack([X_reduced[i] for i in topic_docs_indices], axis=0)
        sub_clusterer = hdbscan.HDBSCAN(
            min_cluster_size=3,
            metric="euclidean",
            cluster_selection_method="eom",
        )
        sub_labels = sub_clusterer.fit_predict(X_topic)
        unique_sub_labels = sorted(set(sub_labels))

        # If second-level clustering fails or trivial, fallback to one subtopic
        if len(unique_sub_labels) <= 1:
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

        subtopics: List[Subtopic] = []
        for s_label in unique_sub_labels:
            if s_label == -1:
                continue  # optional: skip noise here or bucket it

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

        # Generate title & summary
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

    # Optionally handle noise docs (label == -1) as a "Misc" topic
    noise_indices = [i for i, lbl in enumerate(labels) if lbl == -1]
    if noise_indices:
        docs_list = [idx_to_doc[i] for i in noise_indices]
        docs_out = [TopicDoc.model_validate(d) for d in docs_list]
        misc_title, misc_summary = _generate_title_and_summary(docs_list)
        misc_topic = Topic(
            topic_id="T-misc",
            title=misc_title or "Miscellaneous",
            summary=misc_summary,
            documents_count=len(docs_list),
            subtopics=[
                Subtopic(
                    subtopic_id="T-misc-S0",
                    title=misc_title or "Miscellaneous",
                    summary=misc_summary,
                    documents=docs_out,
                )
            ],
        )
        topics.append(misc_topic)

    return TopicsResponse(time_range_days=days, topics=topics)

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
