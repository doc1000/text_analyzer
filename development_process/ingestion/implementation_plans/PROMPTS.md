
## Implementation Prompts
Follow development_process\ingestion\system_rules\AI_CONTEXT.md.

Implement Phase 1 from development_process\ingestion\INGESTION_INTEGRATION.md only.

Before coding:
- summarize the phase goal
- list the input files you will use
- list what is out of scope

During implementation:
- modify only files listed in the phase
- do not create extra modules unless required

After coding:
- summarize files changed
- confirm definition of done
- confirm out-of-scope items were not touched



## more robust phase prompt:

Follow these repository guidance files before implementing anything:

- development_process\ingestion\system_rules\ARCHITECTURE_RULES.md
- development_process\ingestion\system_rules\CURSOR_CONSTITUTION.md
- development_process\ingestion\system_rules\AI_CONTEXT.md

Implement Phase 1 from:
development_process\ingestion\INGESTION_INTEGRATION.md

Before coding:
- summarize the phase goal
- list the input files you will use
- list what is out of scope

During implementation:
- modify only files listed in the phase
- do not create extra modules unless explicitly required
- do not refactor existing logic
- do not change unrelated endpoints or systems

After coding:
- summarize files changed
- confirm definition of done
- confirm out-of-scope items were not touched



## Phase 2 prompt

Follow these repository guidance files before implementing anything:

- development_process\ingestion\system_rules\ARCHITECTURE_RULES.md
- development_process\ingestion\system_rules\CURSOR_CONSTITUTION.md
- development_process\ingestion\system_rules\AI_CONTEXT.md

Implement Phase 2 from:
development_process\ingestion\INGESTION_INTEGRATION.md

Phase intent reminder:
This phase is only about creating the VaultBubbles-side ingestion client and local configuration needed to call the external doc-ingestion service.
This phase must NOT add the upload endpoint yet unless the phase explicitly says so.
This phase must NOT integrate with ingest_queue yet unless the phase explicitly says so.

Critical architectural boundary:
- VaultBubbles owns persistence, permissions, and async processing.
- vbub-doc-ingestion owns parsing and CanonicalDocument generation.
- Do not move parsing logic into VaultBubbles.

Special implementation guardrails for this phase:
- Create a small, explicit ingestion client module rather than embedding HTTP calls in main.py.
- Do not hardcode localhost URLs inside business logic.
- Read the ingestion service base URL from config/settings only.
- Keep the client synchronous or aligned with the existing backend style unless the phase explicitly requires async HTTP.
- Do not add retries, circuit breakers, task queues, or advanced HTTP abstractions.
- Do not add auth changes unless the phase explicitly requires internal auth headers.
- Do not refactor, reorganize, or relocate existing logic in main.py; only add the minimum wiring required by this phase.
- Do not touch topic modeling, embeddings, clustering, retrieval, or extension ingestion logic.
- Do not create extra modules unless explicitly required by the phase.
- If there is uncertainty about CanonicalDocument typing, create the smallest stable response model or parsing layer necessary for this phase only.

Before coding:
- summarize the phase goal
- list the input files you will use
- list the files you will create
- list the files you will modify
- list what is out of scope
- explain where the ingestion service URL will be configured
- explain where the ingestion client boundary will live

During implementation:
- modify only files listed in the phase
- do not create extra modules unless explicitly required
- keep the ingestion client thin and explicit
- if the phase instructions conflict with existing code, stop and explain the conflict instead of guessing

After coding:
- summarize files changed
- confirm definition of done
- confirm out-of-scope items were not touched
- explain how local VaultBubbles will point to local doc-ingestion
- show the exact config/env variable added for the ingestion service URL
- identify the next integration seam for Phase 3 without implementing it



## Phase 3 prompt
Follow these repository guidance files before implementing anything:

- development_process\ingestion\system_rules\ARCHITECTURE_RULES.md
- development_process\ingestion\system_rules\CURSOR_CONSTITUTION.md
- development_process\ingestion\system_rules\AI_CONTEXT.md

Implement Phase 3 from:
development_process\ingestion\INGESTION_INTEGRATION.md


Critical Phase 3 rules:

- Do not write directly to the documents table from the new file-upload endpoint.
- Do not bypass ingest_queue.
- Do not run chunking, embedding, topic modeling, or retrieval inline in the request handler.
- Reuse the existing async ingest path as early as possible after CanonicalDocument is received.
- Preserve the behavior of the existing POST /ingest endpoint used by the browser extension.
- Translate CanonicalDocument into the existing VaultBubbles ingestion payload in one bounded place only.
- Do not redesign IngestPayload unless absolutely required for compatibility.
- Keep the new /ingest/file endpoint thin and orchestration-driven.
- Preserve user, vault, and permission handling inside VaultBubbles.
- If a placeholder URL or source identifier is needed for uploaded files, use one explicit deterministic convention and call it out clearly.
- Return a queue-oriented response compatible with existing async ingestion expectations unless the phase explicitly requires otherwise.


Before coding:
- summarize the phase goal
- list the input files you will use
- list the files you will create
- list the files you will modify
- list what is out of scope
- explain where the ingestion service URL will be configured
- explain where the ingestion client boundary will live

During implementation:
- modify only files listed in the phase
- do not create extra modules unless explicitly required
- keep the ingestion client thin and explicit
- if the phase instructions conflict with existing code, stop and explain the conflict instead of guessing

After coding:
- summarize files changed
- confirm definition of done
- confirm out-of-scope items were not touched
- explain how local VaultBubbles will point to local doc-ingestion
- show the exact config/env variable added for the ingestion service URL
- identify the next integration seam for Phase 3 without implementing it




## Phase 5 prompt
Follow these repository guidance files before implementing anything:

- development_process\ingestion\system_rules\ARCHITECTURE_RULES.md
- development_process\ingestion\system_rules\CURSOR_CONSTITUTION.md
- development_process\ingestion\system_rules\AI_CONTEXT.md

Implement Phase 5 from:
development_process\ingestion\INGESTION_INTEGRATION.md

Critical Phase 5 rules:

- Do not call the doc-ingestion service directly from the browser.
- The browser must only call VaultBubbles backend.
- Do not redesign or refactor the frontend.
- Only make the existing Upload Files button functional.
- Keep the upload flow limited to the currently supported backend behavior.
- Do not add drag-and-drop, batching, or multi-file workflows unless explicitly required by the phase.
- Reuse existing auth/session/request utilities already present in the frontend.
- Do not modify unrelated UI components, pages, or styles.
- Show only minimal upload state: idle, uploading, success, error.
- Do not change backend response shape from this phase.
- Do not add polling, progress bars, retry managers, or queue dashboards.


Before coding:
- summarize the phase goal
- list the input files you will use
- list the files you will create
- list the files you will modify
- list what is out of scope
- explain where the ingestion service URL will be configured
- explain where the ingestion client boundary will live

During implementation:
- modify only files listed in the phase
- do not create extra modules unless explicitly required
- keep the ingestion client thin and explicit
- if the phase instructions conflict with existing code, stop and explain the conflict instead of guessing

After coding:
- summarize files changed
- confirm definition of done
- confirm out-of-scope items were not touched
- explain how local VaultBubbles will point to local doc-ingestion
- show the exact config/env variable added for the ingestion service URL
- identify the next integration seam for Phase 3 without implementing it
