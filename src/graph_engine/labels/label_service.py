"""
LabelService — extractive label generation for graph groups.

Responsibilities:
- generate_label:             compute a short label string from document titles.
- generate_labels_for_groups: generate and persist labels for all groups in a
                              graph version.
- attach_labels:              enrich a graph view payload dict with cached labels.

Design constraints:
    - No LLM dependency. Labels are computed using simple term-frequency
      analysis over document titles retrieved from public.documents.
    - Label generation must never block graph payload creation. Callers
      should invoke generate_labels_for_groups asynchronously when possible.
    - attach_labels must always return a valid payload, even when no labels
      are cached. Label absence is not an error.
    - Reads only from public.documents (approved upstream contract).
    - Writes only to graph.graph_label via LabelRepository.
"""

from __future__ import annotations

import re
from collections import Counter
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine

from graph_engine.labels.cache import LabelRepository


# Terms to exclude from label generation. Kept minimal to avoid over-filtering;
# no external NLP library is required.
_STOP_WORDS: frozenset[str] = frozenset(
    {
        "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will", "would",
        "could", "should", "may", "might", "shall", "can", "not", "no", "nor",
        "so", "yet", "both", "either", "neither", "as", "if", "then", "than",
        "that", "this", "these", "those", "it", "its", "i", "we", "you", "he",
        "she", "they", "me", "him", "her", "us", "them", "my", "our", "your",
        "his", "their", "what", "which", "who", "whom", "how", "when", "where",
        "why", "all", "any", "each", "every", "few", "more", "most", "other",
        "some", "such", "only", "own", "same", "too", "very", "just", "about",
        "above", "after", "before", "between", "into", "through", "during",
        "over", "under", "again", "further", "once", "here", "there",
    }
)

_FALLBACK_LABEL: str = "Unlabeled group"

# Target word count for generated labels.
_LABEL_WORD_COUNT: int = 3


def _tokenize(text_input: str) -> list[str]:
    """
    Split text into lowercase alphabetic tokens, filtered by stop words.

    Non-alphabetic characters are treated as delimiters.
    """
    raw = re.findall(r"[a-zA-Z]+", text_input.lower())
    return [w for w in raw if w not in _STOP_WORDS and len(w) > 1]


def _fetch_titles(engine: Engine, document_ids: list[UUID]) -> list[str]:
    """
    Retrieve document titles from public.documents for the given IDs.

    Returns an empty list when document_ids is empty or no titles are found.
    Reads only from the approved public.documents table.
    """
    if not document_ids:
        return []

    sql = text(
        """
        SELECT title
        FROM public.documents
        WHERE id = ANY(:doc_ids)
          AND title IS NOT NULL
          AND title <> ''
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(
            sql, {"doc_ids": [str(d) for d in document_ids]}
        ).fetchall()

    return [str(row[0]) for row in rows]


class LabelService:
    """
    Generates and attaches extractive labels for graph groups.

    Args:
        engine:     SQLAlchemy engine pointing to the shared PostgreSQL database.
        repository: LabelRepository instance for label persistence.
    """

    def __init__(self, engine: Engine, repository: LabelRepository) -> None:
        self._engine = engine
        self._repo = repository

    # ------------------------------------------------------------------
    # generate_label
    # ------------------------------------------------------------------

    def generate_label(self, document_ids: list[UUID]) -> str:
        """
        Compute a short extractive label from the titles of the given documents.

        Algorithm:
        1. Fetch titles from public.documents.
        2. Tokenise and remove stop words.
        3. Pick the top-N most frequent terms (N = _LABEL_WORD_COUNT).
        4. Return them joined by space, title-cased.
        5. Fall back to _FALLBACK_LABEL if no usable tokens are found.

        Returns a string of 2–5 words in practice.
        """
        titles = _fetch_titles(self._engine, document_ids)
        if not titles:
            return _FALLBACK_LABEL

        tokens = []
        for title in titles:
            tokens.extend(_tokenize(title))

        if not tokens:
            return _FALLBACK_LABEL

        most_common = Counter(tokens).most_common(_LABEL_WORD_COUNT)
        label_words = [word for word, _ in most_common]

        if not label_words:
            return _FALLBACK_LABEL

        return " ".join(w.capitalize() for w in label_words)

    # ------------------------------------------------------------------
    # generate_labels_for_groups
    # ------------------------------------------------------------------

    def generate_labels_for_groups(
        self,
        groups: dict[str, list[UUID]],
        graph_version_id: UUID,
        vault_id: UUID,
    ) -> dict[str, str]:
        """
        Generate and persist labels for all groups in a graph version.

        For each group_key → document_ids mapping:
        1. Call generate_label() to compute an extractive label.
        2. Persist via LabelRepository.save_label().

        Returns {group_key: label_text} for all processed groups.

        This method may be called asynchronously from callers that do not
        want to block graph payload creation.
        """
        result: dict[str, str] = {}
        for group_key, document_ids in groups.items():
            label = self.generate_label(document_ids)
            self._repo.save_label(
                graph_version_id=graph_version_id,
                vault_id=vault_id,
                group_key=group_key,
                label=label,
            )
            result[group_key] = label
        return result

    # ------------------------------------------------------------------
    # attach_labels
    # ------------------------------------------------------------------

    def attach_labels(
        self,
        graph_view_payload: dict,
        graph_version_id: UUID,
    ) -> dict:
        """
        Attach cached labels to a grouped graph view payload.

        Behaviour:
        - Load all cached labels for the graph version.
        - If no labels exist, return the payload unchanged (no error raised).
        - If the payload contains a 'groups' key, attach a 'group_labels'
          dict mapping group_key → label_text.
        - Node-level group annotations are not modified; callers can cross-
          reference group_labels using the node's existing 'group' field.

        The payload is never mutated; a shallow copy of the top-level dict
        is returned with 'group_labels' added or updated.
        """
        labels = self._repo.get_labels_for_version(graph_version_id)

        if not labels:
            return graph_view_payload

        enriched = dict(graph_view_payload)
        enriched["group_labels"] = labels
        return enriched
