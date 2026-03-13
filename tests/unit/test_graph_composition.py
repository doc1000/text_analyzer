"""
Unit tests for Phase 6 graph-engine components: bridges and composition.

Tests:
- Bridge edges are created correctly between two vaults.
- load_bridges returns edges for both vault directions (a→b and b→a).
- compose_view merges internal edges and bridge edges correctly.
- delete_bridges removes all bridge edges for the vault pair.
- Internal vault edges remain unchanged after bridge operations.
- Vault self-bridge constraint: BridgeEdge rejects source_vault_id == target_vault_id.

All tests use mocked SQLAlchemy engines — no database connection required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch
from uuid import UUID

import numpy as np
import pytest

from graph_engine.graph_core.bridges import BridgeEdge, BridgeService
from graph_engine.graph_core.composition import GraphCompositionService, _bridge_edge_to_sparse
from graph_engine.graph_core.subgraph import SubgraphResult, SubgraphService
from graph_engine.graph_core.types import SparseEdge
from graph_engine.db.models.graph_config import GraphConfigRow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _u(n: int) -> UUID:
    return UUID(int=n)


def _make_engine(side_effects: list) -> tuple[MagicMock, MagicMock]:
    """
    Build a mock engine whose conn.execute() returns the given side_effects
    in order.
    """
    engine = MagicMock()
    conn = MagicMock()
    engine.connect.return_value.__enter__ = MagicMock(return_value=conn)
    engine.connect.return_value.__exit__ = MagicMock(return_value=False)
    conn.execute.side_effect = side_effects
    return engine, conn


def _fetchone_result(value) -> MagicMock:
    result = MagicMock()
    result.fetchone.return_value = value
    return result


def _fetchall_result(rows: list) -> MagicMock:
    result = MagicMock()
    result.fetchall.return_value = rows
    return result


def _make_config(bridge_knn_k: int = 2) -> GraphConfigRow:
    return GraphConfigRow(
        id=_u(999),
        name="test-config",
        embedding_source="all_minilm_v1_384",
        knn_k=3,
        candidate_k=5,
        pruning_mode="none",
        feature_weights={},
        is_active=True,
        bridge_knn_k=bridge_knn_k,
    )


def _bridge_edge_row(
    src_vault: UUID,
    tgt_vault: UUID,
    src_doc: UUID,
    tgt_doc: UUID,
    weight: float = 0.8,
) -> tuple:
    """Produce a raw DB row tuple as returned by fetchall() for bridge_graph_edge."""
    return (
        str(src_vault),
        str(tgt_vault),
        str(src_doc),
        str(tgt_doc),
        weight,
        1.0 - weight,
        None,
        None,
        None,
    )


def _sparse_edge_row(lo: UUID, hi: UUID, weight: float = 0.75) -> tuple:
    return (str(lo), str(hi), weight, None, None, None)


# ---------------------------------------------------------------------------
# TestBridgeEdgeDataclass
# ---------------------------------------------------------------------------

class TestBridgeEdgeDataclass:

    def test_self_vault_raises_value_error(self) -> None:
        """
        BridgeEdge must reject source_vault_id == target_vault_id.
        This mirrors the database CHECK constraint.
        """
        vault = _u(1)
        with pytest.raises(ValueError, match="self-vault not allowed"):
            BridgeEdge(
                source_vault_id=vault,
                target_vault_id=vault,
                source_doc_id=_u(10),
                target_doc_id=_u(20),
                weight=0.5,
            )

    def test_distinct_vaults_accepted(self) -> None:
        edge = BridgeEdge(
            source_vault_id=_u(1),
            target_vault_id=_u(2),
            source_doc_id=_u(10),
            target_doc_id=_u(20),
            weight=0.7,
            distance=0.3,
        )
        assert edge.source_vault_id == _u(1)
        assert edge.target_vault_id == _u(2)
        assert edge.weight == 0.7

    def test_string_uuids_coerced(self) -> None:
        """BridgeEdge should accept string UUIDs and coerce to UUID objects."""
        vault_a = _u(1)
        vault_b = _u(2)
        edge = BridgeEdge(
            source_vault_id=str(vault_a),
            target_vault_id=str(vault_b),
            source_doc_id=str(_u(10)),
            target_doc_id=str(_u(20)),
            weight=0.5,
        )
        assert isinstance(edge.source_vault_id, UUID)
        assert isinstance(edge.target_vault_id, UUID)


# ---------------------------------------------------------------------------
# TestBridgeServiceBuildBridges
# ---------------------------------------------------------------------------

class TestBridgeServiceBuildBridges:

    def _make_build_engine(
        self,
        config_row,
        source_emb_rows: list[tuple],
        target_emb_rows: list[tuple],
    ) -> tuple[MagicMock, MagicMock]:
        """
        Build a mock engine for build_bridges.

        Call order:
          1. get_graph_config   → config row (mappings().fetchone())
          2. get_active_version for source vault (GraphVersionRepo)
          3. get_active_version for target vault (GraphVersionRepo)
          4. _fetch_vault_embeddings source → fetchall
          5. _fetch_vault_embeddings target → fetchall
          6. _persist_bridge_edges → conn.execute (INSERT), conn.commit
        """
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__ = MagicMock(return_value=conn)
        engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        config_mappings = MagicMock()
        config_mappings.fetchone.return_value = config_row
        config_result = MagicMock()
        config_result.mappings.return_value = config_mappings

        source_emb_result = _fetchall_result(source_emb_rows)
        target_emb_result = _fetchall_result(target_emb_rows)
        insert_result = MagicMock()

        conn.execute.side_effect = [
            config_result,
            source_emb_result,
            target_emb_result,
            insert_result,
        ]
        return engine, conn

    def test_build_bridges_returns_correct_count(self) -> None:
        """
        build_bridges with 2 source docs and bridge_knn_k=1 should create
        2 bridge edges (one per source doc).
        """
        vault_a, vault_b = _u(1), _u(2)
        doc_s1, doc_s2 = _u(10), _u(11)
        doc_t1, doc_t2 = _u(20), _u(21)
        config_id = _u(999)

        # Simple orthogonal embeddings (4-dim, already L2-normalised).
        emb_s1 = [1.0, 0.0, 0.0, 0.0]
        emb_s2 = [0.0, 1.0, 0.0, 0.0]
        emb_t1 = [0.0, 0.0, 1.0, 0.0]
        emb_t2 = [0.0, 0.0, 0.0, 1.0]

        config = _make_config(bridge_knn_k=1)

        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__ = MagicMock(return_value=conn)
        engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        # Build mappings mock for graph_config SELECT
        config_mappings = MagicMock()
        config_mappings.fetchone.return_value = _mock_config_row(config)
        config_result = MagicMock()
        config_result.mappings.return_value = config_mappings

        source_emb_result = _fetchall_result([
            (str(doc_s1), emb_s1),
            (str(doc_s2), emb_s2),
        ])
        target_emb_result = _fetchall_result([
            (str(doc_t1), emb_t1),
            (str(doc_t2), emb_t2),
        ])
        insert_result = MagicMock()

        conn.execute.side_effect = [
            config_result,
            source_emb_result,
            target_emb_result,
            insert_result,
        ]

        # Mock version repo to return None (no active versions needed for edge refs)
        with patch(
            "graph_engine.graph_core.bridges.GraphVersionRepo"
        ) as MockVersionRepo:
            version_repo_instance = MagicMock()
            version_repo_instance.get_active_version.return_value = None
            MockVersionRepo.return_value = version_repo_instance

            service = BridgeService(engine)
            count = service.build_bridges(
                source_vault_id=vault_a,
                target_vault_id=vault_b,
                config_id=config_id,
            )

        assert count == 2

    def test_build_bridges_raises_if_source_has_no_embeddings(self) -> None:
        config = _make_config()
        config_id = _u(999)

        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__ = MagicMock(return_value=conn)
        engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        config_mappings = MagicMock()
        config_mappings.fetchone.return_value = _mock_config_row(config)
        config_result = MagicMock()
        config_result.mappings.return_value = config_mappings

        # Source vault has no embeddings
        source_emb_result = _fetchall_result([])

        conn.execute.side_effect = [config_result, source_emb_result]

        with patch("graph_engine.graph_core.bridges.GraphVersionRepo") as MockVR:
            MockVR.return_value.get_active_version.return_value = None
            service = BridgeService(engine)
            with pytest.raises(ValueError, match="No embeddings found for source vault"):
                service.build_bridges(
                    source_vault_id=_u(1),
                    target_vault_id=_u(2),
                    config_id=config_id,
                )

    def test_build_bridges_raises_if_target_has_no_embeddings(self) -> None:
        config = _make_config()
        config_id = _u(999)

        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__ = MagicMock(return_value=conn)
        engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        config_mappings = MagicMock()
        config_mappings.fetchone.return_value = _mock_config_row(config)
        config_result = MagicMock()
        config_result.mappings.return_value = config_mappings

        source_emb_result = _fetchall_result([
            (str(_u(10)), [1.0, 0.0]),
        ])
        target_emb_result = _fetchall_result([])

        conn.execute.side_effect = [config_result, source_emb_result, target_emb_result]

        with patch("graph_engine.graph_core.bridges.GraphVersionRepo") as MockVR:
            MockVR.return_value.get_active_version.return_value = None
            service = BridgeService(engine)
            with pytest.raises(ValueError, match="No embeddings found for target vault"):
                service.build_bridges(
                    source_vault_id=_u(1),
                    target_vault_id=_u(2),
                    config_id=config_id,
                )


# ---------------------------------------------------------------------------
# TestBridgeServiceLoadBridges
# ---------------------------------------------------------------------------

class TestBridgeServiceLoadBridges:

    def test_load_bridges_returns_edges_for_a_to_b(self) -> None:
        """
        load_bridges must return all edges stored with (source=a, target=b).
        """
        vault_a, vault_b = _u(1), _u(2)
        doc_s, doc_t = _u(10), _u(20)

        engine, conn = _make_engine([
            _fetchall_result([
                _bridge_edge_row(vault_a, vault_b, doc_s, doc_t, 0.9),
            ]),
        ])

        with patch("graph_engine.graph_core.bridges.GraphVersionRepo"):
            service = BridgeService(engine)
            edges = service.load_bridges(vault_a, vault_b)

        assert len(edges) == 1
        assert edges[0].source_vault_id == vault_a
        assert edges[0].target_vault_id == vault_b
        assert edges[0].weight == pytest.approx(0.9)

    def test_load_bridges_returns_edges_regardless_of_direction(self) -> None:
        """
        load_bridges(a, b) must return edges stored as (source=b, target=a)
        as well as (source=a, target=b).
        """
        vault_a, vault_b = _u(1), _u(2)
        doc_s1, doc_t1 = _u(10), _u(20)
        doc_s2, doc_t2 = _u(21), _u(11)

        engine, conn = _make_engine([
            _fetchall_result([
                _bridge_edge_row(vault_a, vault_b, doc_s1, doc_t1, 0.85),
                _bridge_edge_row(vault_b, vault_a, doc_s2, doc_t2, 0.70),
            ]),
        ])

        with patch("graph_engine.graph_core.bridges.GraphVersionRepo"):
            service = BridgeService(engine)
            edges = service.load_bridges(vault_a, vault_b)

        assert len(edges) == 2
        vault_pairs = {(e.source_vault_id, e.target_vault_id) for e in edges}
        assert (vault_a, vault_b) in vault_pairs
        assert (vault_b, vault_a) in vault_pairs

    def test_load_bridges_returns_empty_when_none_exist(self) -> None:
        vault_a, vault_b = _u(1), _u(2)

        engine, conn = _make_engine([_fetchall_result([])])

        with patch("graph_engine.graph_core.bridges.GraphVersionRepo"):
            service = BridgeService(engine)
            edges = service.load_bridges(vault_a, vault_b)

        assert edges == []

    def test_load_bridges_returns_bridge_edge_instances(self) -> None:
        vault_a, vault_b = _u(1), _u(2)

        engine, conn = _make_engine([
            _fetchall_result([
                _bridge_edge_row(vault_a, vault_b, _u(10), _u(20), 0.6),
            ]),
        ])

        with patch("graph_engine.graph_core.bridges.GraphVersionRepo"):
            service = BridgeService(engine)
            edges = service.load_bridges(vault_a, vault_b)

        assert all(isinstance(e, BridgeEdge) for e in edges)


# ---------------------------------------------------------------------------
# TestBridgeServiceDeleteBridges
# ---------------------------------------------------------------------------

class TestBridgeServiceDeleteBridges:

    def test_delete_bridges_returns_deleted_count(self) -> None:
        """
        delete_bridges must return the number of deleted rows.
        """
        vault_a, vault_b = _u(1), _u(2)

        engine, conn = _make_engine([
            _fetchone_result((3,)),  # DELETE CTE returns count = 3
        ])

        with patch("graph_engine.graph_core.bridges.GraphVersionRepo"):
            service = BridgeService(engine)
            count = service.delete_bridges(vault_a, vault_b)

        assert count == 3

    def test_delete_bridges_returns_zero_when_none_exist(self) -> None:
        vault_a, vault_b = _u(1), _u(2)

        engine, conn = _make_engine([
            _fetchone_result((0,)),
        ])

        with patch("graph_engine.graph_core.bridges.GraphVersionRepo"):
            service = BridgeService(engine)
            count = service.delete_bridges(vault_a, vault_b)

        assert count == 0

    def test_delete_bridges_does_not_touch_internal_edges(self) -> None:
        """
        delete_bridges must not issue any DELETE against graph.vault_graph_edge.
        The only table touched must be graph.bridge_graph_edge.
        """
        vault_a, vault_b = _u(1), _u(2)

        engine, conn = _make_engine([_fetchone_result((1,))])

        with patch("graph_engine.graph_core.bridges.GraphVersionRepo"):
            service = BridgeService(engine)
            service.delete_bridges(vault_a, vault_b)

        # Inspect the SQL that was executed.
        executed_sql: str = conn.execute.call_args[0][0].text
        assert "vault_graph_edge" not in executed_sql
        assert "bridge_graph_edge" in executed_sql


# ---------------------------------------------------------------------------
# TestSelfBridgeConstraint
# ---------------------------------------------------------------------------

class TestSelfBridgeConstraint:

    def test_bridge_edge_rejects_same_vault(self) -> None:
        """
        Attempting to create a BridgeEdge with the same vault for source and
        target must raise ValueError. This mirrors CHECK (source_vault_id <> target_vault_id).
        """
        same_vault = _u(42)
        with pytest.raises(ValueError, match="self-vault not allowed"):
            BridgeEdge(
                source_vault_id=same_vault,
                target_vault_id=same_vault,
                source_doc_id=_u(1),
                target_doc_id=_u(2),
                weight=0.5,
            )


# ---------------------------------------------------------------------------
# TestBridgeEdgeToSparse
# ---------------------------------------------------------------------------

class TestBridgeEdgeToSparse:

    def test_converts_to_sparse_edge_with_canonical_ordering(self) -> None:
        """
        _bridge_edge_to_sparse must produce a SparseEdge with doc_lo < doc_hi,
        regardless of the direction of the BridgeEdge.
        """
        # source_doc > target_doc in UUID ordering.
        src_doc = _u(99)
        tgt_doc = _u(10)
        be = BridgeEdge(
            source_vault_id=_u(1),
            target_vault_id=_u(2),
            source_doc_id=src_doc,
            target_doc_id=tgt_doc,
            weight=0.8,
            distance=0.2,
        )
        sparse = _bridge_edge_to_sparse(be)
        assert isinstance(sparse, SparseEdge)
        assert sparse.doc_lo < sparse.doc_hi
        assert sparse.weight == pytest.approx(0.8)

    def test_weight_and_distance_preserved(self) -> None:
        be = BridgeEdge(
            source_vault_id=_u(1),
            target_vault_id=_u(2),
            source_doc_id=_u(5),
            target_doc_id=_u(50),
            weight=0.65,
            distance=0.35,
        )
        sparse = _bridge_edge_to_sparse(be)
        assert sparse.weight == pytest.approx(0.65)
        assert sparse.distance == pytest.approx(0.35)


# ---------------------------------------------------------------------------
# TestGraphCompositionService
# ---------------------------------------------------------------------------

class TestGraphCompositionService:

    def _make_subgraph_result(
        self,
        vault_id: UUID,
        nodes: list[UUID],
        edges: list[SparseEdge],
    ) -> SubgraphResult:
        return SubgraphResult(
            nodes=nodes,
            edges=edges,
            graph_version_id=_u(1000),
            vault_id=vault_id,
            node_count=len(nodes),
            edge_count=len(edges),
        )

    def test_compose_view_merges_internal_and_bridge_edges(self) -> None:
        """
        compose_view must merge internal subgraph edges and bridge edges into
        a single SubgraphResult with combined edge count.
        """
        vault_a, vault_b = _u(1), _u(2)
        doc_a1, doc_a2 = _u(10), _u(11)
        doc_b1, doc_b2 = _u(20), _u(21)

        internal_edge_a = SparseEdge.make(doc_a1, doc_a2, 0.9)
        internal_edge_b = SparseEdge.make(doc_b1, doc_b2, 0.85)
        bridge_edge = BridgeEdge(
            source_vault_id=vault_a,
            target_vault_id=vault_b,
            source_doc_id=doc_a1,
            target_doc_id=doc_b1,
            weight=0.7,
        )

        subgraph_a = self._make_subgraph_result(
            vault_a, [doc_a1, doc_a2], [internal_edge_a]
        )
        subgraph_b = self._make_subgraph_result(
            vault_b, [doc_b1, doc_b2], [internal_edge_b]
        )

        engine = MagicMock()

        with (
            patch(
                "graph_engine.graph_core.composition.SubgraphService"
            ) as MockSubgraph,
            patch(
                "graph_engine.graph_core.composition.BridgeService"
            ) as MockBridge,
            patch(
                "graph_engine.graph_core.composition.GraphVersionRepo"
            ),
        ):
            subgraph_service = MagicMock()
            subgraph_service.extract_subgraph.side_effect = [subgraph_a, subgraph_b]
            MockSubgraph.return_value = subgraph_service

            bridge_service = MagicMock()
            bridge_service.load_bridges.return_value = [bridge_edge]
            MockBridge.return_value = bridge_service

            # Mock _fetch_vault_document_ids
            with patch.object(
                GraphCompositionService,
                "_fetch_vault_document_ids",
                side_effect=[[doc_a1, doc_a2], [doc_b1, doc_b2]],
            ):
                service = GraphCompositionService(engine)
                result = service.compose_view(vault_ids=[vault_a, vault_b])

        # 2 internal + 1 bridge = 3 total edges.
        assert result.edge_count == 3
        assert result.node_count == 4
        assert set(result.nodes) == {doc_a1, doc_a2, doc_b1, doc_b2}

    def test_compose_view_vault_id_is_none_sentinel(self) -> None:
        """
        compose_view must return a SubgraphResult with vault_id set to the
        composed sentinel (UUID(int=0)), not any real vault ID.
        """
        vault_a = _u(1)
        doc_a = _u(10)

        subgraph_a = self._make_subgraph_result(vault_a, [doc_a], [])

        engine = MagicMock()

        with (
            patch("graph_engine.graph_core.composition.SubgraphService") as MockSub,
            patch("graph_engine.graph_core.composition.BridgeService") as MockBridge,
            patch("graph_engine.graph_core.composition.GraphVersionRepo"),
        ):
            MockSub.return_value.extract_subgraph.return_value = subgraph_a
            MockBridge.return_value.load_bridges.return_value = []

            with patch.object(
                GraphCompositionService,
                "_fetch_vault_document_ids",
                return_value=[doc_a],
            ):
                service = GraphCompositionService(engine)
                result = service.compose_view(vault_ids=[vault_a])

        assert result.vault_id == UUID(int=0)

    def test_compose_view_does_not_rebuild_internal_graphs(self) -> None:
        """
        compose_view must not call build_vault_graph or any method that
        reconstructs graph state. It must only call extract_subgraph and
        load_bridges.
        """
        vault_a, vault_b = _u(1), _u(2)

        engine = MagicMock()

        with (
            patch("graph_engine.graph_core.composition.SubgraphService") as MockSub,
            patch("graph_engine.graph_core.composition.BridgeService") as MockBridge,
            patch("graph_engine.graph_core.composition.GraphVersionRepo"),
        ):
            subgraph_a = self._make_subgraph_result(vault_a, [_u(10)], [])
            subgraph_b = self._make_subgraph_result(vault_b, [_u(20)], [])

            MockSub.return_value.extract_subgraph.side_effect = [subgraph_a, subgraph_b]
            MockBridge.return_value.load_bridges.return_value = []

            with patch.object(
                GraphCompositionService,
                "_fetch_vault_document_ids",
                side_effect=[[_u(10)], [_u(20)]],
            ):
                service = GraphCompositionService(engine)
                service.compose_view(vault_ids=[vault_a, vault_b])

            # build_vault_graph must never be called.
            assert not hasattr(MockSub.return_value, "build_vault_graph") or \
                not MockSub.return_value.build_vault_graph.called

    def test_compose_view_with_document_id_filter(self) -> None:
        """
        When document_ids is supplied, bridge edges where neither endpoint is
        in the filter set must be excluded.
        """
        vault_a, vault_b = _u(1), _u(2)
        doc_a = _u(10)
        doc_b_in = _u(20)
        doc_b_out = _u(21)

        subgraph_a = self._make_subgraph_result(vault_a, [doc_a], [])
        subgraph_b = self._make_subgraph_result(vault_b, [doc_b_in], [])

        bridge_included = BridgeEdge(
            source_vault_id=vault_a,
            target_vault_id=vault_b,
            source_doc_id=doc_a,
            target_doc_id=doc_b_in,
            weight=0.8,
        )
        bridge_excluded = BridgeEdge(
            source_vault_id=vault_a,
            target_vault_id=vault_b,
            source_doc_id=doc_a,
            target_doc_id=doc_b_out,
            weight=0.6,
        )

        engine = MagicMock()

        with (
            patch("graph_engine.graph_core.composition.SubgraphService") as MockSub,
            patch("graph_engine.graph_core.composition.BridgeService") as MockBridge,
            patch("graph_engine.graph_core.composition.GraphVersionRepo"),
        ):
            MockSub.return_value.extract_subgraph.side_effect = [subgraph_a, subgraph_b]
            MockBridge.return_value.load_bridges.return_value = [
                bridge_included,
                bridge_excluded,
            ]

            service = GraphCompositionService(engine)
            result = service.compose_view(
                vault_ids=[vault_a, vault_b],
                document_ids=[doc_a, doc_b_in],
            )

        # Only the bridge to doc_b_in should be included.
        bridge_pairs = {
            (e.doc_lo, e.doc_hi)
            for e in result.edges
        }
        assert (min(doc_a, doc_b_in), max(doc_a, doc_b_in)) in bridge_pairs
        assert (min(doc_a, doc_b_out), max(doc_a, doc_b_out)) not in bridge_pairs

    def test_internal_vault_edges_unchanged_after_compose(self) -> None:
        """
        Subgraph edges returned by extract_subgraph must appear unchanged in
        the compose_view result (compose_view must never mutate them).
        """
        vault_a = _u(1)
        doc_lo, doc_hi = _u(5), _u(6)
        internal_edge = SparseEdge.make(doc_lo, doc_hi, 0.95)

        subgraph_a = self._make_subgraph_result(
            vault_a, [doc_lo, doc_hi], [internal_edge]
        )

        engine = MagicMock()

        with (
            patch("graph_engine.graph_core.composition.SubgraphService") as MockSub,
            patch("graph_engine.graph_core.composition.BridgeService") as MockBridge,
            patch("graph_engine.graph_core.composition.GraphVersionRepo"),
        ):
            MockSub.return_value.extract_subgraph.return_value = subgraph_a
            MockBridge.return_value.load_bridges.return_value = []

            with patch.object(
                GraphCompositionService,
                "_fetch_vault_document_ids",
                return_value=[doc_lo, doc_hi],
            ):
                service = GraphCompositionService(engine)
                result = service.compose_view(vault_ids=[vault_a])

        assert any(
            e.doc_lo == internal_edge.doc_lo
            and e.doc_hi == internal_edge.doc_hi
            and e.weight == pytest.approx(internal_edge.weight)
            for e in result.edges
        )


# ---------------------------------------------------------------------------
# Helpers for mock config row
# ---------------------------------------------------------------------------

def _mock_config_row(config: GraphConfigRow) -> MagicMock:
    """Produce a mapping-style mock row for GraphConfigRow.from_row()."""
    row = MagicMock()
    row.id = str(config.id)
    row.name = config.name
    row.description = config.description
    row.embedding_source = config.embedding_source
    row.summary_embedding_source = config.summary_embedding_source
    row.feature_weights = config.feature_weights
    row.knn_k = config.knn_k
    row.candidate_k = config.candidate_k
    row.pruning_mode = config.pruning_mode
    row.bridge_knn_k = config.bridge_knn_k
    row.is_active = config.is_active
    row.created_by = None
    row.created_at = None
    row.updated_at = None
    return row
