"""Background worker process for Vault Bubbles (batch33): the heavy jobs, out of the web process.

    python -m app.worker        (services/vb-app/run.ps1 -Worker; task "vaultbubbles worker")

Runs what the web app runs in its own process when VB_BACKGROUND is not "worker":
  - the ingest queue (extension captures): polled every VB_INGEST_POLL_SECONDS (default 5)
  - scheduled deduplication (when enabled in preferences)
  - the vault folder and todo.txt sources (VB_VAULT_SOURCE / VB_TODO_SOURCE)
The web app started with VB_BACKGROUND=worker only queues captures; embedding a capture or a
changed vault file then never competes with page requests in the same process.
One worker at a time: the ingest loop holds a Postgres advisory lock.
"""
from __future__ import annotations

import logging
import os
import time

LOCK_KEY = 0x76627771  # "vbwq": pg advisory lock id for the ingest loop


def _lock():
    """Hold the worker lock on a dedicated connection (returned so it stays open)."""
    import psycopg2

    dsn = os.environ.get("DATABASE_URL", "").replace("postgresql+psycopg2://", "postgresql://")
    while True:
        try:
            con = psycopg2.connect(dsn)
            con.autocommit = True
            cur = con.cursor()
            cur.execute("SELECT pg_try_advisory_lock(%s)", [LOCK_KEY])
            if cur.fetchone()[0]:
                return con
            con.close()
            print("[worker] another worker holds the lock; waiting", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[worker] lock connection failed ({exc}); retrying", flush=True)
        time.sleep(30)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    from .config import PREFERENCES
    from .ingest_worker import process_pending_ingest_queue
    from .scheduler import init_scheduler
    from .vault_source import start_background

    lock_con = _lock()
    if PREFERENCES.models.embedding_provider == "local":
        from .helpers import warm_local_encoder
        warm_local_encoder()
    init_scheduler(schedule_times=PREFERENCES.deduplication.schedule_times, ingest=False)
    start_background()
    poll = max(1.0, float(os.environ.get("VB_INGEST_POLL_SECONDS", "5")))
    print(f"[worker] up: ingest queue every {poll:.0f} s", flush=True)
    while True:
        try:
            stats = process_pending_ingest_queue()
            if stats.get("processed") or stats.get("reset_stuck") or stats.get("errors"):
                print(f"[worker] ingest queue: {stats}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[worker] ingest queue failed: {type(exc).__name__}: {exc}", flush=True)
        if lock_con.closed:
            lock_con = _lock()
        time.sleep(poll)


if __name__ == "__main__":
    main()
