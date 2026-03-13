"""
LabelRepository — CRUD operations for graph.graph_label rows.

Responsibilities:
- save_label:              upsert a label for a (graph_version_id, group_key) pair.
- get_label:               return label text for a specific group, or None.
- get_labels_for_version:  return all labels for a graph version as {group_key: label}.
- override_label:          replace a label and mark it as a user override.
- delete_labels:           remove all labels for a graph version.

Design constraints:
    - Writes only to graph.graph_label (graph schema).
    - No dense matrix or pairwise data is involved.
    - Labels are derived artifacts; they can be deleted and regenerated freely.
    - user_override and overridden_by track manual overrides for audit purposes.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine


class LabelRepository:
    """
    Repository for graph.graph_label rows.

    Each method opens its own connection; the engine is injected externally.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ------------------------------------------------------------------
    # save_label
    # ------------------------------------------------------------------

    def save_label(
        self,
        graph_version_id: UUID,
        vault_id: UUID,
        group_key: str,
        label: str,
        method: str = "extractive",
        confidence: float | None = None,
    ) -> None:
        """
        Upsert a label for (graph_version_id, group_key).

        On conflict the label, method, and confidence are updated.
        user_override is reset to false on programmatic upserts so that a
        fresh generation does not accidentally retain the override flag.
        """
        sql = text(
            """
            INSERT INTO graph.graph_label
                (graph_version_id, vault_id, group_key, label, method, confidence)
            VALUES
                (:graph_version_id, :vault_id, :group_key, :label, :method, :confidence)
            ON CONFLICT (graph_version_id, group_key)
            DO UPDATE SET
                label        = EXCLUDED.label,
                method       = EXCLUDED.method,
                confidence   = EXCLUDED.confidence,
                user_override = false,
                overridden_by = NULL,
                created_at   = now()
            """
        )
        with self._engine.connect() as conn:
            conn.execute(
                sql,
                {
                    "graph_version_id": str(graph_version_id),
                    "vault_id": str(vault_id),
                    "group_key": group_key,
                    "label": label,
                    "method": method,
                    "confidence": confidence,
                },
            )
            conn.commit()

    # ------------------------------------------------------------------
    # get_label
    # ------------------------------------------------------------------

    def get_label(self, graph_version_id: UUID, group_key: str) -> str | None:
        """
        Return the label text for a specific group in a graph version.

        Returns None if no label exists.
        """
        sql = text(
            """
            SELECT label
            FROM graph.graph_label
            WHERE graph_version_id = :graph_version_id
              AND group_key = :group_key
            LIMIT 1
            """
        )
        with self._engine.connect() as conn:
            row = conn.execute(
                sql,
                {
                    "graph_version_id": str(graph_version_id),
                    "group_key": group_key,
                },
            ).fetchone()

        if row is None:
            return None
        return str(row[0])

    # ------------------------------------------------------------------
    # get_labels_for_version
    # ------------------------------------------------------------------

    def get_labels_for_version(self, graph_version_id: UUID) -> dict[str, str]:
        """
        Return all labels for a graph version as {group_key: label_text}.

        Returns an empty dict if no labels exist for the version.
        """
        sql = text(
            """
            SELECT group_key, label
            FROM graph.graph_label
            WHERE graph_version_id = :graph_version_id
            ORDER BY group_key
            """
        )
        with self._engine.connect() as conn:
            rows = conn.execute(
                sql,
                {"graph_version_id": str(graph_version_id)},
            ).fetchall()

        return {str(row[0]): str(row[1]) for row in rows}

    # ------------------------------------------------------------------
    # override_label
    # ------------------------------------------------------------------

    def override_label(
        self,
        graph_version_id: UUID,
        group_key: str,
        new_label: str,
        user_id: UUID,
    ) -> None:
        """
        Replace a label with a user-supplied value and mark it as overridden.

        Sets user_override = true and overridden_by = user_id.
        Does nothing if no label row exists for the given version and group_key.
        """
        sql = text(
            """
            UPDATE graph.graph_label
            SET label         = :new_label,
                user_override = true,
                overridden_by = :user_id,
                created_at    = now()
            WHERE graph_version_id = :graph_version_id
              AND group_key        = :group_key
            """
        )
        with self._engine.connect() as conn:
            conn.execute(
                sql,
                {
                    "new_label": new_label,
                    "user_id": str(user_id),
                    "graph_version_id": str(graph_version_id),
                    "group_key": group_key,
                },
            )
            conn.commit()

    # ------------------------------------------------------------------
    # delete_labels
    # ------------------------------------------------------------------

    def delete_labels(self, graph_version_id: UUID) -> None:
        """
        Remove all labels for a graph version.

        Safe to call even if no labels exist.
        """
        sql = text(
            """
            DELETE FROM graph.graph_label
            WHERE graph_version_id = :graph_version_id
            """
        )
        with self._engine.connect() as conn:
            conn.execute(sql, {"graph_version_id": str(graph_version_id)})
            conn.commit()
