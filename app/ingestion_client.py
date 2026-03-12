"""
HTTP client for the vbub-doc-ingestion service, and mapping layer to
VaultBubbles-internal ingest payloads.

This module is the only place that calls the doc-ingestion HTTP API.
It owns two responsibilities:
  1. send_file() — forward raw file bytes to the service, return typed response
  2. map_canonical_to_ingest_payload() — translate CanonicalDocumentResponse
     into the dict shape that ingest_queue.payload_json stores

The database, queue, and embedding logic all live elsewhere; this module
does not import or touch them.

Integration boundary:
    VaultBubbles (caller) -> send_file() -> POST {INGESTION_SERVICE_URL}/ingest/file
    Response: CanonicalDocumentResponse
    Mapping: map_canonical_to_ingest_payload() -> dict (IngestPayload-compatible)
"""
import json
import os
import logging

import requests

from .schemas import CanonicalDocumentResponse

logger = logging.getLogger(__name__)

INGESTION_SERVICE_URL = os.getenv("INGESTION_SERVICE_URL", "http://127.0.0.1:8001")


def send_file(file_bytes: bytes, filename: str, content_type: str) -> CanonicalDocumentResponse:
    """
    Send a file to the doc-ingestion service and return the typed CanonicalDocument response.

    The file is forwarded as a multipart/form-data POST to the doc-ingestion
    /ingest/file endpoint. The service parses the file and returns a
    CanonicalDocument JSON payload, validated into a CanonicalDocumentResponse.

    Args:
        file_bytes: Raw bytes of the uploaded file.
        filename: Original filename, used as the multipart field filename.
        content_type: MIME type of the file (e.g. "application/pdf").

    Returns:
        Parsed and validated CanonicalDocumentResponse.

    Raises:
        requests.HTTPError: If the doc-ingestion service returns a non-2xx status.
        requests.ConnectionError: If the service is unreachable.
        requests.Timeout: If the request exceeds the timeout.
    """
    url = f"{INGESTION_SERVICE_URL}/ingest/file"
    files = {"file": (filename, file_bytes, content_type)}
    client_meta = json.dumps({
        "original_filename": filename,
        "browser_mime": content_type,
        "size_bytes": len(file_bytes),
    })
    response = requests.post(url, files=files, data={"client_meta": client_meta}, timeout=60)
    response.raise_for_status()
    return CanonicalDocumentResponse.model_validate(response.json())


def map_canonical_to_ingest_payload(canonical: CanonicalDocumentResponse) -> dict:
    """
    Translate a CanonicalDocumentResponse into an IngestPayload-compatible dict.

    This is the sole translation point between the doc-ingestion contract and
    VaultBubbles' internal ingest pipeline. The returned dict is stored as
    ingest_queue.payload_json and must pass IngestPayload.model_validate().

    Mapping rules:
        extraction.cleanText  -> text          (required; primary content)
        extraction.title      -> title         (falls back to displayName)
        displayName           -> url           (stored as file://{displayName})
        (hardcoded)           -> mode          ("file")
        metadata.createdAt    -> captured_at   (optional; omitted if absent)

    Args:
        canonical: Validated response from the doc-ingestion service.

    Returns:
        Dict that passes IngestPayload.model_validate() without error.
    """
    title = canonical.extraction.title or canonical.displayName
    url = f"file://{canonical.displayName}"

    captured_at = None
    if canonical.metadata and "createdAt" in canonical.metadata:
        captured_at = canonical.metadata["createdAt"]

    payload: dict = {
        "url": url,
        "text": canonical.extraction.cleanText,
        "title": title,
        "mode": "file",
    }
    if captured_at is not None:
        payload["captured_at"] = captured_at

    return payload


def health_check() -> bool:
    """
    Check whether the doc-ingestion service is reachable.

    Calls GET {INGESTION_SERVICE_URL}/health. Returns True only if the
    service responds with HTTP 200. All other outcomes return False without
    raising.

    Returns:
        True if the service is up and returns 200, False otherwise.
    """
    try:
        url = f"{INGESTION_SERVICE_URL}/health"
        response = requests.get(url, timeout=5)
        return response.status_code == 200
    except requests.RequestException:
        return False
