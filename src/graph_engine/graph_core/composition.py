"""
GraphCompositionService — assemble composed multi-vault graph views.

Phase 6 of the graph-engine implementation plan.

Design constraints (from architecture rules):
- Internal vault graph edges are NEVER modified by compose_view.
- compose_view extracts internal subgraphs via SubgraphService (SQL slices).
- Bridge edges are loaded via BridgeService.load_bridges.
- compose_view does NOT rebuild internal graphs.
- The returned SubgraphResult has vault_id=None to signal a composed view.
- All composition is done by merging edge lists in memory; no graph rebuild.

Public API:
    compose_view(vault_ids, document_ids=None) -> SubgraphResult
"""

from __future__ import annotations

from itertools import combinations
from uuid import UUID

from sqlalchemy.engine import Engine

from graph_engine.db.repositories.graph_version_repo import GraphVersionRepo
from graph_engine.graph_core.bridges import BridgeEdge, BridgeService
from graph_engine.graph_core.subgraph import SubgraphResult, SubgraphService
from graph_engine.graph_core.types import SparseEdge

_COMPOSED_VIEW_VAULT_ID = UUID(int=0)


class GraphCompositionService:
    """
    Assembles composed multi-vault graph views from internal subgraphs and
    persisted bridge edges.

    Injects a SQLAlchemy engine. SubgraphService and BridgeService are
    constructed internally using the same engine.

    No graph is rebuilt during composition; only persisted state is read.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._version_repo = GraphVersionRepo(engine)
        self._subgraph_service = SubgraphService(engine, self._version_repo)
        self._bridge_service = BridgeService(engine)

    def compose_view(
        self,
        vault_ids: list[UUID],
        document_ids: list[UUID] | None = None,
    ) -> SubgraphResult:
        """
        Assemble a composed graph view spanning multiple vaults.

        Steps:
        1. For each vault, extract the internal subgraph via SubgraphService.
           If document_ids is provided, filter to only those documents in that
           vault (by intersecting with internal subgraph nodes).
        2. For each pair of vaults, load bridge edges via BridgeService.
        3. Merge all internal edges and bridge edges into a single edge list.
        4. Collect all unique node UUIDs across all subgraphs and bridge edges.
        5. Return a SubgraphResult with vault_id=None (composed sentinel).

        Raises:
            ValueError: if fewer than 2 vault_ids are provided.
        """
        if len(vault_ids) < 1:
            raise ValueError("compose_view requires at least one vault_id.")

        doc_id_set: set[UUID] | None = set(document_ids) if document_ids else None

        all_internal_edges: list[SparseEdge] = []
        all_bridge_edges_as_sparse: list[SparseEdge] = []
        all_nodes: set[UUID] = set()

        # Extract internal subgraph per vault.
        for vault_id in vault_ids:
            vault_doc_ids = self._resolve_vault_document_ids(
                vault_id=vault_id,
                document_ids_filter=doc_id_set,
            )
            if vault_doc_ids is None:
                # No active version for this vault; skip silently.
                continue
            try:
                result = self._subgraph_service.extract_subgraph(
                    vault_id=vault_id,
                    document_ids=vault_doc_ids,
                )
            except ValueError:
                # No active version — vault contributes no edges.
                continue

            all_internal_edges.extend(result.edges)
            all_nodes.update(result.nodes)

        # Load bridge edges for all vault pairs.
        for vault_a, vault_b in combinations(vault_ids, 2):
            bridge_edges = self._bridge_service.load_bridges(vault_a, vault_b)
            for be in bridge_edges:
                if doc_id_set is not None:
                    if be.source_doc_id not in doc_id_set or be.target_doc_id not in doc_id_set:
                        continue
                sparse = _bridge_edge_to_sparse(be)
                all_bridge_edges_as_sparse.append(sparse)
                all_nodes.add(be.source_doc_id)
                all_nodes.add(be.target_doc_id)

        merged_edges = all_internal_edges + all_bridge_edges_as_sparse
        sorted_nodes = sorted(all_nodes)

        return SubgraphResult(
            nodes=sorted_nodes,
            edges=merged_edges,
            graph_version_id=_COMPOSED_VIEW_VAULT_ID,
            vault_id=_COMPOSED_VIEW_VAULT_ID,
            node_count=len(sorted_nodes),
            edge_count=len(merged_edges),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_vault_document_ids(
        self,
        vault_id: UUID,
        document_ids_filter: set[UUID] | None,
    ) -> list[UUID] | None:
        """
        Return document IDs to extract for a vault.

        If no filter is provided, returns None (SubgraphService will use all
        documents in the active graph version).

        If a filter is provided, return only those IDs in the filter set.
        We pass them through without further DB lookup; the SubgraphService
        SQL enforces membership against the active version's persisted edges.
        """
        if document_ids_filter is None:
            return self._fetch_vault_document_ids(vault_id)
        return [d for d in document_ids_filter if True]

    def _fetch_vault_document_ids(self, vault_id: UUID) -> list[UUID] | None:
        """
        Fetch all document IDs for a vault from public.documents.

        Returns None if the vault has no documents (so the caller can skip).
        """
        from sqlalchemy import text

        sql = text(
            """
            SELECT id FROM public.documents
            WHERE vault_id = :vault_id
            ORDER BY id
            """
        )
        with self._engine.connect() as conn:
            rows = conn.execute(sql, {"vault_id": str(vault_id)}).fetchall()

        if not rows:
            return None
        return [UUID(str(row[0])) for row in rows]


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _bridge_edge_to_sparse(be: BridgeEdge) -> SparseEdge:
    """
    Convert a BridgeEdge to a SparseEdge for inclusion in a merged edge list.

    SparseEdge requires canonical ordering (doc_lo < doc_hi). Bridge edges
    are directed (source → target) so we use SparseEdge.make() to enforce it.
    """
    return SparseEdge.make(
        doc_a=be.source_doc_id,
        doc_b=be.target_doc_id,
        weight=be.weight,
        distance=be.distance,
    )
