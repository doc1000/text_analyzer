"""
Deterministic cluster labeling for semantic_tree_v2.

This module intentionally keeps TF-IDF in the app layer:
- TF-IDF is computed once per vault relabel run.
- Per-node labels reuse the vault-wide matrix.
- SQL remains the atomic write path via semantic_tree_v2.update_node_label.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from uuid import UUID

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from .db import DOCUMENT_TABLE
from .config import PREFERENCES
from .helpers import _ollama_chat, _openai_chat
from .models import Document, SemanticTreeNode, SemanticTreeNodeDocument


SCHEMA = "semantic_tree_v2"

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_-]+")
_STOP_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "onto",
    "your",
    "you",
    "are",
    "was",
    "were",
    "will",
    "have",
    "has",
    "had",
    "about",
    "after",
    "before",
    "over",
    "under",
    "between",
    "also",
    "just",
    "they",
    "them",
    "their",
    "there",
    "where",
    "when",
    "what",
    "which",
    "who",
    "why",
    "how",
    "document"
}
_GENERIC_LABEL_TERMS = ("cluster", "undefined", "misc", "insights", "unclassified")
LLM_REFINEMENT_CONFIDENCE_THRESHOLD = 0.95


@dataclass
class VaultLabelContext:
    doc_index: Dict[UUID, int]
    feature_names: List[str]
    tfidf_matrix: Optional[object]
    doc_texts: Dict[UUID, str]
    doc_titles: Dict[UUID, str]
    doc_tags: Dict[UUID, List[str]]


def _tokenize(text_value: str) -> List[str]:
    if not text_value:
        return []
    tokens = [t.lower() for t in _WORD_RE.findall(text_value.lower())]
    return [t for t in tokens if len(t) >= 3 and t not in _STOP_WORDS]


def _top_counts(counter: Counter, key_name: str, top_k: int) -> List[dict]:
    if not counter:
        return []
    return [{key_name: term, "freq": int(freq)} for term, freq in counter.most_common(top_k)]


def _extract_summary_phrases(text_value: str, top_k: int = 8) -> List[dict]:
    tokens = _tokenize(text_value)
    if not tokens:
        return []

    phrase_counter: Counter = Counter()
    # Unigrams
    for tok in tokens:
        phrase_counter[tok] += 1
    # Bigrams
    for i in range(len(tokens) - 1):
        phrase_counter[f"{tokens[i]} {tokens[i + 1]}"] += 1
    return _top_counts(phrase_counter, "phrase", top_k)


def _aggregate_title_terms(titles: List[str], top_k: int = 8) -> List[dict]:
    counter: Counter = Counter()
    for title in titles:
        for tok in _tokenize(title):
            counter[tok] += 1
    return _top_counts(counter, "term", top_k)


def _aggregate_tags(tags_by_doc: Dict[UUID, List[str]], doc_ids: List[UUID]) -> List[dict]:
    if not doc_ids:
        return []
    counter: Counter = Counter()
    for did in doc_ids:
        for tag in tags_by_doc.get(did, []) or []:
            cleaned = (tag or "").strip()
            if cleaned:
                counter[cleaned] += 1
    total_docs = max(len(doc_ids), 1)
    out = []
    for tag, freq in counter.most_common(10):
        out.append({"tag": tag, "freq": int(freq), "ratio": float(freq) / float(total_docs)})
    return out


def _build_vault_label_context(
    db: Session,
    vault_id: UUID,
    node_to_docs: Dict[UUID, List[UUID]],
) -> VaultLabelContext:
    # Unique docs across all nodes in this vault relabel pass.
    ordered_doc_ids: List[UUID] = []
    seen = set()
    for ids in node_to_docs.values():
        for did in ids:
            if did not in seen:
                seen.add(did)
                ordered_doc_ids.append(did)

    if not ordered_doc_ids:
        return VaultLabelContext({}, [], None, {}, {}, {})

    rows = (
        db.query(
            Document.id,
            Document.title,
            Document.user_tags,
            Document.extracted_text,
            Document.captured_text,
            Document.full_text,
            DOCUMENT_TABLE.summary_text,
        )
        .outerjoin(DOCUMENT_TABLE, DOCUMENT_TABLE.document_id == Document.id)
        .filter(Document.vault_id == vault_id, Document.id.in_(ordered_doc_ids))
        .all()
    )

    doc_texts: Dict[UUID, str] = {}
    doc_titles: Dict[UUID, str] = {}
    doc_tags: Dict[UUID, List[str]] = {}
    for r in rows:
        doc_id = r[0]
        title = (r[1] or "").strip()
        tags = list(r[2] or [])
        extracted = (r[3] or "").strip()
        captured = (r[4] or "").strip()
        full_text = (r[5] or "").strip()
        summary_text = (r[6] or "").strip()

        # Prefer summary_text, then NLP text fields, then title.
        text_value = summary_text or extracted or captured or full_text or title
        doc_texts[doc_id] = text_value
        doc_titles[doc_id] = title
        doc_tags[doc_id] = tags

    # Keep stable order for reproducible signature behavior.
    doc_ids = [did for did in ordered_doc_ids if did in doc_texts]
    doc_index = {did: idx for idx, did in enumerate(doc_ids)}
    corpus = [doc_texts[did] for did in doc_ids]

    tfidf_matrix = None
    feature_names: List[str] = []
    if corpus and any((txt or "").strip() for txt in corpus):
        from sklearn.feature_extraction.text import TfidfVectorizer

        vectorizer = TfidfVectorizer(
            lowercase=True,
            stop_words="english",
            ngram_range=(1, 2),
            max_features=5000,
            min_df=1,
        )
        try:
            tfidf_matrix = vectorizer.fit_transform(corpus)
            feature_names = list(vectorizer.get_feature_names_out())
        except ValueError:
            # Empty vocabulary after tokenization.
            tfidf_matrix = None
            feature_names = []

    return VaultLabelContext(
        doc_index=doc_index,
        feature_names=feature_names,
        tfidf_matrix=tfidf_matrix,
        doc_texts=doc_texts,
        doc_titles=doc_titles,
        doc_tags=doc_tags,
    )


def _resolve_subtree_docs_for_nodes(
    db: Session,
    vault_id: UUID,
    node_ids: List[UUID],
) -> Dict[UUID, List[UUID]]:
    """
    Resolve subtree documents for each root node in node_ids using one recursive CTE.
    """
    if not node_ids:
        return {}

    result = db.execute(
        text(
            f"""
            WITH RECURSIVE subtree AS (
              SELECT id AS root_id, id AS node_id
              FROM {SCHEMA}.tree_node
              WHERE vault_id = :vault_id
                AND id = ANY(CAST(:node_ids AS uuid[]))

              UNION ALL

              SELECT s.root_id, tn.id AS node_id
              FROM {SCHEMA}.tree_node tn
              JOIN subtree s ON tn.parent_id = s.node_id
              WHERE tn.vault_id = :vault_id
            )
            SELECT s.root_id, nd.document_id
            FROM subtree s
            LEFT JOIN {SCHEMA}.node_document nd
              ON nd.vault_id = :vault_id
             AND nd.node_id = s.node_id
            """
        ),
        {
            "vault_id": str(vault_id),
            "node_ids": [str(x) for x in node_ids],
        },
    )

    by_node: Dict[UUID, set] = {nid: set() for nid in node_ids}
    for root_id, document_id in result.fetchall():
        if root_id in by_node and document_id is not None:
            by_node[root_id].add(document_id)

    # Keep deterministic ordering for stable signatures.
    return {
        nid: sorted(list(docs), key=lambda d: str(d))
        for nid, docs in by_node.items()
    }


def _top_tfidf_terms(
    ctx: VaultLabelContext,
    doc_ids: List[UUID],
    top_k: int = 10,
) -> List[dict]:
    if ctx.tfidf_matrix is None or not ctx.feature_names:
        return []

    row_idxs = [ctx.doc_index[did] for did in doc_ids if did in ctx.doc_index]
    if not row_idxs:
        return []

    # Aggregate doc rows once per node.
    node_vec = ctx.tfidf_matrix[row_idxs].sum(axis=0)
    dense = np.asarray(node_vec).ravel()
    if dense.size == 0:
        return []

    nz = np.where(dense > 0)[0]
    if len(nz) == 0:
        return []
    ranked = sorted(nz, key=lambda i: float(dense[i]), reverse=True)[:top_k]
    return [
        {"phrase": ctx.feature_names[i], "score": float(dense[i])}
        for i in ranked
    ]


def _fallback_label(doc_count: int) -> str:
    if doc_count >= 20:
        return "General"
    if doc_count >= 5:
        return "Topic"
    return "Cluster"


def _select_deterministic_label(signals: dict) -> Tuple[str, float]:
    tags = signals.get("tags", [])
    tfidf_terms = signals.get("tfidf_terms", [])
    title_terms = signals.get("title_terms", [])
    summary_phrases = signals.get("summary_phrases", [])
    doc_count = int(signals.get("doc_count", 0))

    # Preserve strong anchor behavior.
    if tags:
        top_tag = tags[0]
        if float(top_tag.get("ratio", 0.0)) >= 0.6:
            return str(top_tag.get("tag") or _fallback_label(doc_count)), 0.95

    candidate_scores: Dict[str, float] = defaultdict(float)
    for t in tfidf_terms:
        phrase = (t.get("phrase") or "").strip()
        if phrase:
            candidate_scores[phrase] += 0.40 * float(t.get("score", 0.0))
    for t in summary_phrases:
        phrase = (t.get("phrase") or "").strip()
        if phrase:
            candidate_scores[phrase] += 0.30 * float(t.get("freq", 0))
    for t in title_terms:
        term = (t.get("term") or "").strip()
        if term:
            candidate_scores[term] += 0.20 * float(t.get("freq", 0))
    for t in tags:
        tag = (t.get("tag") or "").strip()
        if tag:
            candidate_scores[tag] += 0.30 * float(t.get("ratio", 0.0))

    if not candidate_scores:
        return _fallback_label(doc_count), 0.35

    label, raw_score = max(candidate_scores.items(), key=lambda kv: kv[1])
    confidence = max(0.35, min(0.92, float(raw_score) / 3.0))
    # Keep labels concise for UI readability.
    words = label.split()
    if len(words) > 6:
        label = " ".join(words[:6])
    return label, confidence


def _compute_signature_hash(node_id: UUID, signals: dict, doc_ids: List[UUID]) -> str:
    # Stable, normalized subset for change detection.
    sig_payload = {
        "node_id": str(node_id),
        "doc_count": int(signals.get("doc_count", 0)),
        "doc_ids": [str(x) for x in sorted(doc_ids, key=lambda d: str(d))],
        "tags": [
            {"tag": x.get("tag"), "freq": int(x.get("freq", 0))}
            for x in signals.get("tags", [])[:10]
        ],
        "tfidf_terms": [
            {"phrase": x.get("phrase"), "score": round(float(x.get("score", 0.0)), 6)}
            for x in signals.get("tfidf_terms", [])[:10]
        ],
        "title_terms": [
            {"term": x.get("term"), "freq": int(x.get("freq", 0))}
            for x in signals.get("title_terms", [])[:10]
        ],
        "summary_phrases": [
            {"phrase": x.get("phrase"), "freq": int(x.get("freq", 0))}
            for x in signals.get("summary_phrases", [])[:10]
        ],
    }
    encoded = json.dumps(sig_payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _build_node_signals(
    ctx: VaultLabelContext,
    node_doc_ids: List[UUID],
) -> dict:
    doc_ids = [did for did in node_doc_ids if did in ctx.doc_texts]
    titles = [ctx.doc_titles.get(did, "") for did in doc_ids if ctx.doc_titles.get(did)]
    merged_text = "\n".join(ctx.doc_texts.get(did, "") for did in doc_ids if ctx.doc_texts.get(did))
    signals = {
        "doc_count": len(doc_ids),
        "tags": _aggregate_tags(ctx.doc_tags, doc_ids),
        "tfidf_terms": _top_tfidf_terms(ctx, doc_ids, top_k=10),
        "title_terms": _aggregate_title_terms(titles, top_k=10),
        "summary_phrases": _extract_summary_phrases(merged_text, top_k=10),
    }
    return signals


def relabel_vault_deterministic(db: Session, vault_id: UUID) -> dict:
    """
    Deterministically relabel eligible auto cluster nodes in a vault.

    Returns simple stats:
    {
      "vault_id": "...",
      "eligible_nodes": int,
      "updated_nodes": int,
      "skipped_unchanged": int
    }
    """
    return relabel_vault_deterministic_with_status(db, vault_id, status="auto")


def relabel_nodes(
    db: Session,
    vault_id: UUID,
    node_ids: List[UUID],
    status: str = "needs_llm",
) -> dict:
    """
    Deterministically relabel a subset of cluster nodes (e.g. after ingestion placement).
    Uses same logic as relabel_vault_deterministic_with_status but scoped to node_ids.
    """
    if not node_ids:
        return {"vault_id": str(vault_id), "eligible_nodes": 0, "updated_nodes": 0, "skipped_unchanged": 0}

    eligible_rows = (
        db.query(SemanticTreeNode.id, SemanticTreeNode.label_signature_hash)
        .filter(
            SemanticTreeNode.vault_id == vault_id,
            SemanticTreeNode.id.in_(node_ids),
            SemanticTreeNode.node_type == "cluster",
            SemanticTreeNode.locked == False,
            SemanticTreeNode.title_source.in_(("auto", "llm")),
        )
        .all()
    )
    if not eligible_rows:
        return {"vault_id": str(vault_id), "eligible_nodes": 0, "updated_nodes": 0, "skipped_unchanged": 0}

    scoped_ids = [row[0] for row in eligible_rows]
    old_hash_by_node = {row[0]: row[1] for row in eligible_rows}
    node_to_docs = _resolve_subtree_docs_for_nodes(db, vault_id, scoped_ids)
    ctx = _build_vault_label_context(db, vault_id, node_to_docs)

    updated_nodes = 0
    skipped_unchanged = 0
    for node_id in scoped_ids:
        doc_ids = node_to_docs.get(node_id, [])
        signals = _build_node_signals(ctx, doc_ids)
        label, confidence = _select_deterministic_label(signals)
        signals["deterministic_label"] = label
        signals["deterministic_confidence"] = confidence

        signature_hash = _compute_signature_hash(node_id, signals, doc_ids)
        if old_hash_by_node.get(node_id) == signature_hash:
            skipped_unchanged += 1
            continue

        effective_status = status

        db.execute(
            text(
                f"""
                SELECT {SCHEMA}.update_node_label(
                  :vault_id, :node_id, :label,
                  CAST(:signals AS jsonb),
                  :signature_hash,
                  :status,
                  :title_source
                )
                """
            ),
            {
                "vault_id": str(vault_id),
                "node_id": str(node_id),
                "label": label,
                "signals": json.dumps(signals),
                "signature_hash": signature_hash,
                "status": effective_status,
                "title_source": None,
            },
        )
        updated_nodes += 1

    return {
        "vault_id": str(vault_id),
        "eligible_nodes": len(scoped_ids),
        "updated_nodes": updated_nodes,
        "skipped_unchanged": skipped_unchanged,
    }


def relabel_vault_deterministic_with_status(
    db: Session,
    vault_id: UUID,
    status: str = "auto",
) -> dict:
    """
    Deterministically relabel eligible auto cluster nodes in a vault and set
    the provided label_status on updated nodes.
    """
    eligible_rows = (
        db.query(SemanticTreeNode.id, SemanticTreeNode.label_signature_hash)
        .filter(
            SemanticTreeNode.vault_id == vault_id,
            SemanticTreeNode.node_type == "cluster",
            SemanticTreeNode.locked == False,
            SemanticTreeNode.title_source == "auto",
        )
        .all()
    )
    if not eligible_rows:
        return {
            "vault_id": str(vault_id),
            "eligible_nodes": 0,
            "updated_nodes": 0,
            "skipped_unchanged": 0,
        }

    node_ids = [row[0] for row in eligible_rows]
    old_hash_by_node = {row[0]: row[1] for row in eligible_rows}

    # Subtree-aware signal extraction: use all descendant documents, not only direct attachments.
    node_to_docs = _resolve_subtree_docs_for_nodes(db, vault_id, node_ids)

    ctx = _build_vault_label_context(db, vault_id, node_to_docs)

    updated_nodes = 0
    skipped_unchanged = 0

    for node_id in node_ids:
        doc_ids = node_to_docs.get(node_id, [])
        signals = _build_node_signals(ctx, doc_ids)
        label, confidence = _select_deterministic_label(signals)
        signals["deterministic_label"] = label
        signals["deterministic_confidence"] = confidence

        signature_hash = _compute_signature_hash(node_id, signals, doc_ids)
        if old_hash_by_node.get(node_id) == signature_hash:
            skipped_unchanged += 1
            continue

        effective_status = status

        db.execute(
            text(
                f"""
                SELECT {SCHEMA}.update_node_label(
                  :vault_id, :node_id, :label,
                  CAST(:signals AS jsonb),
                  :signature_hash,
                  :status,
                  :title_source
                )
                """
            ),
            {
                "vault_id": str(vault_id),
                "node_id": str(node_id),
                "label": label,
                "signals": json.dumps(signals),
                "signature_hash": signature_hash,
                "status": effective_status,
                "title_source": None,
            },
        )
        updated_nodes += 1

    return {
        "vault_id": str(vault_id),
        "eligible_nodes": len(node_ids),
        "updated_nodes": updated_nodes,
        "skipped_unchanged": skipped_unchanged,
    }


def _compose_llm_refinement_prompt(signals: dict) -> str:
    tags = signals.get("tags", [])[:5]
    tfidf_terms = signals.get("tfidf_terms", [])[:8]
    summary_phrases = signals.get("summary_phrases", [])[:8]
    title_terms = signals.get("title_terms", [])[:6]
    doc_count = int(signals.get("doc_count", 0))
    deterministic_label = (signals.get("deterministic_label") or "").strip()

    def _fmt(entries: List[dict], key: str, score_key: Optional[str] = None) -> str:
        if not entries:
            return "- (none)"
        lines = []
        for e in entries:
            term = str(e.get(key) or "").strip()
            if not term:
                continue
            if score_key and e.get(score_key) is not None:
                lines.append(f"- {term} ({score_key}={e.get(score_key)})")
            else:
                lines.append(f"- {term}")
        return "\n".join(lines) if lines else "- (none)"

    return (
        "You are refining a cluster label.\n"
        "Use the provided signals only. Produce a concise label (max 6 words).\n"
        "Avoid generic labels like General, Topic, Cluster, Misc.\n\n"
        f"Document count: {doc_count}\n"
        f"Deterministic label: {deterministic_label or '(none)'}\n\n"
        "Top tags:\n"
        f"{_fmt(tags, 'tag', 'ratio')}\n\n"
        "Top TF-IDF terms:\n"
        f"{_fmt(tfidf_terms, 'phrase', 'score')}\n\n"
        "Top summary phrases:\n"
        f"{_fmt(summary_phrases, 'phrase', 'freq')}\n\n"
        "Top title terms:\n"
        f"{_fmt(title_terms, 'term', 'freq')}\n\n"
        "Respond with exactly one line:\n"
        "LABEL: <label>"
    )


def _generate_llm_label_from_signals(signals: dict) -> str:
    prompt = _compose_llm_refinement_prompt(signals)
    topic_provider = getattr(PREFERENCES.models, "topic_provider", "openai")
    if topic_provider == "ollama":
        topic_model = PREFERENCES.models.ollama.topic_model
        text_out = _ollama_chat(prompt, model=topic_model)
    else:
        text_out = _openai_chat(prompt)

    for line in text_out.splitlines():
        if line.strip().upper().startswith("LABEL:"):
            candidate = line.split(":", 1)[1].strip()
            if candidate:
                return " ".join(candidate.split()[:6])

    fallback = (text_out or "").strip().splitlines()
    if fallback:
        return " ".join(fallback[0].split()[:6])
    return (signals.get("deterministic_label") or _fallback_label(int(signals.get("doc_count", 0)))).strip()


def _is_generic_llm_label(label: str) -> bool:
    val = (label or "").strip().lower()
    if not val:
        return True
    return any(term in val for term in _GENERIC_LABEL_TERMS)


def _prefer_deterministic_over_generic(signals: dict, llm_label: str) -> bool:
    """
    Keep deterministic label when LLM output is generic and deterministic confidence is stronger.
    """
    if not _is_generic_llm_label(llm_label):
        return False
    det_label = (signals.get("deterministic_label") or "").strip()
    det_conf = float(signals.get("deterministic_confidence", 0.0) or 0.0)
    # Generic LLM outputs are treated as low confidence.
    generic_llm_conf = 0.20
    return bool(det_label) and det_conf > generic_llm_conf


def refine_pending_llm_labels(
    db: Session,
    vault_id: Optional[UUID] = None,
    limit: int = 25,
    llm_callable=None,
) -> dict:
    """
    Refine nodes currently queued for LLM processing (label_status='needs_llm').
    Uses stored label_signals only; does not recompute TF-IDF/signals.
    """
    q = db.query(
        SemanticTreeNode.id,
        SemanticTreeNode.vault_id,
        SemanticTreeNode.label_signals,
        SemanticTreeNode.label_signature_hash,
        SemanticTreeNode.title,
    ).filter(
        SemanticTreeNode.node_type == "cluster",
        SemanticTreeNode.locked == False,
        SemanticTreeNode.label_status == "needs_llm",
    )
    if vault_id is not None:
        q = q.filter(SemanticTreeNode.vault_id == vault_id)
    rows = q.order_by(SemanticTreeNode.updated_at.asc()).limit(max(1, int(limit))).all()

    if not rows:
        return {"processed": 0, "finalized": 0, "failed": 0}

    finalized = 0
    failed = 0
    for node_id, node_vault_id, label_signals, signature_hash, current_title in rows:
        signals = label_signals or {}
        try:
            if llm_callable is not None:
                llm_label = str(llm_callable(signals)).strip()
            else:
                llm_label = _generate_llm_label_from_signals(signals)
            if not llm_label:
                llm_label = (signals.get("deterministic_label") or current_title or "Topic").strip()
            if _prefer_deterministic_over_generic(signals, llm_label):
                llm_label = (signals.get("deterministic_label") or current_title or "Topic").strip()

            db.execute(
                text(
                    f"""
                    SELECT {SCHEMA}.update_node_label(
                      :vault_id, :node_id, :label,
                      CAST(:signals AS jsonb),
                      :signature_hash,
                      :status,
                      :title_source
                    )
                    """
                ),
                {
                    "vault_id": str(node_vault_id),
                    "node_id": str(node_id),
                    "label": llm_label,
                    "signals": json.dumps(signals),
                    "signature_hash": signature_hash,
                    "status": "final",
                    "title_source": "llm",
                },
            )
            finalized += 1
        except Exception:
            failed += 1

    return {
        "processed": len(rows),
        "finalized": finalized,
        "failed": failed,
    }

