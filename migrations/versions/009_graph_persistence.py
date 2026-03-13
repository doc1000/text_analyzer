"""
Migration 009 — Graph persistence tables: vault_graph_version and vault_graph_edge.

Phase 2 of the graph-engine implementation plan.

New tables:
    graph.vault_graph_version  — version metadata for each vault graph build
    graph.vault_graph_edge     — sparse internal edges for a graph version

All tables live in the `graph` schema (created by migration 008).

Run with:
    python migrations/versions/009_graph_persistence.py

or via Alembic once Alembic is configured for this repository.
"""

import os

from sqlalchemy import create_engine, text


UP_STATEMENTS = [
    # -----------------------------------------------------------------------
    # vault_graph_version
    # -----------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS graph.vault_graph_version (
        id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        vault_id              UUID NOT NULL REFERENCES public.vaults(id) ON DELETE CASCADE,
        graph_config_id       UUID NOT NULL REFERENCES graph.graph_config(id),
        version_no            INTEGER NOT NULL,
        status                TEXT NOT NULL DEFAULT 'building',
        build_scope           TEXT NOT NULL DEFAULT 'full',
        source_document_count INTEGER NOT NULL DEFAULT 0,
        edge_count            INTEGER NOT NULL DEFAULT 0,
        built_by              UUID REFERENCES public.users(id),
        build_started_at      TIMESTAMP NOT NULL DEFAULT now(),
        build_finished_at     TIMESTAMP,
        is_active             BOOLEAN NOT NULL DEFAULT false,
        parent_version_id     UUID REFERENCES graph.vault_graph_version(id),
        notes                 TEXT,
        UNIQUE (vault_id, version_no)
    );
    """,
    # Partial unique index: enforce single active version per vault.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_active_vault_graph_version
        ON graph.vault_graph_version (vault_id)
        WHERE is_active = true;
    """,
    # -----------------------------------------------------------------------
    # vault_graph_edge
    # -----------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS graph.vault_graph_edge (
        id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        graph_version_id UUID NOT NULL REFERENCES graph.vault_graph_version(id) ON DELETE CASCADE,
        vault_id         UUID NOT NULL REFERENCES public.vaults(id) ON DELETE CASCADE,
        doc_lo           UUID NOT NULL REFERENCES public.documents(id) ON DELETE CASCADE,
        doc_hi           UUID NOT NULL REFERENCES public.documents(id) ON DELETE CASCADE,
        weight           DOUBLE PRECISION NOT NULL,
        distance         DOUBLE PRECISION,
        rank_lo          INTEGER,
        rank_hi          INTEGER,
        edge_type        TEXT NOT NULL DEFAULT 'internal',
        source_type      TEXT NOT NULL DEFAULT 'knn',
        contributions    JSONB NOT NULL DEFAULT '{}'::jsonb,
        is_locked        BOOLEAN NOT NULL DEFAULT false,
        created_at       TIMESTAMP NOT NULL DEFAULT now(),
        CHECK (doc_lo <> doc_hi),
        CHECK (doc_lo < doc_hi),
        UNIQUE (graph_version_id, doc_lo, doc_hi)
    );
    """,
    # Indexes for efficient graph slicing (per Phase 2 plan).
    """
    CREATE INDEX IF NOT EXISTS idx_vault_graph_edge_version_lo
        ON graph.vault_graph_edge (graph_version_id, doc_lo);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_vault_graph_edge_version_hi
        ON graph.vault_graph_edge (graph_version_id, doc_hi);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_vault_graph_edge_vault_version
        ON graph.vault_graph_edge (vault_id, graph_version_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_vault_graph_edge_weight
        ON graph.vault_graph_edge (graph_version_id, weight DESC);
    """,
]

DOWN_STATEMENTS = [
    "DROP TABLE IF EXISTS graph.vault_graph_edge;",
    "DROP TABLE IF EXISTS graph.vault_graph_version;",
]


def upgrade(engine) -> None:
    with engine.connect() as conn:
        for stmt in UP_STATEMENTS:
            conn.execute(text(stmt))
            conn.commit()
    print("009 upgrade: graph.vault_graph_version and graph.vault_graph_edge created.")


def downgrade(engine) -> None:
    with engine.connect() as conn:
        for stmt in DOWN_STATEMENTS:
            conn.execute(text(stmt))
            conn.commit()
    print("009 downgrade: graph.vault_graph_edge and graph.vault_graph_version dropped.")


if __name__ == "__main__":
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is not set.")
    eng = create_engine(database_url)
    upgrade(eng)
