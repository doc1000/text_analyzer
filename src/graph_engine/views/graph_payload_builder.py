"""
graph_payload_builder — core payload dataclasses and build_payload().

Converts a SubgraphResult (from Phase 3 SubgraphService) plus a
document_metadata dict into a GraphViewPayload ready for serialization.

Design constraints:
- No database access.
- No graph mutation.
- No layout computation.
- All fields must be JSON-serializable (UUIDs converted to str).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from graph_engine.graph_core.subgraph import SubgraphResult


@dataclass
class GraphNodePayload:
    """
    UI-facing representation of a single graph node.

    id and vault_id are string UUIDs so the payload is directly
    JSON-serializable without a custom encoder.
    """

    id: str
    title: str
    url: str | None
    vault_id: str
    metadata: dict


@dataclass
class GraphEdgePayload:
    """
    UI-facing representation of a single graph edge.

    source and target are string UUIDs matching GraphNodePayload.id.
    """

    source: str
    target: str
    weight: float
    edge_type: str


@dataclass
class GraphViewPayload:
    """
    Container for a complete graph view ready for UI consumption.

    node_count and edge_count are kept as explicit fields so callers
    do not need to recompute len() on the lists.
    """

    nodes: list[GraphNodePayload]
    edges: list[GraphEdgePayload]
    node_count: int
    edge_count: int
    metadata: dict = field(default_factory=dict)


def build_payload(
    subgraph_result: SubgraphResult,
    document_metadata: dict[UUID, dict],
) -> GraphViewPayload:
    """
    Convert a SubgraphResult into a GraphViewPayload.

    Args:
        subgraph_result:   The extracted subgraph from SubgraphService.
        document_metadata: Mapping from document UUID to a dict containing
                           at minimum 'title', 'url', and 'vault_id'.
                           Unknown nodes receive placeholder values.

    Returns:
        A GraphViewPayload with all nodes and edges mapped to their
        UI-facing payload types.
    """
    nodes: list[GraphNodePayload] = []
    for node_uuid in subgraph_result.nodes:
        meta = document_metadata.get(node_uuid, {})
        nodes.append(
            GraphNodePayload(
                id=str(node_uuid),
                title=str(meta.get("title", "")),
                url=meta.get("url") or None,
                vault_id=str(meta.get("vault_id", str(subgraph_result.vault_id))),
                metadata={
                    k: v
                    for k, v in meta.items()
                    if k not in ("title", "url", "vault_id")
                },
            )
        )

    edges: list[GraphEdgePayload] = []
    for sparse_edge in subgraph_result.edges:
        edges.append(
            GraphEdgePayload(
                source=str(sparse_edge.doc_lo),
                target=str(sparse_edge.doc_hi),
                weight=sparse_edge.weight,
                edge_type=getattr(sparse_edge, "edge_type", "internal"),
            )
        )

    view_metadata: dict = {
        "graph_version_id": str(subgraph_result.graph_version_id),
        "vault_id": str(subgraph_result.vault_id),
    }

    return GraphViewPayload(
        nodes=nodes,
        edges=edges,
        node_count=len(nodes),
        edge_count=len(edges),
        metadata=view_metadata,
    )
