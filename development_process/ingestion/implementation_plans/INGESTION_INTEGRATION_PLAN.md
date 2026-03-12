# Ingestion Integration Plan

Phased plan for integrating the vbub-doc-ingestion service into VaultBubbles.

Scope: file upload only. No refactoring of existing extension ingestion, topic modeling,
embeddings, retrieval, clustering, or auth systems.

Governance references:
- `development_process/CURSOR_CONSTITUTION.md`
- `development_process/ingestion/system_rules/ARCHITECTURE_RULES.md`
- `development_process/ingestion/system_rules/AI_CONTEXT.md`
- `development_process/ingestion/system_rules/SERVICE_BOUNDARIES.md`

---

## Current-Flow Inventory

### Extension ingestion (existing, not modified)

1. Browser extension sends JSON to `POST /ingest`
2. Route handler validates `IngestPayload` (url, text, mode, tags, pdf fields)
3. `IngestPayload` is serialized to `ingest_queue.payload_json`
4. `BackgroundTasks` triggers `process_ingest_item(queue_id)`
5. Worker reconstructs `IngestPayload` from `payload_json`
6. Worker handles PDF URL parsing, trafilatura extraction, linked PDFs
7. Worker creates `Document` row (url, title, captured_text, extracted_text, full_text)
8. Worker calls `embed_doc_chunks(doc)`
9. Worker runs URL and semantic deduplication
10. Worker updates `ingest_queue.status` to `completed` or `failed`

Key files:
- `app/main.py` — `POST /ingest` route (lines 247-298)
- `app/schemas.py` — `IngestPayload` model (lines 80-91)
- `app/ingest_worker.py` — `process_ingest_item()` (lines 72-212)
- `app/models.py` — `Document` (lines 123-144), `IngestQueue` (lines 147-160)
- `app/helpers.py` — `embed_doc_chunks()`
- `app/topics.py` — `find_url_duplicate()`, `find_semantic_duplicate()`

### Frontend (existing, not functional)

- `app/static/index.html` has a disabled "Upload Files" dropdown item (lines 736-750)
- No file input element, no click handler, no upload logic

### Configuration (existing)

- `env.example` — no ingestion service URL
- `app/config.py` — no `INGESTION_SERVICE_URL`

---

## Integration Boundary

```
Browser                VaultBubbles                   vbub-doc-ingestion
  |                        |                                |
  |  POST /ingest/file     |                                |
  |  (multipart upload) -->|                                |
  |                        |  POST /ingest/file             |
  |                        |  (multipart forward) --------->|
  |                        |                                |
  |                        |  <--- CanonicalDocument JSON --|
  |                        |                                |
  |                        |  map to IngestPayload dict     |
  |                        |  insert into ingest_queue      |
  |                        |  trigger process_ingest_item() |
  |                        |                                |
  |  <-- 200 accepted -----|                                |
```

- VaultBubbles receives the raw file from the browser.
- VaultBubbles forwards the file bytes to doc-ingestion via HTTP multipart.
- doc-ingestion parses the file and returns CanonicalDocument JSON.
- VaultBubbles maps CanonicalDocument into an IngestPayload-compatible dict.
- VaultBubbles inserts into `ingest_queue` and reuses the existing async pipeline.
- doc-ingestion never stores anything permanently.
- The browser never talks directly to doc-ingestion.

---

## CanonicalDocument Translation Point

The mapping from CanonicalDocument to VaultBubbles-internal payload happens
in a dedicated mapping function, called from the new `POST /ingest/file` route handler
before insertion into `ingest_queue`.

Expected CanonicalDocument shape (from doc-ingestion):

```
{
  "documentId": "...",
  "sourceType": "upload",
  "displayName": "report.pdf",
  "canonicalMime": "application/pdf",
  "extension": "pdf",
  "binaryRef": { "storageKey": "...", "checksumSha256": "...", "sizeBytes": ... },
  "extraction": {
    "parserName": "PdfExtractor",
    "parserVersion": "0.1.0",
    "title": "Quarterly Report",
    "cleanText": "...",
    "warnings": []
  },
  "metadata": { "createdAt": "...", ... }
}
```

Mapping to IngestPayload-compatible dict:

| CanonicalDocument field  | IngestPayload field | Notes                                 |
|--------------------------|---------------------|---------------------------------------|
| `extraction.cleanText`   | `text`              | Primary text content                  |
| `extraction.title`       | `title`             | Falls back to `displayName` if absent |
| `displayName`            | `url`               | Stored as `file://{displayName}`      |
| (hardcoded)              | `mode`              | `"file"`                              |
| `metadata.createdAt`     | `captured_at`       | Optional, falls back to now           |

The queue stores the VaultBubbles-native payload dict, not the raw CanonicalDocument.

### Worker compatibility analysis

When `process_ingest_item()` processes a file-origin payload:

1. `captured_text = payload.text` — already populated with `cleanText` from doc-ingestion
2. `pdf_to_parse` — `None` (no pdf_to_parse field, `file://` URL does not match `_is_pdf_url`)
3. PDF extraction branch — skipped (no PDF URL)
4. Linked PDFs branch — skipped (no linked_pdf_urls)
5. Trafilatura branch — skipped (`mode != "page"`)
6. Text presence check — passes (text was pre-populated)
7. URL dedup — skipped (`has_extracted_text` is `False` because trafilatura did not run)
8. Document creation — proceeds normally
9. `embed_doc_chunks()` — runs normally
10. Semantic dedup — skipped (`has_extracted_text` is `False`)
11. Queue status — set to `completed`

The only required change: expand `IngestPayload.mode` to accept `"file"` in addition
to `"page"`, `"selection"`, `"note"`.

### Migration risk assessment

Risk to existing extension flow: **none**.

- `POST /ingest` route is not modified
- `IngestPayload` gains one new `mode` value (`"file"`) but existing values are unchanged
- `process_ingest_item()` may gain a minor guard for file-origin logging but existing branches are not altered
- No changes to topic modeling, embeddings, retrieval, clustering, dedup logic, or auth

---

## Phase 1 — Configuration and Ingestion Client

### Goal

Create a thin HTTP client module that VaultBubbles uses to call the doc-ingestion service.
Add the `INGESTION_SERVICE_URL` configuration. Verify the doc-ingestion service is reachable.

### Inputs

- Local doc-ingestion service running at `http://127.0.0.1:8001`
- The doc-ingestion `POST /ingest/file` endpoint accepts multipart file upload
- The doc-ingestion `GET /health` endpoint returns 200 when the service is up

### Files to Create

- `app/ingestion_client.py`

### Files to Modify

- `env.example`

### Tests to Add

- `tests/test_ingestion_client.py`
  - Test `send_file()` returns parsed dict on 200 (mocked HTTP)
  - Test `send_file()` raises on non-200 (mocked HTTP)
  - Test `health_check()` returns True/False (mocked HTTP)

### Out of Scope

- No new endpoint in VaultBubbles
- No frontend changes
- No schema changes
- No worker changes
- No Pydantic model for the doc-ingestion response yet

### Definition of Done

- `app/ingestion_client.py` exists with `send_file(file_bytes, filename, content_type)` and `health_check()` functions
- `INGESTION_SERVICE_URL` is read from environment with default `http://127.0.0.1:8001`
- `env.example` includes the new variable with a comment
- All tests pass

### Stop and Review

Before proceeding, confirm:
- The doc-ingestion service is running locally and responds to `/health`
- `send_file()` contract matches the doc-ingestion API (multipart field name, expected response shape)
- No new dependencies were added beyond stdlib `urllib` or `httpx` if already in requirements

### PHASE CONTRACT

- **Files to Create:** `app/ingestion_client.py`, `tests/test_ingestion_client.py`
- **Files to Modify:** `env.example`
- **Functions Added or Changed:** `send_file()`, `health_check()` (both new in `ingestion_client.py`)
- **Data Structures Affected:** None
- **Endpoints Added or Changed:** None
- **Tests Added:** `tests/test_ingestion_client.py` (3 test cases)

---

## Phase 2 — CanonicalDocument Response Model and Payload Mapping

### Goal

Define a Pydantic model for the CanonicalDocument response so the ingestion client
returns typed data. Create a mapping function that translates it into an
IngestPayload-compatible dict suitable for `ingest_queue.payload_json`.
Expand `IngestPayload.mode` to accept `"file"`.

### Inputs

- CanonicalDocument JSON shape (documented above)
- `IngestPayload` schema in `app/schemas.py` (lines 80-91)
- Mapping table (documented above)

### Files to Create

None.

### Files to Modify

- `app/schemas.py`
- `app/ingestion_client.py`

### Tests to Add

- `tests/test_ingestion_client.py` (extend)
  - Test mapping function produces a valid dict that `IngestPayload.model_validate()` accepts
  - Test mapping with missing optional fields (no title, no metadata.createdAt)
  - Test `mode` is set to `"file"`
  - Test `url` is set to `file://{displayName}`

### Out of Scope

- No new endpoint in VaultBubbles
- No frontend changes
- No worker changes
- Do not change the CanonicalDocument contract (owned by doc-ingestion)
- Do not add fields to `Document` or `IngestQueue` models

### Definition of Done

- `CanonicalDocumentResponse` Pydantic model exists in `app/schemas.py` with nested extraction fields
- `IngestPayload.mode` Literal expanded to include `"file"`
- A `map_canonical_to_ingest_payload(canonical: CanonicalDocumentResponse) -> dict` function exists in `app/ingestion_client.py`
- The function produces a dict that passes `IngestPayload.model_validate()`
- `send_file()` now returns a `CanonicalDocumentResponse` instead of a raw dict
- All tests pass

### Stop and Review

Before proceeding, confirm:
- The `CanonicalDocumentResponse` model matches the actual doc-ingestion response (test with a real response if possible)
- The mapping function output is accepted by `IngestPayload.model_validate()` without error
- Adding `"file"` to the mode Literal does not break existing extension ingestion tests or behavior
- No fields were added to `Document` or `IngestQueue`

### PHASE CONTRACT

- **Files to Create:** None
- **Files to Modify:** `app/schemas.py`, `app/ingestion_client.py`
- **Functions Added or Changed:** `map_canonical_to_ingest_payload()` (new), `send_file()` (changed return type)
- **Data Structures Affected:** `IngestPayload.mode` (expanded Literal), `CanonicalDocumentResponse` (new model)
- **Endpoints Added or Changed:** None
- **Tests Added:** 4 new test cases in `tests/test_ingestion_client.py`

---

## Phase 3 — New POST /ingest/file Endpoint

### Goal

Add a `POST /ingest/file` endpoint to VaultBubbles that accepts a file upload,
calls the doc-ingestion service, maps the response, inserts into `ingest_queue`,
and triggers background processing via the existing async pipeline.

### Inputs

- `app/ingestion_client.py` (from Phase 1 and 2)
- Auth dependencies: `get_current_user`, `resolve_target_vault`, `bearer_scheme` (from `app/auth.py`)
- `IngestQueue` model (from `app/models.py`)
- `process_ingest_item()` (from `app/ingest_worker.py`)
- Existing `POST /ingest` route pattern (from `app/main.py` lines 247-298)

### Files to Create

None.

### Files to Modify

- `app/main.py`

### Tests to Add

- `tests/test_ingest_file.py`
  - Test 200 response on valid file upload (mocked doc-ingestion)
  - Test queue row is created with correct payload_json
  - Test auth is required (401 without token)
  - Test 502 or 503 when doc-ingestion is unreachable
  - Test rejected file types return 400 (if applicable)

### Out of Scope

- No changes to `POST /ingest` (extension route)
- No changes to `process_ingest_item()`
- No changes to `Document` or `IngestQueue` models
- No frontend changes
- No multi-file upload (single file only for v1)
- No progress tracking or upload status endpoint

### Definition of Done

- `POST /ingest/file` exists and accepts `UploadFile` with auth and vault resolution
- The route calls `send_file()`, maps via `map_canonical_to_ingest_payload()`, inserts into `ingest_queue`, and triggers `process_ingest_item()` via `BackgroundTasks`
- The route returns a JSON response with queue item ID and status
- The route handler is thin (validation + orchestration, no business logic)
- All tests pass
- Existing `POST /ingest` tests still pass (if any exist)

### Stop and Review

Before proceeding, confirm:
- The endpoint works end-to-end with the local doc-ingestion service (manual test with curl or httpie)
- A queue row is created with status `pending`
- `process_ingest_item()` is triggered and completes without error
- A `Document` row appears in the database after processing
- The existing `POST /ingest` route is unmodified and still works
- No more than 1 file was modified in this phase

### PHASE CONTRACT

- **Files to Create:** `tests/test_ingest_file.py`
- **Files to Modify:** `app/main.py`
- **Functions Added or Changed:** `ingest_file()` route handler (new)
- **Data Structures Affected:** None
- **Endpoints Added or Changed:** `POST /ingest/file` (new)
- **Tests Added:** `tests/test_ingest_file.py` (5 test cases)

---

## Phase 4 — Ingest Worker Compatibility Verification

### Goal

Verify and, if needed, minimally adjust `process_ingest_item()` to handle
file-origin payloads cleanly. Add a targeted test proving the worker processes
a file-origin `ingest_queue` row correctly.

### Inputs

- Worker analysis (documented above in "Worker compatibility analysis")
- A file-origin `payload_json` dict produced by `map_canonical_to_ingest_payload()`
- `process_ingest_item()` in `app/ingest_worker.py`

### Files to Create

None.

### Files to Modify

- `app/ingest_worker.py` (only if needed — may be verification-only)

### Tests to Add

- `tests/test_ingest_worker_file.py`
  - Test `process_ingest_item()` with a file-origin payload creates a `Document`
  - Test that the worker skips PDF extraction for file-origin payloads
  - Test that the worker skips trafilatura for file-origin payloads
  - Test that `embed_doc_chunks()` is called (mocked)
  - Test queue status is set to `completed`

### Out of Scope

- No changes to PDF extraction, trafilatura, deduplication, or embedding logic
- No changes to the extension ingestion path
- No changes to `IngestPayload`, `Document`, or `IngestQueue` models
- No new endpoints

### Definition of Done

- All existing worker tests still pass
- New tests prove file-origin payloads are processed correctly
- If any guard was added (e.g., early return on `mode == "file"` to skip irrelevant branches), it is minimal and well-commented
- Worker does not attempt network calls (trafilatura, PDF download) for file-origin items

### Stop and Review

Before proceeding, confirm:
- The worker handles file-origin payloads without error
- The worker does not make unnecessary network calls for file-origin items
- No changes were made to existing worker branches that handle extension payloads
- If `process_ingest_item()` was modified, the diff is small (< 10 lines)

### PHASE CONTRACT

- **Files to Create:** `tests/test_ingest_worker_file.py`
- **Files to Modify:** `app/ingest_worker.py` (if needed)
- **Functions Added or Changed:** `process_ingest_item()` (minor guard, if needed)
- **Data Structures Affected:** None
- **Endpoints Added or Changed:** None
- **Tests Added:** `tests/test_ingest_worker_file.py` (5 test cases)

---

## Phase 5 — Frontend Upload Wiring

### Goal

Enable the "Upload Files" button in the frontend. Wire it to call
`POST /ingest/file` with the user's auth token. Show basic upload feedback.

### Inputs

- `app/static/index.html` — disabled dropdown item (lines 736-750)
- `POST /ingest/file` endpoint (from Phase 3)
- Existing auth token handling in the frontend (inspect `index.html` for token storage pattern)

### Files to Create

None.

### Files to Modify

- `app/static/index.html`

### Tests to Add

- Manual testing only for this phase
  - Upload a .txt file and verify it appears in the document list
  - Upload a .pdf file and verify it appears in the document list
  - Verify error feedback when doc-ingestion is down
  - Verify the upload button is no longer disabled

### Out of Scope

- No drag-and-drop upload
- No multi-file upload UI
- No upload progress bar (basic feedback only — e.g., spinner or status text)
- No changes to document list rendering or topic views
- No backend changes

### Definition of Done

- The "Upload Files" dropdown item is enabled and clickable
- Clicking it opens a file picker (hidden `<input type="file">`)
- Selected file is sent to `POST /ingest/file` with the auth token
- Success shows a brief confirmation message
- Failure shows an error message
- The uploaded document appears in the vault's document list after processing

### Stop and Review

Before proceeding, confirm:
- Upload works end-to-end locally (file picker -> backend -> doc-ingestion -> queue -> document)
- Auth is sent correctly (no 401 errors)
- Error states are handled gracefully (service down, invalid file type)
- No changes to backend files

### PHASE CONTRACT

- **Files to Create:** None
- **Files to Modify:** `app/static/index.html`
- **Functions Added or Changed:** JS upload handler (new, inline in index.html)
- **Data Structures Affected:** None
- **Endpoints Added or Changed:** None
- **Tests Added:** Manual test checklist (4 scenarios)

---

## Phase 6 — Deployment Configuration

### Goal

Configure the production VaultBubbles deployment to point at the deployed
doc-ingestion service on Fly. This is a config-only change.

### Inputs

- The deployed doc-ingestion service URL on Fly (to be determined at deploy time)
- `INGESTION_SERVICE_URL` environment variable (added in Phase 1)
- `fly.toml` or Fly secrets configuration

### Files to Create

None.

### Files to Modify

- `fly.toml` (add env variable or document how to set via `fly secrets`)

### Tests to Add

- Smoke test: after deployment, upload a file via the production UI and verify it processes
- Health check: `GET /health` on the remote doc-ingestion service returns 200

### Out of Scope

- No code changes
- No schema changes
- No new endpoints
- Do not deploy the doc-ingestion service itself (that is a separate task)

### Definition of Done

- `INGESTION_SERVICE_URL` is set to the remote doc-ingestion URL in production
- File upload works end-to-end in the deployed environment
- The health check passes for the remote doc-ingestion service
- Local development still works with `INGESTION_SERVICE_URL=http://127.0.0.1:8001`

### Stop and Review

Before proceeding, confirm:
- The remote doc-ingestion service is deployed and healthy
- The production VaultBubbles deployment can reach the remote service
- File upload works in production
- Extension ingestion is unaffected in production

### PHASE CONTRACT

- **Files to Create:** None
- **Files to Modify:** `fly.toml` (or Fly secrets only)
- **Functions Added or Changed:** None
- **Data Structures Affected:** None
- **Endpoints Added or Changed:** None
- **Tests Added:** Smoke test (manual, post-deploy)

---

## Summary of All Files Affected

| Phase | Files Created                            | Files Modified                              |
|-------|------------------------------------------|---------------------------------------------|
| 1     | `app/ingestion_client.py`, test file     | `env.example`                               |
| 2     | —                                        | `app/schemas.py`, `app/ingestion_client.py` |
| 3     | test file                                | `app/main.py`                               |
| 4     | test file                                | `app/ingest_worker.py` (if needed)          |
| 5     | —                                        | `app/static/index.html`                     |
| 6     | —                                        | `fly.toml`                                  |

Maximum files modified per phase: 2. Total new source files: 1 (`app/ingestion_client.py`).

No phase modifies more than 5 files.

No phase touches topic modeling, embeddings, retrieval, clustering, or auth logic.

No phase modifies the existing `POST /ingest` extension route.
