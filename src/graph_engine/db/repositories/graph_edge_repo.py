"""
GraphEdgeRepo — persistence for sparse graph edges.

Writes to and reads from graph.vault_graph_edge using SQLAlchemy Core.

Responsibilities:
- Bulk-insert SparseEdge rows for a graph version (save_graph).
- Reconstruct a SparseGraph from persisted rows (load_graph).
- Delete all edges for a graph version (delete_graph_edges).

Design constraints (from architecture rules):
- Only sparse edges are persisted; dense matrices are never written.
- Canonical edge ordering (doc_lo < doc_hi) is preserved in the database
  by the CHECK constraint and by this code re-using SparseEdge.make().
- Bulk inserts are used to keep write performance acceptable for large graphs.
- The engine is injected externally; this class manages no connections itself.
"""

from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine

from graph_engine.graph_core.types import SparseEdge, SparseGraph


class GraphEdgeRepo:
    """
    Repository for graph.vault_graph_edge rows.

    All methods accept an engine; connection lifecycle is handled internally
    (one connection per method call, auto-committed where needed).
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ------------------------------------------------------------------
    # save_graph
    # ------------------------------------------------------------------

    def save_graph(
        self,
        graph_version_id: UUID,
        vault_id: UUID,
        sparse_graph: SparseGraph,
    ) -> int:
        """
        Bulk-insert all edges from sparse_graph into graph.vault_graph_edge.

        Returns the number of edges inserted.

        Uses executemany for a single round-trip per batch.
        Canonical ordering is already enforced on each SparseEdge; the DB
        CHECK constraint (doc_lo < doc_hi) provides a secondary guard.
        """
        if not sparse_graph.edges:
            return 0

        sql = text(
            """
            INSERT INTO graph.vault_graph_edge (
                graph_version_id,
                vault_id,
                doc_lo,
                doc_hi,
                weight,
                distance,
                rank_lo,
                rank_hi,
                edge_type,
                source_type,
                contributions,
                is_locked
            ) VALUES (
                :graph_version_id,
                :vault_id,
                :doc_lo,
                :doc_hi,
                :weight,
                :distance,
                :rank_lo,
                :rank_hi,
                'internal',
                'knn',
                :contributions,
                false
            )
            """
        )

        rows = [
            {
                "graph_version_id": str(graph_version_id),
                "vault_id": str(vault_id),
                "doc_lo": str(edge.doc_lo),
                "doc_hi": str(edge.doc_hi),
                "weight": edge.weight,
                "distance": edge.distance,
                "rank_lo": edge.rank_lo,
                "rank_hi": edge.rank_hi,
                "contributions": json.dumps({}),
            }
            for edge in sparse_graph.edges
        ]

        with self._engine.connect() as conn:
            conn.execute(sql, rows)
            conn.commit()

        return len(rows)

    # ------------------------------------------------------------------
    # load_graph
    # ------------------------------------------------------------------

    def load_graph(self, graph_version_id: UUID) -> SparseGraph:
        """
        Load all edges for a graph version and reconstruct the SparseGraph.

        Node list is derived from the union of all edge endpoints.
        vault_id and config_id are read from graph.vault_graph_version.

        Raises KeyError if the graph_version_id does not exist.
        """
        version_sql = text(
            """
            SELECT vault_id, graph_config_id
            FROM graph.vault_graph_version
            WHERE id = :version_id
            """
        )
        edge_sql = text(
            """
            SELECT doc_lo, doc_hi, weight, distance, rank_lo, rank_hi
            FROM graph.vault_graph_edge
            WHERE graph_version_id = :version_id
            ORDER BY doc_lo, doc_hi
            """
        )

        with self._engine.connect() as conn:
            version_row = conn.execute(
                version_sql, {"version_id": str(graph_version_id)}
            ).mappings().fetchone()

            if version_row is None:
                raise KeyError(
                    f"graph.vault_graph_version not found: {graph_version_id}"
                )

            vault_id = UUID(str(version_row["vault_id"]))
            config_id = UUID(str(version_row["graph_config_id"]))

            edge_rows = conn.execute(
                edge_sql, {"version_id": str(graph_version_id)}
            ).fetchall()

        edges: list[SparseEdge] = []
        node_set: set[UUID] = set()

        for row in edge_rows:
            doc_lo = UUID(str(row[0]))
            doc_hi = UUID(str(row[1]))
            # doc_lo < doc_hi is guaranteed by the DB CHECK constraint;
            # use the direct constructor to avoid double validation overhead.
            edge = SparseEdge(
                doc_lo=doc_lo,
                doc_hi=doc_hi,
                weight=float(row[2]),
                distance=float(row[3]) if row[3] is not None else None,
                rank_lo=int(row[4]) if row[4] is not None else None,
                rank_hi=int(row[5]) if row[5] is not None else None,
            )
            edges.append(edge)
            node_set.add(doc_lo)
            node_set.add(doc_hi)

        return SparseGraph(
            nodes=sorted(node_set),
            edges=edges,
            vault_id=vault_id,
            config_id=config_id,
        )

    # ------------------------------------------------------------------
    # delete_graph_edges
    # ------------------------------------------------------------------

    def delete_graph_edges(self, graph_version_id: UUID) -> int:
        """
        Delete all edges for a graph version.

        Returns the number of rows deleted.
        """
        sql = text(
            """
            DELETE FROM graph.vault_graph_edge
            WHERE graph_version_id = :version_id
            """
        )
        with self._engine.connect() as conn:
            result = conn.execute(sql, {"version_id": str(graph_version_id)})
            conn.commit()
        return result.rowcount
