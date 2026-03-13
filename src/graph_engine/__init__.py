"""
graph_engine — vault-aware semantic graph subsystem.

Phase 1: graph foundations (in-memory kNN graph construction, graph_config persistence).
"""

from graph_engine.graph_core.builder import GraphBuilder
from graph_engine.graph_core.types import SparseEdge, SparseGraph

__all__ = [
    "GraphBuilder",
    "SparseEdge",
    "SparseGraph",
]
