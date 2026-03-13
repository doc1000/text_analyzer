"""
GraphVersionRepo — lifecycle management for graph.vault_graph_version rows.

Responsibilities:
- create_version:   insert a new 'building' version row with auto-incremented version_no.
- finalize_version: record edge_count, source_document_count, and build_finished_at.
- activate_version: atomically supersede the previous active version and activate the new one.
- fail_version:     mark a version as failed with optional notes.
- get_active_version: return the currently active version dict for a vault, or None.

Design constraints (from architecture rules):
- Activation uses a single transaction to enforce the single-active-version invariant.
- The unique partial index uq_active_vault_graph_version (vault_id WHERE is_active = true)
  provides a database-level guard; this code is the application-level guard.
- No dense matrices or pairwise data are touched here.
- All tables are in the graph schema.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine


class GraphVersionRepo:
    """
    Repository for graph.vault_graph_version rows.

    Each method opens its own connection; the engine is injected externally.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ------------------------------------------------------------------
    # create_version
    # ------------------------------------------------------------------

    def create_version(
        self,
        vault_id: UUID,
        config_id: UUID,
        built_by: UUID | None = None,
    ) -> dict[str, Any]:
        """
        Insert a new graph version row with status 'building'.

        version_no is computed as MAX(version_no) + 1 for the vault (defaults to 1).

        Returns the new row as a dict.
        """
        sql = text(
            """
            INSERT INTO graph.vault_graph_version (
                vault_id,
                graph_config_id,
                version_no,
                status,
                built_by,
                is_active
            )
            VALUES (
                :vault_id,
                :config_id,
                COALESCE(
                    (SELECT MAX(version_no) FROM graph.vault_graph_version
                     WHERE vault_id = :vault_id),
                    0
                ) + 1,
                'building',
                :built_by,
                false
            )
            RETURNING *
            """
        )
        with self._engine.connect() as conn:
            row = conn.execute(
                sql,
                {
                    "vault_id": str(vault_id),
                    "config_id": str(config_id),
                    "built_by": str(built_by) if built_by else None,
                },
            ).mappings().fetchone()
            conn.commit()

        if row is None:
            raise RuntimeError(
                "INSERT INTO graph.vault_graph_version returned no row."
            )
        return dict(row)

    # ------------------------------------------------------------------
    # finalize_version
    # ------------------------------------------------------------------

    def finalize_version(
        self,
        version_id: UUID,
        edge_count: int,
        doc_count: int,
    ) -> None:
        """
        Record build completion statistics on a 'building' version.

        Sets build_finished_at to now(), edge_count, source_document_count.
        Does not change status — that is done by activate_version or fail_version.
        """
        sql = text(
            """
            UPDATE graph.vault_graph_version
            SET build_finished_at     = now(),
                edge_count            = :edge_count,
                source_document_count = :doc_count
            WHERE id = :version_id
            """
        )
        with self._engine.connect() as conn:
            conn.execute(
                sql,
                {
                    "version_id": str(version_id),
                    "edge_count": edge_count,
                    "doc_count": doc_count,
                },
            )
            conn.commit()

    # ------------------------------------------------------------------
    # activate_version
    # ------------------------------------------------------------------

    def activate_version(self, version_id: UUID) -> None:
        """
        Activate a graph version and supersede the previous active version.

        Both operations are performed in a single transaction:
        1. Set the previously active version (if any) for the same vault to
           status='superseded', is_active=false.
        2. Set this version to status='active', is_active=true.

        The unique partial index uq_active_vault_graph_version enforces
        the single-active-version invariant at the database level.
        """
        supersede_sql = text(
            """
            UPDATE graph.vault_graph_version
            SET status    = 'superseded',
                is_active = false
            WHERE vault_id = (
                SELECT vault_id FROM graph.vault_graph_version WHERE id = :version_id
            )
              AND is_active = true
              AND id <> :version_id
            """
        )
        activate_sql = text(
            """
            UPDATE graph.vault_graph_version
            SET status    = 'active',
                is_active = true
            WHERE id = :version_id
            """
        )
        with self._engine.connect() as conn:
            with conn.begin():
                conn.execute(supersede_sql, {"version_id": str(version_id)})
                conn.execute(activate_sql, {"version_id": str(version_id)})

    # ------------------------------------------------------------------
    # fail_version
    # ------------------------------------------------------------------

    def fail_version(
        self,
        version_id: UUID,
        error_message: str | None = None,
    ) -> None:
        """
        Mark a graph version as failed.

        Sets status='failed', build_finished_at=now(), and optionally stores
        error_message in the notes column.
        """
        sql = text(
            """
            UPDATE graph.vault_graph_version
            SET status            = 'failed',
                build_finished_at = now(),
                notes             = :notes
            WHERE id = :version_id
            """
        )
        with self._engine.connect() as conn:
            conn.execute(
                sql,
                {
                    "version_id": str(version_id),
                    "notes": error_message,
                },
            )
            conn.commit()

    # ------------------------------------------------------------------
    # get_active_version
    # ------------------------------------------------------------------

    def get_active_version(self, vault_id: UUID) -> dict[str, Any] | None:
        """
        Return the active graph version row for a vault, or None.

        Uses the partial index uq_active_vault_graph_version for efficient lookup.
        """
        sql = text(
            """
            SELECT *
            FROM graph.vault_graph_version
            WHERE vault_id = :vault_id
              AND is_active = true
            LIMIT 1
            """
        )
        with self._engine.connect() as conn:
            row = conn.execute(
                sql, {"vault_id": str(vault_id)}
            ).mappings().fetchone()

        if row is None:
            return None
        return dict(row)
