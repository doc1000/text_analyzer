"""
Unit tests for app.ingestion_client.

Tests cover the public surface of the ingestion client: send_file(),
health_check(), and map_canonical_to_ingest_payload(). All HTTP calls are
mocked so no running service is required.

Run with:
    pytest tests/test_ingestion_client.py -v
"""
import unittest
from unittest.mock import patch, MagicMock

import requests

from app.ingestion_client import send_file, health_check, map_canonical_to_ingest_payload
from app.schemas import CanonicalDocumentResponse, IngestPayload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_canonical(**overrides) -> CanonicalDocumentResponse:
    """Return a minimal valid CanonicalDocumentResponse for use in tests."""
    data = {
        "documentId": "doc_abc",
        "sourceType": "upload",
        "displayName": "report.pdf",
        "canonicalMime": "application/pdf",
        "extension": "pdf",
        "extraction": {
            "cleanText": "Quarterly results were strong.",
            "title": "Q3 Report",
            "warnings": [],
        },
        "metadata": {"createdAt": "2024-01-15T10:00:00"},
    }
    data.update(overrides)
    return CanonicalDocumentResponse.model_validate(data)


# ---------------------------------------------------------------------------
# send_file() — Phase 1 tests (updated for typed return value)
# ---------------------------------------------------------------------------

class TestSendFile(unittest.TestCase):
    """Tests for send_file()."""

    @patch("app.ingestion_client.requests.post")
    def test_returns_canonical_document_on_200(self, mock_post: MagicMock) -> None:
        """send_file() returns a CanonicalDocumentResponse when the service returns 200."""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "documentId": "doc_abc",
            "sourceType": "upload",
            "displayName": "report.pdf",
            "extraction": {
                "cleanText": "Quarterly results were strong.",
                "title": "Q3 Report",
                "warnings": [],
            },
        }
        mock_post.return_value = mock_response

        result = send_file(b"%PDF-1.4 content", "report.pdf", "application/pdf")

        self.assertIsInstance(result, CanonicalDocumentResponse)
        self.assertEqual(result.documentId, "doc_abc")
        self.assertEqual(result.extraction.cleanText, "Quarterly results were strong.")
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        self.assertIn("files", call_kwargs.kwargs if call_kwargs.kwargs else call_kwargs[1])

    @patch("app.ingestion_client.requests.post")
    def test_raises_on_non_200(self, mock_post: MagicMock) -> None:
        """send_file() propagates requests.HTTPError on non-2xx responses."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.HTTPError(
            "422 Unprocessable Entity"
        )
        mock_post.return_value = mock_response

        with self.assertRaises(requests.HTTPError):
            send_file(b"", "unsupported.xyz", "application/octet-stream")

    @patch("app.ingestion_client.requests.post")
    def test_raises_on_connection_error(self, mock_post: MagicMock) -> None:
        """send_file() propagates requests.ConnectionError when service is unreachable."""
        mock_post.side_effect = requests.ConnectionError("Connection refused")

        with self.assertRaises(requests.ConnectionError):
            send_file(b"hello", "test.txt", "text/plain")


# ---------------------------------------------------------------------------
# health_check() — Phase 1 tests (unchanged)
# ---------------------------------------------------------------------------

class TestHealthCheck(unittest.TestCase):
    """Tests for health_check()."""

    @patch("app.ingestion_client.requests.get")
    def test_returns_true_on_200(self, mock_get: MagicMock) -> None:
        """health_check() returns True when the service responds with 200."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        self.assertTrue(health_check())

    @patch("app.ingestion_client.requests.get")
    def test_returns_false_on_non_200(self, mock_get: MagicMock) -> None:
        """health_check() returns False when the service responds with a non-200 status."""
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_get.return_value = mock_response

        self.assertFalse(health_check())

    @patch("app.ingestion_client.requests.get")
    def test_returns_false_on_connection_error(self, mock_get: MagicMock) -> None:
        """health_check() returns False (does not raise) when the service is unreachable."""
        mock_get.side_effect = requests.ConnectionError("Connection refused")

        self.assertFalse(health_check())


# ---------------------------------------------------------------------------
# map_canonical_to_ingest_payload() — Phase 2 tests
# ---------------------------------------------------------------------------

class TestMapCanonicalToIngestPayload(unittest.TestCase):
    """Tests for map_canonical_to_ingest_payload()."""

    def test_produces_valid_ingest_payload(self) -> None:
        """Mapping output passes IngestPayload.model_validate() without error."""
        canonical = _make_canonical()
        result = map_canonical_to_ingest_payload(canonical)
        validated = IngestPayload.model_validate(result)
        self.assertEqual(validated.text, "Quarterly results were strong.")

    def test_mode_is_file(self) -> None:
        """mode is always set to 'file' for file-origin payloads."""
        canonical = _make_canonical()
        result = map_canonical_to_ingest_payload(canonical)
        self.assertEqual(result["mode"], "file")

    def test_url_uses_file_scheme(self) -> None:
        """url is set to file://{displayName}."""
        canonical = _make_canonical(displayName="my_report.docx")
        result = map_canonical_to_ingest_payload(canonical)
        self.assertEqual(result["url"], "file://my_report.docx")

    def test_missing_optional_fields(self) -> None:
        """Mapping succeeds with no extraction title and no metadata."""
        canonical = CanonicalDocumentResponse.model_validate({
            "displayName": "notes.txt",
            "extraction": {
                "cleanText": "Some notes here.",
            },
        })
        result = map_canonical_to_ingest_payload(canonical)
        validated = IngestPayload.model_validate(result)
        # Falls back to displayName when extraction.title is absent
        self.assertEqual(validated.title, "notes.txt")
        # captured_at is omitted when metadata is absent
        self.assertIsNone(validated.captured_at)


if __name__ == "__main__":
    unittest.main()
