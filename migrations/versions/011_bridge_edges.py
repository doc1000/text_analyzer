"""
Migration 011 — Bridge edges table: graph.bridge_graph_edge.

Phase 6 of the graph-engine implementation plan.

New table:
    graph.bridge_graph_edge  — persists cross-vault bridge edges computed by
                               GraphCompositionService.build_bridges().

Indexes:
    idx_bridge_graph_edge_source_vault  (source_vault_id)
    idx_bridge_graph_edge_target_vault  (target_vault_id)
    idx_bridge_graph_edge_source_doc    (source_doc_id)
    idx_bridge_graph_edge_target_doc    (target_doc_id)

Constraint:
    CHECK (source_vault_id <> target_vault_id) — self-bridges are rejected.

Design rules:
    - Bridge edges are stored separately from internal vault edges
      (graph.vault_graph_edge). They must never modify internal graph state.
    - Dense similarity matrices are ephemeral; only sparse edges are persisted.

Run with:
    python migrations/versions/011_bridge_edges.py

or via Alembic once Alembic is configured for this repository.
"""

import os

from sqlalchemy import create_engine, text


UP_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS graph.bridge_graph_edge (
        id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        source_vault_id         UUID NOT NULL REFERENCES public.vaults(id) ON DELETE CASCADE,
        target_vault_id         UUID NOT NULL REFERENCES public.vaults(id) ON DELETE CASCADE,
        source_doc_id           UUID NOT NULL REFERENCES public.documents(id) ON DELETE CASCADE,
        target_doc_id           UUID NOT NULL REFERENCES public.documents(id) ON DELETE CASCADE,
        weight                  DOUBLE PRECISION NOT NULL,
        distance                DOUBLE PRECISION,
        source_graph_version_id UUID REFERENCES graph.vault_graph_version(id) ON DELETE SET NULL,
        target_graph_version_id UUID REFERENCES graph.vault_graph_version(id) ON DELETE SET NULL,
        bridge_config_id        UUID REFERENCES graph.graph_config(id),
        source_type             TEXT NOT NULL DEFAULT 'bridge_knn',
        contributions           JSONB NOT NULL DEFAULT '{}'::jsonb,
        is_persistent           BOOLEAN NOT NULL DEFAULT true,
        is_locked               BOOLEAN NOT NULL DEFAULT false,
        created_by              UUID REFERENCES public.users(id),
        created_at              TIMESTAMP NOT NULL DEFAULT now(),
        CHECK (source_vault_id <> target_vault_id)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_bridge_graph_edge_source_vault
        ON graph.bridge_graph_edge (source_vault_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_bridge_graph_edge_target_vault
        ON graph.bridge_graph_edge (target_vault_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_bridge_graph_edge_source_doc
        ON graph.bridge_graph_edge (source_doc_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_bridge_graph_edge_target_doc
        ON graph.bridge_graph_edge (target_doc_id);
    """,
]

DOWN_STATEMENTS = [
    "DROP TABLE IF EXISTS graph.bridge_graph_edge;",
]


def upgrade(engine) -> None:
    with engine.connect() as conn:
        for stmt in UP_STATEMENTS:
            conn.execute(text(stmt))
            conn.commit()
    print("011 upgrade: graph.bridge_graph_edge created with indexes.")


def downgrade(engine) -> None:
    with engine.connect() as conn:
        for stmt in DOWN_STATEMENTS:
            conn.execute(text(stmt))
            conn.commit()
    print("011 downgrade: graph.bridge_graph_edge dropped.")


if __name__ == "__main__":
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is not set.")
    eng = create_engine(database_url)
    upgrade(eng)
