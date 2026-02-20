"""
semantic_tree_v2 repository - isolated vault-scoped semantic tree.

Fetches flat tree JSON from semantic_tree_v2.fetch_tree_flat and builds
in-memory tree structure. No endpoint integration.
"""

from typing import Any, Dict, List, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine import Connection as SaConnection


def build_tree(flat_nodes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Build hierarchical tree from flat node list.

    Args:
        flat_nodes: List of dicts with id, parent_id, title, summary, sort_key, doc_count, updated_at

    Returns:
        {"root_ids": [...], "by_id": {id: {**node, "children": [child_ids]}}}
    """
    by_id: Dict[Any, Dict[str, Any]] = {}
    children: Dict[Optional[str], List[Any]] = {}

    for n in flat_nodes:
        node_id = n["id"]
        by_id[node_id] = {**n, "children": []}
        parent_id = n.get("parent_id")
        children.setdefault(parent_id, []).append(node_id)

    for parent_id, child_ids in children.items():
        if parent_id is not None and parent_id in by_id:
            by_id[parent_id]["children"] = child_ids

    root_ids = children.get(None, [])
    return {"root_ids": root_ids, "by_id": by_id}


class SemanticTreeV2Repo:
    """Repository for semantic_tree_v2 schema. Uses SQL functions for permissions."""

    def __init__(self, dsn: str):
        """
        Args:
            dsn: Database connection string (e.g. postgresql+psycopg2://user:pass@host/db)
        """
        self.dsn = dsn
        self._engine: Optional[Engine] = None

    def _get_engine(self) -> Engine:
        if self._engine is None:
            self._engine = create_engine(self.dsn)
        return self._engine

    def fetch_tree(
        self,
        user_id: str,
        vault_id: str,
        conn: Optional[SaConnection] = None,
    ) -> Dict[str, Any]:
        """
        Fetch tree for vault and build in-memory structure.

        Args:
            user_id: User UUID (must have viewer+ role in vault_user_role)
            vault_id: Vault UUID
            conn: Optional existing connection (for transactional tests)

        Returns:
            {"root_ids": [...], "by_id": {...}}

        Raises:
            Exception: On insufficient_privilege from SQL
        """
        if conn is not None:
            result = conn.execute(
                text("SELECT semantic_tree_v2.fetch_tree_flat(:user_id, :vault_id)"),
                {"user_id": user_id, "vault_id": vault_id},
            )
            payload = result.scalar()
        else:
            engine = self._get_engine()
            with engine.connect() as c:
                result = c.execute(
                    text("SELECT semantic_tree_v2.fetch_tree_flat(:user_id, :vault_id)"),
                    {"user_id": user_id, "vault_id": vault_id},
                )
                payload = result.scalar()

        nodes = payload.get("nodes") or []
        return build_tree(nodes)
