"""
Tree management module - THE single entry point for tree-database interactions.

Handles persistent tree structure (tree_nodes, node_documents) for topic modelling.
Clustering logic (PCA/UMAP, hierarchical) stays in topics.py; this module only
persists and queries the tree.
"""

from datetime import datetime
from typing import Dict, List, Optional, Union
from uuid import UUID

import numpy as np
from sqlalchemy.orm import Session
from sqlalchemy import text

from .models import TreeNode, NodeDocument, Document, VaultMembership, REDUCED_EMBED_DIM


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
) -> None:
    """
    Attach a document to a tree node. Permission-safe (checks vault membership in SQL).
    """
    db.execute(
        text("SELECT attach_document_to_node(:node_id, :document_id, :user_id)"),
        {"node_id": str(node_id), "document_id": str(document_id), "user_id": str(user_id)},
    )
    db.commit()


def recompute_node_centroids(db: Session, vault_id: UUID) -> None:
    """
    Recompute centroid and size for all tree nodes in the vault.
    Uses AVG(d.reduced_embedding) from documents in node_documents.
    """
    db.execute(
        text("SELECT recompute_node_centroids(:vault_id)"),
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
    Find the nearest unlocked tree node by cosine distance to the given embedding.
    Returns node_id or None if no nodes exist or all are locked.
    Embedding must be length REDUCED_EMBED_DIM (use pad_to_reduced_dim if shorter).
    """
    vec = pad_to_reduced_dim(embedding)
    vec_str = "[" + ",".join(str(x) for x in vec) + "]"

    result = db.execute(
        text("""
            SELECT id FROM tree_nodes
            WHERE vault_id = :vault_id
            AND locked = FALSE
            AND centroid IS NOT NULL
            ORDER BY centroid <=> CAST(:embedding AS vector)
            LIMIT 1
        """),
        {"vault_id": str(vault_id), "embedding": vec_str},
    )
    row = result.fetchone()
    return row[0] if row else None  # id is already a UUID from PostgreSQL


def get_subtree(
    db: Session,
    root_node_id: UUID,
    user_id: Optional[UUID] = None,
) -> List[TreeNode]:
    """
    Get all nodes in the subtree rooted at root_node_id (including root).
    If user_id provided, verifies vault access before returning.
    """
    root = db.query(TreeNode).filter(TreeNode.id == root_node_id).first()
    if not root:
        return []

    if user_id and not _check_vault_access(db, root.vault_id, user_id):
        return []

    result = db.execute(
        text("""
            WITH RECURSIVE subtree AS (
                SELECT * FROM tree_nodes WHERE id = :root_id
                UNION ALL
                SELECT t.id, t.vault_id, t.parent_id, t.name, t.node_type,
                       t.distance, t.size, t.centroid, t.locked, t.created_at
                FROM tree_nodes t
                JOIN subtree s ON t.parent_id = s.id
            )
            SELECT id FROM subtree
        """),
        {"root_id": str(root_node_id)},
    )
    ids = [row[0] for row in result.fetchall()]  # id is already a UUID from PostgreSQL
    return db.query(TreeNode).filter(TreeNode.id.in_(ids)).all()


def get_tree_roots_for_vault(
    db: Session,
    vault_id: UUID,
    user_id: Optional[UUID] = None,
) -> List[TreeNode]:
    """
    Get all root nodes (parent_id IS NULL) for a vault.
    If user_id provided, verifies vault access before returning.
    """
    if user_id and not _check_vault_access(db, vault_id, user_id):
        return []

    return (
        db.query(TreeNode)
        .filter(TreeNode.vault_id == vault_id, TreeNode.parent_id.is_(None))
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
    Build TreeNode hierarchy and NodeDocument links from clustering output.

    Creates TreeNode for each (level, cluster_label). Level 2 = roots, level 1/0 = children.
    Attaches documents to level 0 (leaf) nodes only.

    Args:
        db: Database session
        vault_id: Vault scope
        user_id: User for permission checks when attaching
        labels_by_level: {level_idx: cluster_labels_array} from cluster_embeddings_multilevel
        structure: {level_idx: {cluster_label: [doc_ids]}} from cluster_embeddings_multilevel
        hierarchy: {level_idx: {fine_cluster_label: coarse_cluster_label}} from get_cluster_hierarchy
        doc_ids: Ordered document UUIDs (matches embedding matrix order)

    Returns:
        List of root node UUIDs (level 2)
    """
    levels = sorted(labels_by_level.keys())
    node_map: Dict[tuple, UUID] = {}  # (level, cluster_label) -> node_id

    # Create nodes level by level (coarsest first)
    for level in reversed(levels):  # level 2, 1, 0
        for cluster_label in np.unique(labels_by_level[level]):
            cluster_label = int(cluster_label)
            parent_id = None
            if level < max(levels):
                parent_cluster = hierarchy[level][cluster_label]
                parent_id = node_map.get((level + 1, parent_cluster))

            node = TreeNode(
                vault_id=vault_id,
                parent_id=parent_id,
                node_type="cluster",
                size=0,
            )
            db.add(node)
            db.flush()
            node_map[(level, cluster_label)] = node.id

    db.commit()

    # Attach documents to level 0 nodes
    level_0 = min(levels)
    for cluster_label, doc_id_list in structure[level_0].items():
        node_id = node_map[(level_0, cluster_label)]
        for doc_id in doc_id_list:
            attach_document_to_node(db, node_id, doc_id, user_id)

    root_level = max(levels)
    root_ids = [node_map[(root_level, int(lbl))] for lbl in np.unique(labels_by_level[root_level])]
    return root_ids


def get_tree_nodes_with_documents_in_range(
    db: Session,
    vault_id: UUID,
    since: datetime,
    user_id: Optional[UUID] = None,
) -> List[TreeNode]:
    """
    Return tree nodes that have at least one document with captured_at >= since.

    Args:
        db: Database session
        vault_id: Vault scope
        since: Minimum document captured_at
        user_id: If provided, verifies vault access

    Returns:
        Distinct TreeNode objects
    """
    if user_id and not _check_vault_access(db, vault_id, user_id):
        return []

    return (
        db.query(TreeNode)
        .join(NodeDocument, NodeDocument.node_id == TreeNode.id)
        .join(Document, Document.id == NodeDocument.document_id)
        .filter(TreeNode.vault_id == vault_id)
        .filter(Document.captured_at >= since)
        .distinct()
        .all()
    )
