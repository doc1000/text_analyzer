"""
Tests for the POST /ingest/file endpoint.

All DB, auth, vault, and doc-ingestion HTTP calls are mocked. No running
database or ingestion service is required.

Run with:
    pytest tests/test_ingest_file.py -v
"""
import uuid
from unittest.mock import MagicMock, patch

import pytest
import requests
from fastapi.testclient import TestClient

from app.main import app
from app.auth import get_current_user, resolve_target_vault
from app.db import get_db
from app.schemas import CanonicalDocumentResponse

# ---------------------------------------------------------------------------
# Shared test constants
# ---------------------------------------------------------------------------

MOCK_QUEUE_ID = uuid.uuid4()

MOCK_USER = MagicMock()
MOCK_USER.id = uuid.uuid4()

MOCK_VAULT = MagicMock()
MOCK_VAULT.id = uuid.uuid4()

_VALID_FILE = ("report.pdf", b"%PDF-1.4 content", "application/pdf")

_CANONICAL_RESPONSE = CanonicalDocumentResponse.model_validate({
    "displayName": "report.pdf",
    "extraction": {
        "cleanText": "Full text of the quarterly report.",
        "title": "Q3 Report",
    },
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_db() -> MagicMock:
    """Return a mock DB session whose refresh() sets the row id."""
    db = MagicMock()
    def _set_id(obj):
        obj.id = MOCK_QUEUE_ID
    db.refresh.side_effect = _set_id
    return db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_db():
    return _make_mock_db()


@pytest.fixture
def auth_client(mock_db):
    """TestClient with auth, vault, and DB dependencies fully mocked."""
    def _get_db():
        yield mock_db

    app.dependency_overrides[get_current_user] = lambda: MOCK_USER
    app.dependency_overrides[resolve_target_vault] = lambda: MOCK_VAULT
    app.dependency_overrides[get_db] = _get_db

    yield TestClient(app, raise_server_exceptions=False)

    app.dependency_overrides.clear()


@pytest.fixture
def anon_client():
    """TestClient with only DB mocked; no auth override (for auth tests)."""
    db = _make_mock_db()

    def _get_db():
        yield db

    app.dependency_overrides[get_db] = _get_db

    yield TestClient(app, raise_server_exceptions=False)

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_ingest_file_success(auth_client):
    """Valid file upload returns 200 with status=queued, queue_id, and filename."""
    with patch("app.main.send_file", return_value=_CANONICAL_RESPONSE), \
         patch("app.main.process_ingest_item"):
        response = auth_client.post(
            "/ingest/file",
            files={"file": _VALID_FILE},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "queued"
    assert data["filename"] == "report.pdf"
    assert "queue_id" in data
    assert data["queue_id"] == str(MOCK_QUEUE_ID)


def test_ingest_file_queue_row_created(auth_client, mock_db):
    """A queue row is inserted with the correct payload_json mapping."""
    with patch("app.main.send_file", return_value=_CANONICAL_RESPONSE), \
         patch("app.main.process_ingest_item"):
        auth_client.post(
            "/ingest/file",
            files={"file": _VALID_FILE},
        )

    mock_db.add.assert_called_once()
    queue_row = mock_db.add.call_args[0][0]
    assert queue_row.payload_json["mode"] == "file"
    assert queue_row.payload_json["url"].startswith("file://")
    assert "text" in queue_row.payload_json
    assert queue_row.status == "pending"


def test_ingest_file_requires_auth(anon_client):
    """Without a valid token, the endpoint returns 401 when auth is required."""
    with patch.dict("os.environ", {"VB_REQUIRE_AUTH": "true"}), \
         patch("app.main.send_file", return_value=_CANONICAL_RESPONSE), \
         patch("app.main.process_ingest_item"):
        response = anon_client.post(
            "/ingest/file",
            files={"file": _VALID_FILE},
            # No Authorization header
        )

    assert response.status_code == 401


def test_ingest_file_service_unreachable_returns_503(auth_client):
    """ConnectionError from the doc-ingestion service surfaces as HTTP 503."""
    with patch("app.main.send_file", side_effect=requests.ConnectionError("refused")), \
         patch("app.main.process_ingest_item"):
        response = auth_client.post(
            "/ingest/file",
            files={"file": _VALID_FILE},
        )

    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"].lower()


def test_ingest_file_service_error_returns_502(auth_client):
    """HTTPError from the doc-ingestion service surfaces as HTTP 502."""
    with patch("app.main.send_file", side_effect=requests.HTTPError("422 Unprocessable")), \
         patch("app.main.process_ingest_item"):
        response = auth_client.post(
            "/ingest/file",
            files={"file": _VALID_FILE},
        )

    assert response.status_code == 502
    assert "error" in response.json()["detail"].lower()
