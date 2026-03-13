"""
GraphBuilder — in-memory sparse kNN graph construction from document embeddings.

Phase 1: builds a SparseGraph entirely in memory from embeddings.
Phase 2: persists the SparseGraph to graph.vault_graph_edge and manages
         a graph version row in graph.vault_graph_version.
Phase 8: replaces direct embedding access with FeatureFusionEngine so that
         multiple weighted feature layers can contribute to graph construction.

Design constraints (from architecture rules):
- Dense distance/similarity matrices are ephemeral; never persisted.
- Canonical edge ordering: doc_lo < doc_hi (enforced via SparseEdge.make()).
- Each undirected pair stored exactly once.
- Empty vault raises ValueError.
- SparseGraph is still returned unchanged; persistence is additional behaviour.
"""

from __future__ import annotations

from uuid import UUID

import numpy as np
from sqlalchemy import text
from sqlalchemy.engine import Engine

from graph_engine.db.models.graph_config import GraphConfigRow, get_graph_config
from graph_engine.db.repositories.graph_edge_repo import GraphEdgeRepo
from graph_engine.db.repositories.graph_version_repo import GraphVersionRepo
from graph_engine.features.embedding_provider import EmbeddingFeatureProvider
from graph_engine.features.fusion import FeatureFusionEngine
from graph_engine.features.summary_embedding_provider import SummaryEmbeddingFeatureProvider
from graph_engine.graph_core.types import SparseEdge, SparseGraph


class GraphBuilder:
    """
    Constructs an in-memory sparse semantic graph for a vault and persists it.

    The engine must point to the shared PostgreSQL database.
    All matrix operations are ephemeral and never written to the database.

    Phase 2 adds optional persistence: if built_by is supplied or
    persist=True (the default), the graph is saved to the database and a
    version row is created, finalised, and activated.

    Phase 8 delegates similarity computation to FeatureFusionEngine so that
    multiple weighted feature layers (embeddings, summary embeddings, …) can
    contribute to graph construction via graph_config.feature_weights.

    The method always returns the SparseGraph regardless of persistence.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._version_repo = GraphVersionRepo(engine)
        self._edge_repo = GraphEdgeRepo(engine)

    def build_vault_graph(
        self,
        vault_id: UUID,
        config_id: UUID,
        built_by: UUID | None = None,
        persist: bool = True,
    ) -> SparseGraph:
        """
        Build a sparse kNN graph for all documents in a vault.

        Steps:
        1. Load graph config.
        2. Fetch document IDs in the vault.
        3. Build FeatureFusionEngine from config.feature_weights
           (defaults to embeddings-only with weight 1.0 when empty).
        4. Compute fused cosine similarity matrix in memory (ephemeral).
        5. For each document select top knn_k neighbors from candidate_k candidates.
        6. Build SparseEdge list with canonical ordering.
        7. Deduplicate symmetric edges.
        8. (Phase 2) If persist=True: create version, save edges, finalise, activate.
        9. Return SparseGraph.

        Raises:
            ValueError: if vault has no documents or no embeddings are found.

        On persistence failure the version is marked as 'failed' and the
        exception is re-raised so the caller can decide how to handle it.
        """
        config = get_graph_config(self._engine, config_id)

        doc_ids = self._fetch_vault_document_ids(vault_id)
        if not doc_ids:
            raise ValueError(
                f"Vault {vault_id} has no documents. Cannot build graph."
            )

        fusion_engine = self._build_fusion_engine(config)
        sim_matrix, ordered_ids = fusion_engine.compute_fused_similarities(doc_ids)

        if not ordered_ids:
            raise ValueError(
                f"No feature vectors found for vault {vault_id} using source "
                f"'{config.embedding_source}'. Cannot build graph."
            )

        edges = self._build_knn_edges(
            ordered_ids=ordered_ids,
            similarity=sim_matrix,
            knn_k=config.knn_k,
            candidate_k=config.candidate_k,
        )

        sparse_graph = SparseGraph(
            nodes=ordered_ids,
            edges=edges,
            vault_id=vault_id,
            config_id=config_id,
        )

        if persist:
            self._persist_graph(
                sparse_graph=sparse_graph,
                vault_id=vault_id,
                config_id=config_id,
                built_by=built_by,
            )

        return sparse_graph

    # ------------------------------------------------------------------
    # Persistence helpers (Phase 2)
    # ------------------------------------------------------------------

    def _persist_graph(
        self,
        sparse_graph: SparseGraph,
        vault_id: UUID,
        config_id: UUID,
        built_by: UUID | None,
    ) -> None:
        """
        Create a graph version, save all edges, finalise, and activate it.

        If any step fails the version is marked as 'failed' and the
        original exception is re-raised.
        """
        version_row = self._version_repo.create_version(
            vault_id=vault_id,
            config_id=config_id,
            built_by=built_by,
        )
        version_id = UUID(str(version_row["id"]))

        try:
            edge_count = self._edge_repo.save_graph(
                graph_version_id=version_id,
                vault_id=vault_id,
                sparse_graph=sparse_graph,
            )
            self._version_repo.finalize_version(
                version_id=version_id,
                edge_count=edge_count,
                doc_count=len(sparse_graph.nodes),
            )
            self._version_repo.activate_version(version_id=version_id)
        except Exception as exc:
            self._version_repo.fail_version(
                version_id=version_id,
                error_message=str(exc),
            )
            raise

    # ------------------------------------------------------------------
    # Feature fusion (Phase 8)
    # ------------------------------------------------------------------

    def _build_fusion_engine(self, config: GraphConfigRow) -> FeatureFusionEngine:
        """
        Construct a FeatureFusionEngine from graph config.

        feature_weights is a JSONB dict of the form:
            {"embedding": 0.7, "summary_embedding": 0.3}

        If feature_weights is empty the engine defaults to embeddings-only
        with weight 1.0 (backward compatibility with Phase 1/2 configs).
        """
        feature_weights: dict[str, float] = {
            k: float(v) for k, v in (config.feature_weights or {}).items()
        }

        providers: list[tuple[object, float]] = []

        if feature_weights:
            emb_weight = feature_weights.get("embedding", 0.0)
            if emb_weight > 0.0:
                providers.append(
                    (
                        EmbeddingFeatureProvider(self._engine, config.embedding_source),
                        emb_weight,
                    )
                )

            summary_weight = feature_weights.get("summary_embedding", 0.0)
            if summary_weight > 0.0 and config.summary_embedding_source:
                providers.append(
                    (
                        SummaryEmbeddingFeatureProvider(
                            self._engine, config.summary_embedding_source
                        ),
                        summary_weight,
                    )
                )
        else:
            # Default: embeddings-only, weight 1.0.
            providers.append(
                (
                    EmbeddingFeatureProvider(self._engine, config.embedding_source),
                    1.0,
                )
            )

        if not providers:
            # All configured weights were zero or sources missing; fall back.
            providers.append(
                (
                    EmbeddingFeatureProvider(self._engine, config.embedding_source),
                    1.0,
                )
            )

        return FeatureFusionEngine(providers)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_vault_document_ids(self, vault_id: UUID) -> list[UUID]:
        sql = text(
            """
            SELECT id FROM public.documents
            WHERE vault_id = :vault_id
            ORDER BY id
            """
        )
        with self._engine.connect() as conn:
            rows = conn.execute(sql, {"vault_id": str(vault_id)}).fetchall()
        return [UUID(str(row[0])) for row in rows]

    def _build_knn_edges(
        self,
        ordered_ids: list[UUID],
        similarity: np.ndarray,
        knn_k: int,
        candidate_k: int,
    ) -> list[SparseEdge]:
        """
        Compute kNN edges from a pre-computed cosine similarity matrix.

        similarity shape: (n_docs, n_docs) — ephemeral, provided by
        FeatureFusionEngine.compute_fused_similarities().

        Steps:
        - For each row, select top candidate_k candidates (excluding self).
        - From those, keep top knn_k by similarity.
        - Build SparseEdge list with canonical ordering and deduplication.

        The similarity matrix is passed in as a local variable and is
        never persisted.
        """
        n = len(ordered_ids)
        knn_k = min(knn_k, n - 1)
        candidate_k = min(candidate_k, n - 1)

        seen: set[tuple[UUID, UUID]] = set()
        edges: list[SparseEdge] = []

        for i in range(n):
            sim_row = similarity[i].copy()

            # Exclude self (index i) from candidates.
            sim_row[i] = -np.inf

            # Select top candidate_k indices by similarity.
            if candidate_k >= n - 1:
                candidate_indices = list(range(n))
                candidate_indices.remove(i)
            else:
                # argpartition gives top-k in O(n); then sort within the partition.
                part = np.argpartition(sim_row, -(candidate_k))
                candidate_indices = part[-(candidate_k):].tolist()

            # Sort candidates by descending similarity and take top knn_k.
            candidate_indices.sort(key=lambda j: sim_row[j], reverse=True)
            neighbor_indices = candidate_indices[:knn_k]

            doc_i = ordered_ids[i]
            for rank, j in enumerate(neighbor_indices):
                doc_j = ordered_ids[j]
                lo, hi = (doc_i, doc_j) if doc_i < doc_j else (doc_j, doc_i)
                pair = (lo, hi)
                if pair in seen:
                    continue
                seen.add(pair)

                sim_val = float(sim_row[j])
                # Clamp to [0, 1] to guard against floating-point noise.
                sim_val = max(0.0, min(1.0, sim_val))
                distance = 1.0 - sim_val

                rank_lo = rank if doc_i == lo else None
                rank_hi = rank if doc_i == hi else None

                edges.append(
                    SparseEdge(
                        doc_lo=lo,
                        doc_hi=hi,
                        weight=sim_val,
                        distance=distance,
                        rank_lo=rank_lo,
                        rank_hi=rank_hi,
                    )
                )

        return edges
