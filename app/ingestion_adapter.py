"""
In-process adapter boundary between VaultBubbles and the vbub-doc-ingestion package.

This module is the only place in VaultBubbles that imports from vbub_doc_ingestion.
It owns two responsibilities:
  1. parse_file() — call the package's orchestration pipeline in-process and return
     a typed CanonicalDocument.
  2. map_canonical_to_ingest_payload() — translate CanonicalDocument into the dict
     shape that ingest_queue.payload_json stores.

The database, queue, and embedding logic all live elsewhere; this module does not
import or touch them.

Integration boundary:
    VaultBubbles (caller) -> parse_file() -> orchestrate_ingestion() [in-process]
    Response: CanonicalDocument (package type, snake_case attributes)
    Mapping: map_canonical_to_ingest_payload() -> dict (IngestPayload-compatible)

If a future use case requires HTTP-based ingestion again, a new
ingestion_http_adapter.py can expose the same parse_file() /
map_canonical_to_ingest_payload() signatures and main.py can switch its import
without changing the call sites.
"""
import logging

from vbub_doc_ingestion import CanonicalDocument, ClientMeta, orchestrate_ingestion

logger = logging.getLogger(__name__)


def parse_file(file_bytes: bytes, filename: str, content_type: str) -> CanonicalDocument:
    """
    Run the vbub-doc-ingestion pipeline in-process and return a CanonicalDocument.

    Constructs a ClientMeta from the upload parameters and delegates all
    validation, type detection, parsing, and normalization to the package.
    FileValidationError propagates to the caller for HTTP status mapping.

    Args:
        file_bytes: Raw bytes of the uploaded file.
        filename: Original filename supplied by the client.
        content_type: MIME type hint supplied by the client (e.g. "application/pdf").

    Returns:
        Fully populated CanonicalDocument with status ready_for_indexing.

    Raises:
        vbub_doc_ingestion.services.file_validation_service.FileValidationError:
            If the file fails size, type, or format validation.
        Exception: Any unexpected error from the pipeline propagates as-is.
    """
    client_meta = ClientMeta(
        original_filename=filename,
        browser_mime=content_type,
        size_bytes=len(file_bytes),
    )
    logger.info("parse_file called | filename=%s | size_bytes=%d", filename, len(file_bytes))
    return orchestrate_ingestion(file_bytes, filename, client_meta)


def map_canonical_to_ingest_payload(canonical: CanonicalDocument) -> dict:
    """
    Translate a CanonicalDocument into an IngestPayload-compatible dict.

    This is the sole translation point between the vbub-doc-ingestion contract
    and VaultBubbles' internal ingest pipeline. The returned dict is stored as
    ingest_queue.payload_json and must pass IngestPayload.model_validate().

    Mapping rules:
        extraction.clean_text   -> text          (required; primary content)
        extraction.title        -> title         (falls back to display_name)
        display_name            -> url           (stored as file://{display_name})
        (hardcoded)             -> mode          ("file")
        metadata.created_at     -> captured_at   (ISO 8601 string; always present)

    Args:
        canonical: CanonicalDocument returned by parse_file().

    Returns:
        Dict that passes IngestPayload.model_validate() without error.
    """
    title = canonical.extraction.title or canonical.display_name
    url = f"file://{canonical.display_name}"
    captured_at = canonical.metadata.created_at.isoformat()

    return {
        "url": url,
        "text": canonical.extraction.clean_text,
        "title": title,
        "mode": "file",
        "captured_at": captured_at,
    }
