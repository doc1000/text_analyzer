"""The user's vault folder as a source into the personal vault (batch30).

One-way: files -> VB. VB never writes, moves or deletes anything in the vault folder.

    VB_VAULT_SOURCE=1                  turn the scanner on (off by default; test instances stay off)
    VB_VAULT_TARGET_VAULT=<vault id>   the personal vault the files go into (its owner is the creator)
    VB_VAULT_ROOT=~/Vault              the vault folder; source_ref is a path relative to it
    VB_VAULT_FOLDERS=inbox             comma-separated folders under the root that are watched
    VB_VAULT_SCAN_SECONDS=30           seconds between scans (a scan stats files; it reads only changed ones)

Rules (nothing in VB is erased by the folder):
  - a new file -> a new document (source_kind vault_file), chunk and sentence embeddings
  - a file edited in place (its hash changed) -> the document's title and text are updated and
    re-embedded; tags, captured date and id are kept
  - a file moved or renamed (the same bytes show up at another path, inside the watched folders or,
    by file name, anywhere under the root) -> the document's source_ref and url follow it
  - a file deleted, or moved where it cannot be found -> the document and its text stay, marked
    source_status = missing (a soft broken link); if the file comes back, the link is restored
  - a document the user deletes in VB is remembered in source_tombstones and not re-imported
    (unless the file changes)
  - one scanner at a time across processes (a Postgres advisory lock)
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

def _say(msg: str) -> None:
    """The app log captures stdout (like the other background jobs)."""
    print(f"[vault source] {msg}", flush=True)

TYPES = {".md", ".txt"}
SKIP_DIRS = {".git", ".obsidian", ".trash", ".tmp.driveupload", "node_modules", "__pycache__"}
LOCK_KEY = 0x76627661  # "vbva": pg advisory lock id
KIND = "vault_file"


def settings() -> dict:
    return {
        "enabled": os.environ.get("VB_VAULT_SOURCE", "0").strip().lower() in ("1", "true", "yes", "on"),
        "vault_id": os.environ.get("VB_VAULT_TARGET_VAULT", "").strip(),
        "root": Path(os.environ.get("VB_VAULT_ROOT") or Path.home() / "Vault").expanduser(),
        "folders": [f.strip().strip("/\\") for f in os.environ.get("VB_VAULT_FOLDERS", "inbox").split(",") if f.strip()],
        "interval": max(5.0, float(os.environ.get("VB_VAULT_SCAN_SECONDS", "30"))),
    }


def _walk(base: Path):
    """Files of the supported types under base, skipping hidden/tool folders and Office lock files."""
    if not base.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn.startswith(("~$", ".")) or Path(fn).suffix.lower() not in TYPES:
                continue
            yield Path(dirpath) / fn


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _naive_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


class VaultFolderSource:
    def __init__(self, root: Path, folders: list, vault_id: str):
        self.root = Path(root).resolve()
        self.folders = folders
        self.vault_id = vault_id
        self._stat: dict = {}  # rel -> (mtime_ns, size) as of the last successful scan

    def rel(self, p: Path) -> str:
        return p.resolve().relative_to(self.root).as_posix()

    def watched(self) -> dict:
        out = {}
        for f in self.folders:
            for p in _walk(self.root / f):
                st = p.stat()
                out[self.rel(p)] = (st.st_mtime_ns, st.st_size)
        return out

    def _find_by_name(self, name: str, sha: str):
        """A file that left the watched folders: look for the same bytes by file name under the root."""
        for p in _walk(self.root):
            if p.name == name:
                try:
                    if _sha(p) == sha:
                        return self.rel(p)
                except OSError:
                    continue
        return None

    def scan_once(self, db) -> dict:
        from sqlalchemy import text as sql

        from .collections import read_item
        from .db import EMBED_TABLE
        from .helpers import embed_docs_bulk
        from .models import Document, Vault

        vault = db.query(Vault).filter(Vault.id == self.vault_id).first()
        if vault is None:
            raise RuntimeError(f"VB_VAULT_TARGET_VAULT {self.vault_id} not found")
        from sqlalchemy.orm import defer

        docs = (db.query(Document)
                .options(defer(Document.captured_text), defer(Document.full_text), defer(Document.extracted_text))
                .filter(Document.vault_id == vault.id, Document.source_kind == KIND).all())
        by_ref = {d.source_ref: d for d in docs}
        tomb = {(r, h) for r, h in db.execute(sql(
            "SELECT source_ref, source_hash FROM source_tombstones WHERE vault_id = :v AND source_kind = :k"),
            {"v": vault.id, "k": KIND})}

        current = self.watched()
        # tracked files outside the watched folders (moved there earlier): stat them directly
        for ref, d in by_ref.items():
            if ref not in current and d.source_status != "missing":
                p = self.root / ref
                if p.is_file():
                    st = p.stat()
                    current[ref] = (st.st_mtime_ns, st.st_size)

        counts = {"new": 0, "updated": 0, "moved": 0, "missing": 0, "restored": 0, "skipped": 0}
        reembed, fresh = [], []
        now = datetime.now(timezone.utc)
        new_files = {}  # rel -> sha, not tracked yet
        for rel, st in current.items():
            if self._stat.get(rel) == st:
                continue  # unchanged since the last scan (tracked, tombstoned or skipped)
            try:
                h = _sha(self.root / rel)
            except OSError:
                continue
            d = by_ref.get(rel)
            if d is None:
                new_files[rel] = h
                continue
            if d.source_status == "missing":
                d.source_status = "ok"
                counts["restored"] += 1
            if d.source_hash != h:
                item = read_item(self.root / rel, self.root)
                if item:
                    d.title, d.captured_text, d.full_text = item["title"], item["text"], item["text"]
                    d.extracted_text = None
                    d.source_hash = h
                    reembed.append(d)
                    counts["updated"] += 1

        # files no longer where VB last saw them
        gone = [d for ref, d in by_ref.items() if ref not in current]
        by_hash = {}
        for d in gone:
            by_hash.setdefault(d.source_hash, d)
        # a new path with the same bytes as a gone (or missing) document is a move
        for rel, h in list(new_files.items()):
            d = by_hash.pop(h, None)
            if d is not None:
                self._relink(d, rel)
                counts["moved" if d.source_status != "missing" else "restored"] += 1
                d.source_status = "ok"
                new_files.pop(rel)
        # still gone: moved out of the watched folders (same name and bytes elsewhere) or missing
        for d in by_hash.values():
            if d.source_status == "missing":
                continue
            rel = self._find_by_name(Path(d.source_ref).name, d.source_hash) if d.source_hash else None
            if rel:
                self._relink(d, rel)
                counts["moved"] += 1
            else:
                d.source_status = "missing"
                counts["missing"] += 1

        for rel, h in new_files.items():
            if (rel, h) in tomb:
                counts["skipped"] += 1
                continue
            item = read_item(self.root / rel, self.root)
            if not item:
                counts["skipped"] += 1
                continue
            d = Document(vault_id=vault.id, created_by=vault.owner_id, url=item["url"], title=item["title"],
                         captured_text=item["text"], full_text=item["text"],
                         captured_at=_naive_utc(item["captured_at"]), source_kind=KIND, source_ref=rel,
                         source_hash=h, source_status="ok", imported_at=now)
            db.add(d)
            fresh.append(d)
            counts["new"] += 1

        if reembed:
            db.query(EMBED_TABLE).filter(EMBED_TABLE.document_id.in_([d.id for d in reembed])).delete(
                synchronize_session=False)  # sentences cascade
        db.commit()
        for i in range(0, len(fresh + reembed), 25):
            embed_docs_bulk((fresh + reembed)[i:i + 25])
        self._stat = {k: v for k, v in current.items()}
        return counts

    def _relink(self, d, rel: str) -> None:
        d.source_ref = rel
        d.url = (self.root / rel).resolve().as_uri()


# ---------------------------------------------------------------- background thread
_thread = None


def _loop(cfg: dict) -> None:
    import psycopg2

    from .db import get_db

    dsn = os.environ.get("DATABASE_URL", "").replace("postgresql+psycopg2://", "postgresql://")
    src = VaultFolderSource(cfg["root"], cfg["folders"], cfg["vault_id"])
    lock_con = None
    while True:
        try:
            if lock_con is None or lock_con.closed:
                lock_con = psycopg2.connect(dsn)
                lock_con.autocommit = True
            cur = lock_con.cursor()
            cur.execute("SELECT pg_try_advisory_lock(%s)", [LOCK_KEY])
            if cur.fetchone()[0]:
                break
            _say("another process holds the scanner lock; waiting")
        except Exception as exc:  # noqa: BLE001
            _say(f"lock connection failed ({exc}); retrying")
            lock_con = None
        time.sleep(cfg["interval"])
    _say(f"on: {cfg['root']} {cfg['folders']} -> vault {cfg['vault_id']}, every {cfg['interval']:.0f} s")
    while True:
        t0 = time.perf_counter()
        gen = get_db()
        db = next(gen)
        try:
            c = src.scan_once(db)
            if any(c.values()):
                _say(", ".join(f"{v} {k}" for k, v in c.items() if v) + f" in {time.perf_counter() - t0:.1f} s")
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            _say(f"scan failed: {type(exc).__name__}: {exc}")
        finally:
            try:
                next(gen)
            except StopIteration:
                pass
        time.sleep(cfg["interval"])


def start_background() -> bool:
    global _thread
    cfg = settings()
    if not cfg["enabled"]:
        return False
    if not cfg["vault_id"]:
        _say("VB_VAULT_SOURCE=1 but VB_VAULT_TARGET_VAULT is not set; off")
        return False
    if _thread is None:
        _thread = threading.Thread(target=_loop, args=(cfg,), name="vault-source", daemon=True)
        _thread.start()
    return True
