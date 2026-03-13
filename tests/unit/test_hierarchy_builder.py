"""
Unit tests for Phase 7 graph-engine components: hierarchy builder and cache.

Tests:
- Z linkage matrix shape is correct (n-1, 4) for n nodes.
- Hierarchy payload contains all documents as leaves.
- Cache save/load round trip returns the stored payload.
- Expired cache returns None.
- Cache invalidation removes entries.
- Single-node graph handled correctly (degenerate case).

All tests use mocked SQLAlchemy engines — no database connection required.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call
from uuid import UUID

import numpy as np
import pytest

from graph_engine.graph_core.hierarchy import (
    build_hierarchy_payload,
    build_z_linkage,
    cache_hierarchy,
    invalidate_hierarchy_cache,
    load_cached_hierarchy,
)
from graph_engine.graph_core.subgraph import SubgraphResult
from graph_engine.graph_core.types import SparseEdge


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _u(n: int) -> UUID:
    return UUID(int=n)


def _make_engine(side_effects: list) -> tuple[MagicMock, MagicMock]:
    """Build a mock engine whose conn.execute() returns side_effects in order."""
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


def _make_subgraph(
    nodes: list[UUID],
    edges: list[SparseEdge],
    vault_id: UUID | None = None,
    graph_version_id: UUID | None = None,
) -> SubgraphResult:
    vid = vault_id or _u(1)
    gvid = graph_version_id or _u(100)
    return SubgraphResult(
        nodes=nodes,
        edges=edges,
        graph_version_id=gvid,
        vault_id=vid,
        node_count=len(nodes),
        edge_count=len(edges),
    )


def _all_leaf_doc_ids(tree: dict) -> list[str]:
    """Recursively collect all document_id values from leaf nodes."""
    if "document_id" in tree:
        return [tree["document_id"]]
    result: list[str] = []
    for child in tree.get("children", []):
        result.extend(_all_leaf_doc_ids(child))
    return result


# ---------------------------------------------------------------------------
# TestBuildZLinkage
# ---------------------------------------------------------------------------

class TestBuildZLinkage:

    def test_z_array_shape_is_correct_for_n_nodes(self) -> None:
        """
        For n nodes, the Z linkage array must have shape (n-1, 4).
        """
        n = 4
        nodes = [_u(i) for i in range(1, n + 1)]
        edges = [
            SparseEdge.make(_u(1), _u(2), 0.9),
            SparseEdge.make(_u(2), _u(3), 0.8),
            SparseEdge.make(_u(3), _u(4), 0.7),
        ]
        subgraph = _make_subgraph(nodes, edges)

        result = build_z_linkage(subgraph)

        z_array = result["z_array"]
        assert len(z_array) == n - 1, f"Expected {n - 1} rows, got {len(z_array)}"
        for row in z_array:
            assert len(row) == 4, f"Each linkage row must have 4 columns, got {len(row)}"

    def test_labels_match_node_count(self) -> None:
        nodes = [_u(1), _u(2), _u(3)]
        edges = [SparseEdge.make(_u(1), _u(2), 0.85)]
        subgraph = _make_subgraph(nodes, edges)

        result = build_z_linkage(subgraph)

        assert len(result["labels"]) == 3

    def test_labels_are_string_uuids(self) -> None:
        nodes = [_u(1), _u(2)]
        edges = [SparseEdge.make(_u(1), _u(2), 0.75)]
        subgraph = _make_subgraph(nodes, edges)

        result = build_z_linkage(subgraph)

        for label in result["labels"]:
            # Must be a valid UUID string.
            UUID(label)

    def test_method_is_preserved_in_result(self) -> None:
        nodes = [_u(1), _u(2), _u(3)]
        edges = [
            SparseEdge.make(_u(1), _u(2), 0.9),
            SparseEdge.make(_u(2), _u(3), 0.8),
        ]
        subgraph = _make_subgraph(nodes, edges)

        result = build_z_linkage(subgraph, method="ward")

        assert result["method"] == "ward"

    def test_z_array_values_are_json_serialisable(self) -> None:
        nodes = [_u(1), _u(2), _u(3)]
        edges = [
            SparseEdge.make(_u(1), _u(2), 0.9),
            SparseEdge.make(_u(2), _u(3), 0.6),
        ]
        subgraph = _make_subgraph(nodes, edges)
        result = build_z_linkage(subgraph)

        # Must not raise.
        serialised = json.dumps(result)
        assert isinstance(serialised, str)

    def test_raises_for_empty_subgraph(self) -> None:
        subgraph = _make_subgraph([], [])

        with pytest.raises(ValueError, match="no nodes"):
            build_z_linkage(subgraph)

    def test_two_node_subgraph_produces_single_row(self) -> None:
        """Two nodes → Z array with exactly 1 row."""
        nodes = [_u(1), _u(2)]
        edges = [SparseEdge.make(_u(1), _u(2), 0.8)]
        subgraph = _make_subgraph(nodes, edges)

        result = build_z_linkage(subgraph)

        assert len(result["z_array"]) == 1
        assert len(result["z_array"][0]) == 4

    def test_disconnected_nodes_use_max_distance(self) -> None:
        """
        Nodes without edges between them must use distance = 1.0 (maximum
        dissimilarity). The Z array must still have the correct shape.
        """
        nodes = [_u(1), _u(2), _u(3)]
        # No edges — all distances are 1.0.
        subgraph = _make_subgraph(nodes, [])

        result = build_z_linkage(subgraph)

        assert len(result["z_array"]) == 2  # n - 1 = 2


# ---------------------------------------------------------------------------
# TestSingleNodeSubgraph
# ---------------------------------------------------------------------------

class TestSingleNodeSubgraph:

    def test_z_linkage_single_node_returns_empty_z_array(self) -> None:
        """A 1-node subgraph must return an empty z_array without raising."""
        nodes = [_u(42)]
        subgraph = _make_subgraph(nodes, [])

        result = build_z_linkage(subgraph)

        assert result["z_array"] == []
        assert result["labels"] == [str(_u(42))]

    def test_hierarchy_payload_single_node_is_leaf(self) -> None:
        """A 1-node subgraph must produce a single leaf node in the tree."""
        nodes = [_u(42)]
        subgraph = _make_subgraph(nodes, [])

        tree = build_hierarchy_payload(subgraph)

        assert tree["document_id"] == str(_u(42))
        assert "children" not in tree


# ---------------------------------------------------------------------------
# TestBuildHierarchyPayload
# ---------------------------------------------------------------------------

class TestBuildHierarchyPayload:

    def test_all_documents_appear_as_leaves(self) -> None:
        """
        Every document in the subgraph must appear exactly once as a leaf
        in the hierarchy payload tree.
        """
        n = 5
        nodes = [_u(i) for i in range(1, n + 1)]
        edges = [
            SparseEdge.make(_u(1), _u(2), 0.9),
            SparseEdge.make(_u(2), _u(3), 0.85),
            SparseEdge.make(_u(3), _u(4), 0.8),
            SparseEdge.make(_u(4), _u(5), 0.75),
        ]
        subgraph = _make_subgraph(nodes, edges)

        tree = build_hierarchy_payload(subgraph)

        leaf_ids = _all_leaf_doc_ids(tree)
        expected_ids = {str(node) for node in nodes}
        assert set(leaf_ids) == expected_ids

    def test_payload_is_json_serialisable(self) -> None:
        nodes = [_u(1), _u(2), _u(3)]
        edges = [
            SparseEdge.make(_u(1), _u(2), 0.9),
            SparseEdge.make(_u(1), _u(3), 0.7),
        ]
        subgraph = _make_subgraph(nodes, edges)

        tree = build_hierarchy_payload(subgraph)

        serialised = json.dumps(tree)
        assert isinstance(serialised, str)

    def test_payload_node_count_matches_subgraph(self) -> None:
        n = 4
        nodes = [_u(i) for i in range(1, n + 1)]
        edges = [
            SparseEdge.make(_u(1), _u(2), 0.9),
            SparseEdge.make(_u(2), _u(3), 0.8),
            SparseEdge.make(_u(3), _u(4), 0.7),
        ]
        subgraph = _make_subgraph(nodes, edges)

        tree = build_hierarchy_payload(subgraph)

        assert tree["node_count"] == n

    def test_raises_for_empty_subgraph(self) -> None:
        subgraph = _make_subgraph([], [])

        with pytest.raises(ValueError, match="no nodes"):
            build_hierarchy_payload(subgraph)

    def test_internal_nodes_have_children(self) -> None:
        """
        Internal nodes in a 3+ node tree must have a 'children' list with
        at least 2 elements.
        """
        nodes = [_u(1), _u(2), _u(3)]
        edges = [
            SparseEdge.make(_u(1), _u(2), 0.9),
            SparseEdge.make(_u(2), _u(3), 0.8),
        ]
        subgraph = _make_subgraph(nodes, edges)
        tree = build_hierarchy_payload(subgraph)

        def _all_internal_nodes_have_two_children(node: dict) -> bool:
            if "document_id" in node:
                return True
            children = node.get("children", [])
            if len(children) != 2:
                return False
            return all(_all_internal_nodes_have_two_children(c) for c in children)

        assert _all_internal_nodes_have_two_children(tree)

    def test_leaf_nodes_have_no_children(self) -> None:
        nodes = [_u(1), _u(2), _u(3)]
        edges = [
            SparseEdge.make(_u(1), _u(2), 0.9),
            SparseEdge.make(_u(1), _u(3), 0.8),
        ]
        subgraph = _make_subgraph(nodes, edges)
        tree = build_hierarchy_payload(subgraph)

        def _check_leaves(node: dict) -> None:
            if "document_id" in node:
                assert "children" not in node
            else:
                for child in node.get("children", []):
                    _check_leaves(child)

        _check_leaves(tree)


# ---------------------------------------------------------------------------
# TestCacheHierarchy
# ---------------------------------------------------------------------------

class TestCacheHierarchy:

    def test_cache_hierarchy_executes_insert(self) -> None:
        """
        cache_hierarchy must execute exactly one INSERT statement.
        """
        engine, conn = _make_engine([MagicMock()])
        version_id = _u(100)
        vault_id = _u(1)
        payload = {"z_array": [], "labels": ["a"], "method": "average"}

        cache_hierarchy(
            engine=engine,
            graph_version_id=version_id,
            vault_id=vault_id,
            cache_type="z_linkage",
            payload=payload,
        )

        assert conn.execute.call_count == 1
        executed_sql: str = conn.execute.call_args[0][0].text
        assert "INSERT INTO" in executed_sql
        assert "graph_hierarchy_cache" in executed_sql

    def test_cache_hierarchy_raises_for_non_serialisable_payload(self) -> None:
        """
        cache_hierarchy must raise TypeError before touching the database if
        the payload is not JSON-serialisable.
        """
        engine = MagicMock()
        non_serialisable = {"data": object()}

        with pytest.raises((TypeError, ValueError)):
            cache_hierarchy(
                engine=engine,
                graph_version_id=_u(1),
                vault_id=_u(2),
                cache_type="z_linkage",
                payload=non_serialisable,
            )

        # Engine must not have been touched.
        engine.connect.assert_not_called()

    def test_cache_hierarchy_with_document_ids(self) -> None:
        """
        cache_hierarchy passes document_ids as a list of strings.
        """
        engine, conn = _make_engine([MagicMock()])
        doc_ids = [_u(10), _u(11)]
        payload = {"node_count": 2}

        cache_hierarchy(
            engine=engine,
            graph_version_id=_u(100),
            vault_id=_u(1),
            cache_type="local_hierarchy_payload",
            payload=payload,
            document_ids=doc_ids,
        )

        params = conn.execute.call_args[0][1]
        assert params["document_ids"] == [str(_u(10)), str(_u(11))]

    def test_cache_hierarchy_with_expires_at(self) -> None:
        engine, conn = _make_engine([MagicMock()])
        expires = datetime(2030, 1, 1, tzinfo=timezone.utc)
        payload = {"z_array": []}

        cache_hierarchy(
            engine=engine,
            graph_version_id=_u(100),
            vault_id=_u(1),
            cache_type="z_linkage",
            payload=payload,
            expires_at=expires,
        )

        params = conn.execute.call_args[0][1]
        assert params["expires_at"] is not None


# ---------------------------------------------------------------------------
# TestLoadCachedHierarchy
# ---------------------------------------------------------------------------

class TestLoadCachedHierarchy:

    def test_load_returns_payload_when_no_expiry(self) -> None:
        """
        load_cached_hierarchy must return the payload dict when expires_at is NULL.
        """
        payload = {"z_array": [[0, 1, 0.3, 2]], "labels": ["a", "b"], "method": "average"}
        row = (json.dumps(payload), None)

        engine, conn = _make_engine([_fetchone_result(row)])

        result = load_cached_hierarchy(
            engine=engine,
            graph_version_id=_u(100),
            vault_id=_u(1),
            cache_type="z_linkage",
        )

        assert result == payload

    def test_load_returns_payload_when_not_expired(self) -> None:
        """
        A cache row with expires_at in the future must be returned.
        """
        payload = {"node_count": 3}
        future = datetime.now(tz=timezone.utc) + timedelta(hours=24)
        row = (json.dumps(payload), future)

        engine, conn = _make_engine([_fetchone_result(row)])

        result = load_cached_hierarchy(
            engine=engine,
            graph_version_id=_u(100),
            vault_id=_u(1),
            cache_type="local_hierarchy_payload",
        )

        assert result == payload

    def test_load_returns_none_when_expired(self) -> None:
        """
        A cache row with expires_at in the past must return None.
        """
        payload = {"node_count": 5}
        past = datetime.now(tz=timezone.utc) - timedelta(seconds=1)
        row = (json.dumps(payload), past)

        engine, conn = _make_engine([_fetchone_result(row)])

        result = load_cached_hierarchy(
            engine=engine,
            graph_version_id=_u(100),
            vault_id=_u(1),
            cache_type="z_linkage",
        )

        assert result is None

    def test_load_returns_none_when_no_cache_exists(self) -> None:
        engine, conn = _make_engine([_fetchone_result(None)])

        result = load_cached_hierarchy(
            engine=engine,
            graph_version_id=_u(100),
            vault_id=_u(1),
            cache_type="z_linkage",
        )

        assert result is None

    def test_load_queries_correct_table(self) -> None:
        engine, conn = _make_engine([_fetchone_result(None)])

        load_cached_hierarchy(
            engine=engine,
            graph_version_id=_u(100),
            vault_id=_u(1),
            cache_type="z_linkage",
        )

        executed_sql: str = conn.execute.call_args[0][0].text
        assert "graph_hierarchy_cache" in executed_sql

    def test_load_cache_roundtrip(self) -> None:
        """
        Simulate a full save + load cycle using in-memory state.

        cache_hierarchy writes to the mock; load_cached_hierarchy reads it back.
        We verify that the payload stored by cache_hierarchy is the same dict
        that load_cached_hierarchy returns.
        """
        original_payload = {
            "z_array": [[0.0, 1.0, 0.25, 2.0]],
            "labels": [str(_u(10)), str(_u(11))],
            "method": "average",
        }

        stored_json: list[str] = []

        # Write engine: capture what was inserted.
        write_engine = MagicMock()
        write_conn = MagicMock()
        write_engine.connect.return_value.__enter__ = MagicMock(return_value=write_conn)
        write_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        def _capture_insert(stmt, params):
            stored_json.append(params["payload"])
            return MagicMock()

        write_conn.execute.side_effect = _capture_insert

        cache_hierarchy(
            engine=write_engine,
            graph_version_id=_u(100),
            vault_id=_u(1),
            cache_type="z_linkage",
            payload=original_payload,
        )

        # Read engine: return what was stored.
        read_engine = MagicMock()
        read_conn = MagicMock()
        read_engine.connect.return_value.__enter__ = MagicMock(return_value=read_conn)
        read_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        stored_payload_json = stored_json[0]
        row = (stored_payload_json, None)
        read_result = MagicMock()
        read_result.fetchone.return_value = row
        read_conn.execute.return_value = read_result

        loaded = load_cached_hierarchy(
            engine=read_engine,
            graph_version_id=_u(100),
            vault_id=_u(1),
            cache_type="z_linkage",
        )

        assert loaded == original_payload


# ---------------------------------------------------------------------------
# TestInvalidateHierarchyCache
# ---------------------------------------------------------------------------

class TestInvalidateHierarchyCache:

    def test_invalidate_returns_deleted_count(self) -> None:
        """
        invalidate_hierarchy_cache must return the number of rows deleted.
        """
        engine, conn = _make_engine([_fetchone_result((3,))])

        count = invalidate_hierarchy_cache(
            engine=engine,
            graph_version_id=_u(100),
            vault_id=_u(1),
        )

        assert count == 3

    def test_invalidate_returns_zero_when_no_cache_exists(self) -> None:
        engine, conn = _make_engine([_fetchone_result((0,))])

        count = invalidate_hierarchy_cache(
            engine=engine,
            graph_version_id=_u(100),
            vault_id=_u(1),
        )

        assert count == 0

    def test_invalidate_without_vault_id_deletes_all_for_version(self) -> None:
        """
        When vault_id is None, the SQL must omit the vault_id filter so all
        cache rows for the graph version are deleted.
        """
        engine, conn = _make_engine([_fetchone_result((5,))])

        count = invalidate_hierarchy_cache(
            engine=engine,
            graph_version_id=_u(100),
            vault_id=None,
        )

        assert count == 5
        executed_sql: str = conn.execute.call_args[0][0].text
        assert "vault_id" not in executed_sql

    def test_invalidate_with_vault_id_includes_vault_filter(self) -> None:
        """
        When vault_id is provided, the SQL must include the vault_id filter.
        """
        engine, conn = _make_engine([_fetchone_result((2,))])

        invalidate_hierarchy_cache(
            engine=engine,
            graph_version_id=_u(100),
            vault_id=_u(1),
        )

        executed_sql: str = conn.execute.call_args[0][0].text
        assert "vault_id" in executed_sql

    def test_invalidate_only_deletes_hierarchy_cache_table(self) -> None:
        """
        invalidate_hierarchy_cache must only touch graph.graph_hierarchy_cache.
        It must never touch graph.vault_graph_edge or any other canonical table.
        """
        engine, conn = _make_engine([_fetchone_result((1,))])

        invalidate_hierarchy_cache(
            engine=engine,
            graph_version_id=_u(100),
            vault_id=_u(1),
        )

        executed_sql: str = conn.execute.call_args[0][0].text
        assert "graph_hierarchy_cache" in executed_sql
        assert "vault_graph_edge" not in executed_sql

    def test_invalidate_commits_transaction(self) -> None:
        """
        invalidate_hierarchy_cache must commit after the DELETE.
        """
        engine, conn = _make_engine([_fetchone_result((1,))])

        invalidate_hierarchy_cache(
            engine=engine,
            graph_version_id=_u(100),
            vault_id=_u(1),
        )

        conn.commit.assert_called_once()
