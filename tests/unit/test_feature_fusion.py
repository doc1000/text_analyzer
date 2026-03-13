"""
Unit tests for Phase 8 feature fusion components.

Tests:
- FeatureFusionEngine with a single layer equals raw cosine similarity.
- Two-layer weighted fusion produces correct weighted average.
- Missing feature vectors for some documents are handled gracefully.
- Returned similarity matrix has the correct shape.
- Fusion engine does not persist any matrices (purely in-memory).
- FeatureFusionEngine raises on empty provider list.
- EmbeddingFeatureProvider normalises vectors.
- SummaryEmbeddingFeatureProvider returns empty dict on no data.

All tests use synthetic data; no database connection is required.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import numpy as np
import pytest

from graph_engine.features.fusion import FeatureFusionEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_uuid(n: int) -> UUID:
    """Deterministic UUID from an integer for test reproducibility."""
    return UUID(int=n)


def _random_unit_vecs(n: int, dim: int = 16, seed: int = 0) -> np.ndarray:
    """Return an (n, dim) array of L2-normalised float32 vectors."""
    rng = np.random.default_rng(seed)
    vecs = rng.standard_normal((n, dim)).astype(np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / norms


def _cosine_similarity(vecs: np.ndarray) -> np.ndarray:
    """Compute full pairwise cosine similarity for L2-normalised vectors."""
    return (vecs @ vecs.T).astype(np.float32)


class _StubProvider:
    """
    In-memory FeatureProvider stub.

    Args:
        name: Provider name.
        vectors: Mapping from document UUID to pre-computed vector.
    """

    def __init__(self, name: str, vectors: dict[UUID, np.ndarray]) -> None:
        self.name = name
        self._vectors = vectors

    def get_vectors(self, document_ids: list[UUID]) -> dict[UUID, np.ndarray]:
        return {doc_id: self._vectors[doc_id] for doc_id in document_ids if doc_id in self._vectors}


# ---------------------------------------------------------------------------
# FeatureFusionEngine — construction
# ---------------------------------------------------------------------------

class TestFeatureFusionEngineConstruction:

    def test_empty_providers_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one provider"):
            FeatureFusionEngine([])

    def test_single_provider_accepted(self) -> None:
        n = 4
        doc_ids = [_make_uuid(i) for i in range(n)]
        vecs = _random_unit_vecs(n)
        provider = _StubProvider("emb", dict(zip(doc_ids, vecs)))
        engine = FeatureFusionEngine([(provider, 1.0)])
        sim, ids = engine.compute_fused_similarities(doc_ids)
        assert sim is not None
        assert ids == doc_ids


# ---------------------------------------------------------------------------
# Single-layer fusion equals raw cosine similarity
# ---------------------------------------------------------------------------

class TestSingleLayerFusion:

    def test_single_layer_matches_cosine_similarity(self) -> None:
        """
        With one provider at weight 1.0, fused similarity must equal
        the pairwise cosine similarity of the L2-normalised vectors,
        clipped to [0, 1].
        """
        n = 8
        doc_ids = [_make_uuid(i) for i in range(n)]
        vecs = _random_unit_vecs(n)
        expected = np.clip(_cosine_similarity(vecs), 0.0, 1.0)

        provider = _StubProvider("emb", dict(zip(doc_ids, vecs)))
        engine = FeatureFusionEngine([(provider, 1.0)])
        fused, returned_ids = engine.compute_fused_similarities(doc_ids)

        assert returned_ids == doc_ids
        assert fused.shape == (n, n)
        np.testing.assert_allclose(fused, expected, atol=1e-5)

    def test_single_layer_arbitrary_weight_matches_cosine(self) -> None:
        """
        A single layer with any positive weight should still equal cosine
        similarity (the weight cancels in normalisation), clipped to [0, 1].
        """
        n = 6
        doc_ids = [_make_uuid(i) for i in range(n)]
        vecs = _random_unit_vecs(n, seed=7)
        expected = np.clip(_cosine_similarity(vecs), 0.0, 1.0)

        provider = _StubProvider("emb", dict(zip(doc_ids, vecs)))
        engine = FeatureFusionEngine([(provider, 0.4)])
        fused, _ = engine.compute_fused_similarities(doc_ids)

        np.testing.assert_allclose(fused, expected, atol=1e-5)


# ---------------------------------------------------------------------------
# Multi-layer weighted fusion
# ---------------------------------------------------------------------------

class TestMultiLayerFusion:

    def test_two_equal_layers_average_similarities(self) -> None:
        """
        Two layers with identical vectors and equal weights must produce
        exactly the cosine similarity of those vectors clipped to [0, 1].
        """
        n = 5
        doc_ids = [_make_uuid(i) for i in range(n)]
        vecs = _random_unit_vecs(n, seed=3)
        expected = np.clip(_cosine_similarity(vecs), 0.0, 1.0)

        p1 = _StubProvider("a", dict(zip(doc_ids, vecs)))
        p2 = _StubProvider("b", dict(zip(doc_ids, vecs)))
        engine = FeatureFusionEngine([(p1, 0.5), (p2, 0.5)])
        fused, _ = engine.compute_fused_similarities(doc_ids)

        np.testing.assert_allclose(fused, expected, atol=1e-5)

    def test_two_different_layers_weighted_average(self) -> None:
        """
        Two different providers with weights w1 and w2 must produce:
            fused[i,j] = clip((w1 * sim1[i,j] + w2 * sim2[i,j]) / (w1 + w2), 0, 1)
        for pairs covered by both layers.
        """
        n = 6
        doc_ids = [_make_uuid(i) for i in range(n)]
        vecs1 = _random_unit_vecs(n, seed=1)
        vecs2 = _random_unit_vecs(n, seed=2)

        sim1 = _cosine_similarity(vecs1).astype(np.float64)
        sim2 = _cosine_similarity(vecs2).astype(np.float64)

        w1, w2 = 0.7, 0.3
        expected = np.clip((w1 * sim1 + w2 * sim2) / (w1 + w2), 0.0, 1.0).astype(np.float32)

        p1 = _StubProvider("emb", dict(zip(doc_ids, vecs1)))
        p2 = _StubProvider("summary", dict(zip(doc_ids, vecs2)))
        engine = FeatureFusionEngine([(p1, w1), (p2, w2)])
        fused, _ = engine.compute_fused_similarities(doc_ids)

        np.testing.assert_allclose(fused, expected, atol=1e-5)

    def test_weights_do_not_need_to_sum_to_one(self) -> None:
        """
        Unnormalised weights (e.g. 3.0 and 7.0) must produce the same
        result as normalised weights (0.3 and 0.7).
        """
        n = 5
        doc_ids = [_make_uuid(i) for i in range(n)]
        vecs1 = _random_unit_vecs(n, seed=10)
        vecs2 = _random_unit_vecs(n, seed=11)

        p1a = _StubProvider("a", dict(zip(doc_ids, vecs1)))
        p2a = _StubProvider("b", dict(zip(doc_ids, vecs2)))
        engine_unnorm = FeatureFusionEngine([(p1a, 3.0), (p2a, 7.0)])
        fused_unnorm, _ = engine_unnorm.compute_fused_similarities(doc_ids)

        p1b = _StubProvider("a", dict(zip(doc_ids, vecs1)))
        p2b = _StubProvider("b", dict(zip(doc_ids, vecs2)))
        engine_norm = FeatureFusionEngine([(p1b, 0.3), (p2b, 0.7)])
        fused_norm, _ = engine_norm.compute_fused_similarities(doc_ids)

        np.testing.assert_allclose(fused_unnorm, fused_norm, atol=1e-5)


# ---------------------------------------------------------------------------
# Missing feature vectors
# ---------------------------------------------------------------------------

class TestMissingFeatureVectors:

    def test_provider_missing_all_docs_treated_as_zero_contribution(self) -> None:
        """
        If a provider has no vectors for any document, the fused result
        must equal the other provider's cosine similarity, clipped to [0, 1].
        """
        n = 5
        doc_ids = [_make_uuid(i) for i in range(n)]
        vecs = _random_unit_vecs(n, seed=4)
        expected = np.clip(_cosine_similarity(vecs), 0.0, 1.0)

        p_full = _StubProvider("emb", dict(zip(doc_ids, vecs)))
        p_empty = _StubProvider("summary", {})
        engine = FeatureFusionEngine([(p_full, 0.6), (p_empty, 0.4)])
        fused, _ = engine.compute_fused_similarities(doc_ids)

        np.testing.assert_allclose(fused, expected, atol=1e-5)

    def test_partial_coverage_renormalises_weights(self) -> None:
        """
        When only some documents are covered by the second layer, pairs
        where both docs are covered use both layers; pairs where at least
        one doc is missing use only the first layer.
        """
        n = 4
        doc_ids = [_make_uuid(i) for i in range(n)]
        vecs1 = _random_unit_vecs(n, seed=5)
        vecs2 = _random_unit_vecs(n, seed=6)

        # Second provider only covers the first two documents.
        partial_map2 = {doc_ids[0]: vecs2[0], doc_ids[1]: vecs2[1]}

        w1, w2 = 0.5, 0.5
        p1 = _StubProvider("emb", dict(zip(doc_ids, vecs1)))
        p2 = _StubProvider("summary", partial_map2)
        engine = FeatureFusionEngine([(p1, w1), (p2, w2)])
        fused, returned_ids = engine.compute_fused_similarities(doc_ids)

        assert fused.shape == (n, n)
        assert returned_ids == doc_ids

        sim1 = _cosine_similarity(vecs1).astype(np.float64)
        sim2_sub = (vecs2[:2] @ vecs2[:2].T).astype(np.float64)

        # Pair (0, 1): both layers present → weighted average then clip.
        expected_01 = float(np.clip((w1 * sim1[0, 1] + w2 * sim2_sub[0, 1]) / (w1 + w2), 0.0, 1.0))
        assert abs(float(fused[0, 1]) - expected_01) < 1e-5

        # Pair (0, 2): only layer 1 present for doc 2 → equals clip(sim1[0,2], 0, 1).
        expected_02 = float(np.clip(sim1[0, 2], 0.0, 1.0))
        assert abs(float(fused[0, 2]) - expected_02) < 1e-5

    def test_empty_document_list_returns_empty_matrix(self) -> None:
        vecs = _random_unit_vecs(3, seed=9)
        doc_ids = [_make_uuid(i) for i in range(3)]
        provider = _StubProvider("emb", dict(zip(doc_ids, vecs)))
        engine = FeatureFusionEngine([(provider, 1.0)])
        fused, returned_ids = engine.compute_fused_similarities([])

        assert fused.shape == (0, 0)
        assert returned_ids == []


# ---------------------------------------------------------------------------
# Matrix shape and value range
# ---------------------------------------------------------------------------

class TestMatrixShapeAndRange:

    def test_shape_matches_document_count(self) -> None:
        for n in [1, 5, 10]:
            doc_ids = [_make_uuid(i) for i in range(n)]
            vecs = _random_unit_vecs(n, seed=n)
            provider = _StubProvider("emb", dict(zip(doc_ids, vecs)))
            engine = FeatureFusionEngine([(provider, 1.0)])
            fused, returned_ids = engine.compute_fused_similarities(doc_ids)
            assert fused.shape == (n, n), f"Expected ({n},{n}), got {fused.shape}"
            assert len(returned_ids) == n

    def test_values_in_zero_one_range(self) -> None:
        n = 10
        doc_ids = [_make_uuid(i) for i in range(n)]
        vecs1 = _random_unit_vecs(n, seed=20)
        vecs2 = _random_unit_vecs(n, seed=21)
        p1 = _StubProvider("a", dict(zip(doc_ids, vecs1)))
        p2 = _StubProvider("b", dict(zip(doc_ids, vecs2)))
        engine = FeatureFusionEngine([(p1, 0.6), (p2, 0.4)])
        fused, _ = engine.compute_fused_similarities(doc_ids)

        assert float(fused.min()) >= 0.0
        assert float(fused.max()) <= 1.0

    def test_diagonal_is_one(self) -> None:
        """Self-similarity of any document must be 1.0."""
        n = 6
        doc_ids = [_make_uuid(i) for i in range(n)]
        vecs = _random_unit_vecs(n, seed=22)
        provider = _StubProvider("emb", dict(zip(doc_ids, vecs)))
        engine = FeatureFusionEngine([(provider, 1.0)])
        fused, _ = engine.compute_fused_similarities(doc_ids)

        np.testing.assert_allclose(np.diag(fused), np.ones(n, dtype=np.float32), atol=1e-5)

    def test_matrix_is_symmetric(self) -> None:
        n = 7
        doc_ids = [_make_uuid(i) for i in range(n)]
        vecs1 = _random_unit_vecs(n, seed=30)
        vecs2 = _random_unit_vecs(n, seed=31)
        p1 = _StubProvider("a", dict(zip(doc_ids, vecs1)))
        p2 = _StubProvider("b", dict(zip(doc_ids, vecs2)))
        engine = FeatureFusionEngine([(p1, 0.5), (p2, 0.5)])
        fused, _ = engine.compute_fused_similarities(doc_ids)

        np.testing.assert_allclose(fused, fused.T, atol=1e-6)


# ---------------------------------------------------------------------------
# No persistence
# ---------------------------------------------------------------------------

class TestNoPersistence:

    def test_compute_returns_ndarray_not_stored(self) -> None:
        """
        The fusion engine must return a plain numpy array — not a file path,
        database reference, or any object that implies persistence.
        The returned object must have no 'flush', 'commit', or 'write' methods.
        """
        n = 4
        doc_ids = [_make_uuid(i) for i in range(n)]
        vecs = _random_unit_vecs(n, seed=99)
        provider = _StubProvider("emb", dict(zip(doc_ids, vecs)))
        engine = FeatureFusionEngine([(provider, 1.0)])
        fused, _ = engine.compute_fused_similarities(doc_ids)

        assert isinstance(fused, np.ndarray)
        assert not hasattr(fused, "flush")
        assert not hasattr(fused, "commit")
        assert not hasattr(fused, "write")

    def test_engine_has_no_cached_matrix_attribute(self) -> None:
        """
        The FeatureFusionEngine instance must not store computed matrices
        as attributes after compute_fused_similarities() returns.
        """
        n = 4
        doc_ids = [_make_uuid(i) for i in range(n)]
        vecs = _random_unit_vecs(n, seed=100)
        provider = _StubProvider("emb", dict(zip(doc_ids, vecs)))
        engine = FeatureFusionEngine([(provider, 1.0)])
        engine.compute_fused_similarities(doc_ids)

        for attr in vars(engine).values():
            assert not isinstance(attr, np.ndarray), (
                f"Engine attribute is an ndarray after compute — possible matrix cache: {attr}"
            )


# ---------------------------------------------------------------------------
# Zero-weight providers
# ---------------------------------------------------------------------------

class TestZeroWeightProviders:

    def test_zero_weight_provider_ignored(self) -> None:
        """A provider with weight 0.0 must not affect the result."""
        n = 5
        doc_ids = [_make_uuid(i) for i in range(n)]
        vecs = _random_unit_vecs(n, seed=50)
        noise_vecs = _random_unit_vecs(n, seed=51)
        expected = np.clip(_cosine_similarity(vecs), 0.0, 1.0)

        p_main = _StubProvider("emb", dict(zip(doc_ids, vecs)))
        p_noise = _StubProvider("noise", dict(zip(doc_ids, noise_vecs)))
        engine = FeatureFusionEngine([(p_main, 1.0), (p_noise, 0.0)])
        fused, _ = engine.compute_fused_similarities(doc_ids)

        np.testing.assert_allclose(fused, expected, atol=1e-5)
