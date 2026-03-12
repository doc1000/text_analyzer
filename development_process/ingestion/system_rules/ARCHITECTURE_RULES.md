# Architecture Rules — Ingestion Service

## Purpose
This repo implements a small, auditable document-ingestion service.

Its job is to:
- accept one uploaded file
- validate and preserve the original binary
- detect type and choose one parser
- extract and normalize text once
- emit a single canonical payload for downstream systems

Its job is not to:
- perform semantic enrichment
- run topic modeling
- perform NER
- do OCR
- manage editing workflows
- become a general workflow engine

---

## Architectural Principles

1. **Normalize once, consume everywhere**
   - Validation, type detection, parsing, and cleanup happen once.
   - Downstream consumers should not repeat type checks.

2. **Thin API layer**
   - FastAPI routes should be thin.
   - Route handlers should validate requests and call orchestration only.

3. **Business logic is framework-independent**
   - Core logic must not depend on FastAPI request objects.
   - Core logic should be callable from HTTP, tests, or future direct module import.

4. **Parser chosen once**
   - `ParserRouter` is the only component that selects the parser.
   - Downstream services should not branch on file type.

5. **Single ingestion contract**
   - `CanonicalDocument` is the only downstream payload.
   - Changes to the payload schema should be explicit and deliberate.

6. **Original binary preserved**
   - The original uploaded file is stored immutably.
   - Extracted text and metadata are derived artifacts, not replacements.

7. **User intent preserved**
   - User-supplied tags and source context enter at ingestion time.
   - They are validated and included in canonical metadata.

8. **Small, explicit modules**
   - Each file should have one primary responsibility.
   - Prefer explicit flow over hidden framework magic.

9. **Light dependency philosophy**
   - Prefer stdlib or lightweight libraries first.
   - Do not add heavy frameworks without a clear reason.

10. **Test behavior, not implementation trivia**
    - Tests should focus on request flow, contracts, extraction results, and cleanup outcomes.

---

## Repo Boundaries

This repo owns:
- upload intake
- validation
- binary storage
- parser routing
- extraction
- cleanup/normalization
- canonical document construction

This repo does not own:
- note-taking UI
- embedding generation
- vector indexing
- topic modeling
- semantic enrichment
- user-facing document editing

---

## Current v1 Scope

Supported formats for v1:
- txt
- md
- pdf
- docx
- csv
- xlsx

Out of scope for v1:
- OCR
- Tika fallback
- HTML ingestion
- PPTX/ODT
- background queues
- advanced auth
- enrichment pipelines

---

## Preferred Libraries

- FastAPI + Pydantic
- PyMuPDF for PDF
- python-docx for DOCX
- stdlib `csv`
- openpyxl for XLSX
- python-magic for MIME detection

Avoid in the ingestion critical path unless explicitly needed:
- pandas
- LangChain
- Tika
- OCR frameworks

---

## File Structure Rules

- `api/` contains HTTP transport only
- `orchestration/` coordinates ingestion flow
- `services/` contains validation, routing, cleanup, and assembly logic
- `extractors/` contains format-specific extraction
- `storage/` contains binary and metadata persistence helpers
- `domain/` contains contracts, models, enums

---

## Documentation Rules

Each module should include:
- a top-of-file docstring explaining responsibility
- concise docstrings for public classes/functions
- comments for non-obvious decisions only

Each major folder may include a `README.md` describing:
- what belongs there
- call order
- important constraints

---

## Change Rules

Before adding a new feature, ask:
1. Is this part of ingestion, or downstream enrichment?
2. Does this expand v1 scope unnecessarily?
3. Can this be added later without breaking the contract?
4. Does this require a new dependency?
5. Does this belong in a new file, or in an existing module?

If the answer is “later,” defer it.

---

## Deployment Direction

- Local development first
- FastAPI service
- Same Fly app in production
- Separate process group for ingestion
- Clean internal API boundary from backend to ingestion service
