"""
EmbeddingFeatureProvider — reads document-level embeddings from the
embedding schema and returns L2-normalised vectors.

This is the required initial feature layer.  It reads from the table
specified by graph_config.embedding_source (qualified under the
'embedding' schema if not already qualified).

Only chunk_index = 0 is used as the canonical document-level embedding.
Documents without an embedding at chunk 0 are silently omitted.
"""

from __future__ import annotations

from uuid import UUID

import numpy as np
from sqlalchemy import text
from sqlalchemy.engine import Engine


class EmbeddingFeatureProvider:
    """
    Reads document embeddings from an approved upstream embedding table.

    Satisfies the FeatureProvider protocol.

    Args:
        engine: SQLAlchemy engine pointing to the shared PostgreSQL database.
        embedding_source: Table name within the 'embedding' schema
            (e.g. 'all_minilm_v1_384'), or a fully qualified name
            (e.g. 'embedding.all_minilm_v1_384').
    """

    def __init__(self, engine: Engine, embedding_source: str) -> None:
        self._engine = engine
        raw = embedding_source.strip()
        self._qualified_table = raw if "." in raw else f"embedding.{raw}"
        self.name: str = f"embedding:{raw}"

    def get_vectors(self, document_ids: list[UUID]) -> dict[UUID, np.ndarray]:
        """
        Fetch one embedding vector per document (chunk_index = 0).

        Returns L2-normalised float32 vectors.  Documents without an
        embedding at chunk 0 are omitted from the result.
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
        with self._engine.connect() as conn:
            rows = conn.execute(sql, {"doc_ids": doc_id_strs}).fetchall()

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
