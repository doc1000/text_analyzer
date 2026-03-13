"""
DB model and accessors for graph.graph_config.

Uses SQLAlchemy Core (no ORM) against the shared PostgreSQL database.
The engine must be provided externally — this module does not manage
connection lifecycle or environment variables.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine


@dataclass
class GraphConfigRow:
    """Mirrors graph.graph_config columns exactly."""

    id: UUID
    name: str
    embedding_source: str
    knn_k: int
    candidate_k: int
    pruning_mode: str
    feature_weights: dict[str, Any]
    is_active: bool
    description: str | None = None
    summary_embedding_source: str | None = None
    bridge_knn_k: int | None = None
    created_by: UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_row(cls, row: Any) -> "GraphConfigRow":
        """Construct from a SQLAlchemy Row (mapping-style access)."""
        return cls(
            id=UUID(str(row.id)),
            name=row.name,
            description=row.description,
            embedding_source=row.embedding_source,
            summary_embedding_source=row.summary_embedding_source,
            feature_weights=row.feature_weights or {},
            knn_k=row.knn_k,
            candidate_k=row.candidate_k,
            pruning_mode=row.pruning_mode,
            bridge_knn_k=row.bridge_knn_k,
            is_active=row.is_active,
            created_by=UUID(str(row.created_by)) if row.created_by else None,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


def get_graph_config(engine: Engine, config_id: UUID) -> GraphConfigRow:
    """
    Fetch a single graph_config row by primary key.

    Raises KeyError if the config does not exist.
    """
    sql = text(
        "SELECT * FROM graph.graph_config WHERE id = :config_id"
    )
    with engine.connect() as conn:
        row = conn.execute(sql, {"config_id": str(config_id)}).mappings().fetchone()
    if row is None:
        raise KeyError(f"graph_config not found: {config_id}")
    return GraphConfigRow.from_row(row)


def get_active_graph_config(engine: Engine) -> GraphConfigRow | None:
    """
    Return the first active graph_config row, or None if none exists.
    """
    sql = text(
        "SELECT * FROM graph.graph_config WHERE is_active = true ORDER BY created_at ASC LIMIT 1"
    )
    with engine.connect() as conn:
        row = conn.execute(sql).mappings().fetchone()
    if row is None:
        return None
    return GraphConfigRow.from_row(row)


def create_graph_config(
    engine: Engine,
    name: str,
    embedding_source: str,
    knn_k: int,
    candidate_k: int,
    description: str | None = None,
    summary_embedding_source: str | None = None,
    feature_weights: dict[str, Any] | None = None,
    pruning_mode: str = "none",
    bridge_knn_k: int | None = None,
    is_active: bool = True,
    created_by: UUID | None = None,
) -> GraphConfigRow:
    """
    Insert a new graph_config row and return the created record.
    """
    import json

    sql = text(
        """
        INSERT INTO graph.graph_config (
            name,
            description,
            embedding_source,
            summary_embedding_source,
            feature_weights,
            knn_k,
            candidate_k,
            pruning_mode,
            bridge_knn_k,
            is_active,
            created_by
        ) VALUES (
            :name,
            :description,
            :embedding_source,
            :summary_embedding_source,
            :feature_weights,
            :knn_k,
            :candidate_k,
            :pruning_mode,
            :bridge_knn_k,
            :is_active,
            :created_by
        )
        RETURNING *
        """
    )
    params: dict[str, Any] = {
        "name": name,
        "description": description,
        "embedding_source": embedding_source,
        "summary_embedding_source": summary_embedding_source,
        "feature_weights": json.dumps(feature_weights or {}),
        "knn_k": knn_k,
        "candidate_k": candidate_k,
        "pruning_mode": pruning_mode,
        "bridge_knn_k": bridge_knn_k,
        "is_active": is_active,
        "created_by": str(created_by) if created_by else None,
    }
    with engine.connect() as conn:
        row = conn.execute(sql, params).mappings().fetchone()
        conn.commit()
    if row is None:
        raise RuntimeError("INSERT INTO graph.graph_config returned no row.")
    return GraphConfigRow.from_row(row)
