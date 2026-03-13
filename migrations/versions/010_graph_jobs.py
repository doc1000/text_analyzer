"""
Migration 010 — Graph job tracking tables: graph_job and graph_action_log.

Phase 4 of the graph-engine implementation plan.

New tables:
    graph.graph_job         — records graph operation requests and their status
    graph.graph_action_log  — records user structural actions that affect the graph

All tables live in the `graph` schema (created by migration 008).

Job types:
    build_vault_graph, repair_local_graph, rebuild_bridges, derive_hierarchy_cache

Run with:
    python migrations/versions/010_graph_jobs.py

or via Alembic once Alembic is configured for this repository.
"""

import os

from sqlalchemy import create_engine, text


UP_STATEMENTS = [
    # -----------------------------------------------------------------------
    # graph_job
    # -----------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS graph.graph_job (
        id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        job_type         TEXT NOT NULL,
        job_scope        TEXT NOT NULL,
        vault_id         UUID REFERENCES public.vaults(id) ON DELETE CASCADE,
        target_vault_id  UUID REFERENCES public.vaults(id) ON DELETE CASCADE,
        graph_version_id UUID REFERENCES graph.vault_graph_version(id) ON DELETE SET NULL,
        status           TEXT NOT NULL DEFAULT 'queued',
        requested_by     UUID REFERENCES public.users(id),
        payload          JSONB NOT NULL DEFAULT '{}'::jsonb,
        started_at       TIMESTAMP,
        finished_at      TIMESTAMP,
        error_message    TEXT,
        created_at       TIMESTAMP NOT NULL DEFAULT now()
    );
    """,
    # -----------------------------------------------------------------------
    # graph_action_log
    # -----------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS graph.graph_action_log (
        id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id          UUID NOT NULL REFERENCES public.users(id),
        action_type      TEXT NOT NULL,
        action_scope     TEXT NOT NULL,
        vault_id         UUID REFERENCES public.vaults(id) ON DELETE CASCADE,
        target_vault_id  UUID REFERENCES public.vaults(id) ON DELETE CASCADE,
        document_ids     UUID[] NOT NULL DEFAULT '{}'::uuid[],
        payload          JSONB NOT NULL DEFAULT '{}'::jsonb,
        strength         DOUBLE PRECISION,
        persisted_effect BOOLEAN NOT NULL DEFAULT false,
        created_at       TIMESTAMP NOT NULL DEFAULT now()
    );
    """,
]

DOWN_STATEMENTS = [
    "DROP TABLE IF EXISTS graph.graph_action_log;",
    "DROP TABLE IF EXISTS graph.graph_job;",
]


def upgrade(engine) -> None:
    with engine.connect() as conn:
        for stmt in UP_STATEMENTS:
            conn.execute(text(stmt))
            conn.commit()
    print("010 upgrade: graph.graph_job and graph.graph_action_log created.")


def downgrade(engine) -> None:
    with engine.connect() as conn:
        for stmt in DOWN_STATEMENTS:
            conn.execute(text(stmt))
            conn.commit()
    print("010 downgrade: graph.graph_action_log and graph.graph_job dropped.")


if __name__ == "__main__":
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is not set.")
    eng = create_engine(database_url)
    upgrade(eng)
