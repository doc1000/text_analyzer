"""
Unit tests for GraphVersionRepo (Phase 2 graph-engine).

Tests:
- create_version inserts with status 'building' and version_no increments.
- finalize_version updates edge_count, source_document_count, build_finished_at.
- activate_version sets is_active=true and supersedes the previous active version.
- Only one active version may exist per vault at a time.
- fail_version sets status='failed' and stores optional error message.
- get_active_version returns the active row, or None if none exists.

All tests use a mocked SQLAlchemy engine — no database connection required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call
from uuid import UUID

import pytest

from graph_engine.db.repositories.graph_version_repo import GraphVersionRepo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _u(n: int) -> UUID:
    return UUID(int=n)


def _make_conn_engine():
    """Return (engine, conn) pair with enter/exit wiring."""
    engine = MagicMock()
    conn = MagicMock()
    engine.connect.return_value.__enter__ = MagicMock(return_value=conn)
    engine.connect.return_value.__exit__ = MagicMock(return_value=False)
    return engine, conn


def _make_version_row(
    id_: UUID,
    vault_id: UUID,
    version_no: int,
    status: str = "building",
    is_active: bool = False,
) -> dict:
    return {
        "id": str(id_),
        "vault_id": str(vault_id),
        "graph_config_id": str(_u(200)),
        "version_no": version_no,
        "status": status,
        "is_active": is_active,
        "build_scope": "full",
        "source_document_count": 0,
        "edge_count": 0,
        "built_by": None,
        "build_started_at": None,
        "build_finished_at": None,
        "parent_version_id": None,
        "notes": None,
    }


# ---------------------------------------------------------------------------
# create_version tests
# ---------------------------------------------------------------------------

class TestCreateVersion:

    def test_returns_row_dict(self) -> None:
        """create_version must return the inserted row as a dict."""
        vault_id = _u(10)
        config_id = _u(20)
        row = _make_version_row(_u(1), vault_id, version_no=1)

        engine, conn = _make_conn_engine()
        mapping_mock = MagicMock()
        mapping_mock.fetchone.return_value = dict(row)
        conn.execute.return_value.mappings.return_value = mapping_mock

        repo = GraphVersionRepo(engine)
        result = repo.create_version(vault_id, config_id)

        assert isinstance(result, dict)
        assert result["vault_id"] == str(vault_id)

    def test_execute_called_once(self) -> None:
        """create_version must call conn.execute exactly once (INSERT ... RETURNING)."""
        vault_id = _u(10)
        config_id = _u(20)
        row = _make_version_row(_u(1), vault_id, version_no=1)

        engine, conn = _make_conn_engine()
        mapping_mock = MagicMock()
        mapping_mock.fetchone.return_value = dict(row)
        conn.execute.return_value.mappings.return_value = mapping_mock

        repo = GraphVersionRepo(engine)
        repo.create_version(vault_id, config_id)

        assert conn.execute.call_count == 1

    def test_raises_if_returning_no_row(self) -> None:
        """create_version raises RuntimeError if INSERT returns no row."""
        engine, conn = _make_conn_engine()
        mapping_mock = MagicMock()
        mapping_mock.fetchone.return_value = None
        conn.execute.return_value.mappings.return_value = mapping_mock

        repo = GraphVersionRepo(engine)
        with pytest.raises(RuntimeError, match="returned no row"):
            repo.create_version(_u(10), _u(20))

    def test_built_by_passed_when_supplied(self) -> None:
        """built_by UUID is forwarded to the INSERT parameters."""
        vault_id = _u(10)
        config_id = _u(20)
        built_by = _u(99)
        row = _make_version_row(_u(1), vault_id, version_no=1)

        engine, conn = _make_conn_engine()
        mapping_mock = MagicMock()
        mapping_mock.fetchone.return_value = dict(row)
        conn.execute.return_value.mappings.return_value = mapping_mock

        repo = GraphVersionRepo(engine)
        repo.create_version(vault_id, config_id, built_by=built_by)

        call_params = conn.execute.call_args[0][1]
        assert call_params["built_by"] == str(built_by)

    def test_built_by_none_when_not_supplied(self) -> None:
        """built_by defaults to None in the INSERT parameters."""
        vault_id = _u(10)
        config_id = _u(20)
        row = _make_version_row(_u(1), vault_id, version_no=1)

        engine, conn = _make_conn_engine()
        mapping_mock = MagicMock()
        mapping_mock.fetchone.return_value = dict(row)
        conn.execute.return_value.mappings.return_value = mapping_mock

        repo = GraphVersionRepo(engine)
        repo.create_version(vault_id, config_id)

        call_params = conn.execute.call_args[0][1]
        assert call_params["built_by"] is None


# ---------------------------------------------------------------------------
# finalize_version tests
# ---------------------------------------------------------------------------

class TestFinalizeVersion:

    def test_execute_called_once(self) -> None:
        """finalize_version must call execute exactly once (UPDATE)."""
        engine, conn = _make_conn_engine()
        repo = GraphVersionRepo(engine)
        repo.finalize_version(version_id=_u(1), edge_count=50, doc_count=30)

        assert conn.execute.call_count == 1

    def test_params_forwarded_correctly(self) -> None:
        """finalize_version must pass edge_count and doc_count to the UPDATE."""
        engine, conn = _make_conn_engine()
        repo = GraphVersionRepo(engine)
        repo.finalize_version(version_id=_u(1), edge_count=42, doc_count=15)

        call_params = conn.execute.call_args[0][1]
        assert call_params["edge_count"] == 42
        assert call_params["doc_count"] == 15
        assert call_params["version_id"] == str(_u(1))


# ---------------------------------------------------------------------------
# activate_version tests
# ---------------------------------------------------------------------------

class TestActivateVersion:

    def _make_begin_engine(self):
        """Engine mock that supports context manager on conn.begin()."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__ = MagicMock(return_value=conn)
        engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        conn.begin.return_value.__enter__ = MagicMock(return_value=None)
        conn.begin.return_value.__exit__ = MagicMock(return_value=False)
        return engine, conn

    def test_execute_called_twice(self) -> None:
        """activate_version must issue two UPDATE statements (supersede + activate)."""
        engine, conn = self._make_begin_engine()
        repo = GraphVersionRepo(engine)
        repo.activate_version(_u(5))

        assert conn.execute.call_count == 2

    def test_both_calls_receive_version_id(self) -> None:
        """Both UPDATE statements must reference the same version_id."""
        version_id = _u(5)
        engine, conn = self._make_begin_engine()
        repo = GraphVersionRepo(engine)
        repo.activate_version(version_id)

        for c in conn.execute.call_args_list:
            params = c[0][1]
            assert params["version_id"] == str(version_id)

    def test_uses_transaction(self) -> None:
        """activate_version must open a transaction via conn.begin()."""
        engine, conn = self._make_begin_engine()
        repo = GraphVersionRepo(engine)
        repo.activate_version(_u(5))

        conn.begin.assert_called_once()


# ---------------------------------------------------------------------------
# fail_version tests
# ---------------------------------------------------------------------------

class TestFailVersion:

    def test_execute_called_once(self) -> None:
        engine, conn = _make_conn_engine()
        repo = GraphVersionRepo(engine)
        repo.fail_version(_u(7), error_message="something went wrong")

        assert conn.execute.call_count == 1

    def test_error_message_stored_in_notes(self) -> None:
        """fail_version must forward error_message as 'notes' param."""
        engine, conn = _make_conn_engine()
        repo = GraphVersionRepo(engine)
        repo.fail_version(_u(7), error_message="disk full")

        call_params = conn.execute.call_args[0][1]
        assert call_params["notes"] == "disk full"

    def test_notes_none_when_no_message(self) -> None:
        """fail_version passes notes=None when no error_message given."""
        engine, conn = _make_conn_engine()
        repo = GraphVersionRepo(engine)
        repo.fail_version(_u(7))

        call_params = conn.execute.call_args[0][1]
        assert call_params["notes"] is None

    def test_version_id_forwarded(self) -> None:
        version_id = _u(77)
        engine, conn = _make_conn_engine()
        repo = GraphVersionRepo(engine)
        repo.fail_version(version_id)

        call_params = conn.execute.call_args[0][1]
        assert call_params["version_id"] == str(version_id)


# ---------------------------------------------------------------------------
# get_active_version tests
# ---------------------------------------------------------------------------

class TestGetActiveVersion:

    def test_returns_dict_when_active_exists(self) -> None:
        """get_active_version returns a dict when an active version is found."""
        vault_id = _u(10)
        row = _make_version_row(_u(1), vault_id, version_no=3, status="active", is_active=True)

        engine, conn = _make_conn_engine()
        mapping_mock = MagicMock()
        mapping_mock.fetchone.return_value = dict(row)
        conn.execute.return_value.mappings.return_value = mapping_mock

        repo = GraphVersionRepo(engine)
        result = repo.get_active_version(vault_id)

        assert result is not None
        assert result["is_active"] is True
        assert result["status"] == "active"

    def test_returns_none_when_no_active_version(self) -> None:
        """get_active_version returns None when there is no active version."""
        vault_id = _u(10)

        engine, conn = _make_conn_engine()
        mapping_mock = MagicMock()
        mapping_mock.fetchone.return_value = None
        conn.execute.return_value.mappings.return_value = mapping_mock

        repo = GraphVersionRepo(engine)
        result = repo.get_active_version(vault_id)

        assert result is None

    def test_vault_id_passed_to_query(self) -> None:
        """get_active_version forwards vault_id to the SELECT parameters."""
        vault_id = _u(55)

        engine, conn = _make_conn_engine()
        mapping_mock = MagicMock()
        mapping_mock.fetchone.return_value = None
        conn.execute.return_value.mappings.return_value = mapping_mock

        repo = GraphVersionRepo(engine)
        repo.get_active_version(vault_id)

        call_params = conn.execute.call_args[0][1]
        assert call_params["vault_id"] == str(vault_id)


# ---------------------------------------------------------------------------
# Logical invariant tests (simulated)
# ---------------------------------------------------------------------------

class TestVersionLifecycleInvariants:
    """
    Tests that verify the logical sequence of version lifecycle calls.

    These tests simulate a sequence of operations and verify that the
    correct methods are called in order and with the correct arguments.
    No actual DB state is managed here — the DB partial index enforces
    the single-active-version rule at the SQL level.
    """

    def test_version_no_increments_on_second_build(self) -> None:
        """
        Simulates two consecutive create_version calls for the same vault.

        The second call should return version_no=2 (the SQL COALESCE expression
        in the INSERT handles this; here we verify the repo returns what the DB
        reports back, which in a real DB would be 2).
        """
        vault_id = _u(10)
        config_id = _u(20)

        row_v1 = _make_version_row(_u(1), vault_id, version_no=1)
        row_v2 = _make_version_row(_u(2), vault_id, version_no=2)

        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__ = MagicMock(return_value=conn)
        engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        # Alternate RETURNING rows: first call → v1, second call → v2
        mapping_mock_1 = MagicMock()
        mapping_mock_1.fetchone.return_value = dict(row_v1)
        result_1 = MagicMock()
        result_1.mappings.return_value = mapping_mock_1

        mapping_mock_2 = MagicMock()
        mapping_mock_2.fetchone.return_value = dict(row_v2)
        result_2 = MagicMock()
        result_2.mappings.return_value = mapping_mock_2

        conn.execute.side_effect = [result_1, result_2]

        repo = GraphVersionRepo(engine)
        r1 = repo.create_version(vault_id, config_id)
        r2 = repo.create_version(vault_id, config_id)

        assert r1["version_no"] == 1
        assert r2["version_no"] == 2

    def test_activation_sequence_two_calls(self) -> None:
        """
        Simulates create → activate sequence.

        Verifies activate_version is called with the correct version_id.
        """
        vault_id = _u(10)
        config_id = _u(20)
        version_id = _u(1)

        row = _make_version_row(version_id, vault_id, version_no=1)

        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__ = MagicMock(return_value=conn)
        engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        conn.begin.return_value.__enter__ = MagicMock(return_value=None)
        conn.begin.return_value.__exit__ = MagicMock(return_value=False)

        mapping_mock = MagicMock()
        mapping_mock.fetchone.return_value = dict(row)
        result = MagicMock()
        result.mappings.return_value = mapping_mock

        conn.execute.return_value = result

        repo = GraphVersionRepo(engine)
        created = repo.create_version(vault_id, config_id)

        # Reset call count before activate
        conn.execute.reset_mock()
        conn.begin.reset_mock()

        repo.activate_version(UUID(str(created["id"])))

        assert conn.begin.call_count == 1
        assert conn.execute.call_count == 2  # supersede + activate

    def test_failure_marking_after_save_error(self) -> None:
        """
        Simulates the builder fail path: create_version succeeds,
        then fail_version is called with an error message.

        Verifies that fail_version sets the correct version_id in its params.
        """
        vault_id = _u(10)
        config_id = _u(20)
        version_id = _u(1)

        row = _make_version_row(version_id, vault_id, version_no=1)

        engine, conn = _make_conn_engine()

        mapping_mock = MagicMock()
        mapping_mock.fetchone.return_value = dict(row)
        result = MagicMock()
        result.mappings.return_value = mapping_mock

        conn.execute.return_value = result

        repo = GraphVersionRepo(engine)
        created = repo.create_version(vault_id, config_id)

        conn.execute.reset_mock()
        error_msg = "edge insert failed"
        repo.fail_version(UUID(str(created["id"])), error_message=error_msg)

        call_params = conn.execute.call_args[0][1]
        assert call_params["version_id"] == str(version_id)
        assert call_params["notes"] == error_msg
