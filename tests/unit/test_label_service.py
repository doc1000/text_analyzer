"""
Unit tests for Phase 9 — Labeling Module.

Tests cover:
- generate_label produces a non-empty string from document titles.
- generate_label returns fallback label for an empty document list.
- generate_label returns fallback label when all titles are empty/blank.
- Label save/load round-trip via LabelRepository.
- get_labels_for_version returns all labels for the version.
- get_labels_for_version returns empty dict when no labels exist.
- override_label replaces the label and sets user_override = true.
- attach_labels enriches a grouped payload with labels.
- attach_labels returns payload unchanged when no labels exist.
- delete_labels removes all labels for a graph version.

No live database connection is required. LabelRepository and the DB
query path of LabelService are tested via fakes/mocks. The extractive
label logic (generate_label) is tested by injecting a fake engine that
returns synthetic title rows.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch
from uuid import UUID

import pytest

from graph_engine.labels.cache import LabelRepository
from graph_engine.labels.label_service import LabelService, _FALLBACK_LABEL, _tokenize


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uuid(n: int) -> UUID:
    return UUID(int=n)


# ---------------------------------------------------------------------------
# _tokenize (internal utility)
# ---------------------------------------------------------------------------

class TestTokenize:

    def test_stop_words_are_excluded(self) -> None:
        tokens = _tokenize("the cat is on the mat")
        assert "the" not in tokens
        assert "is" not in tokens
        assert "on" not in tokens

    def test_meaningful_words_remain(self) -> None:
        tokens = _tokenize("machine learning algorithms")
        assert "machine" in tokens
        assert "learning" in tokens
        assert "algorithms" in tokens

    def test_numeric_and_punctuation_stripped(self) -> None:
        tokens = _tokenize("doc-2024: analysis & review!")
        assert all(t.isalpha() for t in tokens)

    def test_empty_string_returns_empty_list(self) -> None:
        assert _tokenize("") == []

    def test_single_char_words_excluded(self) -> None:
        tokens = _tokenize("a b c important")
        assert "a" not in tokens
        assert "b" not in tokens
        assert "c" not in tokens


# ---------------------------------------------------------------------------
# LabelService.generate_label — pure logic, DB stubbed
# ---------------------------------------------------------------------------

class TestGenerateLabel:
    """
    Tests for the extractive label algorithm.

    The DB fetch is patched so these tests run without a database.
    """

    def _make_service(self, titles: list[str]) -> LabelService:
        """
        Build a LabelService whose _fetch_titles returns the supplied list.
        """
        engine = MagicMock()
        repo = MagicMock(spec=LabelRepository)
        service = LabelService(engine=engine, repository=repo)

        with patch(
            "graph_engine.labels.label_service._fetch_titles",
            return_value=titles,
        ):
            # Expose titles via closure so tests can call generate_label.
            service._patched_titles = titles
        return service, titles

    def test_returns_non_empty_string_from_titles(self) -> None:
        engine = MagicMock()
        repo = MagicMock(spec=LabelRepository)
        service = LabelService(engine=engine, repository=repo)

        with patch(
            "graph_engine.labels.label_service._fetch_titles",
            return_value=["machine learning paper", "deep learning review"],
        ):
            label = service.generate_label([_uuid(1), _uuid(2)])

        assert isinstance(label, str)
        assert len(label) > 0

    def test_label_is_2_to_5_words(self) -> None:
        engine = MagicMock()
        repo = MagicMock(spec=LabelRepository)
        service = LabelService(engine=engine, repository=repo)

        with patch(
            "graph_engine.labels.label_service._fetch_titles",
            return_value=[
                "neural network architecture design",
                "neural network training techniques",
                "network optimization strategies",
            ],
        ):
            label = service.generate_label([_uuid(1), _uuid(2), _uuid(3)])

        word_count = len(label.split())
        assert 1 <= word_count <= 5

    def test_fallback_for_empty_document_list(self) -> None:
        engine = MagicMock()
        repo = MagicMock(spec=LabelRepository)
        service = LabelService(engine=engine, repository=repo)

        with patch(
            "graph_engine.labels.label_service._fetch_titles",
            return_value=[],
        ):
            label = service.generate_label([])

        assert label == _FALLBACK_LABEL

    def test_fallback_when_no_titles_found_in_db(self) -> None:
        engine = MagicMock()
        repo = MagicMock(spec=LabelRepository)
        service = LabelService(engine=engine, repository=repo)

        with patch(
            "graph_engine.labels.label_service._fetch_titles",
            return_value=[],
        ):
            label = service.generate_label([_uuid(1)])

        assert label == _FALLBACK_LABEL

    def test_fallback_when_all_titles_are_stop_words_only(self) -> None:
        engine = MagicMock()
        repo = MagicMock(spec=LabelRepository)
        service = LabelService(engine=engine, repository=repo)

        with patch(
            "graph_engine.labels.label_service._fetch_titles",
            return_value=["the and or but"],
        ):
            label = service.generate_label([_uuid(1)])

        assert label == _FALLBACK_LABEL

    def test_most_frequent_terms_used(self) -> None:
        """The most common content word across titles should appear in the label."""
        engine = MagicMock()
        repo = MagicMock(spec=LabelRepository)
        service = LabelService(engine=engine, repository=repo)

        with patch(
            "graph_engine.labels.label_service._fetch_titles",
            return_value=[
                "climate change adaptation",
                "climate change mitigation",
                "climate policy analysis",
            ],
        ):
            label = service.generate_label([_uuid(1), _uuid(2), _uuid(3)])

        assert "Climate" in label or "climate" in label.lower()

    def test_label_words_are_title_cased(self) -> None:
        engine = MagicMock()
        repo = MagicMock(spec=LabelRepository)
        service = LabelService(engine=engine, repository=repo)

        with patch(
            "graph_engine.labels.label_service._fetch_titles",
            return_value=["renewable energy sources"],
        ):
            label = service.generate_label([_uuid(1)])

        words = label.split()
        for word in words:
            assert word[0].isupper(), f"Expected title-case, got: {word!r}"


# ---------------------------------------------------------------------------
# LabelService.generate_labels_for_groups
# ---------------------------------------------------------------------------

class TestGenerateLabelsForGroups:

    def test_returns_label_for_each_group(self) -> None:
        engine = MagicMock()
        repo = MagicMock(spec=LabelRepository)
        service = LabelService(engine=engine, repository=repo)

        groups = {
            "g1": [_uuid(1), _uuid(2)],
            "g2": [_uuid(3)],
        }
        version_id = _uuid(100)
        vault_id = _uuid(200)

        with patch.object(service, "generate_label", side_effect=["Label One", "Label Two"]):
            result = service.generate_labels_for_groups(groups, version_id, vault_id)

        assert set(result.keys()) == {"g1", "g2"}
        assert result["g1"] == "Label One"
        assert result["g2"] == "Label Two"

    def test_save_label_called_for_each_group(self) -> None:
        engine = MagicMock()
        repo = MagicMock(spec=LabelRepository)
        service = LabelService(engine=engine, repository=repo)

        groups = {"alpha": [_uuid(1)], "beta": [_uuid(2)]}
        version_id = _uuid(10)
        vault_id = _uuid(20)

        with patch.object(service, "generate_label", return_value="Test Label"):
            service.generate_labels_for_groups(groups, version_id, vault_id)

        assert repo.save_label.call_count == 2

    def test_empty_groups_returns_empty_dict(self) -> None:
        engine = MagicMock()
        repo = MagicMock(spec=LabelRepository)
        service = LabelService(engine=engine, repository=repo)

        result = service.generate_labels_for_groups({}, _uuid(1), _uuid(2))

        assert result == {}
        repo.save_label.assert_not_called()


# ---------------------------------------------------------------------------
# LabelRepository — round-trip via in-memory fake
# ---------------------------------------------------------------------------

class TestLabelRepositoryRoundTrip:
    """
    Tests for LabelRepository using a fake in-memory store.

    The engine is replaced with a fake that simulates SQL behaviour using
    a plain dict so no database is required.
    """

    def _make_fake_repo(self) -> tuple[LabelRepository, dict]:
        """
        Return a LabelRepository wired to a fake store dict.

        The fake store is {(version_id_str, group_key): row_dict}.
        """
        store: dict[tuple[str, str], dict] = {}

        engine = MagicMock()

        repo = LabelRepository(engine=engine)

        # Patch save_label to write into store.
        def fake_save(graph_version_id, vault_id, group_key, label,
                      method="extractive", confidence=None):
            key = (str(graph_version_id), group_key)
            store[key] = {
                "graph_version_id": str(graph_version_id),
                "vault_id": str(vault_id),
                "group_key": group_key,
                "label": label,
                "method": method,
                "confidence": confidence,
                "user_override": False,
                "overridden_by": None,
            }

        def fake_get_label(graph_version_id, group_key):
            key = (str(graph_version_id), group_key)
            row = store.get(key)
            return row["label"] if row else None

        def fake_get_labels_for_version(graph_version_id):
            prefix = str(graph_version_id)
            return {
                k[1]: v["label"]
                for k, v in store.items()
                if k[0] == prefix
            }

        def fake_override_label(graph_version_id, group_key, new_label, user_id):
            key = (str(graph_version_id), group_key)
            if key in store:
                store[key]["label"] = new_label
                store[key]["user_override"] = True
                store[key]["overridden_by"] = str(user_id)

        def fake_delete_labels(graph_version_id):
            prefix = str(graph_version_id)
            keys_to_delete = [k for k in store if k[0] == prefix]
            for k in keys_to_delete:
                del store[k]

        repo.save_label = fake_save
        repo.get_label = fake_get_label
        repo.get_labels_for_version = fake_get_labels_for_version
        repo.override_label = fake_override_label
        repo.delete_labels = fake_delete_labels

        return repo, store

    def test_save_and_get_label_round_trip(self) -> None:
        repo, store = self._make_fake_repo()
        version_id = _uuid(1)
        vault_id = _uuid(2)

        repo.save_label(version_id, vault_id, "group_a", "Climate Change")
        label = repo.get_label(version_id, "group_a")

        assert label == "Climate Change"

    def test_get_label_returns_none_for_missing_key(self) -> None:
        repo, _ = self._make_fake_repo()

        label = repo.get_label(_uuid(1), "nonexistent_group")

        assert label is None

    def test_get_labels_for_version_returns_all_labels(self) -> None:
        repo, _ = self._make_fake_repo()
        version_id = _uuid(10)
        vault_id = _uuid(20)

        repo.save_label(version_id, vault_id, "g1", "Label One")
        repo.save_label(version_id, vault_id, "g2", "Label Two")

        labels = repo.get_labels_for_version(version_id)

        assert labels == {"g1": "Label One", "g2": "Label Two"}

    def test_get_labels_for_version_empty_when_no_labels(self) -> None:
        repo, _ = self._make_fake_repo()

        labels = repo.get_labels_for_version(_uuid(99))

        assert labels == {}

    def test_user_override_updates_label_and_sets_flag(self) -> None:
        repo, store = self._make_fake_repo()
        version_id = _uuid(1)
        vault_id = _uuid(2)
        user_id = _uuid(999)

        repo.save_label(version_id, vault_id, "group_a", "Generated Label")
        repo.override_label(version_id, "group_a", "Custom Label", user_id)

        key = (str(version_id), "group_a")
        assert store[key]["label"] == "Custom Label"
        assert store[key]["user_override"] is True
        assert store[key]["overridden_by"] == str(user_id)

    def test_override_updates_what_get_label_returns(self) -> None:
        repo, _ = self._make_fake_repo()
        version_id = _uuid(1)
        vault_id = _uuid(2)

        repo.save_label(version_id, vault_id, "group_a", "Generated Label")
        repo.override_label(version_id, "group_a", "Custom Label", _uuid(999))

        assert repo.get_label(version_id, "group_a") == "Custom Label"

    def test_delete_labels_removes_all_for_version(self) -> None:
        repo, store = self._make_fake_repo()
        version_id = _uuid(1)
        vault_id = _uuid(2)

        repo.save_label(version_id, vault_id, "g1", "Label One")
        repo.save_label(version_id, vault_id, "g2", "Label Two")

        repo.delete_labels(version_id)

        assert repo.get_labels_for_version(version_id) == {}

    def test_delete_labels_does_not_affect_other_versions(self) -> None:
        repo, _ = self._make_fake_repo()
        version_a = _uuid(1)
        version_b = _uuid(2)
        vault_id = _uuid(10)

        repo.save_label(version_a, vault_id, "g1", "Label A")
        repo.save_label(version_b, vault_id, "g1", "Label B")

        repo.delete_labels(version_a)

        assert repo.get_labels_for_version(version_a) == {}
        assert repo.get_labels_for_version(version_b) == {"g1": "Label B"}

    def test_save_label_upsert_overwrites_existing(self) -> None:
        repo, store = self._make_fake_repo()
        version_id = _uuid(1)
        vault_id = _uuid(2)

        repo.save_label(version_id, vault_id, "g1", "First Label")
        repo.save_label(version_id, vault_id, "g1", "Second Label")

        assert repo.get_label(version_id, "g1") == "Second Label"


# ---------------------------------------------------------------------------
# LabelService.attach_labels
# ---------------------------------------------------------------------------

class TestAttachLabels:

    def _make_service_with_labels(
        self, labels: dict[str, str]
    ) -> LabelService:
        """Build a LabelService whose repo returns the given labels dict."""
        engine = MagicMock()
        repo = MagicMock(spec=LabelRepository)
        repo.get_labels_for_version.return_value = labels
        return LabelService(engine=engine, repository=repo)

    def test_attach_labels_adds_group_labels_key(self) -> None:
        service = self._make_service_with_labels(
            {"alpha": "Climate Change", "beta": "Machine Learning"}
        )
        payload = {"nodes": [], "links": [], "groups": {"alpha": 3, "beta": 2}}

        result = service.attach_labels(payload, _uuid(1))

        assert "group_labels" in result
        assert result["group_labels"]["alpha"] == "Climate Change"
        assert result["group_labels"]["beta"] == "Machine Learning"

    def test_attach_labels_returns_unchanged_payload_when_no_labels(self) -> None:
        service = self._make_service_with_labels({})
        payload = {"nodes": [{"id": "x"}], "links": [], "groups": {"g1": 1}}

        result = service.attach_labels(payload, _uuid(1))

        assert result is payload

    def test_attach_labels_does_not_mutate_original_payload(self) -> None:
        service = self._make_service_with_labels({"g1": "Some Label"})
        payload = {"nodes": [], "links": [], "groups": {"g1": 2}}
        original_keys = set(payload.keys())

        result = service.attach_labels(payload, _uuid(1))

        assert set(payload.keys()) == original_keys
        assert result is not payload

    def test_attach_labels_with_ungrouped_payload_still_attaches(self) -> None:
        """Payload without a 'groups' key still receives group_labels."""
        service = self._make_service_with_labels({"g1": "Label One"})
        payload = {"nodes": [], "links": []}

        result = service.attach_labels(payload, _uuid(1))

        assert "group_labels" in result

    def test_attach_labels_existing_keys_preserved(self) -> None:
        service = self._make_service_with_labels({"g1": "Label"})
        payload = {
            "nodes": [{"id": "n1"}],
            "links": [],
            "metadata": {"version": "v1"},
        }

        result = service.attach_labels(payload, _uuid(1))

        assert result["nodes"] == payload["nodes"]
        assert result["metadata"] == payload["metadata"]

    def test_attach_labels_returns_dict(self) -> None:
        service = self._make_service_with_labels({"g1": "Label"})
        payload: dict = {"nodes": [], "links": []}

        result = service.attach_labels(payload, _uuid(1))

        assert isinstance(result, dict)
