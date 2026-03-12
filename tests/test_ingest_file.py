"""
Tests for the POST /ingest/file endpoint.

All DB, auth, vault, and ingestion calls are mocked. No running database or
ingestion service is required. The ingestion pipeline is now in-process via
the vbub-doc-ingestion package; parse_file() is patched at app.main.

Run with:
    pytest tests/test_ingest_file.py -v
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from vbub_doc_ingestion import CanonicalDocument
from vbub_doc_ingestion.domain.contracts import (
    BinaryRef,
    DocumentMetadata,
    ExtractionResult,
    SourceLocator,
)
from vbub_doc_ingestion.domain.enums import IngestionStatus
from vbub_doc_ingestion.services.file_validation_service import FileValidationError

from app.main import app
from app.auth import get_current_user, resolve_target_vault
from app.db import get_db


# ---------------------------------------------------------------------------
# Shared test constants
# ---------------------------------------------------------------------------

MOCK_QUEUE_ID = uuid.uuid4()

MOCK_USER = MagicMock()
MOCK_USER.id = uuid.uuid4()

MOCK_VAULT = MagicMock()
MOCK_VAULT.id = uuid.uuid4()

_VALID_FILE = ("report.pdf", b"%PDF-1.4 content", "application/pdf")

_CANONICAL_DOC = CanonicalDocument(
    document_id="doc_abc123",
    display_name="report.pdf",
    canonical_mime="application/pdf",
    extension="pdf",
    binary_ref=BinaryRef(
        storage_key="blobs/report.pdf",
        checksum_sha256="deadbeef",
        size_bytes=16,
    ),
    source_locator=SourceLocator(),
    extraction=ExtractionResult(
        parser_name="PdfExtractor",
        parser_version="0.1.0",
        title="Q3 Report",
        clean_text="Full text of the quarterly report.",
        warnings=[],
    ),
    metadata=DocumentMetadata(
        tags=[],
        created_at=datetime(2026, 3, 12, 10, 0, 0, tzinfo=timezone.utc),
    ),
    status=IngestionStatus.ready_for_indexing,
)


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
    with patch("app.main.parse_file", return_value=_CANONICAL_DOC), \
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
    with patch("app.main.parse_file", return_value=_CANONICAL_DOC), \
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
         patch("app.main.parse_file", return_value=_CANONICAL_DOC), \
         patch("app.main.process_ingest_item"):
        response = anon_client.post(
            "/ingest/file",
            files={"file": _VALID_FILE},
        )

    assert response.status_code == 401


def test_ingest_file_validation_error_returns_422(auth_client):
    """FileValidationError from the ingestion package surfaces as HTTP 422."""
    with patch("app.main.parse_file",
               side_effect=FileValidationError("Unsupported file type '.exe'.")), \
         patch("app.main.process_ingest_item"):
        response = auth_client.post(
            "/ingest/file",
            files={"file": _VALID_FILE},
        )

    assert response.status_code == 422
    assert "unsupported" in response.json()["detail"].lower()


def test_ingest_file_unexpected_error_returns_500(auth_client):
    """An unexpected exception from the ingestion pipeline surfaces as HTTP 500."""
    with patch("app.main.parse_file",
               side_effect=RuntimeError("unexpected pipeline failure")), \
         patch("app.main.process_ingest_item"):
        response = auth_client.post(
            "/ingest/file",
            files={"file": _VALID_FILE},
        )

    assert response.status_code == 500
    assert "ingestion failed" in response.json()["detail"].lower()
