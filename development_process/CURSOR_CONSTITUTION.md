# Cursor AI Coding Constitution

1. Build only the phase requested.
2. Do not implement future phases preemptively.
3. Do not add new dependencies unless clearly justified.
4. Keep FastAPI routes thin.
5. Keep business logic outside route handlers.
6. Preserve the API / orchestration / services / extractors / storage split.
7. Do not change the canonical payload shape without explicitly calling it out.
8. Prefer explicit typed code over clever abstractions.
9. Keep files single-purpose and easy to review.
10. Add docstrings to modules and public classes/functions.
11. Add tests only for the current phase.
12. Do not introduce OCR, NER, LangChain, Tika, pandas, queues, or auth complexity unless explicitly requested.
13. When uncertain, choose the simpler design.
14. After each implementation step, summarize:
    - files created or modified
    - contracts added or changed
    - what remains stubbed
    - what should be reviewed before continuing
15. Do not silently refactor unrelated parts of the repo.
16. If a requested change seems to expand scope, stop and say so explicitly.
