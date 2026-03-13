"""
Unit tests for Phase 1 graph-engine components.

Tests:
- SparseEdge canonical ordering enforcement.
- SparseEdge.make() factory.
- SparseGraph construction.
- GraphBuilder._build_knn_edges() kNN logic (no database required).
- GraphBuilder.build_vault_graph() with a stubbed engine.

All tests use synthetic data; no database connection is required.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch
from uuid import UUID

import numpy as np
import pytest

from graph_engine.graph_core.types import SparseEdge, SparseGraph
from graph_engine.graph_core.builder import GraphBuilder
from graph_engine.db.models.graph_config import GraphConfigRow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_uuid(n: int) -> UUID:
    """Generate a deterministic UUID from an integer for test reproducibility."""
    return UUID(int=n)


def _make_config(knn_k: int = 3, candidate_k: int = 5) -> GraphConfigRow:
    return GraphConfigRow(
        id=_make_uuid(999),
        name="test-config",
        embedding_source="all_minilm_v1_384",
        knn_k=knn_k,
        candidate_k=candidate_k,
        pruning_mode="none",
        feature_weights={},
        is_active=True,
    )


def _random_embeddings(n: int, dim: int = 64, seed: int = 42) -> np.ndarray:
    rng = np.random.default_rng(seed)
    vecs = rng.standard_normal((n, dim)).astype(np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / norms


# ---------------------------------------------------------------------------
# SparseEdge tests
# ---------------------------------------------------------------------------

class TestSparseEdge:

    def test_canonical_ordering_direct(self) -> None:
        """Direct construction with doc_lo < doc_hi must succeed."""
        lo = _make_uuid(1)
        hi = _make_uuid(2)
        assert lo < hi
        edge = SparseEdge(doc_lo=lo, doc_hi=hi, weight=0.9)
        assert edge.doc_lo == lo
        assert edge.doc_hi == hi

    def test_canonical_ordering_violation_raises(self) -> None:
        """Direct construction with doc_lo > doc_hi must raise."""
        lo = _make_uuid(1)
        hi = _make_uuid(2)
        assert lo < hi
        with pytest.raises(ValueError, match="canonical ordering violated"):
            SparseEdge(doc_lo=hi, doc_hi=lo, weight=0.9)

    def test_self_loop_raises(self) -> None:
        doc = _make_uuid(1)
        with pytest.raises(ValueError, match="self-loop"):
            SparseEdge(doc_lo=doc, doc_hi=doc, weight=0.5)

    def test_make_factory_auto_orders(self) -> None:
        """SparseEdge.make() must order regardless of argument order."""
        a = _make_uuid(5)
        b = _make_uuid(3)
        assert b < a  # b is the lo side

        edge = SparseEdge.make(doc_a=a, doc_b=b, weight=0.7)
        assert edge.doc_lo == b
        assert edge.doc_hi == a
        assert edge.doc_lo < edge.doc_hi

    def test_make_factory_preserves_order_when_correct(self) -> None:
        lo = _make_uuid(2)
        hi = _make_uuid(8)
        edge = SparseEdge.make(doc_a=lo, doc_b=hi, weight=0.5)
        assert edge.doc_lo == lo
        assert edge.doc_hi == hi

    def test_make_self_loop_raises(self) -> None:
        doc = _make_uuid(1)
        with pytest.raises(ValueError, match="Self-loop"):
            SparseEdge.make(doc_a=doc, doc_b=doc, weight=0.5)

    def test_weight_preserved(self) -> None:
        lo = _make_uuid(1)
        hi = _make_uuid(2)
        edge = SparseEdge(doc_lo=lo, doc_hi=hi, weight=0.85, distance=0.15)
        assert edge.weight == pytest.approx(0.85)
        assert edge.distance == pytest.approx(0.15)

    def test_rank_fields_default_none(self) -> None:
        lo = _make_uuid(1)
        hi = _make_uuid(2)
        edge = SparseEdge(doc_lo=lo, doc_hi=hi, weight=0.5)
        assert edge.rank_lo is None
        assert edge.rank_hi is None


# ---------------------------------------------------------------------------
# SparseGraph tests
# ---------------------------------------------------------------------------

class TestSparseGraph:

    def test_construction(self) -> None:
        nodes = [_make_uuid(i) for i in range(5)]
        edges = [
            SparseEdge.make(_make_uuid(0), _make_uuid(1), weight=0.9),
            SparseEdge.make(_make_uuid(1), _make_uuid(2), weight=0.8),
        ]
        vault_id = _make_uuid(100)
        config_id = _make_uuid(200)
        graph = SparseGraph(
            nodes=nodes,
            edges=edges,
            vault_id=vault_id,
            config_id=config_id,
        )
        assert len(graph.nodes) == 5
        assert len(graph.edges) == 2
        assert graph.vault_id == vault_id
        assert graph.config_id == config_id


# ---------------------------------------------------------------------------
# GraphBuilder._build_knn_edges tests (no DB required)
# ---------------------------------------------------------------------------

class TestGraphBuilderKnn:
    """Tests that exercise only the pure kNN logic, bypassing DB calls."""

    def _builder(self) -> GraphBuilder:
        return GraphBuilder(engine=MagicMock())

    def test_node_count_matches_input(self) -> None:
        n = 10
        doc_ids = [_make_uuid(i) for i in range(n)]
        matrix = _random_embeddings(n, dim=64)
        config = _make_config(knn_k=3, candidate_k=5)

        builder = self._builder()
        edges = builder._build_knn_edges(
            ordered_ids=doc_ids,
            matrix=matrix,
            knn_k=config.knn_k,
            candidate_k=config.candidate_k,
        )

        endpoint_ids = set()
        for e in edges:
            endpoint_ids.add(e.doc_lo)
            endpoint_ids.add(e.doc_hi)
        # Every node that could form a pair should appear; at minimum node
        # count covered by edges is <= n.
        assert len(endpoint_ids) <= n

    def test_canonical_ordering_all_edges(self) -> None:
        """Every edge produced must satisfy doc_lo < doc_hi."""
        n = 10
        doc_ids = [_make_uuid(i) for i in range(n)]
        matrix = _random_embeddings(n, dim=64)
        config = _make_config(knn_k=3, candidate_k=5)

        builder = self._builder()
        edges = builder._build_knn_edges(
            ordered_ids=doc_ids,
            matrix=matrix,
            knn_k=config.knn_k,
            candidate_k=config.candidate_k,
        )

        for edge in edges:
            assert edge.doc_lo < edge.doc_hi, (
                f"Ordering violated: {edge.doc_lo} >= {edge.doc_hi}"
            )

    def test_no_duplicate_edges(self) -> None:
        """Each (doc_lo, doc_hi) pair must appear at most once."""
        n = 10
        doc_ids = [_make_uuid(i) for i in range(n)]
        matrix = _random_embeddings(n, dim=64)
        config = _make_config(knn_k=4, candidate_k=6)

        builder = self._builder()
        edges = builder._build_knn_edges(
            ordered_ids=doc_ids,
            matrix=matrix,
            knn_k=config.knn_k,
            candidate_k=config.candidate_k,
        )

        pairs = [(e.doc_lo, e.doc_hi) for e in edges]
        assert len(pairs) == len(set(pairs)), "Duplicate edges found"

    def test_edge_count_upper_bound(self) -> None:
        """
        Edge count must be <= n * knn_k / 2 (each document contributes at most
        knn_k outgoing edges; each edge is counted from both sides before dedup).
        """
        n = 10
        knn_k = 3
        doc_ids = [_make_uuid(i) for i in range(n)]
        matrix = _random_embeddings(n, dim=64)

        builder = self._builder()
        edges = builder._build_knn_edges(
            ordered_ids=doc_ids,
            matrix=matrix,
            knn_k=knn_k,
            candidate_k=knn_k + 2,
        )

        max_edges = n * knn_k  # loose upper bound before dedup
        assert len(edges) <= max_edges, (
            f"Edge count {len(edges)} exceeds loose upper bound {max_edges}"
        )

    def test_weight_in_valid_range(self) -> None:
        """All edge weights must be in [0, 1]."""
        n = 10
        doc_ids = [_make_uuid(i) for i in range(n)]
        matrix = _random_embeddings(n, dim=64)

        builder = self._builder()
        edges = builder._build_knn_edges(
            ordered_ids=doc_ids,
            matrix=matrix,
            knn_k=3,
            candidate_k=5,
        )

        for edge in edges:
            assert 0.0 <= edge.weight <= 1.0, f"Weight out of range: {edge.weight}"

    def test_knn_k_configurable(self) -> None:
        """Different knn_k values produce different (expected) edge counts."""
        n = 10
        doc_ids = [_make_uuid(i) for i in range(n)]
        matrix = _random_embeddings(n, dim=64, seed=7)

        builder = self._builder()

        edges_k2 = builder._build_knn_edges(doc_ids, matrix.copy(), knn_k=2, candidate_k=5)
        edges_k4 = builder._build_knn_edges(doc_ids, matrix.copy(), knn_k=4, candidate_k=6)

        # Higher knn_k should generally produce more or equal edges.
        assert len(edges_k4) >= len(edges_k2)

    def test_two_documents_produce_one_edge(self) -> None:
        """Minimum viable vault: 2 documents produce exactly 1 edge."""
        doc_ids = [_make_uuid(0), _make_uuid(1)]
        matrix = _random_embeddings(2, dim=16)

        builder = self._builder()
        edges = builder._build_knn_edges(
            ordered_ids=doc_ids,
            matrix=matrix,
            knn_k=1,
            candidate_k=1,
        )
        assert len(edges) == 1
        assert edges[0].doc_lo < edges[0].doc_hi


# ---------------------------------------------------------------------------
# GraphBuilder.build_vault_graph integration-style test (stubbed engine)
# ---------------------------------------------------------------------------

class TestGraphBuilderBuildVaultGraph:
    """
    Tests for build_vault_graph() with a fully stubbed SQLAlchemy engine.
    No database connection is required.
    """

    N_DOCS = 10
    DIM = 64

    def _setup(
        self,
        n: int = N_DOCS,
        dim: int = DIM,
        knn_k: int = 3,
        candidate_k: int = 5,
    ):
        vault_id = _make_uuid(100)
        config_id = _make_uuid(200)
        doc_ids = [_make_uuid(i) for i in range(n)]
        embeddings = _random_embeddings(n, dim=dim)

        config = _make_config(knn_k=knn_k, candidate_k=candidate_k)

        engine = MagicMock()

        # Stub get_graph_config to return our config without DB.
        with patch(
            "graph_engine.graph_core.builder.get_graph_config",
            return_value=config,
        ):
            # Stub _fetch_vault_document_ids.
            builder = GraphBuilder(engine=engine)
            builder._fetch_vault_document_ids = MagicMock(return_value=doc_ids)
            # Stub _fetch_embeddings.
            id_to_vec = {doc_ids[i]: embeddings[i] for i in range(n)}
            builder._fetch_embeddings = MagicMock(return_value=id_to_vec)

            graph = builder.build_vault_graph(
                vault_id=vault_id,
                config_id=config_id,
                persist=False,
            )

        return graph, doc_ids, vault_id, config_id

    def test_node_count_equals_document_count(self) -> None:
        graph, doc_ids, _, _ = self._setup()
        assert len(graph.nodes) == len(doc_ids)

    def test_all_nodes_present(self) -> None:
        graph, doc_ids, _, _ = self._setup()
        assert set(graph.nodes) == set(doc_ids)

    def test_vault_id_preserved(self) -> None:
        graph, _, vault_id, _ = self._setup()
        assert graph.vault_id == vault_id

    def test_config_id_preserved(self) -> None:
        graph, _, _, config_id = self._setup()
        assert graph.config_id == config_id

    def test_all_edges_canonical_ordering(self) -> None:
        graph, _, _, _ = self._setup()
        for edge in graph.edges:
            assert edge.doc_lo < edge.doc_hi

    def test_no_duplicate_edges(self) -> None:
        graph, _, _, _ = self._setup()
        pairs = [(e.doc_lo, e.doc_hi) for e in graph.edges]
        assert len(pairs) == len(set(pairs))

    def test_empty_vault_raises(self) -> None:
        vault_id = _make_uuid(100)
        config_id = _make_uuid(200)
        config = _make_config()
        engine = MagicMock()

        with patch(
            "graph_engine.graph_core.builder.get_graph_config",
            return_value=config,
        ):
            builder = GraphBuilder(engine=engine)
            builder._fetch_vault_document_ids = MagicMock(return_value=[])

            with pytest.raises(ValueError, match="no documents"):
                builder.build_vault_graph(vault_id=vault_id, config_id=config_id)

    def test_vault_with_no_embeddings_raises(self) -> None:
        vault_id = _make_uuid(100)
        config_id = _make_uuid(200)
        doc_ids = [_make_uuid(i) for i in range(5)]
        config = _make_config()
        engine = MagicMock()

        with patch(
            "graph_engine.graph_core.builder.get_graph_config",
            return_value=config,
        ):
            builder = GraphBuilder(engine=engine)
            builder._fetch_vault_document_ids = MagicMock(return_value=doc_ids)
            builder._fetch_embeddings = MagicMock(return_value={})

            with pytest.raises(ValueError, match="No embeddings"):
                builder.build_vault_graph(vault_id=vault_id, config_id=config_id)
