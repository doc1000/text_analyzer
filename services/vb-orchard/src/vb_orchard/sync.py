"""Postgres -> orchard-db sync (the only writer of the orchard-db file).

Each pass compares Postgres document fingerprints (id + md5 of url, title, text, captured_at) with
the fingerprints recorded in orchard-db (table ``vb_sync.items``):

- new or changed documents  -> ``insert_items`` (embed + place in the current tree, no re-cut)
- deleted documents, a missing file or alias, or ``recut_after`` incremental inserts
                            -> full rebuild into a temp file, swapped in place

Wake-ups: a Postgres ``LISTEN`` on the ``vb_documents`` channel (if the NOTIFY trigger is
installed) and a fallback poll every ``poll_seconds``. The encoder and imports stay loaded.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import engine, pgsource
from .lock import WriterLock

log = logging.getLogger("vb_orchard.sync")

STATE_DDL = ("CREATE SCHEMA IF NOT EXISTS vb_sync; "
             "CREATE TABLE IF NOT EXISTS vb_sync.items (item_id VARCHAR PRIMARY KEY, fingerprint VARCHAR NOT NULL); "
             "CREATE TABLE IF NOT EXISTS vb_sync.meta (key VARCHAR PRIMARY KEY, value VARCHAR)")


def get_encoder(kind: str = "minilm"):
    if kind == "fake":
        from .fake_encoder import FakeEncoder

        return FakeEncoder()
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    from orchard.backends.minilm import MiniLMEmbeddingBackend

    enc = MiniLMEmbeddingBackend(local_files_only=True)
    enc.encode(["warm up"])  # load the model now, not on the first capture
    return enc


# ---------------------------------------------------------------- orchard-db side state
def _rw(path):
    from treedb import store as tstore

    return tstore.connect_with_retry(str(path), read_only=False, timeout_s=30)


def read_state(path) -> dict:
    if not Path(path).exists():
        return {}
    from treedb import store as tstore

    con = tstore.connect_with_retry(str(path), read_only=True, timeout_s=30)
    try:
        has = con.execute("SELECT count(*) FROM information_schema.tables "
                          "WHERE table_schema = 'vb_sync' AND table_name = 'items'").fetchone()[0]
        if not has:
            return {}
        return dict(con.execute("SELECT item_id, fingerprint FROM vb_sync.items").fetchall())
    finally:
        con.close()


def write_state(path, upserts: dict, *, replace: bool = False, meta: Optional[dict] = None) -> None:
    con = _rw(path)
    try:
        con.execute(STATE_DDL)
        con.execute("BEGIN")
        if replace:
            con.execute("DELETE FROM vb_sync.items")
        if upserts:
            con.executemany("INSERT OR REPLACE INTO vb_sync.items VALUES (?, ?)", list(upserts.items()))
        for k, v in (meta or {}).items():
            con.execute("INSERT OR REPLACE INTO vb_sync.meta VALUES (?, ?)", [k, str(v)])
        con.execute("COMMIT")
    finally:
        con.close()


def alias_exists(path, alias) -> bool:
    if not Path(path).exists():
        return False
    from treedb import registry
    from treedb import store as tstore

    con = tstore.connect_with_retry(str(path), read_only=True, timeout_s=30)
    try:
        registry.resolve(con, alias)
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        con.close()


def _swap_in(tmp: Path, dst: Path, timeout_s: float = 30.0) -> None:
    """Replace dst by tmp. Readers open the file per request, so retry while one has it open."""
    wal = Path(str(dst) + ".wal")
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            if wal.exists():
                wal.unlink()
            os.replace(tmp, dst)
            return
        except PermissionError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.2)


# ---------------------------------------------------------------- one pass
@dataclass
class PassResult:
    action: str = "none"  # none | insert | rebuild | empty
    inserted: int = 0
    changed: int = 0
    removed: int = 0
    total: int = 0
    seconds: float = 0.0
    reason: str = ""
    attach_failed: list = field(default_factory=list)

    def line(self) -> str:
        return (f"{self.action}: total {self.total}, new {self.inserted}, changed {self.changed}, "
                f"removed {self.removed}, {self.seconds:.2f} s" + (f" ({self.reason})" if self.reason else ""))


class Syncer:
    """Keeps ONE vault's orchard-db file in step with Postgres (one tree per vault)."""

    def __init__(self, settings, vault, *, encoder=None):
        self.s = settings
        self.vault = vault if isinstance(vault, dict) else {"id": str(vault), "kind": None, "name": None}
        self.db = Path(settings.db_for(self.vault["id"]))
        self.encoder = encoder
        self.since_recut = 0

    def _meta(self, **extra) -> dict:
        v = self.vault
        return {"vault_id": v["id"], "vault_kind": v.get("kind") or "", "vault_name": v.get("name") or "",
                "vaults": v["id"], **extra}

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def rebuild(self, pg, fps: dict, reason: str) -> PassResult:
        t0 = time.perf_counter()
        docs = pgsource.fetch_docs(pg, list(fps))
        if not docs:
            return PassResult(action="empty", total=0, reason="no documents in this vault")
        self.db.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.db.with_name(self.db.stem + ".rebuild" + self.db.suffix)
        for p in (tmp, Path(str(tmp) + ".wal")):
            if p.exists():
                p.unlink()
        engine.rebuild(tmp, docs, alias=self.s.alias, encoder=self.encoder)
        write_state(tmp, {"vb:" + i: f for i, f in fps.items()}, replace=True,
                    meta=self._meta(last_rebuild=self._now()))
        _swap_in(tmp, self.db)
        self.since_recut = 0
        return PassResult(action="rebuild", total=len(docs), seconds=time.perf_counter() - t0, reason=reason)

    def sync_once(self, pg) -> PassResult:
        t0 = time.perf_counter()
        fps = pgsource.fingerprints(pg, [self.vault["id"]])
        if not self.db.exists() or not alias_exists(self.db, self.s.alias):
            return self.rebuild(pg, fps, "no orchard-db yet")
        known = read_state(self.db)
        if not known:
            return self.rebuild(pg, fps, "no sync state")
        new = [i for i in fps if "vb:" + i not in known]
        changed = [i for i in fps if "vb:" + i in known and known["vb:" + i] != fps[i]]
        removed = [k for k in known if k[3:] not in fps]
        if removed:
            r = self.rebuild(pg, fps, f"{len(removed)} removed")
            r.removed = len(removed)
            return r
        todo = new + changed
        if not todo:
            return PassResult(total=len(fps), seconds=time.perf_counter() - t0)
        docs = pgsource.fetch_docs(pg, todo)
        # an edit (e.g. tags) keeps captured_at; updated_at = now tells readers the item changed
        now = self._now()
        changed_ids = {"vb:" + i for i in changed}
        for d in docs:
            if d["item_id"] in changed_ids:
                d["metadata"]["updated_at"] = now
        res = engine.ingest_new(self.db, docs, alias=self.s.alias, encoder=self.encoder)
        write_state(self.db, {"vb:" + i: fps[i] for i in todo}, meta=self._meta(last_insert=now))
        self.since_recut += len(new)
        return PassResult(action="insert", inserted=len(new), changed=len(changed), total=len(fps),
                          seconds=time.perf_counter() - t0,
                          attach_failed=[k for k, v in (getattr(res, "attach", None) or {}).items() if v is not None])

    def maybe_recut(self, pg) -> Optional[PassResult]:
        if self.s.recut_after and self.since_recut >= self.s.recut_after:
            return self.rebuild(pg, pgsource.fingerprints(pg, [self.vault["id"]]),
                                f"re-cut after {self.since_recut} inserts")
        return None


class MultiSyncer:
    """One Syncer per vault the settings select. A vault that appears later (a new collection) gets
    one on the next pass; the file of a vault that is gone (a deleted collection) is removed."""

    def __init__(self, settings, *, encoder=None):
        self.s = settings
        self.encoder = encoder
        self.syncers: dict = {}

    def _refresh(self, pg) -> list:
        vaults = pgsource.resolve_vaults(pg, self.s)
        ids = {v["id"] for v in vaults}
        for v in vaults:
            sy = self.syncers.get(v["id"])
            if sy is None:
                self.syncers[v["id"]] = Syncer(self.s, v, encoder=self.encoder)
            else:
                sy.vault = v  # kind or name may have changed
        for vid in [k for k in self.syncers if k not in ids]:
            self._forget(self.syncers.pop(vid))
        return vaults

    @staticmethod
    def _forget(sy) -> None:
        for p in (sy.db, Path(str(sy.db) + ".wal")):
            try:
                if p.exists():
                    p.unlink()
                    log.info("removed %s (vault %s is gone)", p.name, sy.vault["id"])
            except OSError as exc:  # a reader has it open; the next pass retries
                log.warning("could not remove %s: %s", p, exc)

    def sync_all(self, pg) -> list:
        out = []
        for v in self._refresh(pg):
            out.append((v, self.syncers[v["id"]].sync_once(pg)))
        return out

    def maybe_recut_all(self, pg) -> list:
        out = []
        for sy in list(self.syncers.values()):
            r = sy.maybe_recut(pg)
            if r:
                out.append((sy.vault, r))
        return out


def _label(v) -> str:
    return f"{v.get('kind') or 'vault'} {v.get('name') or ''} {v['id'][:8]}".strip()


# ---------------------------------------------------------------- service loop
def _wait_for_notify(listen_con, timeout: float) -> bool:
    """True if a NOTIFY arrived within timeout; then drains a short burst (debounce)."""
    got = False
    for _ in listen_con.notifies(timeout=timeout, stop_after=1):
        got = True
    if got:
        for _ in listen_con.notifies(timeout=0.3):
            pass
    return got


def run(settings, *, once: bool = False, encoder=None, on_pass=None) -> None:
    url = settings.require_database()
    with WriterLock(settings.lock_path):
        log.info("writer lock held on %s (one file per vault in %s)", settings.lock_path, settings.db_dir)
        enc = encoder if encoder is not None else get_encoder(settings.encoder)
        multi = MultiSyncer(settings, encoder=enc)
        backoff = 1.0
        while True:
            try:
                with pgsource.connect(url) as pg, pgsource.connect(url) as listen:
                    fast = pgsource.trigger_installed(pg)
                    if fast:
                        listen.execute(f"LISTEN {pgsource.CHANNEL}")
                    log.info("connected; %s, fallback poll %.0f s",
                             "LISTEN on vb_documents" if fast else "no NOTIFY trigger, polling only",
                             settings.poll_seconds)
                    backoff = 1.0
                    while True:
                        for v, r in multi.sync_all(pg):
                            if r.action != "none":
                                log.info("%s: %s", _label(v), r.line())
                            if on_pass:
                                on_pass(r)
                        if once:
                            return
                        if fast:
                            woke = _wait_for_notify(listen, settings.poll_seconds)
                        else:
                            woke = False
                            time.sleep(settings.poll_seconds)
                        if not woke:  # idle: a good moment for a scheduled re-cut
                            for v, rc in multi.maybe_recut_all(pg):
                                log.info("%s: %s", _label(v), rc.line())
            except KeyboardInterrupt:
                return
            except Exception as exc:  # noqa: BLE001 - Postgres restart, network blip, busy file
                if once:
                    raise
                log.warning("sync error: %s: %s; retrying in %.0f s", type(exc).__name__, exc, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
