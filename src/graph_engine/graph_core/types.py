"""
Core type definitions for the graph-engine sparse graph model.

SparseEdge and SparseGraph are the canonical in-memory representations.
They are never persisted as dense structures; only individual edges are
written to graph.vault_graph_edge (Phase 2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID


@dataclass
class SparseEdge:
    """
    A single undirected edge in the sparse semantic graph.

    Canonical ordering is enforced: doc_lo < doc_hi (lexicographic UUID comparison).
    This ensures each undirected pair is represented exactly once.
    """

    doc_lo: UUID
    doc_hi: UUID
    weight: float
    distance: float | None = None
    rank_lo: int | None = None
    rank_hi: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.doc_lo, UUID):
            self.doc_lo = UUID(str(self.doc_lo))
        if not isinstance(self.doc_hi, UUID):
            self.doc_hi = UUID(str(self.doc_hi))
        if self.doc_lo == self.doc_hi:
            raise ValueError(
                f"SparseEdge self-loop is not allowed: doc_lo == doc_hi == {self.doc_lo}"
            )
        if self.doc_lo > self.doc_hi:
            raise ValueError(
                f"SparseEdge canonical ordering violated: doc_lo ({self.doc_lo}) "
                f"must be < doc_hi ({self.doc_hi}). "
                "Use SparseEdge.make() to create edges with automatic ordering."
            )

    @classmethod
    def make(
        cls,
        doc_a: UUID,
        doc_b: UUID,
        weight: float,
        distance: float | None = None,
        rank_a: int | None = None,
        rank_b: int | None = None,
    ) -> "SparseEdge":
        """
        Factory that enforces canonical ordering automatically.

        rank_a and rank_b are assigned to rank_lo / rank_hi after the ordering
        swap, so they always correspond to doc_lo and doc_hi respectively.
        """
        if doc_a == doc_b:
            raise ValueError(f"Self-loop not allowed: doc_a == doc_b == {doc_a}")

        if not isinstance(doc_a, UUID):
            doc_a = UUID(str(doc_a))
        if not isinstance(doc_b, UUID):
            doc_b = UUID(str(doc_b))

        if doc_a < doc_b:
            return cls(
                doc_lo=doc_a,
                doc_hi=doc_b,
                weight=weight,
                distance=distance,
                rank_lo=rank_a,
                rank_hi=rank_b,
            )
        else:
            return cls(
                doc_lo=doc_b,
                doc_hi=doc_a,
                weight=weight,
                distance=distance,
                rank_lo=rank_b,
                rank_hi=rank_a,
            )


@dataclass
class SparseGraph:
    """
    In-memory sparse graph for a vault.

    nodes: all document UUIDs included in this graph build.
    edges: deduplicated undirected sparse edges (each pair stored once).
    vault_id: the vault this graph belongs to.
    config_id: the graph_config row used to build this graph.
    """

    nodes: list[UUID]
    edges: list[SparseEdge]
    vault_id: UUID
    config_id: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.vault_id, UUID):
            self.vault_id = UUID(str(self.vault_id))
        if not isinstance(self.config_id, UUID):
            self.config_id = UUID(str(self.config_id))
