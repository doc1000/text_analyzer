"""
Unit tests for Phase 5 — Graph Views for UI.

Tests cover:
- GraphNodePayload / GraphEdgePayload / GraphViewPayload construction.
- build_payload(): node/edge counts, field mapping, metadata fields.
- build_force_payload(): node/link counts, endpoint validity, serialisation.
- build_grouped_payload(): group annotation, null for ungrouped nodes,
  group summary counts.
- Empty subgraph produces a valid, serialisable empty payload.
- All payloads are JSON-serialisable (json.dumps must not raise).

No database connection is required; all inputs are constructed in memory.
"""

from __future__ import annotations

import json
from uuid import UUID

import pytest

from graph_engine.graph_core.subgraph import SubgraphResult
from graph_engine.graph_core.types import SparseEdge
from graph_engine.views.graph_payload_builder import (
    GraphEdgePayload,
    GraphNodePayload,
    GraphViewPayload,
    build_payload,
)
from graph_engine.views.force_payload import build_force_payload, build_grouped_payload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uuid(n: int) -> UUID:
    """Generate a deterministic UUID from an integer."""
    return UUID(int=n)


def _make_subgraph(
    node_count: int = 4,
    edges: list[SparseEdge] | None = None,
    vault_id: UUID | None = None,
    graph_version_id: UUID | None = None,
) -> SubgraphResult:
    """
    Build a synthetic SubgraphResult for testing.

    Nodes are _uuid(0) .. _uuid(node_count - 1).
    If edges is None, two default edges are created (when node_count >= 2).
    """
    vault_id = vault_id or _uuid(100)
    graph_version_id = graph_version_id or _uuid(999)
    nodes = [_uuid(i) for i in range(node_count)]

    if edges is None and node_count >= 2:
        edges = [
            SparseEdge.make(_uuid(0), _uuid(1), weight=0.9),
            SparseEdge.make(_uuid(1), _uuid(2), weight=0.7) if node_count >= 3 else
            SparseEdge.make(_uuid(0), _uuid(1), weight=0.9),
        ]
        # deduplicate if only 2 nodes
        edges = list({(e.doc_lo, e.doc_hi): e for e in edges}.values())
    elif edges is None:
        edges = []

    return SubgraphResult(
        nodes=nodes,
        edges=edges,
        graph_version_id=graph_version_id,
        vault_id=vault_id,
        node_count=len(nodes),
        edge_count=len(edges),
    )


def _make_metadata(nodes: list[UUID], vault_id: UUID) -> dict[UUID, dict]:
    return {
        node: {
            "title": f"Document {i}",
            "url": f"https://example.com/doc/{i}",
            "vault_id": vault_id,
        }
        for i, node in enumerate(nodes)
    }


# ---------------------------------------------------------------------------
# GraphNodePayload / GraphEdgePayload / GraphViewPayload construction
# ---------------------------------------------------------------------------

class TestPayloadDataclasses:

    def test_graph_node_payload_fields(self) -> None:
        node = GraphNodePayload(
            id="abc",
            title="My Doc",
            url="https://example.com",
            vault_id="vault-1",
            metadata={"custom": "value"},
        )
        assert node.id == "abc"
        assert node.title == "My Doc"
        assert node.url == "https://example.com"
        assert node.vault_id == "vault-1"
        assert node.metadata == {"custom": "value"}

    def test_graph_node_payload_url_none(self) -> None:
        node = GraphNodePayload(
            id="abc",
            title="No URL",
            url=None,
            vault_id="vault-1",
            metadata={},
        )
        assert node.url is None

    def test_graph_edge_payload_fields(self) -> None:
        edge = GraphEdgePayload(
            source="a",
            target="b",
            weight=0.75,
            edge_type="internal",
        )
        assert edge.source == "a"
        assert edge.target == "b"
        assert edge.weight == pytest.approx(0.75)
        assert edge.edge_type == "internal"

    def test_graph_view_payload_counts(self) -> None:
        nodes = [
            GraphNodePayload(id="a", title="A", url=None, vault_id="v", metadata={}),
            GraphNodePayload(id="b", title="B", url=None, vault_id="v", metadata={}),
        ]
        edges = [
            GraphEdgePayload(source="a", target="b", weight=0.8, edge_type="internal"),
        ]
        payload = GraphViewPayload(
            nodes=nodes,
            edges=edges,
            node_count=2,
            edge_count=1,
        )
        assert payload.node_count == 2
        assert payload.edge_count == 1
        assert len(payload.nodes) == 2
        assert len(payload.edges) == 1

    def test_graph_view_payload_metadata_default_empty(self) -> None:
        payload = GraphViewPayload(nodes=[], edges=[], node_count=0, edge_count=0)
        assert payload.metadata == {}


# ---------------------------------------------------------------------------
# build_payload()
# ---------------------------------------------------------------------------

class TestBuildPayload:

    def test_node_count_preserved(self) -> None:
        subgraph = _make_subgraph(node_count=5)
        meta = _make_metadata(subgraph.nodes, subgraph.vault_id)
        result = build_payload(subgraph, meta)
        assert result.node_count == 5
        assert len(result.nodes) == 5

    def test_edge_count_preserved(self) -> None:
        subgraph = _make_subgraph(node_count=4)
        meta = _make_metadata(subgraph.nodes, subgraph.vault_id)
        result = build_payload(subgraph, meta)
        assert result.edge_count == len(subgraph.edges)
        assert len(result.edges) == len(subgraph.edges)

    def test_node_ids_are_string_uuids(self) -> None:
        subgraph = _make_subgraph(node_count=3)
        meta = _make_metadata(subgraph.nodes, subgraph.vault_id)
        result = build_payload(subgraph, meta)
        for node in result.nodes:
            # Must be parseable as a UUID.
            UUID(node.id)

    def test_edge_endpoints_are_string_uuids(self) -> None:
        subgraph = _make_subgraph(node_count=3)
        meta = _make_metadata(subgraph.nodes, subgraph.vault_id)
        result = build_payload(subgraph, meta)
        for edge in result.edges:
            UUID(edge.source)
            UUID(edge.target)

    def test_title_mapped_from_metadata(self) -> None:
        subgraph = _make_subgraph(node_count=2)
        meta = _make_metadata(subgraph.nodes, subgraph.vault_id)
        result = build_payload(subgraph, meta)
        for node in result.nodes:
            assert node.title.startswith("Document")

    def test_url_mapped_from_metadata(self) -> None:
        subgraph = _make_subgraph(node_count=2)
        meta = _make_metadata(subgraph.nodes, subgraph.vault_id)
        result = build_payload(subgraph, meta)
        for node in result.nodes:
            assert node.url is not None
            assert "example.com" in node.url

    def test_missing_metadata_node_gets_defaults(self) -> None:
        """Nodes absent from document_metadata receive empty/placeholder values."""
        subgraph = _make_subgraph(node_count=2)
        result = build_payload(subgraph, document_metadata={})
        for node in result.nodes:
            assert node.title == ""
            assert node.url is None

    def test_vault_id_in_metadata_output(self) -> None:
        vault_id = _uuid(100)
        graph_version_id = _uuid(999)
        subgraph = _make_subgraph(
            node_count=2, vault_id=vault_id, graph_version_id=graph_version_id
        )
        meta = _make_metadata(subgraph.nodes, vault_id)
        result = build_payload(subgraph, meta)
        assert result.metadata.get("vault_id") == str(vault_id)
        assert result.metadata.get("graph_version_id") == str(graph_version_id)

    def test_edge_weight_preserved(self) -> None:
        subgraph = _make_subgraph(node_count=3)
        meta = _make_metadata(subgraph.nodes, subgraph.vault_id)
        result = build_payload(subgraph, meta)
        original_weights = {
            (str(e.doc_lo), str(e.doc_hi)): e.weight for e in subgraph.edges
        }
        for edge in result.edges:
            key = (edge.source, edge.target)
            assert edge.weight == pytest.approx(original_weights[key])


# ---------------------------------------------------------------------------
# build_force_payload()
# ---------------------------------------------------------------------------

class TestBuildForcePayload:

    def _payload(self, node_count: int = 4) -> GraphViewPayload:
        subgraph = _make_subgraph(node_count=node_count)
        meta = _make_metadata(subgraph.nodes, subgraph.vault_id)
        return build_payload(subgraph, meta)

    def test_nodes_count_matches(self) -> None:
        vp = self._payload(4)
        force = build_force_payload(vp)
        assert len(force["nodes"]) == vp.node_count

    def test_links_count_matches(self) -> None:
        vp = self._payload(4)
        force = build_force_payload(vp)
        assert len(force["links"]) == vp.edge_count

    def test_all_link_endpoints_are_valid_node_ids(self) -> None:
        vp = self._payload(4)
        force = build_force_payload(vp)
        node_ids = {n["id"] for n in force["nodes"]}
        for link in force["links"]:
            assert link["source"] in node_ids, (
                f"Link source {link['source']} not in node ids"
            )
            assert link["target"] in node_ids, (
                f"Link target {link['target']} not in node ids"
            )

    def test_links_use_value_field_for_weight(self) -> None:
        vp = self._payload(3)
        force = build_force_payload(vp)
        for link in force["links"]:
            assert "value" in link
            assert isinstance(link["value"], float)

    def test_nodes_have_group_null(self) -> None:
        """build_force_payload sets group=null on all nodes (no grouping applied)."""
        vp = self._payload(3)
        force = build_force_payload(vp)
        for node in force["nodes"]:
            assert node["group"] is None

    def test_metadata_keys_present(self) -> None:
        vp = self._payload(3)
        force = build_force_payload(vp)
        assert "node_count" in force["metadata"]
        assert "edge_count" in force["metadata"]

    def test_metadata_counts_correct(self) -> None:
        vp = self._payload(4)
        force = build_force_payload(vp)
        assert force["metadata"]["node_count"] == vp.node_count
        assert force["metadata"]["edge_count"] == vp.edge_count

    def test_payload_is_json_serializable(self) -> None:
        vp = self._payload(4)
        force = build_force_payload(vp)
        serialized = json.dumps(force)
        assert isinstance(serialized, str)

    def test_edge_type_present_in_links(self) -> None:
        vp = self._payload(4)
        force = build_force_payload(vp)
        for link in force["links"]:
            assert "edge_type" in link


# ---------------------------------------------------------------------------
# build_grouped_payload()
# ---------------------------------------------------------------------------

class TestBuildGroupedPayload:

    def _payload(self, node_count: int = 5) -> tuple[GraphViewPayload, list[str]]:
        subgraph = _make_subgraph(node_count=node_count)
        meta = _make_metadata(subgraph.nodes, subgraph.vault_id)
        vp = build_payload(subgraph, meta)
        node_ids = [n.id for n in vp.nodes]
        return vp, node_ids

    def test_group_label_assigned_correctly(self) -> None:
        vp, node_ids = self._payload(5)
        groups = {"alpha": [node_ids[0], node_ids[1]], "beta": [node_ids[2]]}
        result = build_grouped_payload(vp, groups)
        by_id = {n["id"]: n for n in result["nodes"]}
        assert by_id[node_ids[0]]["group"] == "alpha"
        assert by_id[node_ids[1]]["group"] == "alpha"
        assert by_id[node_ids[2]]["group"] == "beta"

    def test_ungrouped_nodes_get_null(self) -> None:
        vp, node_ids = self._payload(5)
        groups = {"alpha": [node_ids[0]]}
        result = build_grouped_payload(vp, groups)
        by_id = {n["id"]: n for n in result["nodes"]}
        for nid in node_ids[1:]:
            assert by_id[nid]["group"] is None

    def test_all_nodes_present_in_output(self) -> None:
        vp, node_ids = self._payload(5)
        groups = {"g1": node_ids[:3]}
        result = build_grouped_payload(vp, groups)
        assert len(result["nodes"]) == len(node_ids)

    def test_groups_summary_key_present(self) -> None:
        vp, node_ids = self._payload(5)
        groups = {"alpha": node_ids[:2], "beta": node_ids[2:4]}
        result = build_grouped_payload(vp, groups)
        assert "groups" in result
        assert result["groups"]["alpha"] == 2
        assert result["groups"]["beta"] == 2

    def test_groups_summary_counts_correct(self) -> None:
        vp, node_ids = self._payload(5)
        groups = {"g1": node_ids[:1], "g2": node_ids[1:3], "g3": node_ids[3:5]}
        result = build_grouped_payload(vp, groups)
        assert result["groups"]["g1"] == 1
        assert result["groups"]["g2"] == 2
        assert result["groups"]["g3"] == 2

    def test_empty_groups_dict_all_null(self) -> None:
        vp, node_ids = self._payload(3)
        result = build_grouped_payload(vp, {})
        for node in result["nodes"]:
            assert node["group"] is None

    def test_link_endpoints_valid_in_grouped_payload(self) -> None:
        vp, node_ids = self._payload(4)
        groups = {"alpha": node_ids[:2]}
        result = build_grouped_payload(vp, groups)
        node_id_set = {n["id"] for n in result["nodes"]}
        for link in result["links"]:
            assert link["source"] in node_id_set
            assert link["target"] in node_id_set

    def test_grouped_payload_is_json_serializable(self) -> None:
        vp, node_ids = self._payload(4)
        groups = {"alpha": node_ids[:2]}
        result = build_grouped_payload(vp, groups)
        serialized = json.dumps(result)
        assert isinstance(serialized, str)

    def test_links_count_unchanged(self) -> None:
        vp, node_ids = self._payload(4)
        groups = {"alpha": node_ids[:2]}
        result = build_grouped_payload(vp, groups)
        assert len(result["links"]) == vp.edge_count


# ---------------------------------------------------------------------------
# Empty subgraph edge cases
# ---------------------------------------------------------------------------

class TestEmptySubgraph:

    def _empty_subgraph(self) -> SubgraphResult:
        return SubgraphResult(
            nodes=[],
            edges=[],
            graph_version_id=_uuid(999),
            vault_id=_uuid(100),
            node_count=0,
            edge_count=0,
        )

    def test_build_payload_empty(self) -> None:
        result = build_payload(self._empty_subgraph(), {})
        assert result.node_count == 0
        assert result.edge_count == 0
        assert result.nodes == []
        assert result.edges == []

    def test_build_force_payload_empty_structure(self) -> None:
        vp = build_payload(self._empty_subgraph(), {})
        force = build_force_payload(vp)
        assert force["nodes"] == []
        assert force["links"] == []
        assert isinstance(force["metadata"], dict)

    def test_build_force_payload_empty_is_json_serializable(self) -> None:
        vp = build_payload(self._empty_subgraph(), {})
        force = build_force_payload(vp)
        serialized = json.dumps(force)
        assert isinstance(serialized, str)

    def test_build_grouped_payload_empty(self) -> None:
        vp = build_payload(self._empty_subgraph(), {})
        result = build_grouped_payload(vp, {"grp": []})
        assert result["nodes"] == []
        assert result["links"] == []
        assert result["groups"]["grp"] == 0

    def test_build_grouped_payload_empty_is_json_serializable(self) -> None:
        vp = build_payload(self._empty_subgraph(), {})
        result = build_grouped_payload(vp, {})
        serialized = json.dumps(result)
        assert isinstance(serialized, str)

    def test_metadata_counts_zero_in_empty_payload(self) -> None:
        vp = build_payload(self._empty_subgraph(), {})
        force = build_force_payload(vp)
        assert force["metadata"]["node_count"] == 0
        assert force["metadata"]["edge_count"] == 0
