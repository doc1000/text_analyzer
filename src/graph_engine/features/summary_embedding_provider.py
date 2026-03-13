"""
SummaryEmbeddingFeatureProvider — reads summary-level embeddings from the
embedding schema and returns L2-normalised vectors.

This is an optional initial feature layer.  It reads from the table
specified by graph_config.summary_embedding_source.  If no summary
embeddings exist for any requested document, an empty dict is returned
and the FeatureFusionEngine renormalises weights accordingly.
"""

from __future__ import annotations

from uuid import UUID

import numpy as np
from sqlalchemy import text
from sqlalchemy.engine import Engine


class SummaryEmbeddingFeatureProvider:
    """
    Reads document summary embeddings from an approved upstream table.

    Satisfies the FeatureProvider protocol.

    Behaves identically to EmbeddingFeatureProvider but reads from the
    summary embedding source.  A completely empty result (no summaries
    available at all) is a valid outcome and does not raise an error.

    Args:
        engine: SQLAlchemy engine pointing to the shared PostgreSQL database.
        summary_embedding_source: Table name within the 'embedding' schema
            (e.g. 'summary_minilm_v1_384'), or a fully qualified name.
    """

    def __init__(self, engine: Engine, summary_embedding_source: str) -> None:
        self._engine = engine
        raw = summary_embedding_source.strip()
        self._qualified_table = raw if "." in raw else f"embedding.{raw}"
        self.name: str = f"summary_embedding:{raw}"

    def get_vectors(self, document_ids: list[UUID]) -> dict[UUID, np.ndarray]:
        """
        Fetch one summary embedding vector per document (chunk_index = 0).

        Returns L2-normalised float32 vectors.  Returns an empty dict if
        no summary embeddings exist for any of the requested documents.
        """
        if not document_ids:
            return {}

        sql = text(
            f"""
            SELECT document_id, embedding
            FROM {self._qualified_table}
            WHERE document_id = ANY(:doc_ids)
              AND chunk_index = 0
            """
        )
        doc_id_strs = [str(d) for d in document_ids]
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(sql, {"doc_ids": doc_id_strs}).fetchall()
        except Exception:
            # Summary table may not exist yet; treat as empty result.
            return {}

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
