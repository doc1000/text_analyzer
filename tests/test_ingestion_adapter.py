"""
Unit tests for app.ingestion_adapter.

Tests cover the public surface of the in-process adapter: parse_file() and
map_canonical_to_ingest_payload(). The vbub_doc_ingestion package call is
mocked so no file system or installed-package behaviour is required.

Run with:
    pytest tests/test_ingestion_adapter.py -v
"""
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from vbub_doc_ingestion import CanonicalDocument
from vbub_doc_ingestion.domain.contracts import (
    BinaryRef,
    DocumentMetadata,
    ExtractionResult,
    SourceLocator,
)
from vbub_doc_ingestion.domain.enums import IngestionStatus
from vbub_doc_ingestion.services.file_validation_service import FileValidationError

from app.ingestion_adapter import map_canonical_to_ingest_payload, parse_file
from app.schemas import IngestPayload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIXED_TS = datetime(2026, 3, 12, 10, 0, 0, tzinfo=timezone.utc)


def _make_canonical(
    display_name: str = "report.pdf",
    clean_text: str = "Quarterly results.",
    title: str | None = "Q3 Report",
    created_at: datetime = _FIXED_TS,
) -> CanonicalDocument:
    """Return a minimal but fully valid CanonicalDocument for use in tests."""
    return CanonicalDocument(
        document_id="doc_abc123",
        display_name=display_name,
        canonical_mime="application/pdf",
        extension="pdf",
        binary_ref=BinaryRef(
            storage_key="blobs/report.pdf",
            checksum_sha256="deadbeef",
            size_bytes=1024,
        ),
        source_locator=SourceLocator(),
        extraction=ExtractionResult(
            parser_name="PdfExtractor",
            parser_version="0.1.0",
            title=title,
            clean_text=clean_text,
            warnings=[],
        ),
        metadata=DocumentMetadata(tags=[], created_at=created_at),
        status=IngestionStatus.ready_for_indexing,
    )


# ---------------------------------------------------------------------------
# parse_file()
# ---------------------------------------------------------------------------

class TestParseFile(unittest.TestCase):
    """Tests for parse_file()."""

    @patch("app.ingestion_adapter.orchestrate_ingestion")
    def test_calls_orchestrate_ingestion_with_correct_client_meta(
        self, mock_orchestrate: MagicMock
    ) -> None:
        """parse_file() builds ClientMeta from its args and delegates to orchestrate_ingestion."""
        expected_doc = _make_canonical()
        mock_orchestrate.return_value = expected_doc

        result = parse_file(b"%PDF content", "report.pdf", "application/pdf")

        self.assertIs(result, expected_doc)
        mock_orchestrate.assert_called_once()
        _args, _kwargs = mock_orchestrate.call_args
        file_bytes_arg, filename_arg, client_meta_arg = _args
        self.assertEqual(file_bytes_arg, b"%PDF content")
        self.assertEqual(filename_arg, "report.pdf")
        self.assertEqual(client_meta_arg.original_filename, "report.pdf")
        self.assertEqual(client_meta_arg.browser_mime, "application/pdf")
        self.assertEqual(client_meta_arg.size_bytes, len(b"%PDF content"))

    @patch("app.ingestion_adapter.orchestrate_ingestion")
    def test_propagates_file_validation_error(
        self, mock_orchestrate: MagicMock
    ) -> None:
        """parse_file() lets FileValidationError from the package propagate unchanged."""
        mock_orchestrate.side_effect = FileValidationError("File too large.")

        with self.assertRaises(FileValidationError):
            parse_file(b"x" * 1000, "huge.pdf", "application/pdf")

    @patch("app.ingestion_adapter.orchestrate_ingestion")
    def test_propagates_unexpected_exception(
        self, mock_orchestrate: MagicMock
    ) -> None:
        """parse_file() lets arbitrary exceptions from the pipeline propagate."""
        mock_orchestrate.side_effect = RuntimeError("unexpected pipeline error")

        with self.assertRaises(RuntimeError):
            parse_file(b"data", "file.txt", "text/plain")


# ---------------------------------------------------------------------------
# map_canonical_to_ingest_payload()
# ---------------------------------------------------------------------------

class TestMapCanonicalToIngestPayload(unittest.TestCase):
    """Tests for map_canonical_to_ingest_payload()."""

    def test_produces_valid_ingest_payload(self) -> None:
        """Mapping output passes IngestPayload.model_validate() without error."""
        canonical = _make_canonical()
        result = map_canonical_to_ingest_payload(canonical)
        validated = IngestPayload.model_validate(result)
        self.assertEqual(validated.text, "Quarterly results.")

    def test_mode_is_file(self) -> None:
        """mode is always set to 'file' for file-origin payloads."""
        result = map_canonical_to_ingest_payload(_make_canonical())
        self.assertEqual(result["mode"], "file")

    def test_url_uses_file_scheme(self) -> None:
        """url is stored as file://{display_name}."""
        result = map_canonical_to_ingest_payload(_make_canonical(display_name="my_notes.docx"))
        self.assertEqual(result["url"], "file://my_notes.docx")

    def test_title_from_extraction(self) -> None:
        """title comes from extraction.title when present."""
        result = map_canonical_to_ingest_payload(
            _make_canonical(display_name="doc.pdf", title="Extracted Title")
        )
        self.assertEqual(result["title"], "Extracted Title")

    def test_title_falls_back_to_display_name(self) -> None:
        """title falls back to display_name when extraction.title is None."""
        result = map_canonical_to_ingest_payload(
            _make_canonical(display_name="notes.txt", title=None)
        )
        self.assertEqual(result["title"], "notes.txt")

    def test_captured_at_is_iso_string(self) -> None:
        """captured_at is serialised to an ISO 8601 string from metadata.created_at."""
        result = map_canonical_to_ingest_payload(_make_canonical(created_at=_FIXED_TS))
        self.assertEqual(result["captured_at"], _FIXED_TS.isoformat())

    def test_captured_at_accepted_by_ingest_payload(self) -> None:
        """IngestPayload accepts the ISO string in captured_at without error."""
        result = map_canonical_to_ingest_payload(_make_canonical())
        validated = IngestPayload.model_validate(result)
        self.assertIsNotNone(validated.captured_at)

    def test_clean_text_maps_to_text_field(self) -> None:
        """extraction.clean_text becomes the text field in the payload."""
        result = map_canonical_to_ingest_payload(
            _make_canonical(clean_text="Important document content.")
        )
        self.assertEqual(result["text"], "Important document content.")


if __name__ == "__main__":
    unittest.main()
