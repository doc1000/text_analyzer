"""
BridgeService — cross-vault bridge edge construction and persistence.

Phase 6 of the graph-engine implementation plan.

Design constraints (from architecture rules):
- Bridge edges are stored in graph.bridge_graph_edge, separate from
  internal vault edges in graph.vault_graph_edge.
- Internal vault graph edges are NEVER modified by any bridge operation.
- Dense cosine similarity matrices are ephemeral; never persisted.
- Only sparse bridge edges are written to the database.

Public API:
    build_bridges(source_vault_id, target_vault_id, config_id, built_by) -> int
    load_bridges(vault_id_a, vault_id_b) -> list[BridgeEdge]
    delete_bridges(source_vault_id, target_vault_id) -> int
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import numpy as np
from sqlalchemy import text
from sqlalchemy.engine import Engine

from graph_engine.db.models.graph_config import GraphConfigRow, get_graph_config
from graph_engine.db.repositories.graph_version_repo import GraphVersionRepo


_DEFAULT_BRIDGE_KNN_K = 5


@dataclass
class BridgeEdge:
    """
    A single directed bridge edge connecting a document in one vault to a
    document in another vault.

    Bridge edges are directional in the database (source → target) but are
    loaded bidirectionally by load_bridges so callers see the full pair
    regardless of which vault was the source at build time.
    """

    source_vault_id: UUID
    target_vault_id: UUID
    source_doc_id: UUID
    target_doc_id: UUID
    weight: float
    distance: float | None = None
    source_graph_version_id: UUID | None = None
    target_graph_version_id: UUID | None = None
    bridge_config_id: UUID | None = None

    def __post_init__(self) -> None:
        for attr in (
            "source_vault_id",
            "target_vault_id",
            "source_doc_id",
            "target_doc_id",
        ):
            val = getattr(self, attr)
            if not isinstance(val, UUID):
                object.__setattr__(self, attr, UUID(str(val)))
        for attr in (
            "source_graph_version_id",
            "target_graph_version_id",
            "bridge_config_id",
        ):
            val = getattr(self, attr)
            if val is not None and not isinstance(val, UUID):
                object.__setattr__(self, attr, UUID(str(val)))

        if self.source_vault_id == self.target_vault_id:
            raise ValueError(
                f"BridgeEdge self-vault not allowed: "
                f"source_vault_id == target_vault_id == {self.source_vault_id}"
            )


class BridgeService:
    """
    Builds, loads, and deletes cross-vault bridge edges.

    Injects a SQLAlchemy engine, a GraphVersionRepo (to resolve active
    versions), and optionally a feature fetcher callable for testing.

    All dense similarity matrices are local variables that are never persisted.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._version_repo = GraphVersionRepo(engine)

    # ------------------------------------------------------------------
    # build_bridges
    # ------------------------------------------------------------------

    def build_bridges(
        self,
        source_vault_id: UUID,
        target_vault_id: UUID,
        config_id: UUID,
        built_by: UUID | None = None,
    ) -> int:
        """
        Compute cross-vault bridge edges and persist them.

        Steps:
        1. Load graph config for bridge_knn_k.
        2. Resolve active graph versions for both vaults.
        3. Fetch document embeddings for both vaults.
        4. Compute cross-vault cosine similarity matrix (ephemeral).
        5. For each source document select top bridge_knn_k nearest target docs.
        6. Persist bridge edges to graph.bridge_graph_edge.
        7. Return count of created edges.

        Raises:
            ValueError: if either vault has no documents with embeddings.
        """
        config = get_graph_config(self._engine, config_id)

        source_version_id = self._resolve_active_version(source_vault_id)
        target_version_id = self._resolve_active_version(target_vault_id)

        bridge_k = config.bridge_knn_k if config.bridge_knn_k is not None else _DEFAULT_BRIDGE_KNN_K

        source_embeddings = self._fetch_vault_embeddings(source_vault_id, config)
        if not source_embeddings:
            raise ValueError(
                f"No embeddings found for source vault {source_vault_id}. "
                "Cannot build bridges."
            )

        target_embeddings = self._fetch_vault_embeddings(target_vault_id, config)
        if not target_embeddings:
            raise ValueError(
                f"No embeddings found for target vault {target_vault_id}. "
                "Cannot build bridges."
            )

        source_ids = list(source_embeddings.keys())
        target_ids = list(target_embeddings.keys())

        source_matrix = np.array(
            [source_embeddings[d] for d in source_ids], dtype=np.float32
        )
        target_matrix = np.array(
            [target_embeddings[d] for d in target_ids], dtype=np.float32
        )

        # Ephemeral cross-vault cosine similarity. Shape: (n_source, n_target).
        similarity = source_matrix @ target_matrix.T

        edges = self._build_bridge_edges(
            source_ids=source_ids,
            target_ids=target_ids,
            similarity=similarity,
            bridge_k=bridge_k,
            source_vault_id=source_vault_id,
            target_vault_id=target_vault_id,
            source_version_id=source_version_id,
            target_version_id=target_version_id,
            config_id=config_id,
            built_by=built_by,
        )

        if not edges:
            return 0

        return self._persist_bridge_edges(edges)

    # ------------------------------------------------------------------
    # load_bridges
    # ------------------------------------------------------------------

    def load_bridges(
        self,
        vault_id_a: UUID,
        vault_id_b: UUID,
    ) -> list[BridgeEdge]:
        """
        Return all bridge edges connecting the two vaults, regardless of
        which was the source at build time.

        Queries for (source=a, target=b) OR (source=b, target=a).
        """
        sql = text(
            """
            SELECT
                source_vault_id,
                target_vault_id,
                source_doc_id,
                target_doc_id,
                weight,
                distance,
                source_graph_version_id,
                target_graph_version_id,
                bridge_config_id
            FROM graph.bridge_graph_edge
            WHERE
                (source_vault_id = :vault_a AND target_vault_id = :vault_b)
                OR
                (source_vault_id = :vault_b AND target_vault_id = :vault_a)
            ORDER BY weight DESC
            """
        )
        with self._engine.connect() as conn:
            rows = conn.execute(
                sql,
                {
                    "vault_a": str(vault_id_a),
                    "vault_b": str(vault_id_b),
                },
            ).fetchall()

        return [_row_to_bridge_edge(row) for row in rows]

    # ------------------------------------------------------------------
    # delete_bridges
    # ------------------------------------------------------------------

    def delete_bridges(
        self,
        source_vault_id: UUID,
        target_vault_id: UUID,
    ) -> int:
        """
        Delete all bridge edges between the vault pair (both directions).

        Returns the count of deleted rows.
        """
        sql = text(
            """
            WITH deleted AS (
                DELETE FROM graph.bridge_graph_edge
                WHERE
                    (source_vault_id = :vault_a AND target_vault_id = :vault_b)
                    OR
                    (source_vault_id = :vault_b AND target_vault_id = :vault_a)
                RETURNING id
            )
            SELECT count(*) FROM deleted
            """
        )
        with self._engine.connect() as conn:
            row = conn.execute(
                sql,
                {
                    "vault_a": str(source_vault_id),
                    "vault_b": str(target_vault_id),
                },
            ).fetchone()
            conn.commit()

        return int(row[0]) if row else 0

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_active_version(self, vault_id: UUID) -> UUID | None:
        """
        Return the active graph version UUID for a vault, or None if none exists.

        A missing version is non-fatal for bridge building — bridge edges will
        simply have NULL source/target graph version references.
        """
        active = self._version_repo.get_active_version(vault_id)
        if active is None:
            return None
        return UUID(str(active["id"]))

    def _fetch_vault_embeddings(
        self,
        vault_id: UUID,
        config: GraphConfigRow,
    ) -> dict[UUID, np.ndarray]:
        """
        Fetch one L2-normalised embedding vector per document for a vault.

        Uses the same embedding_source as the internal graph build.
        chunk_index = 0 is the representative document-level embedding.
        Documents without an embedding are silently skipped.
        """
        table_name = config.embedding_source.strip()
        qualified = table_name if "." in table_name else f"embedding.{table_name}"

        sql = text(
            f"""
            SELECT e.document_id, e.embedding
            FROM {qualified} e
            JOIN public.documents d ON d.id = e.document_id
            WHERE d.vault_id = :vault_id
              AND e.chunk_index = 0
            """
        )
        with self._engine.connect() as conn:
            rows = conn.execute(sql, {"vault_id": str(vault_id)}).fetchall()

        result: dict[UUID, np.ndarray] = {}
        for doc_id_raw, embedding_raw in rows:
            if embedding_raw is None:
                continue
            vec = np.array(embedding_raw, dtype=np.float32)
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec = vec / norm
            result[UUID(str(doc_id_raw))] = vec
        return result

    def _build_bridge_edges(
        self,
        source_ids: list[UUID],
        target_ids: list[UUID],
        similarity: np.ndarray,
        bridge_k: int,
        source_vault_id: UUID,
        target_vault_id: UUID,
        source_version_id: UUID | None,
        target_version_id: UUID | None,
        config_id: UUID,
        built_by: UUID | None,
    ) -> list[dict]:
        """
        Build bridge edge row dicts from the cross-vault similarity matrix.

        similarity shape: (n_source, n_target) — ephemeral.

        For each source document selects top bridge_k target documents.
        No deduplication is needed because source and target are from distinct
        vaults; the (source_doc_id, target_doc_id) pairing is always unique.
        """
        n_target = len(target_ids)
        effective_k = min(bridge_k, n_target)

        rows: list[dict] = []
        for i, source_doc_id in enumerate(source_ids):
            sim_row = similarity[i]
            if effective_k >= n_target:
                neighbor_indices = list(range(n_target))
                neighbor_indices.sort(key=lambda j: sim_row[j], reverse=True)
            else:
                part = np.argpartition(sim_row, -effective_k)
                neighbor_indices = part[-effective_k:].tolist()
                neighbor_indices.sort(key=lambda j: sim_row[j], reverse=True)

            for j in neighbor_indices[:effective_k]:
                sim_val = float(sim_row[j])
                sim_val = max(0.0, min(1.0, sim_val))
                distance = 1.0 - sim_val
                rows.append(
                    {
                        "source_vault_id": str(source_vault_id),
                        "target_vault_id": str(target_vault_id),
                        "source_doc_id": str(source_doc_id),
                        "target_doc_id": str(target_ids[j]),
                        "weight": sim_val,
                        "distance": distance,
                        "source_graph_version_id": str(source_version_id) if source_version_id else None,
                        "target_graph_version_id": str(target_version_id) if target_version_id else None,
                        "bridge_config_id": str(config_id),
                        "created_by": str(built_by) if built_by else None,
                    }
                )
        return rows

    def _persist_bridge_edges(self, rows: list[dict]) -> int:
        """
        Bulk-insert bridge edge rows into graph.bridge_graph_edge.

        Returns the count of inserted rows.
        """
        sql = text(
            """
            INSERT INTO graph.bridge_graph_edge (
                source_vault_id,
                target_vault_id,
                source_doc_id,
                target_doc_id,
                weight,
                distance,
                source_graph_version_id,
                target_graph_version_id,
                bridge_config_id,
                created_by
            ) VALUES (
                :source_vault_id,
                :target_vault_id,
                :source_doc_id,
                :target_doc_id,
                :weight,
                :distance,
                :source_graph_version_id,
                :target_graph_version_id,
                :bridge_config_id,
                :created_by
            )
            """
        )
        with self._engine.connect() as conn:
            conn.execute(sql, rows)
            conn.commit()
        return len(rows)


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _row_to_bridge_edge(row) -> BridgeEdge:
    """Convert a raw DB row to a BridgeEdge dataclass."""
    return BridgeEdge(
        source_vault_id=UUID(str(row[0])),
        target_vault_id=UUID(str(row[1])),
        source_doc_id=UUID(str(row[2])),
        target_doc_id=UUID(str(row[3])),
        weight=float(row[4]),
        distance=float(row[5]) if row[5] is not None else None,
        source_graph_version_id=UUID(str(row[6])) if row[6] is not None else None,
        target_graph_version_id=UUID(str(row[7])) if row[7] is not None else None,
        bridge_config_id=UUID(str(row[8])) if row[8] is not None else None,
    )
