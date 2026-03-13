"""
HierarchyBuilder — derived hierarchy / Z-linkage artifacts from graph slices.

Design constraints (from architecture rules):
- Hierarchies are derived artifacts only. They must never modify canonical
  graph edges or graph version state.
- Dense distance matrices are ephemeral; they are never persisted.
- The cache table (graph.graph_hierarchy_cache) stores only JSON payloads.
- A 1-node subgraph produces no linkage (cannot cluster a single point); the
  caller receives a safe degenerate result rather than an exception.

Public API:
    build_z_linkage(subgraph_result)            -> dict
    build_hierarchy_payload(subgraph_result)    -> dict
    cache_hierarchy(...)                        -> None
    load_cached_hierarchy(...)                  -> dict | None
    invalidate_hierarchy_cache(...)             -> int
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import numpy as np
from scipy.cluster.hierarchy import linkage, to_tree  # type: ignore[import]
from scipy.spatial.distance import squareform  # type: ignore[import]
from sqlalchemy import text
from sqlalchemy.engine import Engine

from graph_engine.graph_core.subgraph import SubgraphResult


# ---------------------------------------------------------------------------
# Z-linkage builder
# ---------------------------------------------------------------------------

def build_z_linkage(
    subgraph_result: SubgraphResult,
    method: str = "average",
) -> dict[str, Any]:
    """
    Compute a hierarchical clustering Z linkage array from a subgraph.

    Steps:
    1. Build an (n x n) symmetric distance matrix from edge weights.
       For pairs without an edge the default distance is 1.0 (maximum
       dissimilarity in a [0, 1] weight space).
    2. Convert the square matrix to a condensed distance vector.
    3. Call scipy.cluster.hierarchy.linkage with the requested method.
    4. Return a dict with:
         z_array  — linkage matrix as a list of lists (JSON-serialisable)
         labels   — ordered node UUID strings corresponding to matrix rows
         method   — the linkage method used

    For a 1-node subgraph, returns a degenerate result with an empty z_array.
    Raises ValueError if the subgraph has no nodes.
    """
    nodes = subgraph_result.nodes
    if not nodes:
        raise ValueError("Cannot build Z linkage from a subgraph with no nodes.")

    n = len(nodes)
    labels = [str(node) for node in nodes]

    if n == 1:
        return {"z_array": [], "labels": labels, "method": method}

    # Build index lookup for O(1) position resolution.
    index: dict[str, int] = {label: i for i, label in enumerate(labels)}

    # Initialise full-distance matrix (default distance = 1.0).
    dist_matrix = np.ones((n, n), dtype=np.float64)
    np.fill_diagonal(dist_matrix, 0.0)

    for edge in subgraph_result.edges:
        lo_str = str(edge.doc_lo)
        hi_str = str(edge.doc_hi)
        if lo_str not in index or hi_str not in index:
            # Edge refers to a node outside this subgraph — skip safely.
            continue
        i = index[lo_str]
        j = index[hi_str]
        # Weight is similarity; convert to distance = 1 - weight, clamped to [0, 1].
        distance = float(np.clip(1.0 - edge.weight, 0.0, 1.0))
        dist_matrix[i, j] = distance
        dist_matrix[j, i] = distance

    # Condense the square matrix to the upper-triangle vector expected by scipy.
    condensed = squareform(dist_matrix, checks=False)

    z_array_np = linkage(condensed, method=method)

    return {
        "z_array": z_array_np.tolist(),
        "labels": labels,
        "method": method,
    }


# ---------------------------------------------------------------------------
# Tree payload builder
# ---------------------------------------------------------------------------

def build_hierarchy_payload(
    subgraph_result: SubgraphResult,
    method: str = "average",
) -> dict[str, Any]:
    """
    Convert the Z linkage result into a nested JSON-serialisable tree.

    Tree structure:
        {
            "id": str,
            "children": [...],    # present on internal nodes only
            "document_id": str,   # present on leaf nodes only
            "node_count": int,
        }

    For a 1-node subgraph, returns a single leaf node (no clustering).
    Raises ValueError if the subgraph has no nodes.
    """
    nodes = subgraph_result.nodes
    if not nodes:
        raise ValueError("Cannot build hierarchy payload from a subgraph with no nodes.")

    n = len(nodes)

    if n == 1:
        doc_id = str(nodes[0])
        return {
            "id": doc_id,
            "document_id": doc_id,
            "node_count": 1,
        }

    z_result = build_z_linkage(subgraph_result, method=method)
    labels = z_result["labels"]
    z_array = np.array(z_result["z_array"], dtype=np.float64)

    root_node, _ = to_tree(z_array, rd=True)

    tree = _scipy_node_to_dict(root_node, labels)
    tree["node_count"] = n
    return tree


def _scipy_node_to_dict(
    node,  # scipy.cluster.hierarchy.ClusterNode — not typed to avoid import
    labels: list[str],
) -> dict[str, Any]:
    """
    Recursively convert a scipy ClusterNode into a JSON-serialisable dict.

    Leaf nodes contain `document_id`.
    Internal nodes contain `children`.
    """
    if node.is_leaf():
        doc_id = labels[node.id]
        return {
            "id": doc_id,
            "document_id": doc_id,
        }

    left = _scipy_node_to_dict(node.left, labels)
    right = _scipy_node_to_dict(node.right, labels)
    return {
        "id": f"internal_{node.id}",
        "children": [left, right],
    }


# ---------------------------------------------------------------------------
# Cache utilities
# ---------------------------------------------------------------------------

def cache_hierarchy(
    engine: Engine,
    graph_version_id: UUID,
    vault_id: UUID,
    cache_type: str,
    payload: dict[str, Any],
    document_ids: list[UUID] | None = None,
    source_scope: str = "vault",
    expires_at: datetime | None = None,
) -> None:
    """
    Persist a hierarchy payload to graph.graph_hierarchy_cache.

    payload must be JSON-serialisable. A TypeError is raised before the INSERT
    if serialisation fails.
    """
    # Validate serialisability before touching the database.
    json.dumps(payload)

    doc_ids_strs: list[str] | None = (
        [str(d) for d in document_ids] if document_ids is not None else None
    )
    expires_at_value = (
        expires_at.isoformat() if expires_at is not None else None
    )

    sql = text(
        """
        INSERT INTO graph.graph_hierarchy_cache (
            graph_version_id,
            vault_id,
            cache_type,
            source_scope,
            document_ids,
            payload,
            expires_at
        )
        VALUES (
            :graph_version_id,
            :vault_id,
            :cache_type,
            :source_scope,
            :document_ids,
            :payload::jsonb,
            :expires_at
        )
        """
    )

    with engine.connect() as conn:
        conn.execute(
            sql,
            {
                "graph_version_id": str(graph_version_id),
                "vault_id": str(vault_id),
                "cache_type": cache_type,
                "source_scope": source_scope,
                "document_ids": doc_ids_strs,
                "payload": json.dumps(payload),
                "expires_at": expires_at_value,
            },
        )
        conn.commit()


def load_cached_hierarchy(
    engine: Engine,
    graph_version_id: UUID,
    vault_id: UUID,
    cache_type: str,
) -> dict[str, Any] | None:
    """
    Return the cached hierarchy payload if a valid (non-expired) cache exists.

    Returns None if:
    - No matching cache row is found.
    - The row exists but expires_at is in the past.
    """
    sql = text(
        """
        SELECT payload, expires_at
        FROM graph.graph_hierarchy_cache
        WHERE graph_version_id = :graph_version_id
          AND vault_id         = :vault_id
          AND cache_type       = :cache_type
        ORDER BY created_at DESC
        LIMIT 1
        """
    )

    with engine.connect() as conn:
        row = conn.execute(
            sql,
            {
                "graph_version_id": str(graph_version_id),
                "vault_id": str(vault_id),
                "cache_type": cache_type,
            },
        ).fetchone()

    if row is None:
        return None

    payload_raw, expires_at = row[0], row[1]

    if expires_at is not None:
        # Normalise to UTC-aware for comparison.
        now_utc = datetime.now(tz=timezone.utc)
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if now_utc > expires_at:
            return None

    if isinstance(payload_raw, str):
        return json.loads(payload_raw)
    return dict(payload_raw)


def invalidate_hierarchy_cache(
    engine: Engine,
    graph_version_id: UUID,
    vault_id: UUID | None = None,
) -> int:
    """
    Delete hierarchy cache entries for a graph version.

    If vault_id is None, all cache rows for the version are deleted.
    Returns the count of deleted rows.
    """
    if vault_id is None:
        sql = text(
            """
            WITH deleted AS (
                DELETE FROM graph.graph_hierarchy_cache
                WHERE graph_version_id = :graph_version_id
                RETURNING id
            )
            SELECT count(*) FROM deleted
            """
        )
        params: dict[str, Any] = {"graph_version_id": str(graph_version_id)}
    else:
        sql = text(
            """
            WITH deleted AS (
                DELETE FROM graph.graph_hierarchy_cache
                WHERE graph_version_id = :graph_version_id
                  AND vault_id         = :vault_id
                RETURNING id
            )
            SELECT count(*) FROM deleted
            """
        )
        params = {
            "graph_version_id": str(graph_version_id),
            "vault_id": str(vault_id),
        }

    with engine.connect() as conn:
        row = conn.execute(sql, params).fetchone()
        conn.commit()

    return int(row[0]) if row else 0
