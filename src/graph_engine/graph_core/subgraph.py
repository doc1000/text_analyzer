"""
SubgraphService — SQL-based subgraph extraction from persisted vault graphs.

Design constraints (from architecture rules):
- Graph slicing is performed entirely in SQL against graph.vault_graph_edge.
- The full graph is never loaded into memory.
- GraphVersionRepo is used to resolve the active version when none is provided.
- Returned edges use SparseEdge from Phase 1 types.

Two extraction modes:
- extract_subgraph:     induced subgraph over a caller-supplied document set.
- extract_ego_subgraph: 1-hop neighborhood around a single center document.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine

from graph_engine.db.repositories.graph_version_repo import GraphVersionRepo
from graph_engine.graph_core.types import SparseEdge


@dataclass
class SubgraphResult:
    """
    Result of a subgraph extraction operation.

    nodes:            distinct document UUIDs present in the returned edges,
                      plus any isolated documents explicitly requested that
                      have no edges.
    edges:            sparse edges within the extracted subgraph.
    graph_version_id: version row used for the extraction.
    vault_id:         vault the subgraph was extracted from.
    node_count:       len(nodes) — derived field, kept explicit for callers.
    edge_count:       len(edges) — derived field, kept explicit for callers.
    """

    nodes: list[UUID]
    edges: list[SparseEdge]
    graph_version_id: UUID
    vault_id: UUID
    node_count: int
    edge_count: int


class SubgraphService:
    """
    SQL-based subgraph extraction service for a single vault's graph.

    Injects a SQLAlchemy engine and a GraphVersionRepo.
    No graph data is loaded beyond what is needed for the requested slice.
    """

    def __init__(self, engine: Engine, version_repo: GraphVersionRepo) -> None:
        self._engine = engine
        self._version_repo = version_repo

    # ------------------------------------------------------------------
    # extract_subgraph
    # ------------------------------------------------------------------

    def extract_subgraph(
        self,
        vault_id: UUID,
        document_ids: list[UUID],
        graph_version_id: UUID | None = None,
    ) -> SubgraphResult:
        """
        Extract the induced subgraph over the provided document set.

        Only edges where BOTH endpoints are inside document_ids are returned.
        Graph slicing is performed in SQL; no other edge data is fetched.

        If graph_version_id is None, the active version for vault_id is used.
        Raises ValueError if no active version exists and none was supplied.
        Raises KeyError if the supplied graph_version_id does not exist.
        """
        resolved_version_id = self._resolve_version(vault_id, graph_version_id)

        if not document_ids:
            return SubgraphResult(
                nodes=[],
                edges=[],
                graph_version_id=resolved_version_id,
                vault_id=vault_id,
                node_count=0,
                edge_count=0,
            )

        sql = text(
            """
            SELECT doc_lo, doc_hi, weight, distance, rank_lo, rank_hi
            FROM graph.vault_graph_edge
            WHERE graph_version_id = :version_id
              AND doc_lo = ANY(:doc_ids)
              AND doc_hi = ANY(:doc_ids)
            ORDER BY doc_lo, doc_hi
            """
        )

        doc_id_strs = [str(d) for d in document_ids]

        with self._engine.connect() as conn:
            rows = conn.execute(
                sql,
                {
                    "version_id": str(resolved_version_id),
                    "doc_ids": doc_id_strs,
                },
            ).fetchall()

        edges, connected_nodes = _rows_to_edges_and_nodes(rows)

        # Include all requested documents as nodes, even those with no edges.
        all_nodes = sorted(set(document_ids) | connected_nodes)

        return SubgraphResult(
            nodes=all_nodes,
            edges=edges,
            graph_version_id=resolved_version_id,
            vault_id=vault_id,
            node_count=len(all_nodes),
            edge_count=len(edges),
        )

    # ------------------------------------------------------------------
    # extract_ego_subgraph
    # ------------------------------------------------------------------

    def extract_ego_subgraph(
        self,
        vault_id: UUID,
        center_doc_id: UUID,
        depth: int = 1,
        graph_version_id: UUID | None = None,
    ) -> SubgraphResult:
        """
        Extract the ego subgraph around a center document.

        At depth=1:
          1. Find all edges incident to center_doc_id via SQL.
          2. Collect all neighbor IDs from those edges.
          3. Return the induced subgraph over {center} ∪ {neighbors}.

        Depth > 1 is implemented via iterative expansion (not recursive SQL).
        Each expansion step issues a new SQL query for the newly discovered frontier.

        If graph_version_id is None, the active version for vault_id is used.
        """
        if depth < 1:
            raise ValueError(f"depth must be >= 1, got {depth}")

        resolved_version_id = self._resolve_version(vault_id, graph_version_id)

        # BFS-style iterative expansion.
        visited: set[UUID] = {center_doc_id}
        frontier: set[UUID] = {center_doc_id}

        for _ in range(depth):
            if not frontier:
                break
            new_neighbors = self._fetch_neighbors(
                frontier=frontier,
                version_id=resolved_version_id,
            )
            newly_added = new_neighbors - visited
            visited |= newly_added
            frontier = newly_added

        # Return the induced subgraph over the full visited set.
        return self.extract_subgraph(
            vault_id=vault_id,
            document_ids=list(visited),
            graph_version_id=resolved_version_id,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_version(
        self,
        vault_id: UUID,
        graph_version_id: UUID | None,
    ) -> UUID:
        """
        Return graph_version_id if provided; otherwise look up the active
        version for vault_id via GraphVersionRepo.

        Raises ValueError if no active version exists.
        Raises KeyError if the supplied version_id does not match any version.
        """
        if graph_version_id is not None:
            self._assert_version_exists(graph_version_id)
            return graph_version_id

        active = self._version_repo.get_active_version(vault_id)
        if active is None:
            raise ValueError(
                f"No active graph version found for vault {vault_id}. "
                "Build the vault graph before extracting subgraphs."
            )
        return UUID(str(active["id"]))

    def _assert_version_exists(self, graph_version_id: UUID) -> None:
        """
        Raise KeyError if the given graph_version_id does not exist in
        graph.vault_graph_version.
        """
        sql = text(
            """
            SELECT 1
            FROM graph.vault_graph_version
            WHERE id = :version_id
            LIMIT 1
            """
        )
        with self._engine.connect() as conn:
            row = conn.execute(
                sql, {"version_id": str(graph_version_id)}
            ).fetchone()

        if row is None:
            raise KeyError(
                f"graph.vault_graph_version not found: {graph_version_id}"
            )

    def _fetch_neighbors(
        self,
        frontier: set[UUID],
        version_id: UUID,
    ) -> set[UUID]:
        """
        Return the set of all document IDs adjacent to any node in frontier.

        SQL: select edges where doc_lo OR doc_hi is in the frontier set,
        then collect all endpoints as neighbors.
        """
        sql = text(
            """
            SELECT doc_lo, doc_hi
            FROM graph.vault_graph_edge
            WHERE graph_version_id = :version_id
              AND (doc_lo = ANY(:frontier_ids) OR doc_hi = ANY(:frontier_ids))
            """
        )
        frontier_strs = [str(f) for f in frontier]

        with self._engine.connect() as conn:
            rows = conn.execute(
                sql,
                {
                    "version_id": str(version_id),
                    "frontier_ids": frontier_strs,
                },
            ).fetchall()

        neighbors: set[UUID] = set()
        for row in rows:
            neighbors.add(UUID(str(row[0])))
            neighbors.add(UUID(str(row[1])))
        return neighbors


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _rows_to_edges_and_nodes(
    rows: list,
) -> tuple[list[SparseEdge], set[UUID]]:
    """
    Convert raw DB rows into SparseEdge objects and collect the node set.

    Expected column order: doc_lo, doc_hi, weight, distance, rank_lo, rank_hi.
    doc_lo < doc_hi is guaranteed by the DB CHECK constraint.
    """
    edges: list[SparseEdge] = []
    node_set: set[UUID] = set()

    for row in rows:
        doc_lo = UUID(str(row[0]))
        doc_hi = UUID(str(row[1]))
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

    return edges, node_set
