"""Capture-to-view latency: Postgres INSERT -> item visible in the viewer's /api/tree.

Creates a throwaway vault (owned by --owner, default test@example.com) seeded with copies of that
user's documents, runs a separate `vb-orchard sync` + `serve` on a scratch orchard-db and port,
inserts --n new documents one at a time, and times each until /api/tree counts it. Removes the
vault and its documents afterwards. Never touches other vaults or the real orchard-db.

    uv run python scripts/latency.py --n 5
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

import psycopg

from vb_orchard.config import Settings


def tree_count(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/tree?days=all", timeout=5) as r:
            return json.loads(r.read()).get("item_count")
    except Exception:  # noqa: BLE001
        return None


def wait_count(port, n, timeout):
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        if tree_count(port) == n:
            return time.perf_counter() - t0
        time.sleep(0.05)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--seed", type=int, default=15)
    ap.add_argument("--owner", default="test@example.com")
    ap.add_argument("--port", type=int, default=8799)
    a = ap.parse_args()
    url = Settings.load().require_database()
    work = Path(tempfile.mkdtemp(prefix="vb-latency-"))
    vault = str(uuid.uuid4())
    env = dict(os.environ, VB_VAULT_IDS=vault, ORCHARD_DB_PATH=str(work / "lat.duckdb"),
               VB_ORCHARD_PORT=str(a.port), VB_ORCHARD_RECUT_AFTER="0", VB_ORCHARD_POLL_S="60")
    procs = []
    with psycopg.connect(url, autocommit=True) as con:
        uid = con.execute("SELECT id FROM users WHERE email=%s", [a.owner]).fetchone()[0]
        con.execute("INSERT INTO vaults (id,name,owner_id,is_personal,created_at) "
                    "VALUES (%s,'vb-orchard latency test',%s,false,now() at time zone 'utc')", [vault, uid])
        src = con.execute("SELECT d.url,d.title,d.full_text FROM documents d JOIN vaults v ON v.id=d.vault_id "
                          "WHERE v.owner_id=%s AND length(d.full_text)>500 ORDER BY d.captured_at LIMIT %s",
                          [uid, a.seed + a.n]).fetchall()
        assert len(src) >= a.seed + a.n, f"need {a.seed + a.n} source docs, have {len(src)}"
        ins = ("INSERT INTO documents (id,url,title,full_text,captured_at,vault_id) "
               "VALUES (gen_random_uuid(),%s,%s,%s,now(),%s)")
        try:
            for u, t, f in src[:a.seed]:
                con.execute(ins, [u + "#lat", t, f, vault])
            cmd = [sys.executable, "-m", "vb_orchard.cli"]
            procs.append(subprocess.Popen(cmd + ["sync"], env=env, stdout=open(work / "sync.log", "w"),
                                          stderr=subprocess.STDOUT))
            procs.append(subprocess.Popen(cmd + ["serve"], env=env, stdout=open(work / "serve.log", "w"),
                                          stderr=subprocess.STDOUT))
            boot = wait_count(a.port, a.seed, 180)
            print(f"startup (model load + first build of {a.seed}): {boot:.1f} s" if boot else "startup timed out")
            time.sleep(2)
            lat = []
            for i, (u, t, f) in enumerate(src[a.seed:]):
                t0 = time.perf_counter()
                con.execute(ins, [u + "#lat-new", t, f, vault])
                got = wait_count(a.port, a.seed + i + 1, 60)
                lat.append(got)
                print(f"insert {i + 1}: {got:.2f} s" if got else f"insert {i + 1}: not visible after 60 s")
                time.sleep(1)
            ok = [x for x in lat if x]
            if ok:
                print(f"capture-to-view: median {statistics.median(ok):.2f} s, max {max(ok):.2f} s, "
                      f"n={len(ok)}/{len(lat)}")
        finally:
            for p in procs:
                p.terminate()
            for p in procs:
                p.wait(timeout=30)
            con.execute("DELETE FROM documents WHERE vault_id=%s", [vault])
            con.execute("DELETE FROM vaults WHERE id=%s", [vault])
            print("removed test vault and its documents; logs in", work)


if __name__ == "__main__":
    main()
