"""
graph_engine.labels — label generation and caching subsystem.

Exposes:
    LabelRepository  — CRUD for graph.graph_label rows.
    LabelService     — extractive label generation and payload attachment.
"""

from graph_engine.labels.cache import LabelRepository
from graph_engine.labels.label_service import LabelService

__all__ = ["LabelRepository", "LabelService"]
