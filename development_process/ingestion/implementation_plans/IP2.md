
## Phase 7 — Parser Router Hardening, Text-Like Detection, Architecture Map Update

### Goal

Harden parser routing so the service does not rely only on file suffixes.
Add lightweight content-based detection for text-like files and binary rejection
heuristics so mislabeled files such as `.bat`, `.jsx`, or suffixless plain-text files
can still be routed to `TextExtractor`, while binary files such as `.xls` are rejected
cleanly instead of producing junk `cleanText`.

Also update `ingest_architecture_map.html` to reflect the new Parser Router logic
and any new Python files/modules created for this phase.

No new business-domain features. No changes to downstream payload shape.

### Inputs

- `README.md` — supported formats, current routing behavior, architecture summary
- `ARCHITECTURE_RULES.md` — light dependency philosophy, parser-chosen-once principle
- `CURSOR_CONSTITUTION.md` — implement only this phase, no extra abstractions
- `IMPLEMENTATION_PLAN.md` — current phase order and prior scope decisions
- `ingest_architecture_map.html` — update architecture reference with Phase 7 parser-routing changes
- `app/services/parser_router.py` — current suffix-based routing logic
- `app/services/file_validation_service.py` — current validation and MIME/extension handling
- `app/extractors/text_extractor.py` — existing text path
- `app/extractors/csv_extractor.py` — existing CSV path
- `app/extractors/xlsx_extractor.py` — existing XLSX path
- `app/api/routes_ingest.py` — endpoint behavior for unsupported files
- `app/api/error_handlers.py` — current error mapping
- `app/domain/contracts.py` and/or `app/domain/models.py` — ensure output contract remains unchanged

### Files to Create

| File | Purpose |
|---|---|
| `app/services/content_classification_service.py` | Lightweight server-side content classification helpers used by `ParserRouter`. Contains `TEXT_LIKE_EXTENSIONS`, `BINARY_REJECT_EXTENSIONS`, `is_probably_text()`, and optional signature helpers such as `looks_like_pdf()` and ZIP/container checks if needed. |

### Files to Modify

| File | Change |
|---|---|
| `app/services/parser_router.py` | Replace suffix-only routing with hybrid routing based on extension, MIME hint, and content heuristics. Route known supported formats first, allow text-like aliases, use `is_probably_text()` before `TextExtractor`, and reject unsupported binary formats such as `.xls` with a clear validation error. |
| `app/services/file_validation_service.py` | Ensure canonical extension extraction remains available to router. Do not over-expand validation rules; keep this phase focused on routing correctness. |
| `app/api/error_handlers.py` | Ensure unsupported/binary misroutes return consistent JSON error responses. If already implemented, only adjust error typing/messages if necessary. |
| `app/extractors/text_extractor.py` | If needed, ensure it assumes decoded text input or text-like bytes only; do not make it responsible for binary detection. |
| `ingest_architecture_map.html` | Add concise Phase 7 architecture notes describing Parser Router hardening, content-based text-like classification, unsupported binary rejection, and any newly created Python file(s) in the python tree section. Update only the relevant architecture reference sections; do not rewrite the whole map. |
| `README.md` | If there is a supported-format section, add a short clarification that text-like files may be routed by content heuristics even when the suffix is nonstandard, while unsupported binary formats such as `.xls` are rejected. Keep this concise. |

### Implementation Requirements

#### 1. Add `TEXT_LIKE_EXTENSIONS`

Create a lightweight set of text-like aliases in `content_classification_service.py`.

Recommended contents:

```python
TEXT_LIKE_EXTENSIONS = {
    "txt", "md", "bat", "ps1", "py", "js", "jsx", "ts", "tsx",
    "json", "yaml", "yml", "toml", "ini", "cfg", "conf",
    "log", "sql", "xml", "html", "css", "sh", "env"
}
````

You may add a small number of similar plain-text extensions if they are clearly justified,
but do not turn this into a giant registry.

Also add a small explicit binary reject set for known unsupported binary types that
should never fall through to `TextExtractor`, such as:

```python
BINARY_REJECT_EXTENSIONS = {"xls", "exe", "dll", "bin", "dat"}
```

Keep this set minimal and conservative.

#### 2. Add `is_probably_text()`

Implement a lightweight heuristic function in `content_classification_service.py`:

```python
def is_probably_text(file_bytes: bytes) -> bool:
    ...
```

Recommended heuristic behavior:

* Inspect only an initial sample window, such as the first 4096 to 8192 bytes.
* If the sample is empty, return `True` only if that aligns with current validation policy; otherwise let validation reject empty files elsewhere.
* Reject as non-text if the sample contains a meaningful number or ratio of null bytes (`b"\\x00"`).
* Attempt UTF-8 decode of the sample.
* If UTF-8 decode fails, return `False`.
* After decoding, compute a printable-character ratio.
* Allow common whitespace such as newline, carriage return, and tab.
* Return `True` if the printable ratio is high enough (for example >= 0.85 or similarly justified).
* Keep the implementation simple, explicit, and dependency-free.

Do not add external text-detection libraries.

#### 3. Router policy order

Update `ParserRouter` so routing follows a hybrid policy in this order:

1. Route by strong known supported format first:

   * PDF
   * DOCX
   * XLSX
   * CSV
2. If the extension is in `BINARY_REJECT_EXTENSIONS`, reject clearly.
3. If the extension is in `TEXT_LIKE_EXTENSIONS`, only route to `TextExtractor` if `is_probably_text(file_bytes)` is `True`.
4. If the extension is unknown or missing, but `is_probably_text(file_bytes)` is `True`, route to `TextExtractor`.
5. Otherwise reject as unsupported binary or unsupported file type.

The router must **not** pass likely-binary content to `TextExtractor`.

Do not add Tika, libmagic-dependent content parsing, or any heavy classification framework in this phase.

#### 4. Unsupported `.xls` handling

Explicitly ensure `.xls` does **not** fall through to `TextExtractor`.

Expected behavior:

* return a readable JSON error
* 422 or 415 is acceptable if it matches existing error architecture
* message should clearly say `.xls` is not currently supported and recommend `.xlsx` if appropriate

#### 5. Keep the `CanonicalDocument` contract unchanged

This phase must not modify:

* field names
* payload nesting
* metadata shape
* extraction payload structure

Only routing behavior and unsupported-file handling should change.

#### 6. Update `ingest_architecture_map.html`

Make a focused architecture-reference update, not a full rewrite.

Add concise Phase 7 information covering:

* Parser Router no longer relies on suffix alone
* New content classification step / helper
* `TEXT_LIKE_EXTENSIONS`
* `is_probably_text()` heuristic
* explicit unsupported binary rejection (example: `.xls`)
* any newly created Python file(s) in the python tree section

Suggested places to update:

* Parser Routing stage
* Suggested Python package tree
* Recommended implementation candidates or notes
* Implementation order section with a short Phase 7 addition

Do not over-explain. Keep it readable as a reference artifact.

### Tests to Add

| Test file                                      | What it tests                                                                                                                                   |
| ---------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `tests/test_parser_router.py`                  | Known supported types route to the expected extractor. `.xls` is rejected cleanly. Unknown but text-like suffixes can route to `TextExtractor`. |
| `tests/test_content_classification_service.py` | `is_probably_text()` returns `True` for normal text samples and `False` for null-byte-heavy binary samples.                                     |
| `tests/test_text_like_aliases.py`              | Files such as `.bat`, `.jsx`, or suffixless text files are accepted when content is text-like.                                                  |
| `tests/test_unsupported_binary_rejection.py`   | Binary content mislabeled with a text-like extension does not route to `TextExtractor` and returns a structured error.                          |

### Out of Scope

* Support for `.xls`
* Tika fallback
* OCR
* NER or downstream enrichment
* MIME-detection overhaul
* Changes to `CanonicalDocument`
* Changes to embeddings, topic modeling, or RAG
* Large extension registries
* Additional extractors beyond what already exists
* Any frontend/UI changes beyond updating `ingest_architecture_map.html`

### Definition of Done

* A real plain-text file mislabeled as `.bat`, `.jsx`, or another text-like alias is routed to `TextExtractor` and ingests successfully.
* A suffixless or unknown-extension plain-text file is routed to `TextExtractor` if `is_probably_text()` returns `True`.
* A binary file such as `.xls` is rejected cleanly and does not produce junk `cleanText`.
* Binary content mislabeled with a text-like suffix is not routed to `TextExtractor`.
* Existing supported formats (`txt`, `md`, `pdf`, `docx`, `csv`, `xlsx`) continue to work.
* `pytest` passes.
* `ingest_architecture_map.html` is updated with concise Phase 7 parser-routing notes and any new file(s) in the python tree.

### Stop and Review

* Confirm `ParserRouter` now uses content heuristics in addition to suffixes.
* Confirm `TextExtractor` never receives likely-binary content.
* Confirm `.xls` rejection is explicit and readable.
* Confirm `CanonicalDocument` shape is unchanged.
* Confirm no new heavy dependencies were added.
* Confirm `ingest_architecture_map.html` was updated only where relevant and remains readable.

