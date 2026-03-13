"""
FeatureProvider protocol — the contract all feature layers must satisfy.

A FeatureProvider is a read-only adapter that retrieves L2-normalised
feature vectors for a set of document IDs from an approved upstream source.

Design rules (from GRAPH_ARCHITECTURE_RULES.md):
- Feature extraction lives outside this repository.
- Feature fusion (consuming these vectors) lives inside.
- Providers must never persist any matrix; they only return vectors.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

import numpy as np


class FeatureProvider(Protocol):
    """
    Protocol for any feature layer that can supply document vectors.

    Implementors read from approved upstream tables (e.g. the embedding
    schema) and return L2-normalised vectors.  Documents without a vector
    for this feature layer are silently omitted from the result dict; the
    FeatureFusionEngine handles the missing-document case via weight
    renormalisation.
    """

    name: str

    def get_vectors(self, document_ids: list[UUID]) -> dict[UUID, np.ndarray]:
        """
        Return L2-normalised feature vectors keyed by document UUID.

        Only documents that have a vector for this layer are included.
        The returned numpy arrays must be one-dimensional float32 vectors
        of identical dimension across all returned entries.

        Args:
            document_ids: The documents to retrieve vectors for.

        Returns:
            Mapping from document UUID to normalised 1-D float32 array.
            May be empty if no vectors exist for any of the requested IDs.
        """
        ...
