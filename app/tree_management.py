"""
Tree management module - THE single entry point for tree-database interactions.

Handles persistent tree structure (tree_nodes, node_documents) for topic modelling.
Clustering logic (PCA/UMAP, hierarchical) stays in topics.py; this module only
persists and queries the tree.
"""

from typing import List, Optional, Union
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
            ORDER BY centroid <=> :embedding::vector
            LIMIT 1
        """),
        {"vault_id": str(vault_id), "embedding": vec_str},
    )
    row = result.fetchone()
    return UUID(row[0]) if row else None


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
    ids = [UUID(row[0]) for row in result.fetchall()]
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
