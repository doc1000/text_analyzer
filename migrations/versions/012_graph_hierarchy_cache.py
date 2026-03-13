"""
Migration 012 — Derived hierarchy cache: graph.graph_hierarchy_cache.

Phase 7 of the graph-engine implementation plan.

New table:
    graph.graph_hierarchy_cache — stores optional derived hierarchy artifacts
                                  (Z linkage arrays, nested tree payloads)
                                  keyed to a graph version and vault.

Design rules:
    - Hierarchy caches are derived artifacts only. They must never become
      canonical graph state and may be rebuilt or invalidated at any time.
    - Dense matrices are ephemeral; only serialised JSON payloads are stored.
    - expires_at is nullable; a NULL value means the cache never expires.

Cache type values (stored in cache_type column):
    z_linkage               — serialised Z linkage array + labels
    circle_pack_payload     — circle-pack layout payload (future)
    local_hierarchy_payload — nested tree structure

Run with:
    python migrations/versions/012_graph_hierarchy_cache.py

or via Alembic once Alembic is configured for this repository.
"""

import os

from sqlalchemy import create_engine, text


UP_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS graph.graph_hierarchy_cache (
        id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        graph_version_id  UUID NOT NULL REFERENCES graph.vault_graph_version(id)
                          ON DELETE CASCADE,
        vault_id          UUID NOT NULL REFERENCES public.vaults(id)
                          ON DELETE CASCADE,
        cache_type        TEXT NOT NULL,
        source_scope      TEXT NOT NULL DEFAULT 'vault',
        document_ids      UUID[],
        payload           JSONB NOT NULL,
        created_at        TIMESTAMP NOT NULL DEFAULT now(),
        expires_at        TIMESTAMP
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_graph_hierarchy_cache_version
        ON graph.graph_hierarchy_cache (graph_version_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_graph_hierarchy_cache_vault_version
        ON graph.graph_hierarchy_cache (vault_id, graph_version_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_graph_hierarchy_cache_type
        ON graph.graph_hierarchy_cache (graph_version_id, cache_type);
    """,
]

DOWN_STATEMENTS = [
    "DROP TABLE IF EXISTS graph.graph_hierarchy_cache;",
]


def upgrade(engine) -> None:
    with engine.connect() as conn:
        for stmt in UP_STATEMENTS:
            conn.execute(text(stmt))
            conn.commit()
    print("012 upgrade: graph.graph_hierarchy_cache created with indexes.")


def downgrade(engine) -> None:
    with engine.connect() as conn:
        for stmt in DOWN_STATEMENTS:
            conn.execute(text(stmt))
            conn.commit()
    print("012 downgrade: graph.graph_hierarchy_cache dropped.")


if __name__ == "__main__":
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is not set.")
    eng = create_engine(database_url)
    upgrade(eng)
