# E2E Test Script Instructions

Run the end-to-end test script to validate core VaultBubbles behavior before merges.

## Prerequisites

- API running (local or remote)
- `DATABASE_URL` set (for snapshot and verification code insert)
- `psycopg2-binary` installed (in `app/requirements.txt`)

## Local (Docker)

```powershell
# PowerShell (Windows)
$env:API_BASE = "http://localhost:8000"
$env:DATABASE_URL = "postgresql+psycopg2://badger:badgerpass@localhost:5433/badgerdb"
python tests/run_e2e_tests.py
```

```bash
# Linux / macOS
export API_BASE=http://localhost:8000
export DATABASE_URL=postgresql://badger:badgerpass@localhost:5433/badgerdb
python tests/run_e2e_tests.py
```

**Note:** Use port **5433** when connecting from the host (Docker maps `5433:5432`). Use `localhost` (not `db`) since `db` only resolves inside the Docker network.

## Remote (e.g. Fly.io)

**Important:** Use `https://` for API_BASE (not `http://`) to avoid redirect issues.

```powershell
# PowerShell (Windows)
$env:API_BASE = "https://vaultbubbles.fly.dev"
$env:DATABASE_URL = "postgresql://..."   # Fly Postgres connection URL
python tests/run_e2e_tests.py
```
# make sure you use "https", not "http" or re-directs will break the test
```bash
# Linux / macOS
export API_BASE=https://vaultbubbles.fly.dev
export DATABASE_URL=postgresql://...
python tests/run_e2e_tests.py
```

## What It Tests

- Extension login flow (DB-inserted verification code)
- Auth, vaults, ingest, document CRUD, topics, query
- Pre/post DB snapshots validate no existing user data is modified or deleted

## Optional

- `TEST_USER_EMAIL` — Override the test user email (default: `e2e-test-{timestamp}@example.com`)
- `E2E_INGEST_WAIT_SECONDS` — Seconds to wait for async ingest (default: 60). Increase for slow remote deployments (e.g. `90` or `120`).
