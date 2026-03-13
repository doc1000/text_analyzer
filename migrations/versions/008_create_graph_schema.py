"""
Migration 008 — Create graph schema and graph.graph_config table.

Phase 1 of the graph-engine implementation plan.

Run with:
    python migrations/versions/008_create_graph_schema.py

or via Alembic once Alembic is configured for this repository.
"""

import os

from sqlalchemy import create_engine, text


UP_STATEMENTS = [
    "CREATE SCHEMA IF NOT EXISTS graph;",
    """
    CREATE TABLE IF NOT EXISTS graph.graph_config (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        name TEXT NOT NULL,
        description TEXT,
        embedding_source TEXT NOT NULL,
        summary_embedding_source TEXT,
        feature_weights JSONB NOT NULL DEFAULT '{}'::jsonb,
        knn_k INTEGER NOT NULL,
        candidate_k INTEGER NOT NULL,
        pruning_mode TEXT NOT NULL DEFAULT 'none',
        bridge_knn_k INTEGER,
        is_active BOOLEAN NOT NULL DEFAULT true,
        created_by UUID REFERENCES public.users(id),
        created_at TIMESTAMP NOT NULL DEFAULT now(),
        updated_at TIMESTAMP NOT NULL DEFAULT now()
    );
    """,
]

DOWN_STATEMENTS = [
    "DROP TABLE IF EXISTS graph.graph_config;",
    "DROP SCHEMA IF EXISTS graph;",
]


def upgrade(engine) -> None:
    with engine.connect() as conn:
        for stmt in UP_STATEMENTS:
            conn.execute(text(stmt))
            conn.commit()
    print("008 upgrade: graph schema and graph.graph_config created.")


def downgrade(engine) -> None:
    with engine.connect() as conn:
        for stmt in DOWN_STATEMENTS:
            conn.execute(text(stmt))
            conn.commit()
    print("008 downgrade: graph.graph_config and graph schema dropped.")


if __name__ == "__main__":
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is not set.")
    eng = create_engine(database_url)
    upgrade(eng)
