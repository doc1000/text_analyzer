# Implementation Plan — vbub-doc-ingestion

## Recommended v1 Boundary

The smallest viable v1 is **Phases 1 through 3**. At that point the full
architecture is proven end-to-end with txt/md files: upload, validation,
binary storage, extraction, normalization, canonical document assembly,
and a working FastAPI endpoint. Every layer exists, every contract is
real, and the pipeline can be tested from `curl` or pytest.

Phases 4 and 5 add the remaining v1 formats (pdf, docx, csv, xlsx) by
dropping in new extractor adapters without changing the pipeline shape.

Phase 6 hardens the service for deployment but adds no new business logic.

Do **not** implement any phase beyond what is explicitly requested.

---

## Reference Files

These files define the architecture. Follow them strictly.

| File | Role |
|---|---|
| `README.md` | Project overview, folder structure, data models, API shape |
| `ARCHITECTURE_RULES.md` | Architectural principles, repo boundaries, v1 scope |
| `CURSOR_CONSTITUION.md` | Per-phase coding rules for AI assistants |
| `AI_CONTEXT.md` | Which files to consult and what constraints to follow |
| `ingest_architecture_map.html` | Visual architecture, protocol interfaces, module map |

---

## Folder Structure Target

Directories and `__init__.py` files are created only when the first real
file in that directory is created. Do not pre-create empty packages.

```
vbub_doc_ingestion/                  # repo root
├── ingestion_service/
│   ├── __init__.py
│   └── app/
│       ├── __init__.py
│       ├── main.py
│       ├── api/
│       │   ├── __init__.py
│       │   ├── routes_ingest.py
│       │   └── schemas.py
│       ├── domain/
│       │   ├── __init__.py
│       │   ├── contracts.py
│       │   └── enums.py
│       ├── orchestration/
│       │   ├── __init__.py
│       │   └── ingest_file.py
│       ├── services/
│       │   ├── __init__.py
│       │   ├── file_validation_service.py
│       │   ├── tag_policy_service.py
│       │   ├── parser_router.py
│       │   ├── text_normalization_service.py
│       │   ├── boilerplate_cleanup_service.py   <- Phase 4 only
│       │   └── tabular_text_policy_service.py   <- Phase 5 only
│       ├── extractors/
│       │   ├── __init__.py
│       │   ├── base.py
│       │   ├── text_extractor.py
│       │   ├── pdf_extractor.py                 <- Phase 4
│       │   ├── docx_extractor.py                <- Phase 4
│       │   ├── csv_extractor.py                 <- Phase 5
│       │   └── xlsx_extractor.py                <- Phase 5
│       └── storage/
│           ├── __init__.py
│           └── blob_store.py
├── tests/
│   ├── conftest.py
│   ├── fixtures/
│   │   └── (fixture files added per phase)
│   ├── test_health.py
│   ├── test_validation.py
│   ├── test_tag_policy.py
│   ├── test_blob_store.py
│   ├── test_text_extractor.py
│   ├── test_normalization.py
│   ├── test_ingest_file.py
│   ├── test_pdf_extractor.py                    <- Phase 4
│   ├── test_docx_extractor.py                   <- Phase 4
│   ├── test_csv_extractor.py                    <- Phase 5
│   └── test_xlsx_extractor.py                   <- Phase 5
├── requirements.txt
├── .env.local.example
├── .gitignore
├── README.md
├── ARCHITECTURE_RULES.md
├── CURSOR_CONSTITUION.md
├── AI_CONTEXT.md
├── DECISIONS.md
└── IMPLEMENTATION_PLAN.md
```

---

## Architectural Guardrails

These constraints apply to every phase. Do not violate them.

1. **`upload_gateway.py`** — No auth in v1. Do not create this file.
   The route handler plus the orchestrator cover its responsibilities.

2. **`metadata_repo.py`** — This service returns payloads to callers;
   it does not own persistence. Do not create this file.

3. **`binary_storage_service.py`** — Do not create this wrapper. The
   orchestrator calls `LocalBlobStore` directly and constructs `BinaryRef`
   inline. One file (`blob_store.py`) is sufficient.

4. **`canonical_document_builder.py`** — Do not create a dedicated
   builder service. Use a `CanonicalDocument.assemble(...)` classmethod
   defined on the model itself, or build the document inline in the
   orchestrator. A separate service for a constructor call adds indirection
   without benefit.

5. **`boilerplate_cleanup_service.py`** — Do not create this file until
   Phase 4, when it does real work for PDF. Do not create a stub-only
   version in Phase 3.

6. **`tabular_text_policy_service.py`** — Only needed for CSV/XLSX.
   Do not create until Phase 5.

7. **`tika_fallback_extractor.py`** — Explicitly out of v1 scope.
   Do not create.

8. **`CleanTextResult` model** — Do not create. Compute word count and
   character count inline at the point of assembly. A Pydantic model for
   a string and two derived integers is unnecessary overhead.

9. **`IngestFileRequest` / `IngestFileResponse`** — Do not create these
   wrapper schemas. FastAPI multipart endpoints are defined by function
   signatures, not request schema classes. Use `ClientMeta` directly as
   the parsed metadata model. Return `CanonicalDocument` directly as the
   response model.

10. **`pyproject.toml`** — Defer until packaging is needed. Use only
    `requirements.txt` for now.

11. **`IngestionStatus` values** — Define only values that are actively
    used: `processing` and `ready_for_indexing`. Do not add `received` or
    `failed` speculatively.

12. **`HASH_ALGORITHM` config** — Do not make the hash algorithm
    configurable. SHA-256 is assumed throughout. Hardcode it.

13. **`tests/__init__.py`** — pytest does not require this file. Do not
    create it.

---

## Phase 1 — Scaffold + Contracts + API Stub + Orchestration Skeleton

### Goal

Create the runnable project skeleton, define all domain contracts, wire
the API route and orchestration skeleton, and confirm everything imports
and tests pass. No business logic executes yet — the ingest endpoint
returns 501 — but the full type system is in place and the pipeline
structure is visible.

This is the single cheapest review point: contracts, types, and wiring
are all small and easy to inspect before any real logic is written.

### Inputs

- `README.md` — folder structure, `CanonicalDocument` JSON shape, API contract
- `ARCHITECTURE_RULES.md` — layer boundaries, contract rules, file naming
- `ingest_architecture_map.html` — Protocol interfaces, module responsibilities

### Files to Create

| File | Purpose |
|---|---|
| `requirements.txt` | Pin: `fastapi`, `uvicorn[standard]`, `pydantic`, `pytest`, `httpx`. |
| `.env.local.example` | Example env vars: `APP_ENV`, `LOG_LEVEL`, `MAX_UPLOAD_SIZE_MB`, `BINARY_STORAGE_PATH`, `SERVICE_NAME`. |
| `.gitignore` | Python defaults plus `data/`, `__pycache__/`, `.env`, `.venv/`. |
| `ingestion_service/__init__.py` | Empty. Package marker. |
| `ingestion_service/app/__init__.py` | Empty. |
| `ingestion_service/app/main.py` | Creates the FastAPI `app` instance. Registers `GET /health` returning `{"status": "ok"}`. Includes the ingest router. |
| `ingestion_service/app/api/__init__.py` | Empty. |
| `ingestion_service/app/api/schemas.py` | `ClientMeta` Pydantic model: `original_filename: str`, `browser_mime: Optional[str]`, `size_bytes: Optional[int]`, `local_path_hint: Optional[str]`, `device_label: Optional[str]`, `os_label: Optional[str]`, `user_tags: Optional[list[str]]`. |
| `ingestion_service/app/api/routes_ingest.py` | `APIRouter` with prefix `/ingest`. One route: `POST /file`. Handler reads `file: UploadFile` and `client_meta: str` (Form field, JSON string). Parses `client_meta` into `ClientMeta`. Calls `orchestrate_ingestion()`. Returns 501 for now. |
| `ingestion_service/app/domain/__init__.py` | Empty. |
| `ingestion_service/app/domain/enums.py` | `SupportedFormat` enum: `txt`, `md`, `pdf`, `docx`, `csv`, `xlsx`. `IngestionStatus` enum: `processing`, `ready_for_indexing`. |
| `ingestion_service/app/domain/contracts.py` | All canonical Pydantic models. See Contracts section below. Includes `CanonicalDocument.assemble(...)` classmethod. |
| `ingestion_service/app/orchestration/__init__.py` | Empty. |
| `ingestion_service/app/orchestration/ingest_file.py` | Function `orchestrate_ingestion(file_bytes: bytes, filename: str, client_meta: ClientMeta) -> CanonicalDocument`. Body is labeled TODO comments for each pipeline step. Raises `NotImplementedError`. |
| `tests/conftest.py` | `client` fixture using `fastapi.testclient.TestClient` against the app. |
| `tests/test_health.py` | `GET /health` returns 200. `POST /ingest/file` returns 501. |

### Files to Modify

None (greenfield phase).

### Contracts Defined

All domain contracts are established here and do not change shape in
subsequent phases.

**`ingestion_service/app/domain/contracts.py`**

- `BinaryRef`: `storage_key: str`, `checksum_sha256: str`, `size_bytes: int`
- `SourceLocator`: `device_label: Optional[str]`, `os_label: Optional[str]`, `local_path_hint: Optional[str]`
- `ExtractionResult`: `parser_name: str`, `parser_version: str`, `title: Optional[str]`, `clean_text: str`, `warnings: list[str]`
- `DocumentMetadata`: `tags: list[str]`, `created_at: datetime`
- `CanonicalDocument`: `document_id: str`, `display_name: str`, `canonical_mime: str`, `extension: str`, `binary_ref: BinaryRef`, `source_locator: SourceLocator`, `extraction: ExtractionResult`, `metadata: DocumentMetadata`, `status: IngestionStatus`

Field names are `snake_case` in Python. Configure `model_config` with
`alias_generator = to_camel` and `populate_by_name = True` so JSON
output uses camelCase as shown in the README.

`CanonicalDocument.assemble(...)` is a `@classmethod` that accepts the
outputs of prior pipeline steps and returns a fully constructed
`CanonicalDocument`. It is the canonical assembly point and lives on the
model itself, not in a separate service file.

`ingestion_service/app/domain/models.py` — Do not create. Internal
intermediates (`ValidationResult`) are defined in
`file_validation_service.py` where they are used.

### Tests to Add

| Test file | What it tests |
|---|---|
| `tests/test_health.py` | Health endpoint returns 200. Ingest stub returns 501. |

### Out of Scope

- Any business logic (validation, storage, extraction, normalization)
- All service files under `services/`
- All extractor files under `extractors/`
- `storage/blob_store.py`
- `config.py` and error handlers
- `pyproject.toml`
- `domain/models.py`
- `IngestFileRequest`, `IngestFileResponse`, `CleanTextResult`
- `IngestionStatus.received` and `IngestionStatus.failed`

### Definition of Done

- `pip install -r requirements.txt` succeeds.
- `uvicorn ingestion_service.app.main:app --reload` starts without errors.
- `GET /health` returns `{"status": "ok"}`.
- `POST /ingest/file` returns 501.
      in terminal:
      curl.exe -X POST "http://127.0.0.1:8000/ingest/file" -F "file=@README.md" -F 'client_meta={"original_filename":"README.md","browser_mime":"text/markdown","size_bytes":123}'

- `CanonicalDocument` serializes to camelCase JSON matching the README example.
- `pytest` passes.
      terminal: python -m pytest -v
      -- add -k 'ingest' to specify only tests containing 'ingest' 
### Stop and Review

- Inspect `contracts.py`: all field names, types, and camelCase aliases match the README JSON example exactly.
- Confirm `orchestration/ingest_file.py` contains only TODO comments and a `NotImplementedError` — no logic has crept in.
- Confirm no service files, extractor files, or config files were created.

---

## Phase 2 — Validation + Binary Storage + Tag Policy

### Goal

Implement the first real pipeline steps: file validation (size limit,
MIME detection, extension normalization, SHA-256 hash), tag validation,
and local-filesystem binary storage. Wire these into the orchestrator.

After this phase, uploading a supported file stores the binary and
returns a partial `CanonicalDocument` with real `binaryRef` and
`metadata` fields. The `extraction.cleanText` field is a placeholder
string — extraction is not yet implemented.

### Inputs

- `ARCHITECTURE_RULES.md` — validation rules, storage boundaries, layer constraints
- `README.md` — `BinaryRef` shape, `SourceLocator` shape, env var reference
- `app/domain/contracts.py` — `BinaryRef`, `SourceLocator`, `DocumentMetadata`, `CanonicalDocument.assemble`
- `app/domain/enums.py` — `IngestionStatus`, `SupportedFormat`
- `app/api/schemas.py` — `ClientMeta`
- `app/orchestration/ingest_file.py` — TODO pipeline to fill in

### Files to Create

| File | Purpose |
|---|---|
| `app/services/__init__.py` | Empty. Package marker (first file in this directory). |
| `app/services/file_validation_service.py` | `ValidationResult` dataclass: `canonical_mime`, `extension`, `sha256`, `size_bytes`, `is_valid`, `rejection_reason: Optional[str]`. Function `validate_file(file_bytes: bytes, filename: str, client_meta: ClientMeta) -> ValidationResult`. Checks size against `MAX_UPLOAD_SIZE_MB` (default 25 MB). Normalizes extension from filename. Uses `python-magic` for MIME detection. Computes SHA-256. Raises `ValueError` with a clear message if invalid. |
| `app/services/tag_policy_service.py` | Function `validate_tags(tags: list[str] \| None) -> list[str]`. Max 20 tags, each max 50 chars, lowercase and strip, discard empty strings, deduplicate preserving order. Returns cleaned list. |
| `app/storage/__init__.py` | Empty. Package marker. |
| `app/storage/blob_store.py` | Class `LocalBlobStore`. `put(file_bytes, filename, sha256) -> str`: writes bytes to `<root>/<sha256[:8]>/<sha256>_<filename>`, returns storage key. `get(storage_key) -> bytes`: reads stored bytes. Storage root passed at construction. Uses `pathlib`. |

### Files to Modify

| File | Change |
|---|---|
| `requirements.txt` | Add `python-magic-bin`. |
| `app/orchestration/ingest_file.py` | Replace TODO placeholders for validation, tag policy, and binary storage. After those steps, call `CanonicalDocument.assemble(...)` with real `binary_ref`, `source_locator`, and `metadata`, but with placeholder `ExtractionResult` (`parser_name="none"`, `clean_text="extraction not yet implemented"`, `warnings=[]`). Set `status=IngestionStatus.processing`. |
| `app/api/routes_ingest.py` | Remove 501 stub. Call `orchestrate_ingestion` and return the result with status 200. |

### Tests to Add

| Test file | What it tests |
|---|---|
| `tests/test_validation.py` | Rejects oversized files. Rejects unsupported extensions. Returns correct MIME and SHA-256 for a known fixture. Normalizes `.TXT` to `txt`. |
| `tests/test_tag_policy.py` | Lowercases and strips. Deduplicates. Rejects lists over 20. Rejects tags over 50 chars. Returns `[]` for `None` input. |
| `tests/test_blob_store.py` | `put` writes to expected path. `get` reads back matching bytes. Uses `tmp_path`. |
| `tests/test_ingest_file.py` | `POST /ingest/file` with `sample.txt` returns 200. Response contains `binaryRef.checksumSha256`, `metadata.tags`, `status == "processing"`. |

### Fixtures to Create

| File | Content |
|---|---|
| `tests/fixtures/sample.txt` | A few sentences of plain text, UTF-8. |

### Out of Scope

- Text extraction of any kind
- `parser_router.py` and all extractors
- `text_normalization_service.py`
- `boilerplate_cleanup_service.py`
- `tabular_text_policy_service.py`
- `config.py` and `error_handlers.py`
- Any changes to `contracts.py` or `enums.py`

### Definition of Done

- Uploading `sample.txt` stores the binary on disk under the configured path.
- Response JSON has `binaryRef.checksumSha256` matching the file's actual SHA-256.
- Tags are lowercased and returned correctly in `metadata.tags`.
- Uploading an unsupported extension or an oversized file returns a 4xx with a readable message.
- `pytest` passes.

### Stop and Review

- Confirm binary file is written to `BINARY_STORAGE_PATH` with the expected directory structure.
- Confirm `orchestration/ingest_file.py` only calls validation, tag policy, and `LocalBlobStore` — nothing else.
- Confirm `ExtractionResult` in the response is clearly a placeholder (`parser_name == "none"`).
- Confirm no extractor or normalization code was written.

---

## Phase 3 — TXT/MD End-to-End Pipeline

### Goal

Implement the first real extraction path using txt/md only. Wire the
parser router, `TextExtractor`, and `text_normalization_service` into
the orchestrator so that uploading a `.txt` or `.md` file returns a
fully populated `CanonicalDocument` with `status = ready_for_indexing`
and real normalized text.

This is the **minimum viable architecture checkpoint**: every layer is
exercised with real logic, the pipeline flows from route through all
services to the canonical payload, and the architecture is reviewable
before adding more formats.

### Inputs

- `ARCHITECTURE_RULES.md` — extractor interface contract, layer rules
- `README.md` — `ExtractionResult` shape, `CanonicalDocument` shape
- `ingest_architecture_map.html` — `DocumentExtractor` Protocol definition
- `app/domain/contracts.py` — `ExtractionResult`, `CanonicalDocument.assemble`
- `app/domain/enums.py` — `IngestionStatus`
- `app/orchestration/ingest_file.py` — existing pipeline with partial TODOs
- `app/services/file_validation_service.py` — `ValidationResult` type
- `app/storage/blob_store.py` — `LocalBlobStore` interface

### Files to Create

| File | Purpose |
|---|---|
| `app/extractors/__init__.py` | Empty. Package marker. |
| `app/extractors/base.py` | `DocumentExtractor` as `typing.Protocol`: `def extract(self, file_bytes: bytes, filename: str, canonical_mime: str) -> ExtractionResult`. |
| `app/extractors/text_extractor.py` | Class `TextExtractor` implementing `DocumentExtractor`. Decodes bytes (UTF-8 with BOM handling, fallback latin-1). Strips YAML front matter for `.md`. Extracts title from first `# heading` (md) or first non-empty line (txt). Sets `parser_name = "TextExtractor"`, `parser_version = "0.1.0"`. |
| `app/services/parser_router.py` | Function `route_parser(canonical_mime: str, extension: str) -> DocumentExtractor`. Returns `TextExtractor` for `txt` and `md`. Raises `ValueError("unsupported format: <ext>")` for all other extensions. |
| `app/services/text_normalization_service.py` | Function `normalize_text(raw_text: str) -> str`. Applies: Unicode NFC, `\r\n` → `\n`, collapse 3+ blank lines to 2, strip trailing whitespace per line, normalize smart quotes and dashes to ASCII equivalents. |

### Files to Modify

| File | Change |
|---|---|
| `app/orchestration/ingest_file.py` | Replace remaining TODO placeholders. Full pipeline: (1) `validate_file`, (2) `validate_tags`, (3) `LocalBlobStore.put` + build `BinaryRef` inline, (4) `route_parser`, (5) `extractor.extract(...)`, (6) `normalize_text(raw_text)`, (7) `CanonicalDocument.assemble(...)` with `status = ready_for_indexing`. Generate `document_id = "doc_" + uuid4().hex[:12]`. |

### Tests to Add

| Test file | What it tests |
|---|---|
| `tests/test_text_extractor.py` | Returns expected text for `.txt`. Handles UTF-8 BOM. Strips YAML front matter from `.md`. Extracts title from first heading. Returns correct `parser_name` and `parser_version`. |
| `tests/test_normalization.py` | Collapses excess blank lines. Converts `\r\n` to `\n`. Applies NFC normalization. Replaces smart quotes. |
| `tests/test_ingest_file.py` | Update existing test: `POST /ingest/file` with `sample.txt` returns `status == "readyForIndexing"` and `extraction.cleanText` with real content. Add second test with `sample.md` (front matter stripped, title in response). |

### Fixtures to Create

| File | Content |
|---|---|
| `tests/fixtures/sample.md` | Short markdown with `# Heading`, one paragraph, YAML front matter block. |

### Out of Scope

- PDF extraction
- DOCX extraction
- CSV and XLSX extraction
- `boilerplate_cleanup_service.py` (not created here — no stub)
- `tabular_text_policy_service.py`
- `config.py` and `error_handlers.py`
- Any changes to `contracts.py`, `enums.py`, or `schemas.py`

### Definition of Done

- `POST /ingest/file` with `sample.txt` returns `status == "readyForIndexing"` and real text in `extraction.cleanText`.
- `POST /ingest/file` with `sample.md` returns the heading as `extraction.title` and body text with front matter absent.
- Uploading a `.pdf` returns a clear 4xx error (unsupported format).
- All pipeline layers are exercised: route → orchestration → validation → storage → router → extractor → normalization → assembly.
- `pytest` passes.

### Stop and Review

- Inspect the JSON payload shape against the README example — camelCase keys, nested structure must match.
- Confirm `orchestration/ingest_file.py` is thin: no format branching, no inline parsing logic.
- Confirm no future extractor files (`pdf_extractor.py`, `docx_extractor.py`, etc.) were created.
- Confirm `boilerplate_cleanup_service.py` was not created.

**This is the minimum viable architecture checkpoint.** Review the full
payload and the orchestration flow before adding new formats.

---

## Phase 4 — PDF + DOCX Extractors + Boilerplate Cleanup

### Goal

Add real extractors for the two highest-value binary formats. Introduce
`boilerplate_cleanup_service.py` here — its first real implementation —
to remove repeated headers, footers, and page numbers from PDF output.
Extend the parser router.

### Inputs

- `ARCHITECTURE_RULES.md` — extractor interface rules, layer boundaries
- `README.md` — supported formats, extractor library choices
- `ingest_architecture_map.html` — `DocumentExtractor` Protocol, boilerplate cleanup stage
- `app/extractors/base.py` — `DocumentExtractor` Protocol to implement
- `app/domain/contracts.py` — `ExtractionResult` contract
- `app/services/parser_router.py` — router to extend
- `app/orchestration/ingest_file.py` — pipeline to extend with boilerplate step

### Files to Create

| File | Purpose |
|---|---|
| `app/extractors/pdf_extractor.py` | Class `PdfExtractor` implementing `DocumentExtractor`. Uses PyMuPDF (`fitz`). Extracts text per page via `page.get_text("text")`, joins pages with `\n\n`. Title from `doc.metadata["title"]` if non-empty, else first non-empty line. Sets `parser_name = "PdfExtractor"`, `parser_version = "0.1.0"`. Adds warning for pages yielding no text. |
| `app/extractors/docx_extractor.py` | Class `DocxExtractor` implementing `DocumentExtractor`. Uses `python-docx`. Reads paragraphs, skips empty ones, joins with `\n`. Title from `core_properties.title` if non-empty, else first heading-style paragraph, else first non-empty paragraph. Sets `parser_name = "DocxExtractor"`, `parser_version = "0.1.0"`. |
| `app/services/boilerplate_cleanup_service.py` | Function `remove_boilerplate(text: str, mime: str) -> str`. For `application/pdf`: splits on double-newline page boundaries, removes lines appearing verbatim at start/end of 3+ page blocks, removes digit-only lines (page numbers). For all other MIME types: returns text unchanged. |

### Files to Modify

| File | Change |
|---|---|
| `requirements.txt` | Add `PyMuPDF` and `python-docx`. |
| `app/services/parser_router.py` | Add: `pdf` / `application/pdf` → `PdfExtractor`; `docx` / `application/vnd.openxmlformats-officedocument.wordprocessingml.document` → `DocxExtractor`. |
| `app/orchestration/ingest_file.py` | Insert `remove_boilerplate(raw_text, canonical_mime)` between extraction and normalization. Pipeline becomes: extract → remove_boilerplate → normalize_text → assemble. |

### Tests to Add

| Test file | What it tests |
|---|---|
| `tests/test_pdf_extractor.py` | Returns non-empty text from fixture. Title from metadata or first line. Page with no text adds a warning. |
| `tests/test_docx_extractor.py` | Returns paragraph text. Title priority: `core_properties.title` → heading style → first paragraph. |
| `tests/test_ingest_file.py` | Add: PDF fixture → `status == "readyForIndexing"`, non-empty `cleanText`. DOCX fixture → same. Top-level JSON keys identical across txt, pdf, and docx responses. |

### Fixtures to Create

| File | Content |
|---|---|
| `tests/fixtures/sample.pdf` | 2–3 page text PDF with title, body text, and a repeated header/footer line. |
| `tests/fixtures/sample.docx` | DOCX with `core_properties.title`, a heading paragraph, and two body paragraphs. |

### Out of Scope

- CSV and XLSX extraction
- `tabular_text_policy_service.py`
- `config.py` and `error_handlers.py`
- Any changes to `contracts.py`, `enums.py`, `schemas.py`, or `text_normalization_service.py`
- Boilerplate cleanup for non-PDF formats (the function returns text unchanged for those)

### Definition of Done

- PDF upload extracts readable text and returns a valid canonical document.
- DOCX upload extracts readable text and returns a valid canonical document.
- Repeated header/footer lines in the PDF fixture are absent from `extraction.cleanText`.
- Payload shape is identical across txt, pdf, and docx responses.
- `pytest` passes.

### Stop and Review

- Confirm `boilerplate_cleanup_service.py` is only called for PDF and passes through for all other types.
- Confirm `parser_router.py` still raises `ValueError` for csv and xlsx.
- Confirm `contracts.py` was not modified.
- Confirm no CSV or XLSX extractor files were created.

---

## Phase 5 — CSV + XLSX Extractors + Tabular Text Policy

### Goal

Add extractors for tabular documents. Implement the tabular text policy
that flattens rows into readable pipe-delimited text and suppresses
numeric-heavy rows that carry no semantic value for downstream topic
modeling or RAG.

### Inputs

- `ARCHITECTURE_RULES.md` — extractor interface, preferred libraries (stdlib csv, openpyxl)
- `README.md` — supported formats, tabular flattening description
- `app/extractors/base.py` — `DocumentExtractor` Protocol to implement
- `app/domain/contracts.py` — `ExtractionResult` contract
- `app/services/parser_router.py` — router to extend

### Files to Create

| File | Purpose |
|---|---|
| `app/services/tabular_text_policy_service.py` | Function `filter_rows(rows: list[list[str]], source_type: str) -> list[list[str]]`. Suppresses rows where >80% of non-empty cells parse as `float`. Always retains the first row (header). Returns filtered list. |
| `app/extractors/csv_extractor.py` | Class `CsvExtractor` implementing `DocumentExtractor`. Decodes bytes (UTF-8, fallback latin-1). Sniffs dialect with `csv.Sniffer`. Reads rows via `csv.reader`. Passes rows through `filter_rows(rows, source_type="csv")`. Flattens: one line per row, cells joined by ` \| `. Sets `parser_name = "CsvExtractor"`, `parser_version = "0.1.0"`. |
| `app/extractors/xlsx_extractor.py` | Class `XlsxExtractor` implementing `DocumentExtractor`. Uses `openpyxl.load_workbook(data_only=True, read_only=True)`. Per sheet: reads rows as string lists, passes through `filter_rows`, flattens same as CSV, prepends `## Sheet: <name>`. Joins sheets with `\n\n`. Sets `parser_name = "XlsxExtractor"`, `parser_version = "0.1.0"`. |

### Files to Modify

| File | Change |
|---|---|
| `requirements.txt` | Add `openpyxl`. |
| `app/services/parser_router.py` | Add: `csv` / `text/csv` → `CsvExtractor`; `xlsx` / `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` → `XlsxExtractor`. |

### Tests to Add

| Test file | What it tests |
|---|---|
| `tests/test_csv_extractor.py` | Returns pipe-delimited text. Header row preserved. Rows >80% numeric are absent. |
| `tests/test_xlsx_extractor.py` | Two-sheet fixture produces two labeled blocks. Numeric suppression works. |
| `tests/test_ingest_file.py` | Add: CSV fixture → `status == "readyForIndexing"`, pipe-delimited `cleanText`. XLSX fixture → same, sheet labels present. |

### Fixtures to Create

| File | Content |
|---|---|
| `tests/fixtures/sample.csv` | Header row, two text-rich rows, two numeric-only rows. |
| `tests/fixtures/sample.xlsx` | Two sheets: one text-rich, one numeric-only. |

### Out of Scope

- Any changes to existing extractors (`text_extractor.py`, `pdf_extractor.py`, `docx_extractor.py`)
- `boilerplate_cleanup_service.py` (already complete, do not modify)
- `config.py` and `error_handlers.py`
- Any changes to `contracts.py`, `enums.py`, or `schemas.py`

### Definition of Done

- CSV upload returns readable pipe-delimited text with numeric rows suppressed.
- XLSX upload returns text with sheet labels and numeric rows suppressed.
- `cleanText` is readable by a human or usable by a topic model — not raw numeric noise.
- Payload shape is unchanged from prior phases.
- `pytest` passes across all extractor tests and integration tests.

### Stop and Review

- Confirm `cleanText` for the CSV fixture contains header text and non-numeric rows only.
- Confirm `cleanText` for the XLSX fixture has `## Sheet:` labels.
- Confirm no changes were made to `contracts.py`, `boilerplate_cleanup_service.py`, or any prior extractor.
- Confirm `parser_router.py` now covers all six v1 formats without branching logic in the orchestrator.

**This is the v1 feature-complete checkpoint.** All six formats are
supported. The pipeline is end-to-end.

---

## Phase 6 — Error Handling, Config, Logging, Deployment Prep

### Goal

Harden the service for deployment. Add centralized configuration,
structured error responses, and pipeline logging. No new business logic.
Freeze the v1 payload schema.

### Inputs

- `README.md` — env var reference, deployment context
- `ARCHITECTURE_RULES.md` — deployment direction, config philosophy
- `app/main.py` — app instance to register handlers on
- `app/api/routes_ingest.py` — route to add logging to
- `app/orchestration/ingest_file.py` — pipeline to add logging to
- `app/services/file_validation_service.py` — to define `FileValidationError` and read config
- `app/storage/blob_store.py` — to accept configurable storage root

### Files to Create

| File | Purpose |
|---|---|
| `app/config.py` | `Settings` class using `pydantic-settings`. Fields: `app_env: str = "development"`, `log_level: str = "info"`, `max_upload_size_mb: int = 25`, `binary_storage_path: str = "./data/blobs"`, `service_name: str = "vbub-doc-ingestion"`. Module-level singleton `settings = Settings()`. |
| `app/api/error_handlers.py` | FastAPI exception handlers: `ValueError` → 400, `FileValidationError` → 422, `NotImplementedError` → 501, `Exception` → 500. All return `{"error": "<type>", "detail": "<message>"}`. |

`FileValidationError(ValueError)` is defined in `file_validation_service.py`
and raised instead of plain `ValueError` for validation rejections.

### Files to Modify

| File | Change |
|---|---|
| `requirements.txt` | Add `pydantic-settings`. |
| `app/main.py` | Register error handlers. Add startup log with service name and env. |
| `app/services/file_validation_service.py` | Define `FileValidationError(ValueError)`. Raise it on invalid files. Replace hardcoded size limit with `settings.max_upload_size_mb`. |
| `app/storage/blob_store.py` | Accept storage root as constructor argument. Orchestrator passes `settings.binary_storage_path`. |
| `app/orchestration/ingest_file.py` | Add `logger = logging.getLogger(__name__)`. Log INFO at pipeline entry, at each major step, and on completion. Log WARNING for non-empty `extraction.warnings`. |
| `app/api/routes_ingest.py` | Add `logger = logging.getLogger(__name__)`. Log request received and response sent (filename, status). |

### Tests to Add

| Test file | What it tests |
|---|---|
| `tests/test_error_handling.py` | POST with no file returns 400. Unsupported extension returns 422. Malformed `client_meta` JSON returns 400. All error responses are JSON, not HTML. |

### Out of Scope

- New extractors or format support of any kind
- Changes to `contracts.py`, `enums.py`, or the `CanonicalDocument` shape
- Changes to any extractor (`text_extractor.py`, `pdf_extractor.py`, etc.)
- `pyproject.toml`
- Auth, queues, databases, embeddings

### Definition of Done

- Error responses are consistent JSON objects, not HTML tracebacks.
- Setting `MAX_UPLOAD_SIZE_MB=1` in `.env` and uploading a 2 MB file returns a 422 with a readable message.
- Log output shows step-by-step pipeline progression for a successful upload.
- `pytest` passes.
- The service is ready for local demo or Fly process-group deployment.

### Stop and Review

- Confirm all error responses match `{"error": "...", "detail": "..."}` format.
- Confirm `CanonicalDocument` shape is unchanged — compare against Phase 1 contract definition.
- Confirm no new features or extractors were added.
- Confirm the service starts cleanly with `uvicorn` and environment variables load from `.env`.

---

## What Is Explicitly Out of Scope for All Phases

Do not implement any of the following unless explicitly requested:

- HTML / webpage ingestion
- Tika fallback parser
- OCR
- NER / entity extraction
- Semantic enrichment
- PowerPoint / ODT support
- Background job queues
- Authentication / authorization
- Database persistence (`metadata_repo`)
- Embedding generation
- LangChain integration
- `pandas` in the critical path
- Batch / multi-file upload
- `upload_gateway.py`
- `binary_storage_service.py`
- `canonical_document_builder.py`
- `tika_fallback_extractor.py`
- `pyproject.toml` (until packaging is needed)

---

## Recommended First Coding Step

**Begin with Phase 1.** It delivers the project scaffold, the full
domain type system, the API route, and the orchestration skeleton in
one reviewable step. Every subsequent phase has a clear typed target
to build toward.
