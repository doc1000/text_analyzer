"""
Verification tests for process_ingest_item() with file-origin payloads.

File-origin payloads are created by map_canonical_to_ingest_payload() and stored
in ingest_queue by POST /ingest/file. They have:
    mode="file", url="file://{filename}", text=<cleanText from doc-ingestion>

These tests confirm the worker processes them correctly without attempting
irrelevant network operations (PDF download, trafilatura).

All database and embedding calls are mocked. No running database is required.

Run with:
    pytest tests/test_ingest_worker_file.py -v
"""
import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.ingest_worker import process_ingest_item
from app.models import Document

# ---------------------------------------------------------------------------
# Shared fixtures and helpers
# ---------------------------------------------------------------------------

_QUEUE_ID = str(uuid.uuid4())

_FILE_PAYLOAD = {
    "url": "file://quarterly_report.pdf",
    "text": "Full text of the uploaded quarterly report.",
    "title": "Quarterly Report",
    "mode": "file",
}


def _make_queue_row(payload_json=None):
    """Return a mock IngestQueue row in pending state."""
    row = MagicMock()
    row.id = uuid.UUID(_QUEUE_ID)
    row.status = "pending"
    row.user_id = uuid.uuid4()
    row.vault_id = uuid.uuid4()
    row.payload_json = payload_json or _FILE_PAYLOAD
    return row


def _make_mock_db(queue_row):
    """Return a mock DB session pre-configured to return queue_row on first()."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = queue_row
    return db


@pytest.fixture
def file_job():
    """Provide a (queue_row, mock_db) pair for file-origin payload tests."""
    queue_row = _make_queue_row()
    mock_db = _make_mock_db(queue_row)
    return queue_row, mock_db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_worker_creates_document_for_file_origin_payload(file_job):
    """process_ingest_item() creates a Document row with the canonical text and URL."""
    queue_row, mock_db = file_job

    with patch("app.ingest_worker.SessionLocal", return_value=mock_db), \
         patch("app.ingest_worker.embed_doc_chunks"):
        process_ingest_item(_QUEUE_ID)

    # Collect every object passed to db.add()
    added = [c.args[0] for c in mock_db.add.call_args_list]
    documents = [obj for obj in added if isinstance(obj, Document)]

    assert len(documents) == 1
    doc = documents[0]
    assert doc.url == "file://quarterly_report.pdf"
    assert doc.captured_text == "Full text of the uploaded quarterly report."
    assert doc.title == "Quarterly Report"


def test_worker_skips_pdf_extraction_for_file_origin_payload(file_job):
    """PDF extraction is not attempted for file:// URLs even when they end with .pdf.

    Without the file:// guard in process_ingest_item(), the URL
    'file://quarterly_report.pdf' would satisfy _is_pdf_url() because of the
    .pdf suffix and trigger a spurious download attempt.
    """
    queue_row, mock_db = file_job

    mock_extraction = MagicMock()
    with patch("app.ingest_worker.SessionLocal", return_value=mock_db), \
         patch("app.ingest_worker.embed_doc_chunks"), \
         patch.dict("sys.modules", {"app.extraction": mock_extraction}):
        process_ingest_item(_QUEUE_ID)

    mock_extraction.extract_from_pdf_urls.assert_not_called()


def test_worker_skips_trafilatura_for_file_origin_payload(file_job):
    """Trafilatura extraction is not attempted for mode='file' payloads."""
    queue_row, mock_db = file_job

    mock_extraction = MagicMock()
    with patch("app.ingest_worker.SessionLocal", return_value=mock_db), \
         patch("app.ingest_worker.embed_doc_chunks"), \
         patch.dict("sys.modules", {"app.extraction": mock_extraction}):
        process_ingest_item(_QUEUE_ID)

    mock_extraction.extract_from_url.assert_not_called()


def test_worker_calls_embed_doc_chunks_for_file_origin_payload(file_job):
    """embed_doc_chunks() is called with the created Document."""
    queue_row, mock_db = file_job

    with patch("app.ingest_worker.SessionLocal", return_value=mock_db), \
         patch("app.ingest_worker.embed_doc_chunks") as mock_embed:
        process_ingest_item(_QUEUE_ID)

    mock_embed.assert_called_once()
    doc_arg = mock_embed.call_args.args[0]
    assert isinstance(doc_arg, Document)


def test_worker_sets_queue_status_completed_for_file_origin_payload(file_job):
    """Queue row status is set to 'completed' after successful processing."""
    queue_row, mock_db = file_job

    with patch("app.ingest_worker.SessionLocal", return_value=mock_db), \
         patch("app.ingest_worker.embed_doc_chunks"):
        process_ingest_item(_QUEUE_ID)

    assert queue_row.status == "completed"
