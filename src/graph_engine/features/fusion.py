"""
FeatureFusionEngine — weighted multi-layer cosine similarity fusion.

Accepts a list of (FeatureProvider, weight) pairs and computes a single
fused similarity matrix over the requested documents.

Design rules (from GRAPH_SYSTEM_INTEGRATION_CONTRACT.md):
- Dense similarity matrices are ephemeral; never persisted.
- Missing feature vectors for a document cause per-pair weight
  renormalisation rather than a hard failure.
- The fused matrix is normalised to [0, 1].

Usage::

    fusion = FeatureFusionEngine([
        (EmbeddingFeatureProvider(engine, src), 0.7),
        (SummaryEmbeddingFeatureProvider(engine, summary_src), 0.3),
    ])
    sim_matrix, ordered_ids = fusion.compute_fused_similarities(doc_ids)
"""

from __future__ import annotations

from uuid import UUID

import numpy as np


class FeatureFusionEngine:
    """
    Computes a weighted sum of per-layer cosine similarity matrices.

    All matrices are local variables and are never written to the
    database (architecture constraint).

    Args:
        providers: List of (provider, weight) tuples.  Weights need not
            sum to 1.0; they are renormalised internally.  At least one
            provider must be supplied.

    Raises:
        ValueError: if the providers list is empty.
    """

    def __init__(self, providers: list[tuple[object, float]]) -> None:
        if not providers:
            raise ValueError("FeatureFusionEngine requires at least one provider.")
        self._providers: list[tuple[object, float]] = providers

    def compute_fused_similarities(
        self,
        document_ids: list[UUID],
    ) -> tuple[np.ndarray, list[UUID]]:
        """
        Compute a fused cosine similarity matrix over the given documents.

        Steps:
        1. For each (provider, weight), call provider.get_vectors().
        2. Build a per-layer similarity matrix using only the documents
           present in that layer's result.
        3. Weight and accumulate into a running sum matrix.
        4. Track per-pair effective weight (sum of weights for layers
           where both documents had a vector).
        5. Divide by effective weights to normalise (pairs covered by no
           layer get similarity 0.0).
        6. Clip to [0, 1] to handle floating-point noise.
        7. Return (fused_matrix, ordered_ids) where ordered_ids preserves
           the input document order (documents not in any layer are still
           included with 0.0 rows/columns).

        The similarity matrix is an ephemeral local variable; it is never
        persisted.

        Returns:
            Tuple of:
            - fused similarity matrix, shape (n, n), dtype float32
            - list of document UUIDs matching the matrix row/column order
        """
        n = len(document_ids)
        if n == 0:
            return np.zeros((0, 0), dtype=np.float32), []

        id_to_idx: dict[UUID, int] = {doc_id: i for i, doc_id in enumerate(document_ids)}

        # Accumulation buffers — both are ephemeral.
        weighted_sum = np.zeros((n, n), dtype=np.float64)
        weight_sum = np.zeros((n, n), dtype=np.float64)

        for provider, weight in self._providers:
            if weight <= 0.0:
                continue

            layer_vectors: dict[UUID, np.ndarray] = provider.get_vectors(document_ids)
            if not layer_vectors:
                continue

            # Build ordered arrays for documents present in this layer.
            present_ids = [doc_id for doc_id in document_ids if doc_id in layer_vectors]
            if not present_ids:
                continue

            present_indices = np.array(
                [id_to_idx[doc_id] for doc_id in present_ids], dtype=np.int64
            )
            vecs = np.array(
                [layer_vectors[doc_id] for doc_id in present_ids], dtype=np.float64
            )

            # Cosine similarity for the present-document submatrix.
            # Vectors are already L2-normalised by the providers; the dot
            # product of normalised vectors equals cosine similarity.
            layer_sim = vecs @ vecs.T  # shape: (m, m), ephemeral

            # Scatter into the full n×n matrix at the positions of the
            # documents that are present in this layer.
            rows = present_indices[:, np.newaxis]
            cols = present_indices[np.newaxis, :]
            weighted_sum[rows, cols] += weight * layer_sim
            weight_sum[rows, cols] += weight

        # Normalise per-pair by the sum of weights covering that pair.
        # Pairs where no layer contributed remain 0.0.
        with np.errstate(invalid="ignore", divide="ignore"):
            fused = np.where(weight_sum > 0.0, weighted_sum / weight_sum, 0.0)

        # Clip to [0, 1] to guard against floating-point noise.
        fused = np.clip(fused, 0.0, 1.0).astype(np.float32)

        return fused, list(document_ids)
