"""
Unit tests for SubgraphService (Phase 3 graph-engine).

Tests:
- Induced subgraph returns only edges where both endpoints are in the requested set.
- Ego subgraph (depth=1) returns correct 1-hop neighborhood.
- Empty document set returns empty SubgraphResult (zero nodes, zero edges).
- Nodes with no edges are included in the node list but produce no edges.
- Non-existent graph_version_id raises KeyError.
- No active version raises ValueError.
- Isolated nodes requested explicitly appear in nodes but have no edges.
- extract_ego_subgraph includes center in result even when center has no edges.
- node_count and edge_count on SubgraphResult match len(nodes) and len(edges).

All tests use mocked SQLAlchemy engines — no database connection required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call
from uuid import UUID

import pytest

from graph_engine.db.repositories.graph_version_repo import GraphVersionRepo
from graph_engine.graph_core.subgraph import SubgraphResult, SubgraphService
from graph_engine.graph_core.types import SparseEdge


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _u(n: int) -> UUID:
    return UUID(int=n)


def _make_engine(side_effects: list) -> MagicMock:
    """
    Build a mock engine whose conn.execute() returns the given side_effects
    in order.  Each item in side_effects must be a MagicMock result object.
    """
    engine = MagicMock()
    conn = MagicMock()
    engine.connect.return_value.__enter__ = MagicMock(return_value=conn)
    engine.connect.return_value.__exit__ = MagicMock(return_value=False)
    conn.execute.side_effect = side_effects
    return engine, conn


def _fetchone_result(value) -> MagicMock:
    """Return a mock execute result that returns `value` from fetchone()."""
    result = MagicMock()
    result.fetchone.return_value = value
    return result


def _fetchall_result(rows: list) -> MagicMock:
    """Return a mock execute result that returns `rows` from fetchall()."""
    result = MagicMock()
    result.fetchall.return_value = rows
    return result


def _version_repo_with_active(version_id: UUID) -> GraphVersionRepo:
    """Return a mock GraphVersionRepo whose get_active_version returns a dict."""
    repo = MagicMock(spec=GraphVersionRepo)
    repo.get_active_version.return_value = {"id": str(version_id)}
    return repo


def _version_repo_no_active() -> GraphVersionRepo:
    """Return a mock GraphVersionRepo whose get_active_version returns None."""
    repo = MagicMock(spec=GraphVersionRepo)
    repo.get_active_version.return_value = None
    return repo


def _edge_row(lo: UUID, hi: UUID, weight: float = 0.8) -> tuple:
    """Produce a raw DB row tuple as returned by fetchall()."""
    return (str(lo), str(hi), weight, None, None, None)


# ---------------------------------------------------------------------------
# TestExtractSubgraph
# ---------------------------------------------------------------------------

class TestExtractSubgraph:

    def test_returns_only_edges_with_both_endpoints_in_set(self) -> None:
        """
        extract_subgraph must return only edges where doc_lo AND doc_hi are
        inside the requested document set.

        The SQL WHERE clause enforces this; we verify the SubgraphResult edge
        set only contains edges from our mocked rows.
        """
        version_id = _u(1000)
        vault_id = _u(200)
        doc_a, doc_b, doc_c = _u(1), _u(2), _u(3)

        # Simulate: version exists, then edges returned from induced-subgraph SQL
        engine, conn = _make_engine([
            _fetchone_result((1,)),          # _assert_version_exists
            _fetchall_result([               # induced subgraph SQL
                _edge_row(doc_a, doc_b, 0.9),
                _edge_row(doc_b, doc_c, 0.7),
            ]),
        ])
        version_repo = _version_repo_with_active(version_id)
        service = SubgraphService(engine, version_repo)

        result = service.extract_subgraph(
            vault_id=vault_id,
            document_ids=[doc_a, doc_b, doc_c],
            graph_version_id=version_id,
        )

        assert result.edge_count == 2
        assert result.node_count == 3
        assert result.graph_version_id == version_id
        assert result.vault_id == vault_id

        edge_pairs = {(e.doc_lo, e.doc_hi) for e in result.edges}
        assert (doc_a, doc_b) in edge_pairs
        assert (doc_b, doc_c) in edge_pairs

    def test_empty_document_set_returns_empty_result(self) -> None:
        """
        extract_subgraph with an empty document_ids list must return zero nodes
        and zero edges without hitting the database.
        """
        version_id = _u(1000)
        vault_id = _u(200)

        engine, conn = _make_engine([
            _fetchone_result((1,)),  # _assert_version_exists
        ])
        version_repo = _version_repo_with_active(version_id)
        service = SubgraphService(engine, version_repo)

        result = service.extract_subgraph(
            vault_id=vault_id,
            document_ids=[],
            graph_version_id=version_id,
        )

        assert result.node_count == 0
        assert result.edge_count == 0
        assert result.nodes == []
        assert result.edges == []
        # Only the version existence check should have been called (once).
        assert conn.execute.call_count == 1

    def test_nodes_with_no_edges_appear_in_node_list(self) -> None:
        """
        When requested documents have no edges, the SubgraphResult should
        include them in nodes but have zero edges.
        """
        version_id = _u(1000)
        vault_id = _u(200)
        isolated_doc = _u(99)

        engine, conn = _make_engine([
            _fetchone_result((1,)),         # _assert_version_exists
            _fetchall_result([]),            # SQL returns no edges
        ])
        version_repo = _version_repo_with_active(version_id)
        service = SubgraphService(engine, version_repo)

        result = service.extract_subgraph(
            vault_id=vault_id,
            document_ids=[isolated_doc],
            graph_version_id=version_id,
        )

        assert result.edge_count == 0
        assert result.node_count == 1
        assert isolated_doc in result.nodes

    def test_invalid_graph_version_raises_key_error(self) -> None:
        """
        Providing a graph_version_id that does not exist must raise KeyError.
        """
        bad_version_id = _u(9999)
        vault_id = _u(200)

        engine, conn = _make_engine([
            _fetchone_result(None),  # version does not exist
        ])
        version_repo = _version_repo_with_active(bad_version_id)
        service = SubgraphService(engine, version_repo)

        with pytest.raises(KeyError, match="vault_graph_version not found"):
            service.extract_subgraph(
                vault_id=vault_id,
                document_ids=[_u(1)],
                graph_version_id=bad_version_id,
            )

    def test_no_active_version_raises_value_error(self) -> None:
        """
        When no graph_version_id is provided and no active version exists,
        extract_subgraph must raise ValueError.
        """
        vault_id = _u(200)

        engine, _ = _make_engine([])
        version_repo = _version_repo_no_active()
        service = SubgraphService(engine, version_repo)

        with pytest.raises(ValueError, match="No active graph version"):
            service.extract_subgraph(
                vault_id=vault_id,
                document_ids=[_u(1), _u(2)],
            )

    def test_resolves_active_version_when_none_provided(self) -> None:
        """
        When graph_version_id is None, the active version must be fetched
        from GraphVersionRepo.
        """
        version_id = _u(500)
        vault_id = _u(200)

        engine, conn = _make_engine([
            _fetchall_result([]),  # SQL returns no edges (active version used)
        ])
        version_repo = _version_repo_with_active(version_id)
        service = SubgraphService(engine, version_repo)

        result = service.extract_subgraph(
            vault_id=vault_id,
            document_ids=[_u(1)],
            graph_version_id=None,
        )

        version_repo.get_active_version.assert_called_once_with(vault_id)
        assert result.graph_version_id == version_id

    def test_node_count_and_edge_count_match_lists(self) -> None:
        """
        node_count must equal len(nodes); edge_count must equal len(edges).
        """
        version_id = _u(1000)
        vault_id = _u(200)
        doc_a, doc_b = _u(1), _u(2)

        engine, _ = _make_engine([
            _fetchone_result((1,)),
            _fetchall_result([_edge_row(doc_a, doc_b)]),
        ])
        service = SubgraphService(engine, _version_repo_with_active(version_id))

        result = service.extract_subgraph(
            vault_id=vault_id,
            document_ids=[doc_a, doc_b],
            graph_version_id=version_id,
        )

        assert result.node_count == len(result.nodes)
        assert result.edge_count == len(result.edges)

    def test_edges_use_sparse_edge_type(self) -> None:
        """
        All edges in SubgraphResult must be SparseEdge instances.
        """
        version_id = _u(1000)
        vault_id = _u(200)
        doc_a, doc_b = _u(1), _u(2)

        engine, _ = _make_engine([
            _fetchone_result((1,)),
            _fetchall_result([_edge_row(doc_a, doc_b, 0.75)]),
        ])
        service = SubgraphService(engine, _version_repo_with_active(version_id))

        result = service.extract_subgraph(
            vault_id=vault_id,
            document_ids=[doc_a, doc_b],
            graph_version_id=version_id,
        )

        assert all(isinstance(e, SparseEdge) for e in result.edges)

    def test_canonical_ordering_preserved(self) -> None:
        """
        All edges in SubgraphResult must satisfy doc_lo < doc_hi.
        """
        version_id = _u(1000)
        vault_id = _u(200)
        lo, hi = _u(3), _u(7)

        engine, _ = _make_engine([
            _fetchone_result((1,)),
            _fetchall_result([_edge_row(lo, hi)]),
        ])
        service = SubgraphService(engine, _version_repo_with_active(version_id))

        result = service.extract_subgraph(
            vault_id=vault_id,
            document_ids=[lo, hi],
            graph_version_id=version_id,
        )

        for edge in result.edges:
            assert edge.doc_lo < edge.doc_hi


# ---------------------------------------------------------------------------
# TestExtractEgoSubgraph
# ---------------------------------------------------------------------------

class TestExtractEgoSubgraph:

    def _make_ego_engine(
        self,
        version_id: UUID,
        incident_rows: list[tuple],
        induced_rows: list[tuple],
    ) -> tuple[MagicMock, MagicMock]:
        """
        Build a mock engine for a depth-1 ego extraction.

        Call order:
          1. _assert_version_exists (used inside extract_subgraph, called from
             ego's delegate; but ego uses _resolve_version then calls
             extract_subgraph which calls _assert_version_exists again).
             We guard this with a sentinel row.
          2. _fetch_neighbors: SELECT doc_lo, doc_hi WHERE doc_lo=ANY OR doc_hi=ANY
          3. _assert_version_exists (called inside the delegate extract_subgraph)
          4. Induced subgraph SQL

        Because extract_ego_subgraph calls _resolve_version (which checks version
        existence only when a version_id is explicitly provided) and then delegates
        to extract_subgraph (which also calls _assert_version_exists), we supply
        responses accordingly.
        """
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__ = MagicMock(return_value=conn)
        engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        sentinel = _fetchone_result((1,))
        neighbors_result = _fetchall_result(incident_rows)
        induced_result = _fetchall_result(induced_rows)

        # The version-exists check is called once by _assert_version_exists in
        # _resolve_version (for the ego call) and once inside the delegate
        # extract_subgraph; then the induced SQL runs.
        conn.execute.side_effect = [
            sentinel,          # _assert_version_exists for ego's _resolve_version
            neighbors_result,  # _fetch_neighbors
            sentinel,          # _assert_version_exists inside delegate extract_subgraph
            induced_result,    # induced subgraph SQL
        ]
        return engine, conn

    def test_ego_depth1_returns_center_and_neighbors(self) -> None:
        """
        extract_ego_subgraph at depth=1 must return the center plus all
        directly adjacent nodes, and the edges connecting them.
        """
        version_id = _u(1000)
        vault_id = _u(200)
        center = _u(10)
        neighbor_a = _u(11)
        neighbor_b = _u(12)

        # Incident edges: center-neighbor_a and center-neighbor_b
        incident_rows = [
            _edge_row(center, neighbor_a),
            _edge_row(center, neighbor_b),
        ]
        # Induced subgraph over {center, neighbor_a, neighbor_b}
        induced_rows = [
            _edge_row(center, neighbor_a),
            _edge_row(center, neighbor_b),
        ]

        engine, _ = self._make_ego_engine(version_id, incident_rows, induced_rows)
        version_repo = _version_repo_with_active(version_id)
        service = SubgraphService(engine, version_repo)

        result = service.extract_ego_subgraph(
            vault_id=vault_id,
            center_doc_id=center,
            depth=1,
            graph_version_id=version_id,
        )

        assert center in result.nodes
        assert neighbor_a in result.nodes
        assert neighbor_b in result.nodes
        assert result.edge_count == 2
        assert result.node_count == 3

    def test_ego_center_with_no_edges_returns_single_node(self) -> None:
        """
        When the center document has no edges, the ego subgraph contains only
        the center node and zero edges.
        """
        version_id = _u(1000)
        vault_id = _u(200)
        center = _u(42)

        engine, _ = self._make_ego_engine(
            version_id,
            incident_rows=[],   # no neighbors
            induced_rows=[],    # no edges in induced subgraph
        )
        version_repo = _version_repo_with_active(version_id)
        service = SubgraphService(engine, version_repo)

        result = service.extract_ego_subgraph(
            vault_id=vault_id,
            center_doc_id=center,
            depth=1,
            graph_version_id=version_id,
        )

        assert center in result.nodes
        assert result.edge_count == 0
        assert result.node_count == 1

    def test_ego_resolves_active_version_when_none_provided(self) -> None:
        """
        extract_ego_subgraph with graph_version_id=None must look up the
        active version via GraphVersionRepo.
        """
        version_id = _u(500)
        vault_id = _u(200)
        center = _u(1)
        neighbor = _u(2)

        engine, _ = self._make_ego_engine(
            version_id,
            incident_rows=[_edge_row(center, neighbor)],
            induced_rows=[_edge_row(center, neighbor)],
        )
        version_repo = _version_repo_with_active(version_id)
        service = SubgraphService(engine, version_repo)

        result = service.extract_ego_subgraph(
            vault_id=vault_id,
            center_doc_id=center,
            depth=1,
            graph_version_id=None,
        )

        version_repo.get_active_version.assert_called_with(vault_id)
        assert result.graph_version_id == version_id

    def test_ego_no_active_version_raises_value_error(self) -> None:
        """
        extract_ego_subgraph with no version and no active version must
        raise ValueError.
        """
        vault_id = _u(200)
        engine, _ = _make_engine([])
        version_repo = _version_repo_no_active()
        service = SubgraphService(engine, version_repo)

        with pytest.raises(ValueError, match="No active graph version"):
            service.extract_ego_subgraph(
                vault_id=vault_id,
                center_doc_id=_u(1),
                depth=1,
            )

    def test_ego_invalid_depth_raises_value_error(self) -> None:
        """
        extract_ego_subgraph with depth < 1 must raise ValueError.
        """
        vault_id = _u(200)
        engine, _ = _make_engine([])
        version_repo = _version_repo_with_active(_u(1000))
        service = SubgraphService(engine, version_repo)

        with pytest.raises(ValueError, match="depth must be >= 1"):
            service.extract_ego_subgraph(
                vault_id=vault_id,
                center_doc_id=_u(1),
                depth=0,
                graph_version_id=_u(1000),
            )

    def test_ego_invalid_graph_version_raises_key_error(self) -> None:
        """
        Providing a non-existent graph_version_id to extract_ego_subgraph
        must raise KeyError.
        """
        bad_version_id = _u(9999)
        vault_id = _u(200)

        engine, conn = _make_engine([
            _fetchone_result(None),  # version does not exist
        ])
        version_repo = _version_repo_with_active(bad_version_id)
        service = SubgraphService(engine, version_repo)

        with pytest.raises(KeyError, match="vault_graph_version not found"):
            service.extract_ego_subgraph(
                vault_id=vault_id,
                center_doc_id=_u(1),
                depth=1,
                graph_version_id=bad_version_id,
            )

    def test_ego_node_count_and_edge_count_match_lists(self) -> None:
        """
        node_count and edge_count on ego result must match len(nodes)/len(edges).
        """
        version_id = _u(1000)
        vault_id = _u(200)
        center, neighbor = _u(5), _u(6)

        engine, _ = self._make_ego_engine(
            version_id,
            incident_rows=[_edge_row(center, neighbor)],
            induced_rows=[_edge_row(center, neighbor)],
        )
        service = SubgraphService(engine, _version_repo_with_active(version_id))

        result = service.extract_ego_subgraph(
            vault_id=vault_id,
            center_doc_id=center,
            depth=1,
            graph_version_id=version_id,
        )

        assert result.node_count == len(result.nodes)
        assert result.edge_count == len(result.edges)
