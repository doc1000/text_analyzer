You are helping me create an integration plan inside the VaultBubbles repository.

Your task is to generate the file:
development_process/ingestion/implementation_plans/
    INGESTION_INTEGRATION_PLAN.md

Do NOT implement code.
Do NOT modify existing source files.
Do NOT create endpoints yet.
Only produce a phased integration plan.

Before generating the plan, read the following repository guidance :
development_process/
    - CURSOR_CONSTITUTION.md
development_process/ingestion/system_rules:
    - ARCHITECTURE_RULES.md
    - AI_CONTEXT.md
    - SERVICE_BOUNDARIES

These files define development discipline, architectural boundaries, and system context.

Your plan must follow these rules.
Do not contradict them.
If the integration plan would violate them, choose the safer architecture.

This integration must preserve the separation of concerns between
VaultBubbles (persistence + async processing)
and vbub-doc-ingestion (document parsing).

Context:
- VaultBubbles is an existing FastAPI application with a large, somewhat stringy main.py and many existing concerns: auth, notes, webpage capture, async ingest queue, chunking, embeddings, topic modeling, retrieval, clustering, and UI support.
- I have already built a separate local ingestion service in another repo. That service is responsible only for document parsing and normalization and returns a CanonicalDocument payload.
- I want VaultBubbles to integrate with that ingestion service without duplicating parsing logic.
- I want to do local integration first, pointing VaultBubbles at the local ingestion service.
- After local integration works, I will deploy the ingestion service to Fly and point the online VaultBubbles deployment to the remote ingestion URL.
- The frontend already has an “Upload Files” button, but it is currently non-functional.
- There is no existing file-upload endpoint in VaultBubbles.
- There is an existing POST /ingest endpoint, but it is used by the browser extension to submit webpage HTML or a note payload, not uploaded files.
- I prefer adding a separate endpoint for file uploads, likely POST /ingest/file.
- File upload should ultimately enter the same downstream processing path as current ingestion.

Important existing backend facts:
- Current POST /ingest accepts an IngestPayload and immediately inserts a row into ingest_queue, then triggers async background processing via process_ingest_item(). It does not directly insert into documents from the request handler.
- This means file upload should likely follow the same pattern:
  1. VaultBubbles receives upload
  2. VaultBubbles calls doc-ingestion service
  3. doc-ingestion returns CanonicalDocument
  4. VaultBubbles maps CanonicalDocument into an internal ingestion payload
  5. VaultBubbles inserts into ingest_queue
  6. Existing async worker continues normal downstream processing
- Do not bypass ingest_queue unless there is a very strong reason.
- The goal is to reuse the current async chunking/embedding/topic pipeline with minimal changes.

Current relevant project information:
- FastAPI backend
- Auth already exists
- Existing /ingest endpoint is extension-oriented, not file-upload oriented
- documents table includes fields such as:
  - id
  - url
  - title
  - captured_text
  - extracted_text
  - full_text
  - captured_at
  - vault_id
  - created_by
- ingest_queue exists and stores:
  - payload_json
  - status
  - user_id
  - vault_id
  - document_id
  - created_at
  - processing_started_at
  - error_message
- process_ingest_item() is already part of the system and should be reused if possible
- Frontend upload should go to VaultBubbles backend, not directly to the ingestion service
- VaultBubbles should own persistence and async processing
- doc-ingestion should remain parsing-only

Local development assumptions:
- VaultBubbles local: http://127.0.0.1:8000
- VaultBubbles-test local: http://127.0.0.1:8081
- JupyterLab local: http://127.0.0.1:8888
- doc-ingestion local should move to http://127.0.0.1:8001
- Use a configurable setting such as INGESTION_SERVICE_URL for the VaultBubbles backend

Architectural goals:
- integrate the new ingestion service with minimal disruption
- do not refactor the whole backend in this step
- use a thin adapter/client pattern
- keep the browser talking only to VaultBubbles
- keep the backend responsible for calling doc-ingestion
- keep downstream persistence and async processing in VaultBubbles
- keep document parsing and normalization in doc-ingestion
- create a clean boundary that can be improved later
- minimize risk to existing extension ingestion flows

Strong recommendation to preserve in the plan:
- Add a new endpoint in VaultBubbles for file upload rather than rewriting the existing extension /ingest path immediately
- The new endpoint should be thin and orchestration-driven
- A small ingestion client module/service should call the external doc-ingestion API
- The integration should be done locally first, then made deployable via config only
- Avoid changing topic modeling, embeddings, retrieval, clustering, or unrelated endpoints during this integration

Planning constraints:
- Do not propose a full backend rewrite
- Do not propose direct browser-to-doc-ingestion calls
- Do not introduce new queues, job systems, or storage systems
- Do not move file parsing into VaultBubbles
- Do not expand scope into OCR, NER, or topic modeling refactors
- Do not redesign the entire auth system
- Do not change the CanonicalDocument contract
- Do not assume doc-ingestion stores anything permanently
- Do not bypass VaultBubbles permission and vault ownership logic
Do not refactor, reorganize, or relocate existing logic in main.py; only add the minimal new endpoint wiring required for file upload integration.

Your job:
Create a phased implementation plan for integrating the document ingestion service into VaultBubbles.

The plan should likely include:
- current-flow inventory
- config for local ingestion service URL
- creation of a small ingestion client module
- a new VaultBubbles backend endpoint for file uploads
- mapping CanonicalDocument into the internal async ingest path
- frontend button wiring
- local end-to-end testing
- deployment config switch to remote ingestion URL later

For each phase, include:
- Goal
- Inputs
- Files to Create
- Files to Modify
- Tests to Add
- Out of Scope
- Definition of Done
- Stop and Review

The plan should be conservative and phase-based.
Each phase should be a thin vertical slice.
The plan should make it easy for Sonnet to implement one phase at a time.

Important:
- Explicitly identify the likely integration boundary between VaultBubbles and the doc-ingestion service.
- Explicitly recommend where CanonicalDocument should be translated into VaultBubbles’ internal ingestion payload.
- Explicitly preserve reuse of ingest_queue and process_ingest_item() unless the current code structure clearly prevents that.
- Explicitly call out migration risk if the existing /ingest extension flow is touched.
- Explicitly prefer a new endpoint path for file uploads.

Use the repository’s existing files and architecture where possible.
Prefer minimal, surgical changes.

Output requirements:
- Produce the full contents of INGESTION_INTEGRATION_PLAN.md
- Use clean Markdown
- Make it implementation-ready
- Do not write code
- Do not include commentary outside the plan


Planning Discipline Rules

For every phase include a PHASE CONTRACT section that lists:

- Files to Create
- Files to Modify
- Functions Added or Changed
- Data Structures Affected
- Endpoints Added or Changed
- Tests Added

Each phase must be independently implementable.

No phase may modify more than 5 files unless absolutely required.

The goal is to produce phases that Sonnet can implement sequentially without ambiguity.

