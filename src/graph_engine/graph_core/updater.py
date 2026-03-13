"""
GraphUpdateService — incremental graph maintenance for single-document changes.

Phase 4 of the graph-engine implementation plan.

Supports three operations:
    insert_document — connect a new document to its kNN neighbors.
    delete_document — remove all incident edges; optionally repair orphaned neighbors.
    update_document — delete then re-insert (embedding has changed).

Design constraints (from architecture rules):
- Full graph rebuild must NEVER be triggered by these operations.
- Only local neighborhood computation is allowed (candidate_k at most).
- Cosine similarity matrices are computed in memory and never persisted.
- Canonical edge ordering is always enforced: doc_lo < doc_hi.
- All DB writes target graph.vault_graph_edge and graph.vault_graph_version only.
- Each completed operation is logged to graph.graph_job.

Dependencies injected externally:
    engine           — SQLAlchemy Engine pointing at the shared PostgreSQL database.
    version_repo     — GraphVersionRepo (from Phase 2).
"""

from __future__ import annotations

import json
from uuid import UUID

import numpy as np
from sqlalchemy import text
from sqlalchemy.engine import Engine

from graph_engine.db.models.graph_config import GraphConfigRow, get_graph_config
from graph_engine.db.repositories.graph_version_repo import GraphVersionRepo
from graph_engine.graph_core.types import SparseEdge


class GraphUpdateService:
    """
    Incremental graph maintenance service.

    All operations are local: only the neighborhood of the affected document
    is read or written. No full graph scan is triggered.
    """

    def __init__(self, engine: Engine, version_repo: GraphVersionRepo) -> None:
        self._engine = engine
        self._version_repo = version_repo

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def insert_document(
        self,
        vault_id: UUID,
        document_id: UUID,
        graph_version_id: UUID | None = None,
    ) -> list[SparseEdge]:
        """
        Connect a newly added document to its kNN neighbors.

        Steps:
        1. Resolve the active graph version.
        2. Load the graph config for that version.
        3. Fetch the new document's embedding.
        4. Fetch candidate neighbor embeddings (limited to candidate_k).
        5. Compute cosine similarities in memory (ephemeral).
        6. Select top knn_k neighbors.
        7. Insert new edges into graph.vault_graph_edge (canonical ordering).
        8. Increment edge_count and source_document_count on the version row.
        9. Log a graph_job entry with job_type='repair_local_graph'.

        Returns the list of inserted SparseEdge objects.

        Raises ValueError if no active version exists and none was supplied.
        Raises ValueError if the document has no embedding.
        """
        version_id, config = self._resolve_version_and_config(vault_id, graph_version_id)

        new_vec = self._fetch_single_embedding(document_id, config)
        if new_vec is None:
            raise ValueError(
                f"No embedding found for document {document_id} "
                f"(source='{config.embedding_source}'). Cannot insert into graph."
            )

        candidates = self._fetch_candidate_embeddings(
            vault_id=vault_id,
            exclude_doc_id=document_id,
            config=config,
        )
        if not candidates:
            self._log_graph_job(
                vault_id=vault_id,
                version_id=version_id,
                job_type="repair_local_graph",
                payload={"operation": "insert_document", "document_id": str(document_id)},
            )
            return []

        edges = self._compute_knn_edges(
            new_doc_id=document_id,
            new_vec=new_vec,
            candidates=candidates,
            knn_k=config.knn_k,
        )

        if edges:
            self._insert_edges(
                graph_version_id=version_id,
                vault_id=vault_id,
                edges=edges,
            )
            self._increment_version_metadata(
                version_id=version_id,
                delta_edges=len(edges),
                delta_docs=1,
            )

        self._log_graph_job(
            vault_id=vault_id,
            version_id=version_id,
            job_type="repair_local_graph",
            payload={"operation": "insert_document", "document_id": str(document_id)},
        )

        return edges

    def delete_document(
        self,
        vault_id: UUID,
        document_id: UUID,
        graph_version_id: UUID | None = None,
    ) -> int:
        """
        Remove all edges incident to document_id from the active graph version.

        Steps:
        1. Resolve the active graph version and config.
        2. Identify all neighbors that will lose an edge.
        3. Delete all edges where doc_lo OR doc_hi = document_id.
        4. For each neighbor whose remaining edge count drops below knn_k / 2,
           find new nearest neighbors from the vault pool and insert repair edges.
        5. Update version metadata (edge_count, source_document_count).
        6. Log a graph_job entry.

        Returns the number of edges deleted (before any repair insertions).

        Raises ValueError if no active version exists and none was supplied.
        """
        version_id, config = self._resolve_version_and_config(vault_id, graph_version_id)

        neighbors = self._fetch_incident_neighbors(
            document_id=document_id,
            version_id=version_id,
        )

        deleted_count = self._delete_incident_edges(
            document_id=document_id,
            version_id=version_id,
        )

        if deleted_count > 0:
            repair_edges = self._repair_orphaned_neighbors(
                vault_id=vault_id,
                version_id=version_id,
                config=config,
                neighbors=neighbors,
                deleted_doc_id=document_id,
            )
            self._increment_version_metadata(
                version_id=version_id,
                delta_edges=len(repair_edges) - deleted_count,
                delta_docs=-1,
            )
        else:
            self._increment_version_metadata(
                version_id=version_id,
                delta_edges=0,
                delta_docs=-1,
            )

        self._log_graph_job(
            vault_id=vault_id,
            version_id=version_id,
            job_type="repair_local_graph",
            payload={"operation": "delete_document", "document_id": str(document_id)},
        )

        return deleted_count

    def update_document(
        self,
        vault_id: UUID,
        document_id: UUID,
    ) -> list[SparseEdge]:
        """
        Re-integrate a document whose embedding has changed.

        Calls delete_document() then insert_document() on the active version.
        Both operations resolve the active version independently, so if the
        version changes between the two calls (unlikely in normal use) the
        insert will still land on the then-active version.

        Returns the list of newly inserted SparseEdge objects.
        """
        self.delete_document(vault_id=vault_id, document_id=document_id)
        return self.insert_document(vault_id=vault_id, document_id=document_id)

    # ------------------------------------------------------------------
    # Version resolution
    # ------------------------------------------------------------------

    def _resolve_version_and_config(
        self,
        vault_id: UUID,
        graph_version_id: UUID | None,
    ) -> tuple[UUID, GraphConfigRow]:
        """
        Return (version_id, GraphConfigRow) for the requested (or active) version.

        Raises ValueError if no active version exists.
        """
        if graph_version_id is not None:
            version_id = graph_version_id
        else:
            active = self._version_repo.get_active_version(vault_id)
            if active is None:
                raise ValueError(
                    f"No active graph version for vault {vault_id}. "
                    "Build the vault graph before applying incremental updates."
                )
            version_id = UUID(str(active["id"]))

        config_id = self._fetch_config_id_for_version(version_id)
        config = get_graph_config(self._engine, config_id)
        return version_id, config

    def _fetch_config_id_for_version(self, version_id: UUID) -> UUID:
        sql = text(
            """
            SELECT graph_config_id
            FROM graph.vault_graph_version
            WHERE id = :version_id
            """
        )
        with self._engine.connect() as conn:
            row = conn.execute(sql, {"version_id": str(version_id)}).fetchone()
        if row is None:
            raise KeyError(f"graph.vault_graph_version not found: {version_id}")
        return UUID(str(row[0]))

    # ------------------------------------------------------------------
    # Embedding retrieval
    # ------------------------------------------------------------------

    def _fetch_single_embedding(
        self,
        document_id: UUID,
        config: GraphConfigRow,
    ) -> np.ndarray | None:
        """
        Fetch the document-level embedding (chunk_index = 0) for one document.

        Returns None if no embedding exists.
        """
        table_name = config.embedding_source.strip()
        qualified = table_name if "." in table_name else f"embedding.{table_name}"

        sql = text(
            f"""
            SELECT embedding
            FROM {qualified}
            WHERE document_id = :doc_id
              AND chunk_index = 0
            LIMIT 1
            """
        )
        with self._engine.connect() as conn:
            row = conn.execute(sql, {"doc_id": str(document_id)}).fetchone()

        if row is None or row[0] is None:
            return None

        vec = np.array(row[0], dtype=np.float32)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec

    def _fetch_candidate_embeddings(
        self,
        vault_id: UUID,
        exclude_doc_id: UUID,
        config: GraphConfigRow,
    ) -> dict[UUID, np.ndarray]:
        """
        Fetch up to candidate_k document-level embeddings from the vault,
        excluding exclude_doc_id.

        Only chunk_index = 0 is used (document-level representative embedding).
        The result is an ephemeral in-memory dict; it is never persisted.
        """
        table_name = config.embedding_source.strip()
        qualified = table_name if "." in table_name else f"embedding.{table_name}"

        sql = text(
            f"""
            SELECT e.document_id, e.embedding
            FROM {qualified} e
            JOIN public.documents d ON d.id = e.document_id
            WHERE d.vault_id = :vault_id
              AND e.document_id <> :exclude_id
              AND e.chunk_index = 0
            LIMIT :limit
            """
        )
        with self._engine.connect() as conn:
            rows = conn.execute(
                sql,
                {
                    "vault_id": str(vault_id),
                    "exclude_id": str(exclude_doc_id),
                    "limit": config.candidate_k,
                },
            ).fetchall()

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

    # ------------------------------------------------------------------
    # kNN edge computation (ephemeral)
    # ------------------------------------------------------------------

    def _compute_knn_edges(
        self,
        new_doc_id: UUID,
        new_vec: np.ndarray,
        candidates: dict[UUID, np.ndarray],
        knn_k: int,
    ) -> list[SparseEdge]:
        """
        Compute the top-knn_k edges between new_doc_id and the candidate pool.

        Similarity matrix is a local variable and is never persisted.
        Canonical ordering (doc_lo < doc_hi) is enforced via SparseEdge.make().
        """
        if not candidates:
            return []

        candidate_ids = list(candidates.keys())
        candidate_matrix = np.array(
            [candidates[cid] for cid in candidate_ids], dtype=np.float32
        )

        # Cosine similarity vector: shape (n_candidates,). Ephemeral.
        similarities = candidate_matrix @ new_vec

        effective_k = min(knn_k, len(candidate_ids))
        top_indices = np.argpartition(similarities, -effective_k)[-effective_k:]
        top_indices = top_indices[np.argsort(similarities[top_indices])[::-1]]

        edges: list[SparseEdge] = []
        for rank, idx in enumerate(top_indices):
            sim_val = float(similarities[idx])
            sim_val = max(0.0, min(1.0, sim_val))
            distance = 1.0 - sim_val
            neighbor_id = candidate_ids[idx]

            edge = SparseEdge.make(
                doc_a=new_doc_id,
                doc_b=neighbor_id,
                weight=sim_val,
                distance=distance,
                rank_a=rank,
                rank_b=None,
            )
            edges.append(edge)

        return edges

    # ------------------------------------------------------------------
    # Edge persistence helpers
    # ------------------------------------------------------------------

    def _insert_edges(
        self,
        graph_version_id: UUID,
        vault_id: UUID,
        edges: list[SparseEdge],
    ) -> None:
        """
        Bulk-insert new edges into graph.vault_graph_edge.

        Skips edges that already exist (ON CONFLICT DO NOTHING) to make the
        operation idempotent against retry scenarios.
        """
        if not edges:
            return

        sql = text(
            """
            INSERT INTO graph.vault_graph_edge (
                graph_version_id,
                vault_id,
                doc_lo,
                doc_hi,
                weight,
                distance,
                rank_lo,
                rank_hi,
                edge_type,
                source_type,
                contributions,
                is_locked
            ) VALUES (
                :graph_version_id,
                :vault_id,
                :doc_lo,
                :doc_hi,
                :weight,
                :distance,
                :rank_lo,
                :rank_hi,
                'internal',
                'knn',
                :contributions,
                false
            )
            ON CONFLICT (graph_version_id, doc_lo, doc_hi) DO NOTHING
            """
        )
        rows = [
            {
                "graph_version_id": str(graph_version_id),
                "vault_id": str(vault_id),
                "doc_lo": str(edge.doc_lo),
                "doc_hi": str(edge.doc_hi),
                "weight": edge.weight,
                "distance": edge.distance,
                "rank_lo": edge.rank_lo,
                "rank_hi": edge.rank_hi,
                "contributions": json.dumps({}),
            }
            for edge in edges
        ]
        with self._engine.connect() as conn:
            conn.execute(sql, rows)
            conn.commit()

    def _delete_incident_edges(
        self,
        document_id: UUID,
        version_id: UUID,
    ) -> int:
        """
        Delete all edges incident to document_id for the given version.

        Returns the number of rows deleted.
        """
        sql = text(
            """
            DELETE FROM graph.vault_graph_edge
            WHERE graph_version_id = :version_id
              AND (doc_lo = :doc_id OR doc_hi = :doc_id)
            """
        )
        with self._engine.connect() as conn:
            result = conn.execute(
                sql,
                {"version_id": str(version_id), "doc_id": str(document_id)},
            )
            conn.commit()
        return result.rowcount

    def _fetch_incident_neighbors(
        self,
        document_id: UUID,
        version_id: UUID,
    ) -> set[UUID]:
        """
        Return the set of document IDs that share an edge with document_id.
        """
        sql = text(
            """
            SELECT doc_lo, doc_hi
            FROM graph.vault_graph_edge
            WHERE graph_version_id = :version_id
              AND (doc_lo = :doc_id OR doc_hi = :doc_id)
            """
        )
        with self._engine.connect() as conn:
            rows = conn.execute(
                sql,
                {"version_id": str(version_id), "doc_id": str(document_id)},
            ).fetchall()

        neighbors: set[UUID] = set()
        doc_uuid = UUID(str(document_id))
        for row in rows:
            lo = UUID(str(row[0]))
            hi = UUID(str(row[1]))
            if lo != doc_uuid:
                neighbors.add(lo)
            if hi != doc_uuid:
                neighbors.add(hi)
        return neighbors

    # ------------------------------------------------------------------
    # Neighbor repair
    # ------------------------------------------------------------------

    def _repair_orphaned_neighbors(
        self,
        vault_id: UUID,
        version_id: UUID,
        config: GraphConfigRow,
        neighbors: set[UUID],
        deleted_doc_id: UUID,
    ) -> list[SparseEdge]:
        """
        For neighbors whose edge count dropped below knn_k / 2, find new
        nearest neighbors from the remaining vault pool and insert repair edges.

        Only examines neighbors that were connected to the deleted document.
        Never triggers a full graph rebuild.

        Returns all repair edges that were inserted.
        """
        if not neighbors:
            return []

        threshold = max(1, config.knn_k // 2)
        repair_edges: list[SparseEdge] = []

        for neighbor_id in neighbors:
            remaining_degree = self._fetch_degree(neighbor_id, version_id)
            if remaining_degree >= threshold:
                continue

            neighbor_vec = self._fetch_single_embedding(neighbor_id, config)
            if neighbor_vec is None:
                continue

            candidates = self._fetch_candidate_embeddings(
                vault_id=vault_id,
                exclude_doc_id=neighbor_id,
                config=config,
            )
            # Remove the deleted document and already-connected peers from candidates.
            existing_peers = self._fetch_incident_neighbors(neighbor_id, version_id)
            candidates = {
                cid: vec
                for cid, vec in candidates.items()
                if cid != deleted_doc_id and cid not in existing_peers
            }
            if not candidates:
                continue

            needed = threshold - remaining_degree
            new_edges = self._compute_knn_edges(
                new_doc_id=neighbor_id,
                new_vec=neighbor_vec,
                candidates=candidates,
                knn_k=needed,
            )
            if new_edges:
                self._insert_edges(
                    graph_version_id=version_id,
                    vault_id=vault_id,
                    edges=new_edges,
                )
                repair_edges.extend(new_edges)

        return repair_edges

    def _fetch_degree(self, document_id: UUID, version_id: UUID) -> int:
        """
        Return the number of edges currently incident to document_id in version_id.
        """
        sql = text(
            """
            SELECT COUNT(*)
            FROM graph.vault_graph_edge
            WHERE graph_version_id = :version_id
              AND (doc_lo = :doc_id OR doc_hi = :doc_id)
            """
        )
        with self._engine.connect() as conn:
            row = conn.execute(
                sql,
                {"version_id": str(version_id), "doc_id": str(document_id)},
            ).fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------
    # Version metadata update
    # ------------------------------------------------------------------

    def _increment_version_metadata(
        self,
        version_id: UUID,
        delta_edges: int,
        delta_docs: int,
    ) -> None:
        """
        Increment edge_count and source_document_count by the given deltas.

        Uses GREATEST(..., 0) to prevent negative counts from concurrent
        deletions or unexpected state.
        """
        sql = text(
            """
            UPDATE graph.vault_graph_version
            SET edge_count            = GREATEST(edge_count + :delta_edges, 0),
                source_document_count = GREATEST(source_document_count + :delta_docs, 0)
            WHERE id = :version_id
            """
        )
        with self._engine.connect() as conn:
            conn.execute(
                sql,
                {
                    "version_id": str(version_id),
                    "delta_edges": delta_edges,
                    "delta_docs": delta_docs,
                },
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Job logging
    # ------------------------------------------------------------------

    def _log_graph_job(
        self,
        vault_id: UUID,
        version_id: UUID,
        job_type: str,
        payload: dict,
    ) -> None:
        """
        Insert a completed graph_job row recording the operation.

        Status is set directly to 'completed' since these operations are
        synchronous. started_at and finished_at are both set to now().
        """
        sql = text(
            """
            INSERT INTO graph.graph_job (
                job_type,
                job_scope,
                vault_id,
                graph_version_id,
                status,
                payload,
                started_at,
                finished_at
            ) VALUES (
                :job_type,
                'local',
                :vault_id,
                :graph_version_id,
                'completed',
                :payload,
                now(),
                now()
            )
            """
        )
        with self._engine.connect() as conn:
            conn.execute(
                sql,
                {
                    "job_type": job_type,
                    "vault_id": str(vault_id),
                    "graph_version_id": str(version_id),
                    "payload": json.dumps(payload),
                },
            )
            conn.commit()
