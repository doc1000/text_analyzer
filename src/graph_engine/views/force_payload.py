"""
force_payload — D3-compatible JSON payload builders.

Converts a GraphViewPayload into two output formats:

1. build_force_payload():
   Produces a D3 force-simulation-compatible JSON structure:
   {
     "nodes": [...],
     "links": [...],
     "metadata": {...}
   }
   Links use 'value' for the edge weight (D3 convention).

2. build_grouped_payload():
   Extends the force payload by annotating each node with a
   'group' field derived from a caller-supplied grouping map.
   Nodes not in any group receive group=null.

Design constraints:
- No database access.
- No graph mutation.
- No layout computation (client-side responsibility).
- Output must be JSON-serializable with the standard json module.
"""

from __future__ import annotations

from graph_engine.views.graph_payload_builder import GraphViewPayload


def build_force_payload(graph_view_payload: GraphViewPayload) -> dict:
    """
    Convert a GraphViewPayload into a D3 force-simulation-compatible dict.

    Node dicts include all fields from GraphNodePayload plus a 'group'
    field set to null (no grouping applied at this stage).

    Link dicts use 'value' for edge weight, following D3 conventions.
    Additional edge metadata (edge_type) is preserved for UI use.

    Args:
        graph_view_payload: A fully populated GraphViewPayload.

    Returns:
        A JSON-serializable dict with keys 'nodes', 'links', 'metadata'.
    """
    nodes = [
        {
            "id": node.id,
            "title": node.title,
            "url": node.url,
            "vault_id": node.vault_id,
            "group": None,
            **node.metadata,
        }
        for node in graph_view_payload.nodes
    ]

    links = [
        {
            "source": edge.source,
            "target": edge.target,
            "value": edge.weight,
            "edge_type": edge.edge_type,
        }
        for edge in graph_view_payload.edges
    ]

    metadata = {
        "node_count": graph_view_payload.node_count,
        "edge_count": graph_view_payload.edge_count,
        **graph_view_payload.metadata,
    }

    return {
        "nodes": nodes,
        "links": links,
        "metadata": metadata,
    }


def build_grouped_payload(
    graph_view_payload: GraphViewPayload,
    groups: dict[str, list[str]],
) -> dict:
    """
    Build a force payload with nodes annotated by group membership.

    Args:
        graph_view_payload: A fully populated GraphViewPayload.
        groups: Mapping from group label (str) to list of node IDs (str UUIDs).
                A node may appear in at most one group; if a node ID appears
                in multiple groups, the last encountered group wins.
                Nodes not listed in any group receive group=null.

    Returns:
        A JSON-serializable dict with keys 'nodes', 'links', 'metadata',
        and 'groups'. The 'groups' key maps each label to its member count.
    """
    node_to_group: dict[str, str] = {}
    for label, node_ids in groups.items():
        for node_id in node_ids:
            node_to_group[node_id] = label

    nodes = [
        {
            "id": node.id,
            "title": node.title,
            "url": node.url,
            "vault_id": node.vault_id,
            "group": node_to_group.get(node.id, None),
            **node.metadata,
        }
        for node in graph_view_payload.nodes
    ]

    links = [
        {
            "source": edge.source,
            "target": edge.target,
            "value": edge.weight,
            "edge_type": edge.edge_type,
        }
        for edge in graph_view_payload.edges
    ]

    group_summary = {
        label: len(member_ids) for label, member_ids in groups.items()
    }

    metadata = {
        "node_count": graph_view_payload.node_count,
        "edge_count": graph_view_payload.edge_count,
        **graph_view_payload.metadata,
    }

    return {
        "nodes": nodes,
        "links": links,
        "metadata": metadata,
        "groups": group_summary,
    }
