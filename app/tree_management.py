"""
Tree management module - THE single entry point for tree-database interactions.

Operates exclusively on semantic_tree_v2.tree_node and semantic_tree_v2.node_document.
Manual and cluster nodes coexist in the same tree. Clustering only modifies
node_type='cluster' AND locked=FALSE.
"""

from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional, Set, Tuple, Union
from uuid import UUID

import numpy as np
from sqlalchemy.orm import Session
from sqlalchemy import text

from .models import (
    DocumentAnchor,
    SemanticTreeNode,
    SemanticTreeNodeDocument,
    Document,
    VaultMembership,
    REDUCED_EMBED_DIM,
)

SCHEMA = "semantic_tree_v2"


def pad_to_reduced_dim(
    vec: Union[List[float], np.ndarray],
    dim: int = REDUCED_EMBED_DIM,
) -> List[float]:
    """
    Zero-pad a reduced embedding to fixed dimension.
    Use when PCA/UMAP returns fewer than dim components.
    """
    if isinstance(vec, np.ndarray):
        vec = vec.flatten().tolist()
    if len(vec) >= dim:
        return vec[:dim]
    return list(vec) + [0.0] * (dim - len(vec))


def _check_vault_access(db: Session, vault_id: UUID, user_id: UUID, min_role: str = "viewer") -> bool:
    """Check if user has at least min_role access to vault."""
    role_order = ("viewer", "editor", "admin", "owner")
    try:
        min_idx = role_order.index(min_role)
        allowed = role_order[min_idx:]
    except ValueError:
        allowed = ("owner", "admin", "editor", "viewer")
    exists = (
        db.query(VaultMembership)
        .filter(
            VaultMembership.vault_id == vault_id,
            VaultMembership.user_id == user_id,
            VaultMembership.role.in_(allowed),
        )
        .first()
    )
    return exists is not None


def get_document_anchor(db: Session, document_id: UUID, vault_id: UUID) -> Optional[UUID]:
    """
    Return anchor_node_id if document has a hard anchor, else None.
    """
    row = (
        db.query(DocumentAnchor.anchor_node_id)
        .filter(
            DocumentAnchor.document_id == document_id,
            DocumentAnchor.vault_id == vault_id,
        )
        .first()
    )
    return row[0] if row else None


def set_document_anchor(
    db: Session,
    document_id: UUID,
    vault_id: UUID,
    anchor_node_id: UUID,
    user_id: UUID,
) -> None:
    """
    Set or replace hard anchor for document. Document must stay in anchor subtree.
    """
    existing = (
        db.query(DocumentAnchor)
        .filter(
            DocumentAnchor.document_id == document_id,
            DocumentAnchor.vault_id == vault_id,
        )
        .first()
    )
    if existing:
        existing.anchor_node_id = anchor_node_id
    else:
        db.add(
            DocumentAnchor(
                vault_id=vault_id,
                document_id=document_id,
                anchor_node_id=anchor_node_id,
                created_by=user_id,
                created_at=datetime.now(timezone.utc),
            )
        )
    db.commit()


def get_staging_node_id(db: Session, vault_id: UUID, user_id: UUID) -> UUID:
    """
    Ensure staging node exists for vault and return its id.
    """
    result = db.execute(
        text(f"SELECT {SCHEMA}.ensure_staging_node(:user_id, :vault_id)"),
        {"user_id": str(user_id), "vault_id": str(vault_id)},
    )
    node_id = result.scalar()
    db.commit()
    return node_id


def get_subtree_node_ids(db: Session, root_node_id: UUID) -> Set[UUID]:
    """
    Return all node ids in subtree rooted at root_node_id (including root).
    """
    result = db.execute(
        text(f"""
            WITH RECURSIVE subtree AS (
                SELECT id FROM {SCHEMA}.tree_node WHERE id = :root_id
                UNION ALL
                SELECT t.id FROM {SCHEMA}.tree_node t
                JOIN subtree s ON t.parent_id = s.id
            )
            SELECT id FROM subtree
        """),
        {"root_id": str(root_node_id)},
    )
    return {row[0] for row in result.fetchall()}


def attach_document_to_node(
    db: Session,
    node_id: UUID,
    document_id: UUID,
    user_id: UUID,
    vault_id: UUID,
) -> None:
    """
    Attach a document to a tree node. Uses semantic_tree_v2.attach_document (checks vault_user_role).
    """
    db.execute(
        text(f"SELECT {SCHEMA}.attach_document(:user_id, :vault_id, :node_id, :document_id)"),
        {
            "user_id": str(user_id),
            "vault_id": str(vault_id),
            "node_id": str(node_id),
            "document_id": str(document_id),
        },
    )
    db.commit()


def detach_document_from_vault(db: Session, document_id: UUID, vault_id: UUID) -> None:
    """
    Remove document from all nodes in the vault. Use before re-placing.
    """
    db.query(SemanticTreeNodeDocument).filter(
        SemanticTreeNodeDocument.document_id == document_id,
        SemanticTreeNodeDocument.vault_id == vault_id,
    ).delete(synchronize_session=False)
    db.commit()


def recompute_node_centroids(db: Session, vault_id: UUID) -> None:
    """
    Recompute centroid and size for cluster nodes in the vault.
    Only modifies node_type='cluster' AND locked=FALSE.
    """
    db.execute(
        text(f"SELECT {SCHEMA}.recompute_node_centroids(:vault_id)"),
        {"vault_id": str(vault_id)},
    )
    db.commit()


def relabel_vault(db: Session, vault_id: UUID) -> None:
    """
    Run tag-based relabeling on auto-generated nodes.
    Skips locked and title_source='pinned' nodes.
    """
    db.execute(
        text(f"SELECT {SCHEMA}.relabel_vault(:vault_id)"),
        {"vault_id": str(vault_id)},
    )
    db.commit()


def assign_document_by_similarity(
    db: Session,
    vault_id: UUID,
    embedding: Union[List[float], np.ndarray],
    user_id: Optional[UUID] = None,
    document_id: Optional[UUID] = None,
    min_similarity: float = 0.5,
) -> Optional[UUID]:
    """
    Find the nearest unlocked cluster node by cosine distance to the given embedding.
    If document_id is provided and has an anchor, restricts candidates to anchor subtree.
    If no node found or similarity < min_similarity:
      - When anchored: returns anchor_node_id (document must stay in subtree).
      - When not anchored: returns staging node id (user_id required).
    Returns node_id or None if no nodes exist, all locked, and no staging (user_id missing).
    """
    vec = pad_to_reduced_dim(embedding)
    vec_str = "[" + ",".join(str(x) for x in vec) + "]"

    anchor_node_id: Optional[UUID] = None
    candidate_ids: Optional[Set[UUID]] = None
    if document_id:
        anchor_node_id = get_document_anchor(db, document_id, vault_id)
        if anchor_node_id:
            candidate_ids = get_subtree_node_ids(db, anchor_node_id)

    params: Dict[str, object] = {
        "vault_id": str(vault_id),
        "embedding": vec_str,
    }

    candidate_clause = ""
    if candidate_ids is not None:
        candidate_clause = " AND id = ANY(CAST(:candidate_ids AS uuid[]))"
        params["candidate_ids"] = [str(x) for x in candidate_ids]

    result = db.execute(
        text(f"""
            SELECT id, (centroid <=> CAST(:embedding AS vector))::float AS dist
            FROM {SCHEMA}.tree_node
            WHERE vault_id = :vault_id
            AND node_type = 'cluster'
            AND locked = FALSE
            AND centroid IS NOT NULL
            AND (node_role IS NULL OR node_role = 'normal')
            {candidate_clause}
            ORDER BY centroid <=> CAST(:embedding AS vector)
            LIMIT 1
        """),
        params,
    )
    row = result.fetchone()

    if not row:
        if anchor_node_id:
            return anchor_node_id
        if user_id:
            return get_staging_node_id(db, vault_id, user_id)
        return None

    node_id, dist = row[0], float(row[1])
    similarity = 1.0 - dist
    if similarity < min_similarity:
        if anchor_node_id:
            return anchor_node_id
        if user_id:
            return get_staging_node_id(db, vault_id, user_id)
        return None
    return node_id


def place_document(
    db: Session,
    vault_id: UUID,
    document_id: UUID,
    embedding: Union[List[float], np.ndarray],
    user_id: UUID,
    min_similarity: float = 0.5,
) -> UUID:
    """
    Place a document: detach from existing nodes, find best node (or staging),
    attach, recompute centroids. Returns the node_id the document was attached to.
    Document must belong to vault_id (cross-vault placement rejected).
    """
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc or doc.vault_id != vault_id:
        raise ValueError("Document must belong to vault")
    detach_document_from_vault(db, document_id, vault_id)
    node_id = assign_document_by_similarity(
        db, vault_id, embedding, user_id,
        document_id=document_id,
        min_similarity=min_similarity,
    )
    if not node_id:
        raise ValueError("user_id required for placement when no cluster match")
    attach_document_to_node(db, node_id, document_id, user_id, vault_id)
    recompute_node_centroids(db, vault_id)
    return node_id


def get_subtree(
    db: Session,
    root_node_id: UUID,
    user_id: Optional[UUID] = None,
) -> List[SemanticTreeNode]:
    """
    Get all nodes in the subtree rooted at root_node_id (including root).
    """
    root = db.query(SemanticTreeNode).filter(SemanticTreeNode.id == root_node_id).first()
    if not root:
        return []

    if user_id and not _check_vault_access(db, root.vault_id, user_id):
        return []

    result = db.execute(
        text(f"""
            WITH RECURSIVE subtree AS (
                SELECT id FROM {SCHEMA}.tree_node WHERE id = :root_id
                UNION ALL
                SELECT t.id FROM {SCHEMA}.tree_node t
                JOIN subtree s ON t.parent_id = s.id
            )
            SELECT id FROM subtree
        """),
        {"root_id": str(root_node_id)},
    )
    ids = [row[0] for row in result.fetchall()]
    return db.query(SemanticTreeNode).filter(SemanticTreeNode.id.in_(ids)).all()


def get_tree_roots_for_vault(
    db: Session,
    vault_id: UUID,
    user_id: Optional[UUID] = None,
) -> List[SemanticTreeNode]:
    """
    Get all root nodes (parent_id IS NULL) for a vault.
    """
    if user_id and not _check_vault_access(db, vault_id, user_id):
        return []

    return (
        db.query(SemanticTreeNode)
        .filter(SemanticTreeNode.vault_id == vault_id, SemanticTreeNode.parent_id.is_(None))
        .all()
    )


def build_tree_from_clustering(
    db: Session,
    vault_id: UUID,
    user_id: UUID,
    labels_by_level: Dict[int, np.ndarray],
    structure: Dict[int, Dict[int, List]],
    hierarchy: Dict[int, Dict[int, int]],
    doc_ids: List[UUID],
) -> List[UUID]:
    """
    Build cluster node hierarchy from clustering output. Only modifies node_type='cluster' AND locked=FALSE.
    Deletes existing cluster nodes in vault, creates new ones, attaches documents to level 0 nodes.
    Manual and locked nodes are never touched.
    """
    # Delete existing cluster nodes except staging (CASCADE removes node_document and tree_node_stats)
    db.execute(
        text(f"""
            DELETE FROM {SCHEMA}.tree_node
            WHERE vault_id = :vault_id AND node_type = 'cluster' AND locked = FALSE
            AND (node_role IS NULL OR node_role != 'staging')
        """),
        {"vault_id": str(vault_id)},
    )
    db.commit()

    levels = sorted(labels_by_level.keys())
    node_map: Dict[tuple, UUID] = {}  # (level, cluster_label) -> node_id
    user_str = str(user_id)
    vault_str = str(vault_id)

    for level in reversed(levels):
        for cluster_label in np.unique(labels_by_level[level]):
            cluster_label = int(cluster_label)
            parent_id = None
            if level < max(levels):
                parent_cluster = hierarchy[level][cluster_label]
                parent_id = node_map.get((level + 1, parent_cluster))

            result = db.execute(
                text(f"SELECT {SCHEMA}.create_cluster_node(:user_id, :vault_id, :parent_id)"),
                {"user_id": user_str, "vault_id": vault_str, "parent_id": str(parent_id) if parent_id else None},
            )
            node_id = result.scalar()
            node_map[(level, cluster_label)] = node_id

    db.commit()

    # Attach documents to level 0 nodes
    level_0 = min(levels)
    for cluster_label, doc_id_list in structure[level_0].items():
        node_id = node_map[(level_0, cluster_label)]
        for doc_id in doc_id_list:
            attach_document_to_node(db, node_id, doc_id, user_id, vault_id)

    root_level = max(levels)
    root_ids = [node_map[(root_level, int(lbl))] for lbl in np.unique(labels_by_level[root_level])]
    return root_ids


def get_tree_nodes_with_documents_in_range(
    db: Session,
    vault_id: UUID,
    since: datetime,
    user_id: Optional[UUID] = None,
) -> List[SemanticTreeNode]:
    """
    Return tree nodes that have at least one document with captured_at >= since.
    """
    if user_id and not _check_vault_access(db, vault_id, user_id):
        return []

    return (
        db.query(SemanticTreeNode)
        .join(SemanticTreeNodeDocument, SemanticTreeNodeDocument.node_id == SemanticTreeNode.id)
        .join(Document, Document.id == SemanticTreeNodeDocument.document_id)
        .filter(SemanticTreeNode.vault_id == vault_id)
        .filter(SemanticTreeNodeDocument.vault_id == vault_id)
        .filter(Document.captured_at >= since)
        .distinct()
        .all()
    )


def _compute_overlap(old_ids: Set[UUID], new_ids: Set[UUID]) -> float:
    """Jaccard-like overlap: |intersection| / |union|."""
    if not old_ids and not new_ids:
        return 1.0
    inter = len(old_ids & new_ids)
    union = len(old_ids | new_ids)
    return inter / union if union else 0.0


def _get_scope_cluster_nodes(
    db: Session,
    vault_id: UUID,
    root_node_id: UUID,
    mode: Literal["subtree", "frontier", "staging"],
    user_id: UUID,
) -> Tuple[List[UUID], UUID]:
    """
    Resolve scope: cluster node ids in scope, and parent_id for new nodes.
    Returns (scope_node_ids, parent_id_for_new_nodes).
    """
    if mode == "staging":
        root_node_id = get_staging_node_id(db, vault_id, user_id)

    if mode == "frontier":
        result = db.execute(
            text(f"""
                SELECT id FROM {SCHEMA}.tree_node
                WHERE vault_id = :vault_id AND parent_id = :root_id
                AND node_type = 'cluster' AND locked = FALSE
            """),
            {"vault_id": str(vault_id), "root_id": str(root_node_id)},
        )
        scope_ids = [row[0] for row in result.fetchall()]
        return (scope_ids, root_node_id)

    # subtree or staging: full subtree of cluster nodes
    subtree_ids = get_subtree_node_ids(db, root_node_id)
    result = db.execute(
        text(f"""
            SELECT id FROM {SCHEMA}.tree_node
            WHERE id = ANY(CAST(:ids AS uuid[]))
            AND node_type = 'cluster' AND locked = FALSE
        """),
        {"ids": [str(x) for x in subtree_ids]},
    )
    scope_ids = [row[0] for row in result.fetchall()]
    return (scope_ids, root_node_id)


def recluster_scope(
    db: Session,
    vault_id: UUID,
    root_node_id: UUID,
    mode: Literal["subtree", "frontier", "staging"],
    user_id: UUID,
    overlap_threshold: float = 0.6,
    min_similarity: float = 0.5,
    relabel_after: bool = False,
) -> None:
    """
    Recluster documents in scope. Only operates on node_type='cluster' and locked=FALSE.
    Respects document_anchor constraints. Applies stability matching (reuse node id if overlap >= threshold).
    """
    scope_node_ids, parent_id = _get_scope_cluster_nodes(db, vault_id, root_node_id, mode, user_id)
    if not scope_node_ids:
        return

    # Collect documents from scope nodes
    rows = (
        db.query(SemanticTreeNodeDocument.document_id, SemanticTreeNodeDocument.node_id)
        .filter(
            SemanticTreeNodeDocument.vault_id == vault_id,
            SemanticTreeNodeDocument.node_id.in_(scope_node_ids),
        )
        .all()
    )
    doc_to_old_node: Dict[UUID, UUID] = {r.document_id: r.node_id for r in rows}
    doc_ids = list(doc_to_old_node.keys())
    if not doc_ids:
        return

    # Get reduced embeddings
    docs = db.query(Document).filter(Document.id.in_(doc_ids)).all()
    doc_embeddings = {}
    for d in docs:
        if d.reduced_embedding is not None:
            vec = d.reduced_embedding
            if hasattr(vec, "__iter__") and not isinstance(vec, (str, bytes)):
                doc_embeddings[d.id] = np.array(list(vec), dtype=np.float32)
            else:
                doc_embeddings[d.id] = np.array(vec, dtype=np.float32)

    valid_doc_ids = [did for did in doc_ids if did in doc_embeddings]
    if len(valid_doc_ids) < 2:
        return

    # Build anchor constraints: doc_id -> (anchor_node_id, subtree_ids)
    anchor_info: Dict[UUID, Tuple[UUID, Set[UUID]]] = {}
    for did in valid_doc_ids:
        anchor_id = get_document_anchor(db, did, vault_id)
        if anchor_id:
            anchor_info[did] = (anchor_id, get_subtree_node_ids(db, anchor_id))

    # Clustering
    from .topics import cluster_embeddings_multilevel, reduce_embeddings
    from .agglomerative import get_cluster_hierarchy

    X = np.stack([doc_embeddings[did] for did in valid_doc_ids], axis=0)
    if X.shape[1] > 30:
        X = reduce_embeddings(X)
    if X.shape[1] > 30:
        X = X[:, :30]
    X = np.asarray(X, dtype=np.float32)

    labels_by_level, structure, _ = cluster_embeddings_multilevel(X, doc_ids=valid_doc_ids)
    hierarchy = get_cluster_hierarchy(labels_by_level)
    level_0 = min(labels_by_level.keys())
    new_clusters: Dict[int, List[UUID]] = structure[level_0]

    # Old node -> doc ids
    old_node_docs: Dict[UUID, Set[UUID]] = {}
    for did, nid in doc_to_old_node.items():
        if did in valid_doc_ids:
            old_node_docs.setdefault(nid, set()).add(did)

    # Stability matching: for each new cluster, find best old node by overlap
    STABILITY_THRESHOLD = overlap_threshold
    used_old_ids: Set[UUID] = set()
    new_cluster_to_node: Dict[int, UUID] = {}
    user_str = str(user_id)
    vault_str = str(vault_id)

    for cluster_label, new_doc_ids in new_clusters.items():
        new_doc_set = set(new_doc_ids)
        best_old_id = None
        best_overlap = 0.0
        for old_id, old_docs in old_node_docs.items():
            if old_id in used_old_ids:
                continue
            overlap = _compute_overlap(old_docs, new_doc_set)
            if overlap >= STABILITY_THRESHOLD and overlap > best_overlap:
                best_overlap = overlap
                best_old_id = old_id

        if best_old_id is not None:
            used_old_ids.add(best_old_id)
            new_cluster_to_node[cluster_label] = best_old_id
        else:
            result = db.execute(
                text(f"SELECT {SCHEMA}.create_cluster_node(:user_id, :vault_id, :parent_id)"),
                {"user_id": user_str, "vault_id": vault_str, "parent_id": str(parent_id)},
            )
            new_cluster_to_node[cluster_label] = result.scalar()

    db.commit()

    # Move nodes that were reused to correct parent (if different)
    for old_id in used_old_ids:
        db.execute(
            text(f"""
                UPDATE {SCHEMA}.tree_node
                SET parent_id = :parent_id
                WHERE id = :node_id AND vault_id = :vault_id
            """),
            {"parent_id": str(parent_id), "node_id": str(old_id), "vault_id": vault_str},
        )
    db.commit()

    # Detach all docs from scope nodes, then attach to new clusters
    db.query(SemanticTreeNodeDocument).filter(
        SemanticTreeNodeDocument.vault_id == vault_id,
        SemanticTreeNodeDocument.node_id.in_(scope_node_ids),
    ).delete(synchronize_session=False)
    db.commit()

    # Attach docs to new nodes (respecting anchor constraints)
    staging_id = get_staging_node_id(db, vault_id, user_id)

    for cluster_label, new_doc_ids in new_clusters.items():
        node_id = new_cluster_to_node[cluster_label]
        for doc_id in new_doc_ids:
            if doc_id not in doc_embeddings:
                continue
            target_node = node_id
            if doc_id in anchor_info:
                anchor_node_id, subtree_ids = anchor_info[doc_id]
                if node_id not in subtree_ids:
                    target_node = anchor_node_id
            attach_document_to_node(db, target_node, doc_id, user_id, vault_id)

    # Delete empty old cluster nodes (never delete staging)
    for old_id in scope_node_ids:
        if old_id not in used_old_ids and old_id != staging_id:
            db.execute(
                text(f"""
                    DELETE FROM {SCHEMA}.tree_node
                    WHERE id = :node_id AND vault_id = :vault_id
                    AND node_type = 'cluster' AND locked = FALSE
                    AND (node_role IS NULL OR node_role != 'staging')
                    AND NOT EXISTS (
                        SELECT 1 FROM {SCHEMA}.node_document
                        WHERE node_id = :node_id AND vault_id = :vault_id
                    )
                """),
                {"node_id": str(old_id), "vault_id": vault_str},
            )
    db.commit()

    recompute_node_centroids(db, vault_id)
    if relabel_after:
        relabel_vault(db, vault_id)
