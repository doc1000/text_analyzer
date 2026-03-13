"""
Unit tests for GraphEdgeRepo (Phase 2 graph-engine).

Tests:
- save_graph inserts the correct number of edges.
- load_graph round-trip: edges match after persistence.
- Canonical ordering (doc_lo < doc_hi) is preserved after round-trip.
- delete_graph_edges removes all edges for the version and returns correct count.

All tests use a mocked SQLAlchemy engine — no database connection required.
The mock simulates:
    - save_graph  → executemany + commit via engine.connect()
    - load_graph  → SELECT on vault_graph_version and vault_graph_edge
    - delete_graph_edges → DELETE returning rowcount
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch
from uuid import UUID

import pytest

from graph_engine.db.repositories.graph_edge_repo import GraphEdgeRepo
from graph_engine.graph_core.types import SparseEdge, SparseGraph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _u(n: int) -> UUID:
    return UUID(int=n)


def _make_graph(n_nodes: int = 5, vault_id_int: int = 100, config_id_int: int = 200) -> SparseGraph:
    """Build a small synthetic SparseGraph for testing."""
    nodes = [_u(i) for i in range(n_nodes)]
    # Build edges as a simple chain: 0-1, 1-2, 2-3, 3-4
    edges = [
        SparseEdge.make(_u(i), _u(i + 1), weight=0.9 - i * 0.1)
        for i in range(n_nodes - 1)
    ]
    return SparseGraph(
        nodes=nodes,
        edges=edges,
        vault_id=_u(vault_id_int),
        config_id=_u(config_id_int),
    )


def _make_engine_for_save() -> MagicMock:
    """Return a mock engine that records executemany calls without errors."""
    engine = MagicMock()
    conn = MagicMock()
    engine.connect.return_value.__enter__ = MagicMock(return_value=conn)
    engine.connect.return_value.__exit__ = MagicMock(return_value=False)
    return engine, conn


# ---------------------------------------------------------------------------
# save_graph tests
# ---------------------------------------------------------------------------

class TestGraphEdgeRepoSave:

    def test_save_returns_correct_edge_count(self) -> None:
        """save_graph must return the number of edges in the SparseGraph."""
        graph = _make_graph(n_nodes=5)
        version_id = _u(1000)
        vault_id = _u(100)

        engine, conn = _make_engine_for_save()
        repo = GraphEdgeRepo(engine)

        count = repo.save_graph(
            graph_version_id=version_id,
            vault_id=vault_id,
            sparse_graph=graph,
        )

        assert count == len(graph.edges)

    def test_save_calls_execute_once(self) -> None:
        """save_graph must call execute exactly once (batch insert)."""
        graph = _make_graph(n_nodes=4)
        version_id = _u(1000)
        vault_id = _u(100)

        engine, conn = _make_engine_for_save()
        repo = GraphEdgeRepo(engine)
        repo.save_graph(
            graph_version_id=version_id,
            vault_id=vault_id,
            sparse_graph=graph,
        )

        assert conn.execute.call_count == 1

    def test_save_empty_graph_returns_zero(self) -> None:
        """save_graph on a graph with no edges returns 0 without hitting DB."""
        graph = SparseGraph(
            nodes=[_u(0), _u(1)],
            edges=[],
            vault_id=_u(100),
            config_id=_u(200),
        )
        engine, conn = _make_engine_for_save()
        repo = GraphEdgeRepo(engine)

        count = repo.save_graph(
            graph_version_id=_u(1000),
            vault_id=_u(100),
            sparse_graph=graph,
        )

        assert count == 0
        conn.execute.assert_not_called()

    def test_save_passes_canonical_rows(self) -> None:
        """
        save_graph must pass rows where doc_lo < doc_hi to the DB statement.
        """
        lo = _u(1)
        hi = _u(2)
        assert lo < hi

        graph = SparseGraph(
            nodes=[lo, hi],
            edges=[SparseEdge(doc_lo=lo, doc_hi=hi, weight=0.8)],
            vault_id=_u(100),
            config_id=_u(200),
        )
        engine, conn = _make_engine_for_save()
        repo = GraphEdgeRepo(engine)

        repo.save_graph(
            graph_version_id=_u(999),
            vault_id=_u(100),
            sparse_graph=graph,
        )

        # Capture the rows passed to execute
        _, kwargs_or_args = conn.execute.call_args
        # execute(sql, rows) — rows is the second positional arg
        call_args = conn.execute.call_args
        rows_arg = call_args[0][1]  # positional: (sql, rows)
        assert len(rows_arg) == 1
        row = rows_arg[0]
        assert row["doc_lo"] == str(lo)
        assert row["doc_hi"] == str(hi)
        assert UUID(row["doc_lo"]) < UUID(row["doc_hi"])


# ---------------------------------------------------------------------------
# load_graph tests
# ---------------------------------------------------------------------------

class TestGraphEdgeRepoLoad:

    def _make_load_engine(
        self,
        vault_id: UUID,
        config_id: UUID,
        edge_rows: list[tuple],
    ) -> MagicMock:
        """
        Build a mock engine that returns version metadata and edge rows.

        version_row uses mappings().fetchone() → dict-like object.
        edge_rows uses fetchall() → list of row tuples.
        """
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__ = MagicMock(return_value=conn)
        engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        # First execute() call → vault_graph_version lookup (mapping)
        version_mapping = MagicMock()
        version_mapping.__getitem__ = lambda self, key: (
            str(vault_id) if key == "vault_id" else str(config_id)
        )
        version_result = MagicMock()
        version_result.mappings.return_value.fetchone.return_value = version_mapping

        # Second execute() call → edge rows
        edge_result = MagicMock()
        edge_result.fetchall.return_value = edge_rows

        conn.execute.side_effect = [version_result, edge_result]

        return engine

    def test_load_round_trip_edge_count(self) -> None:
        """load_graph must return the same number of edges that were 'stored'."""
        vault_id = _u(100)
        config_id = _u(200)
        lo = _u(1)
        hi = _u(2)

        edge_rows = [
            (str(lo), str(hi), 0.8, 0.2, 0, None),
        ]
        engine = self._make_load_engine(vault_id, config_id, edge_rows)
        repo = GraphEdgeRepo(engine)

        graph = repo.load_graph(_u(999))

        assert len(graph.edges) == 1

    def test_load_round_trip_canonical_ordering(self) -> None:
        """All edges from load_graph must satisfy doc_lo < doc_hi."""
        vault_id = _u(100)
        config_id = _u(200)

        # Simulate 3 edges already stored with correct canonical order
        pairs = [(_u(0), _u(1)), (_u(1), _u(3)), (_u(2), _u(4))]
        edge_rows = [
            (str(lo), str(hi), 0.9 - i * 0.1, None, i, None)
            for i, (lo, hi) in enumerate(pairs)
        ]

        engine = self._make_load_engine(vault_id, config_id, edge_rows)
        repo = GraphEdgeRepo(engine)

        graph = repo.load_graph(_u(999))

        for edge in graph.edges:
            assert edge.doc_lo < edge.doc_hi, (
                f"Canonical ordering violated: {edge.doc_lo} >= {edge.doc_hi}"
            )

    def test_load_returns_correct_vault_and_config(self) -> None:
        vault_id = _u(100)
        config_id = _u(200)
        engine = self._make_load_engine(vault_id, config_id, edge_rows=[])
        repo = GraphEdgeRepo(engine)

        graph = repo.load_graph(_u(999))

        assert graph.vault_id == vault_id
        assert graph.config_id == config_id

    def test_load_missing_version_raises_key_error(self) -> None:
        """load_graph raises KeyError when the version does not exist."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__ = MagicMock(return_value=conn)
        engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        version_result = MagicMock()
        version_result.mappings.return_value.fetchone.return_value = None
        conn.execute.return_value = version_result

        repo = GraphEdgeRepo(engine)
        with pytest.raises(KeyError, match="vault_graph_version not found"):
            repo.load_graph(_u(9999))

    def test_load_node_set_derived_from_edges(self) -> None:
        """
        load_graph must derive the node list from edge endpoints.
        Nodes not connected by any edge may not appear.
        """
        vault_id = _u(100)
        config_id = _u(200)
        lo, hi = _u(5), _u(7)

        edge_rows = [(str(lo), str(hi), 0.75, 0.25, 0, 0)]
        engine = self._make_load_engine(vault_id, config_id, edge_rows)
        repo = GraphEdgeRepo(engine)

        graph = repo.load_graph(_u(999))

        assert lo in graph.nodes
        assert hi in graph.nodes


# ---------------------------------------------------------------------------
# delete_graph_edges tests
# ---------------------------------------------------------------------------

class TestGraphEdgeRepoDelete:

    def _make_delete_engine(self, rowcount: int) -> MagicMock:
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__ = MagicMock(return_value=conn)
        engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        result = MagicMock()
        result.rowcount = rowcount
        conn.execute.return_value = result

        return engine

    def test_delete_returns_rowcount(self) -> None:
        """delete_graph_edges must return the number of deleted rows."""
        engine = self._make_delete_engine(rowcount=7)
        repo = GraphEdgeRepo(engine)

        deleted = repo.delete_graph_edges(_u(999))

        assert deleted == 7

    def test_delete_zero_edges(self) -> None:
        """delete_graph_edges returns 0 when no edges exist for the version."""
        engine = self._make_delete_engine(rowcount=0)
        repo = GraphEdgeRepo(engine)

        deleted = repo.delete_graph_edges(_u(999))

        assert deleted == 0

    def test_delete_calls_execute_with_version_id(self) -> None:
        """delete_graph_edges must pass the version_id to the DELETE statement."""
        version_id = _u(42)
        engine = self._make_delete_engine(rowcount=3)
        repo = GraphEdgeRepo(engine)

        conn = engine.connect.return_value.__enter__.return_value
        repo.delete_graph_edges(version_id)

        call_args = conn.execute.call_args
        params = call_args[0][1]
        assert params["version_id"] == str(version_id)
