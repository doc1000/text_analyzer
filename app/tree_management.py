"""
Tree management module - THE single entry point for tree-database interactions.

Operates exclusively on semantic_tree_v2.tree_node and semantic_tree_v2.node_document.
Manual and cluster nodes coexist in the same tree. Clustering only modifies
node_type='cluster' AND locked=FALSE.
"""

from datetime import datetime
from typing import Dict, List, Optional, Union
from uuid import UUID

import numpy as np
from sqlalchemy.orm import Session
from sqlalchemy import text

from .models import SemanticTreeNode, SemanticTreeNodeDocument, Document, VaultMembership, REDUCED_EMBED_DIM

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


def assign_document_by_similarity(
    db: Session,
    vault_id: UUID,
    embedding: Union[List[float], np.ndarray],
    user_id: Optional[UUID] = None,
) -> Optional[UUID]:
    """
    Find the nearest unlocked cluster node by cosine distance to the given embedding.
    Returns node_id or None if no nodes exist or all are locked.
    """
    vec = pad_to_reduced_dim(embedding)
    vec_str = "[" + ",".join(str(x) for x in vec) + "]"

    result = db.execute(
        text(f"""
            SELECT id FROM {SCHEMA}.tree_node
            WHERE vault_id = :vault_id
            AND node_type = 'cluster'
            AND locked = FALSE
            AND centroid IS NOT NULL
            ORDER BY centroid <=> CAST(:embedding AS vector)
            LIMIT 1
        """),
        {"vault_id": str(vault_id), "embedding": vec_str},
    )
    row = result.fetchone()
    return row[0] if row else None


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
    # Delete existing cluster nodes (CASCADE removes node_document and tree_node_stats)
    db.execute(
        text(f"DELETE FROM {SCHEMA}.tree_node WHERE vault_id = :vault_id AND node_type = 'cluster' AND locked = FALSE"),
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
