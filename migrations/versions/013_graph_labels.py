"""
Migration 013 — Graph label cache: graph.graph_label.

Phase 9 of the graph-engine implementation plan.

New table:
    graph.graph_label — stores human-readable labels for graph groups,
                        keyed by (graph_version_id, group_key).

Design rules:
    - Labels are derived artifacts. They are never canonical graph state
      and may be regenerated or deleted at any time.
    - The unique constraint on (graph_version_id, group_key) ensures
      at most one label per group per graph version.
    - user_override = true indicates a manually set label; overridden_by
      records the user who made the override.
    - method defaults to 'extractive'; future methods (e.g. 'llm') would
      use a different value without schema changes.
    - confidence is nullable; extractive labels may omit it.

Run with:
    python migrations/versions/013_graph_labels.py

or via Alembic once Alembic is configured for this repository.
"""

import os

from sqlalchemy import create_engine, text


UP_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS graph.graph_label (
        id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        graph_version_id  UUID NOT NULL REFERENCES graph.vault_graph_version(id)
                          ON DELETE CASCADE,
        vault_id          UUID NOT NULL REFERENCES public.vaults(id)
                          ON DELETE CASCADE,
        group_key         TEXT NOT NULL,
        label             TEXT NOT NULL,
        method            TEXT NOT NULL DEFAULT 'extractive',
        confidence        DOUBLE PRECISION,
        user_override     BOOLEAN NOT NULL DEFAULT false,
        overridden_by     UUID REFERENCES public.users(id),
        created_at        TIMESTAMP NOT NULL DEFAULT now(),
        UNIQUE (graph_version_id, group_key)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_graph_label_version
        ON graph.graph_label (graph_version_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_graph_label_vault_version
        ON graph.graph_label (vault_id, graph_version_id);
    """,
]

DOWN_STATEMENTS = [
    "DROP TABLE IF EXISTS graph.graph_label;",
]


def upgrade(engine) -> None:
    with engine.connect() as conn:
        for stmt in UP_STATEMENTS:
            conn.execute(text(stmt))
            conn.commit()
    print("013 upgrade: graph.graph_label created with indexes.")


def downgrade(engine) -> None:
    with engine.connect() as conn:
        for stmt in DOWN_STATEMENTS:
            conn.execute(text(stmt))
            conn.commit()
    print("013 downgrade: graph.graph_label dropped.")


if __name__ == "__main__":
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is not set.")
    eng = create_engine(database_url)
    upgrade(eng)
