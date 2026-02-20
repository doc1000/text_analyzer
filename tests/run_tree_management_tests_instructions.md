# Tree Management Test Script Instructions

Run the tree management test script to validate the tree infrastructure (tree_nodes, node_documents) without touching endpoints or existing topic flow.

## Prerequisites

- Migration 007 applied (`migrations/007_tree_infrastructure.sql`)
- Local DB with user `test@example.com` and documents that have embeddings (chunk or doc-level)
- `DATABASE_URL` set
- `HUGGINGFACE_API_TOKEN` (or your embedding provider) in `.env` or environment; the script loads `.env` automatically

## Local (Docker)

```powershell
# PowerShell (Windows)
$env:DATABASE_URL = "postgresql+psycopg2://badger:badgerpass@localhost:5433/badgerdb"
python tests/run_tree_management_tests.py
```

```bash
# Linux / macOS
export DATABASE_URL=postgresql+psycopg2://badger:badgerpass@localhost:5433/badgerdb
python tests/run_tree_management_tests.py
```

**Note:** Use port **5433** when connecting from the host (Docker maps `5433:5432`).

## What It Tests

1. **Phase 1:** Resolve test@example.com, vault, documents with embeddings
2. **Phase 2:** Populate reduced embeddings, cluster, build tree_nodes and node_documents
3. **Phase 3:** Create 2-3 derived documents (copy + paragraph), embed via existing process, assign to tree
4. **Phase 4:** Delete derived docs, verify clean removal (docs gone, node_documents CASCADE)
5. **Phase 5:** Date-filtered tree retrieval (last 15 days)
6. **Phase 6:** Teardown removes the tree so DB is restored (no tree left after test)

## Optional

- `TEST_USER_EMAIL` — Override the test user (default: `test@example.com`)
