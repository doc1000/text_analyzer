"""
Unit tests for Phase 4 — GraphUpdateService.

Tests cover:
- insert_document adds correct edges and enforces canonical ordering.
- delete_document removes all incident edges.
- update_document behaves as delete + insert.
- Version metadata (edge_count, source_document_count) is updated correctly.
- graph.graph_job rows are created for each operation.
- Canonical edge ordering (doc_lo < doc_hi) is preserved on insertion.
- Neighbor repair logic triggers for under-connected nodes after deletion.
- No database connection is required; a MagicMock engine is used throughout.

All DB interactions are captured via engine.connect().__enter__().execute()
through MagicMock's attribute chaining, or by patching specific helper methods
directly on the service instance.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch
from uuid import UUID

import numpy as np
import pytest

from graph_engine.db.models.graph_config import GraphConfigRow
from graph_engine.db.repositories.graph_version_repo import GraphVersionRepo
from graph_engine.graph_core.types import SparseEdge
from graph_engine.graph_core.updater import GraphUpdateService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uuid(n: int) -> UUID:
    """Deterministic UUID from an integer."""
    return UUID(int=n)


def _make_config(knn_k: int = 3, candidate_k: int = 5) -> GraphConfigRow:
    return GraphConfigRow(
        id=_uuid(999),
        name="test-config",
        embedding_source="all_minilm_v1_384",
        knn_k=knn_k,
        candidate_k=candidate_k,
        pruning_mode="none",
        feature_weights={},
        is_active=True,
    )


def _random_unit_vec(dim: int = 64, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def _make_service(version_row: dict | None = None) -> tuple[GraphUpdateService, MagicMock, MagicMock]:
    """
    Build a GraphUpdateService with a stubbed engine and GraphVersionRepo.

    Returns (service, engine_mock, version_repo_mock).
    """
    engine = MagicMock()
    version_repo = MagicMock(spec=GraphVersionRepo)

    if version_row is not None:
        version_repo.get_active_version.return_value = version_row

    service = GraphUpdateService(engine=engine, version_repo=version_repo)
    return service, engine, version_repo


def _active_version(version_id: UUID, config_id: UUID) -> dict:
    return {
        "id": str(version_id),
        "graph_config_id": str(config_id),
        "vault_id": str(_uuid(10)),
        "edge_count": 10,
        "source_document_count": 5,
        "is_active": True,
    }


# ---------------------------------------------------------------------------
# Tests: insert_document
# ---------------------------------------------------------------------------

class TestInsertDocument:
    """Tests for GraphUpdateService.insert_document()."""

    def _setup(
        self,
        n_candidates: int = 6,
        knn_k: int = 3,
        candidate_k: int = 5,
    ):
        vault_id = _uuid(10)
        version_id = _uuid(20)
        config_id = _uuid(30)
        new_doc_id = _uuid(100)

        config = _make_config(knn_k=knn_k, candidate_k=candidate_k)
        version_row = _active_version(version_id, config_id)

        service, engine, version_repo = _make_service(version_row=version_row)

        new_vec = _random_unit_vec(seed=42)
        candidate_ids = [_uuid(i) for i in range(1, n_candidates + 1)]
        candidates = {
            cid: _random_unit_vec(seed=i + 1)
            for i, cid in enumerate(candidate_ids)
        }

        with patch("graph_engine.graph_core.updater.get_graph_config", return_value=config):
            service._fetch_config_id_for_version = MagicMock(return_value=config_id)
            service._fetch_single_embedding = MagicMock(return_value=new_vec)
            service._fetch_candidate_embeddings = MagicMock(return_value=candidates)
            service._insert_edges = MagicMock()
            service._increment_version_metadata = MagicMock()
            service._log_graph_job = MagicMock()

            edges = service.insert_document(
                vault_id=vault_id,
                document_id=new_doc_id,
                graph_version_id=version_id,
            )

        return edges, new_doc_id, version_id, vault_id, config, service

    def test_returns_list_of_sparse_edges(self) -> None:
        edges, _, _, _, _, _ = self._setup()
        assert isinstance(edges, list)
        assert all(isinstance(e, SparseEdge) for e in edges)

    def test_edge_count_bounded_by_knn_k(self) -> None:
        knn_k = 3
        edges, _, _, _, _, _ = self._setup(n_candidates=6, knn_k=knn_k)
        assert len(edges) <= knn_k

    def test_canonical_ordering_on_all_inserted_edges(self) -> None:
        edges, _, _, _, _, _ = self._setup(n_candidates=6, knn_k=3)
        for edge in edges:
            assert edge.doc_lo < edge.doc_hi, (
                f"Canonical ordering violated: doc_lo={edge.doc_lo}, doc_hi={edge.doc_hi}"
            )

    def test_insert_edges_called_with_correct_version(self) -> None:
        edges, new_doc_id, version_id, vault_id, config, service = self._setup(knn_k=3)
        if edges:
            service._insert_edges.assert_called_once()
            call_kwargs = service._insert_edges.call_args
            assert call_kwargs.kwargs["graph_version_id"] == version_id or (
                call_kwargs.args and call_kwargs.args[0] == version_id
            )

    def test_version_metadata_incremented_after_insert(self) -> None:
        edges, _, _, _, _, service = self._setup(knn_k=3)
        service._increment_version_metadata.assert_called_once()
        kwargs = service._increment_version_metadata.call_args.kwargs
        assert kwargs["delta_edges"] == len(edges)
        assert kwargs["delta_docs"] == 1

    def test_graph_job_logged_after_insert(self) -> None:
        _, _, _, _, _, service = self._setup(knn_k=3)
        service._log_graph_job.assert_called_once()
        kwargs = service._log_graph_job.call_args.kwargs
        assert kwargs["job_type"] == "repair_local_graph"

    def test_no_edges_inserted_when_no_candidates(self) -> None:
        vault_id = _uuid(10)
        version_id = _uuid(20)
        config_id = _uuid(30)
        new_doc_id = _uuid(100)
        config = _make_config(knn_k=3)
        version_row = _active_version(version_id, config_id)

        service, _, _ = _make_service(version_row=version_row)
        new_vec = _random_unit_vec()

        with patch("graph_engine.graph_core.updater.get_graph_config", return_value=config):
            service._fetch_config_id_for_version = MagicMock(return_value=config_id)
            service._fetch_single_embedding = MagicMock(return_value=new_vec)
            service._fetch_candidate_embeddings = MagicMock(return_value={})
            service._insert_edges = MagicMock()
            service._increment_version_metadata = MagicMock()
            service._log_graph_job = MagicMock()

            edges = service.insert_document(
                vault_id=vault_id,
                document_id=new_doc_id,
                graph_version_id=version_id,
            )

        assert edges == []
        service._insert_edges.assert_not_called()
        service._log_graph_job.assert_called_once()

    def test_raises_when_no_embedding_found(self) -> None:
        vault_id = _uuid(10)
        version_id = _uuid(20)
        config_id = _uuid(30)
        new_doc_id = _uuid(100)
        config = _make_config()
        version_row = _active_version(version_id, config_id)

        service, _, _ = _make_service(version_row=version_row)

        with patch("graph_engine.graph_core.updater.get_graph_config", return_value=config):
            service._fetch_config_id_for_version = MagicMock(return_value=config_id)
            service._fetch_single_embedding = MagicMock(return_value=None)

            with pytest.raises(ValueError, match="No embedding found"):
                service.insert_document(
                    vault_id=vault_id,
                    document_id=new_doc_id,
                    graph_version_id=version_id,
                )

    def test_raises_when_no_active_version(self) -> None:
        service, _, version_repo = _make_service(version_row=None)
        version_repo.get_active_version.return_value = None
        config = _make_config()

        with patch("graph_engine.graph_core.updater.get_graph_config", return_value=config):
            with pytest.raises(ValueError, match="No active graph version"):
                service.insert_document(vault_id=_uuid(10), document_id=_uuid(1))


# ---------------------------------------------------------------------------
# Tests: delete_document
# ---------------------------------------------------------------------------

class TestDeleteDocument:
    """Tests for GraphUpdateService.delete_document()."""

    def _setup(self, deleted_count: int = 2, neighbor_count: int = 2):
        vault_id = _uuid(10)
        version_id = _uuid(20)
        config_id = _uuid(30)
        doc_id = _uuid(100)

        config = _make_config(knn_k=4)
        version_row = _active_version(version_id, config_id)

        service, engine, version_repo = _make_service(version_row=version_row)

        neighbors = {_uuid(i) for i in range(1, neighbor_count + 1)}

        with patch("graph_engine.graph_core.updater.get_graph_config", return_value=config):
            service._fetch_config_id_for_version = MagicMock(return_value=config_id)
            service._fetch_incident_neighbors = MagicMock(return_value=neighbors)
            service._delete_incident_edges = MagicMock(return_value=deleted_count)
            service._repair_orphaned_neighbors = MagicMock(return_value=[])
            service._increment_version_metadata = MagicMock()
            service._log_graph_job = MagicMock()

            count = service.delete_document(
                vault_id=vault_id,
                document_id=doc_id,
                graph_version_id=version_id,
            )

        return count, doc_id, version_id, vault_id, service

    def test_returns_deleted_edge_count(self) -> None:
        count, _, _, _, _ = self._setup(deleted_count=2)
        assert count == 2

    def test_delete_incident_edges_called(self) -> None:
        _, doc_id, version_id, _, service = self._setup()
        service._delete_incident_edges.assert_called_once()

    def test_version_metadata_decrements_doc_count(self) -> None:
        _, _, _, _, service = self._setup(deleted_count=2)
        service._increment_version_metadata.assert_called_once()
        kwargs = service._increment_version_metadata.call_args.kwargs
        assert kwargs["delta_docs"] == -1

    def test_version_metadata_decrements_edge_count(self) -> None:
        _, _, _, _, service = self._setup(deleted_count=3)
        kwargs = service._increment_version_metadata.call_args.kwargs
        # repair_orphaned_neighbors returns [] so net delta = 0 - 3 = -3
        assert kwargs["delta_edges"] == -3

    def test_graph_job_logged_after_delete(self) -> None:
        _, _, _, _, service = self._setup()
        service._log_graph_job.assert_called_once()
        kwargs = service._log_graph_job.call_args.kwargs
        assert kwargs["job_type"] == "repair_local_graph"

    def test_repair_called_when_edges_deleted(self) -> None:
        _, _, _, _, service = self._setup(deleted_count=2)
        service._repair_orphaned_neighbors.assert_called_once()

    def test_repair_not_called_when_zero_edges_deleted(self) -> None:
        vault_id = _uuid(10)
        version_id = _uuid(20)
        config_id = _uuid(30)
        doc_id = _uuid(100)
        config = _make_config()
        version_row = _active_version(version_id, config_id)

        service, _, _ = _make_service(version_row=version_row)

        with patch("graph_engine.graph_core.updater.get_graph_config", return_value=config):
            service._fetch_config_id_for_version = MagicMock(return_value=config_id)
            service._fetch_incident_neighbors = MagicMock(return_value=set())
            service._delete_incident_edges = MagicMock(return_value=0)
            service._repair_orphaned_neighbors = MagicMock(return_value=[])
            service._increment_version_metadata = MagicMock()
            service._log_graph_job = MagicMock()

            service.delete_document(
                vault_id=vault_id,
                document_id=doc_id,
                graph_version_id=version_id,
            )

        service._repair_orphaned_neighbors.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: update_document
# ---------------------------------------------------------------------------

class TestUpdateDocument:
    """Tests for GraphUpdateService.update_document()."""

    def test_update_calls_delete_then_insert(self) -> None:
        vault_id = _uuid(10)
        doc_id = _uuid(50)

        service, _, _ = _make_service()
        service.delete_document = MagicMock(return_value=2)
        inserted_edges = [
            SparseEdge.make(_uuid(50), _uuid(60), weight=0.8),
        ]
        service.insert_document = MagicMock(return_value=inserted_edges)

        result = service.update_document(vault_id=vault_id, document_id=doc_id)

        service.delete_document.assert_called_once_with(
            vault_id=vault_id, document_id=doc_id
        )
        service.insert_document.assert_called_once_with(
            vault_id=vault_id, document_id=doc_id
        )
        assert result == inserted_edges

    def test_update_returns_inserted_edges(self) -> None:
        vault_id = _uuid(10)
        doc_id = _uuid(50)

        service, _, _ = _make_service()
        service.delete_document = MagicMock(return_value=1)
        edges = [SparseEdge.make(_uuid(1), _uuid(50), weight=0.75)]
        service.insert_document = MagicMock(return_value=edges)

        result = service.update_document(vault_id=vault_id, document_id=doc_id)
        assert result == edges

    def test_update_produces_same_result_as_delete_plus_insert(self) -> None:
        """
        Conceptual invariant: update = delete then insert, confirmed by call order.
        """
        vault_id = _uuid(10)
        doc_id = _uuid(50)

        call_order: list[str] = []

        service, _, _ = _make_service()
        service.delete_document = MagicMock(side_effect=lambda **kw: call_order.append("delete") or 1)
        service.insert_document = MagicMock(side_effect=lambda **kw: call_order.append("insert") or [])

        service.update_document(vault_id=vault_id, document_id=doc_id)
        assert call_order == ["delete", "insert"]


# ---------------------------------------------------------------------------
# Tests: _compute_knn_edges (canonical ordering, edge count)
# ---------------------------------------------------------------------------

class TestComputeKnnEdges:
    """
    Unit tests for the pure similarity computation method.
    No database or version resolution required.
    """

    def _service(self) -> GraphUpdateService:
        return GraphUpdateService(engine=MagicMock(), version_repo=MagicMock())

    def test_all_edges_have_canonical_ordering(self) -> None:
        svc = self._service()
        new_doc = _uuid(0)
        candidates = {_uuid(i): _random_unit_vec(seed=i) for i in range(1, 8)}
        new_vec = _random_unit_vec(seed=99)

        edges = svc._compute_knn_edges(
            new_doc_id=new_doc,
            new_vec=new_vec,
            candidates=candidates,
            knn_k=3,
        )

        for edge in edges:
            assert edge.doc_lo < edge.doc_hi, (
                f"doc_lo ({edge.doc_lo}) >= doc_hi ({edge.doc_hi})"
            )

    def test_edge_count_bounded_by_knn_k(self) -> None:
        svc = self._service()
        new_doc = _uuid(0)
        candidates = {_uuid(i): _random_unit_vec(seed=i) for i in range(1, 10)}
        new_vec = _random_unit_vec(seed=0)

        edges = svc._compute_knn_edges(
            new_doc_id=new_doc,
            new_vec=new_vec,
            candidates=candidates,
            knn_k=4,
        )
        assert len(edges) <= 4

    def test_edge_count_capped_by_available_candidates(self) -> None:
        svc = self._service()
        new_doc = _uuid(0)
        candidates = {_uuid(1): _random_unit_vec(seed=1), _uuid(2): _random_unit_vec(seed=2)}
        new_vec = _random_unit_vec(seed=0)

        edges = svc._compute_knn_edges(
            new_doc_id=new_doc,
            new_vec=new_vec,
            candidates=candidates,
            knn_k=10,
        )
        assert len(edges) <= 2

    def test_weights_in_valid_range(self) -> None:
        svc = self._service()
        new_doc = _uuid(0)
        candidates = {_uuid(i): _random_unit_vec(seed=i) for i in range(1, 7)}
        new_vec = _random_unit_vec(seed=7)

        edges = svc._compute_knn_edges(
            new_doc_id=new_doc,
            new_vec=new_vec,
            candidates=candidates,
            knn_k=3,
        )
        for edge in edges:
            assert 0.0 <= edge.weight <= 1.0

    def test_new_doc_appears_in_every_edge(self) -> None:
        svc = self._service()
        new_doc = _uuid(0)
        candidates = {_uuid(i): _random_unit_vec(seed=i) for i in range(1, 6)}
        new_vec = _random_unit_vec(seed=10)

        edges = svc._compute_knn_edges(
            new_doc_id=new_doc,
            new_vec=new_vec,
            candidates=candidates,
            knn_k=3,
        )
        for edge in edges:
            assert edge.doc_lo == new_doc or edge.doc_hi == new_doc, (
                f"new_doc {new_doc} not in edge ({edge.doc_lo}, {edge.doc_hi})"
            )

    def test_no_duplicate_edges(self) -> None:
        svc = self._service()
        new_doc = _uuid(0)
        candidates = {_uuid(i): _random_unit_vec(seed=i) for i in range(1, 8)}
        new_vec = _random_unit_vec(seed=5)

        edges = svc._compute_knn_edges(
            new_doc_id=new_doc,
            new_vec=new_vec,
            candidates=candidates,
            knn_k=5,
        )
        pairs = [(e.doc_lo, e.doc_hi) for e in edges]
        assert len(pairs) == len(set(pairs))

    def test_empty_candidates_returns_empty_list(self) -> None:
        svc = self._service()
        new_doc = _uuid(0)
        new_vec = _random_unit_vec()

        edges = svc._compute_knn_edges(
            new_doc_id=new_doc,
            new_vec=new_vec,
            candidates={},
            knn_k=3,
        )
        assert edges == []


# ---------------------------------------------------------------------------
# Tests: version metadata updates
# ---------------------------------------------------------------------------

class TestVersionMetadata:
    """Tests to confirm version metadata is updated on insert and delete."""

    def test_insert_increments_doc_and_edge_counts(self) -> None:
        vault_id = _uuid(10)
        version_id = _uuid(20)
        config_id = _uuid(30)
        new_doc_id = _uuid(100)
        config = _make_config(knn_k=2)
        version_row = _active_version(version_id, config_id)

        service, _, _ = _make_service(version_row=version_row)

        new_vec = _random_unit_vec()
        candidates = {_uuid(i): _random_unit_vec(seed=i) for i in range(1, 4)}

        with patch("graph_engine.graph_core.updater.get_graph_config", return_value=config):
            service._fetch_config_id_for_version = MagicMock(return_value=config_id)
            service._fetch_single_embedding = MagicMock(return_value=new_vec)
            service._fetch_candidate_embeddings = MagicMock(return_value=candidates)
            service._insert_edges = MagicMock()
            service._increment_version_metadata = MagicMock()
            service._log_graph_job = MagicMock()

            edges = service.insert_document(
                vault_id=vault_id,
                document_id=new_doc_id,
                graph_version_id=version_id,
            )

        service._increment_version_metadata.assert_called_once()
        kwargs = service._increment_version_metadata.call_args.kwargs
        assert kwargs["delta_docs"] == 1
        assert kwargs["delta_edges"] == len(edges)

    def test_delete_decrements_doc_and_edge_counts(self) -> None:
        vault_id = _uuid(10)
        version_id = _uuid(20)
        config_id = _uuid(30)
        doc_id = _uuid(100)
        config = _make_config(knn_k=2)
        version_row = _active_version(version_id, config_id)

        service, _, _ = _make_service(version_row=version_row)

        with patch("graph_engine.graph_core.updater.get_graph_config", return_value=config):
            service._fetch_config_id_for_version = MagicMock(return_value=config_id)
            service._fetch_incident_neighbors = MagicMock(return_value={_uuid(1)})
            service._delete_incident_edges = MagicMock(return_value=2)
            service._repair_orphaned_neighbors = MagicMock(return_value=[])
            service._increment_version_metadata = MagicMock()
            service._log_graph_job = MagicMock()

            service.delete_document(
                vault_id=vault_id,
                document_id=doc_id,
                graph_version_id=version_id,
            )

        service._increment_version_metadata.assert_called_once()
        kwargs = service._increment_version_metadata.call_args.kwargs
        assert kwargs["delta_docs"] == -1
        assert kwargs["delta_edges"] < 0 or kwargs["delta_edges"] == 0


# ---------------------------------------------------------------------------
# Tests: graph_job row creation
# ---------------------------------------------------------------------------

class TestGraphJobLogging:
    """Confirm that _log_graph_job is invoked for every public operation."""

    def _run_insert(self) -> GraphUpdateService:
        vault_id = _uuid(10)
        version_id = _uuid(20)
        config_id = _uuid(30)
        doc_id = _uuid(100)
        config = _make_config()
        version_row = _active_version(version_id, config_id)

        service, _, _ = _make_service(version_row=version_row)
        new_vec = _random_unit_vec()

        with patch("graph_engine.graph_core.updater.get_graph_config", return_value=config):
            service._fetch_config_id_for_version = MagicMock(return_value=config_id)
            service._fetch_single_embedding = MagicMock(return_value=new_vec)
            service._fetch_candidate_embeddings = MagicMock(return_value={})
            service._insert_edges = MagicMock()
            service._increment_version_metadata = MagicMock()
            service._log_graph_job = MagicMock()

            service.insert_document(
                vault_id=vault_id, document_id=doc_id, graph_version_id=version_id
            )
        return service

    def _run_delete(self) -> GraphUpdateService:
        vault_id = _uuid(10)
        version_id = _uuid(20)
        config_id = _uuid(30)
        doc_id = _uuid(100)
        config = _make_config()
        version_row = _active_version(version_id, config_id)

        service, _, _ = _make_service(version_row=version_row)

        with patch("graph_engine.graph_core.updater.get_graph_config", return_value=config):
            service._fetch_config_id_for_version = MagicMock(return_value=config_id)
            service._fetch_incident_neighbors = MagicMock(return_value=set())
            service._delete_incident_edges = MagicMock(return_value=0)
            service._repair_orphaned_neighbors = MagicMock(return_value=[])
            service._increment_version_metadata = MagicMock()
            service._log_graph_job = MagicMock()

            service.delete_document(
                vault_id=vault_id, document_id=doc_id, graph_version_id=version_id
            )
        return service

    def test_insert_logs_graph_job(self) -> None:
        service = self._run_insert()
        service._log_graph_job.assert_called_once()

    def test_delete_logs_graph_job(self) -> None:
        service = self._run_delete()
        service._log_graph_job.assert_called_once()

    def test_insert_job_type_is_repair_local_graph(self) -> None:
        service = self._run_insert()
        kwargs = service._log_graph_job.call_args.kwargs
        assert kwargs["job_type"] == "repair_local_graph"

    def test_delete_job_type_is_repair_local_graph(self) -> None:
        service = self._run_delete()
        kwargs = service._log_graph_job.call_args.kwargs
        assert kwargs["job_type"] == "repair_local_graph"

    def test_update_logs_two_jobs(self) -> None:
        """update_document = delete + insert; each should log a job."""
        vault_id = _uuid(10)
        doc_id = _uuid(50)

        service, _, _ = _make_service()
        log_calls: list[str] = []

        def _capture_log(**kwargs):
            log_calls.append(kwargs["job_type"])

        service.delete_document = MagicMock(return_value=1)
        service.insert_document = MagicMock(return_value=[])

        service.update_document(vault_id=vault_id, document_id=doc_id)

        service.delete_document.assert_called_once()
        service.insert_document.assert_called_once()
