# vb-orchard

Vault Bubbles x Data Orchard. Postgres (badgerdb) stays the source of truth; an orchard-db
DuckDB file is derived from it by a sync service, the only writer of that file. A viewer, a
VB-shaped `/do` API and an MCP server read the file. Plan and reasoning:
`DATAORCHARD_SYNC_PLAN.md` in the workspace root (batch22).

This package used to be `app/dataorchard` in the VB app. It is now its own installable package;
the VB Docker image does not include it.

## Install and run

```bash
uv tool install --force services/vb-orchard     # puts vb-orchard on PATH (~/.local/bin)
vb-orchard status
vb-orchard install-trigger                       # once: NOTIFY trigger on documents (fast path)
vb-orchard sync                                  # service; Ctrl+C to stop
vb-orchard serve                                 # http://127.0.0.1:8766/
vb-orchard mcp                                   # stdio MCP server (find / recent / summarise)
```

Start at sign-in (Windows): `powershell -File services/vb-orchard/scripts/install-windows-tasks.ps1`
(`-Remove` to undo). Logs: `~/.vb-orchard/logs/`.

Dependencies orchard, treedb and orchard-harvest are pinned git dependencies on the private GitHub
repos (`[tool.uv.sources]`); torch comes from the CPU-only index everywhere.

## Settings

Environment variables, seeded from `~/.vb-orchard/config.env` (or `$VB_ORCHARD_CONFIG`); never
commit that file. See `src/vb_orchard/config.py` for the full list.

| variable | meaning |
|---|---|
| `VB_DATABASE_URL` | Postgres URL (sync opens it read-only) |
| `VB_OWNER_EMAIL` / `VB_VAULT_ROLES` | sync the vaults this user holds with these roles (default `owner`) |
| `VB_VAULT_IDS` | explicit vault ids instead |
| `ORCHARD_DB_DIR` | folder of orchard-db files, one `<vault_id>.duckdb` per vault (default `~/.vb-orchard`) |
| `VB_ORCHARD_POLL_S` | fallback poll (default 60 s; NOTIFY wakes it at once) |
| `VB_ORCHARD_RECUT_AFTER` | full re-cut after N incremental inserts, when idle (default 25) |
| `VB_ORCHARD_PORT` | viewer / API port (default 8766) |

## How a sync pass works

One sync process keeps one orchard-db file per selected vault (the personal vault and each
collection); trees are never merged. A vault that disappears (a deleted collection) loses its file.
`serve` and the `/do` routes take `?vault=<id>`; without it they use the personal vault.


Fingerprint every document in the chosen vaults (md5 of url, title, text, captured_at) and compare
with `vb_sync.items` in orchard-db. New or changed documents are embedded and placed in the current
tree without re-cutting (`orchard.cultivate_db.insert_items`). Deleted documents, a missing file
or alias, or N incremental inserts cause a full rebuild into a temp file that is swapped in.
A per-file OS lock (`<db>.writer.lock`) enforces a single writer.

Measured 2026-11-17 on the user's machine (89 real docs): model load about 18 s at service start,
full rebuild 2.7 s with the model warm, capture to visible in the viewer median 1.8 s (max 2.0 s,
5 inserts, `scripts/latency.py`).

## Undo

`install-windows-tasks.ps1 -Remove`, `vb-orchard uninstall-trigger`, `uv tool uninstall vb-orchard`,
delete `~/.vb-orchard/`. The service never writes Postgres data.

## Tests

```bash
cd services/vb-orchard && uv sync --extra dev && uv run pytest -q
```
