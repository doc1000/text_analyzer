"""todo.txt files as a source into the personal vault (batch31): one VB item per todo line.

Read-only: VB never writes to the todo files. Scanned in the vault-source thread (same interval).

    VB_TODO_SOURCE=1                         turn it on
    VB_TODO_FILES=<path>;<path>;...          todo.txt-format files (~ allowed)
    (target vault: VB_VAULT_TARGET_VAULT, the personal vault)

Each item: source_kind "todo", source_ref "<folder>/<file>#<key>", source_meta (JSON) with the
parsed line - status open|done, priority, projects, contexts, created, completed, due, extra
key:values, file, line number.

Identity across scans:
  - the key is a hash of the file label and the line's description (without the done marker,
    dates and priority), so marking a line done or changing its priority updates the same item
  - an edited line (new key) is matched to a vanished item of the same file by text similarity
    (>= 0.6) and updated in place, keeping its tags
  - a removed line keeps its VB item and text, marked source_status missing; if it comes back,
    it is restored. Nothing in VB is erased by the files.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

KIND = "todo"
_DATE = r"\d{4}-\d{2}-\d{2}"
_DONE = re.compile(rf"^x\s+(?:({_DATE})\s+)?(?:({_DATE})\s+)?")
_PRI = re.compile(r"^\(([A-Z])\)\s+")
_CREATED = re.compile(rf"^({_DATE})\s+")
_KV = re.compile(r"(?<!\S)([A-Za-z][\w-]*):(?!//)(\S+)")


def settings() -> dict:
    files = [Path(p.strip()).expanduser() for p in os.environ.get("VB_TODO_FILES", "").split(";") if p.strip()]
    return {"enabled": os.environ.get("VB_TODO_SOURCE", "0").strip().lower() in ("1", "true", "yes", "on"),
            "files": files}


def label(path: Path) -> str:
    return f"{path.parent.name}/{path.name}"


def parse_line(raw: str) -> dict | None:
    """One todo.txt line -> parsed fields; None for blank lines and comments."""
    line = raw.strip()
    if not line or line.startswith("#"):
        return None
    out = {"status": "open", "priority": None, "created": None, "completed": None}
    m = _DONE.match(line)
    if m:
        out["status"] = "done"
        # "x <completed> <created>"; a single date is the completion date
        out["completed"] = m.group(1)
        out["created"] = m.group(2)
        line = line[m.end():]
    m = _PRI.match(line)
    if m:
        out["priority"] = m.group(1)
        line = line[m.end():]
    if not out["created"]:
        m = _CREATED.match(line)
        if m:
            out["created"] = m.group(1)
            line = line[m.end():]
    desc = line.strip()
    words = desc.split()
    out["projects"] = sorted({w[1:] for w in words if w.startswith("+") and len(w) > 1})
    out["contexts"] = sorted({w[1:] for w in words if w.startswith("@") and len(w) > 1})
    kv = {k.lower(): v for k, v in _KV.findall(desc)}
    out["due"] = kv.pop("due", None)
    out["extra"] = kv
    out["description"] = desc
    title = _KV.sub("", desc)
    title = re.sub(r"(?<!\S)[+@]\S+", "", title)
    title = re.sub(r"\s+", " ", title).strip() or desc
    out["title"] = title if len(title) <= 140 else title[:137].rstrip() + "..."
    return out


def _key(lab: str, desc: str, n: int) -> str:
    return hashlib.sha256(f"{lab}\n{desc}\n{n}".encode("utf-8")).hexdigest()[:16]


def read_file(path: Path) -> list:
    """Parsed items of one file, each with its key (duplicates of a line get an occurrence number)."""
    lab = label(path)
    seen: dict = {}
    items = []
    for i, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        it = parse_line(raw)
        if it is None:
            continue
        n = seen.get(it["description"], 0)
        seen[it["description"]] = n + 1
        it.update(line=i, file=lab, key=_key(lab, it["description"], n))
        items.append(it)
    return items


def _date(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d") if s else None
    except ValueError:
        return None


def _meta(it: dict) -> dict:
    return {k: it[k] for k in ("status", "priority", "projects", "contexts", "created", "completed",
                               "due", "extra", "file", "line")}


class TodoSource:
    def __init__(self, files: list, vault_id: str):
        self.files = [Path(f) for f in files]
        self.vault_id = vault_id
        self._stat: dict = {}

    def scan_once(self, db) -> dict:
        from sqlalchemy.orm import defer

        from .db import EMBED_TABLE
        from .helpers import embed_docs_bulk
        from .models import Document, Vault

        counts = {"new": 0, "updated": 0, "missing": 0, "restored": 0}
        stats = {}
        for f in self.files:
            try:
                st = f.stat()
                stats[f] = (st.st_mtime_ns, st.st_size)
            except OSError:
                stats[f] = None  # file not there (e.g. not synced yet): leave its items alone
        changed = [f for f in self.files if stats[f] is not None and self._stat.get(f) != stats[f]]
        if not changed:
            return counts
        vault = db.query(Vault).filter(Vault.id == self.vault_id).first()
        if vault is None:
            raise RuntimeError(f"VB_VAULT_TARGET_VAULT {self.vault_id} not found")
        now = datetime.now(timezone.utc)
        fresh, reembed = [], []
        for f in changed:
            lab = label(f)
            items = read_file(f)
            docs = (db.query(Document)
                    .options(defer(Document.full_text), defer(Document.extracted_text))
                    .filter(Document.vault_id == vault.id, Document.source_kind == KIND,
                            Document.source_ref.like(f"{lab}#%")).all())
            by_key = {d.source_ref.split("#", 1)[1]: d for d in docs}
            new_items = []
            for it in items:
                d = by_key.pop(it["key"], None)
                if d is None:
                    new_items.append(it)
                    continue
                if d.source_status == "missing":
                    counts["restored"] += 1
                if self._apply(d, it, text_changed=False):
                    counts["updated"] += 1
            # vanished items vs new lines: an edited line keeps its item (best text match first)
            gone = list(by_key.values())
            pairs = []
            for it in new_items:
                for d in gone:
                    old = d.captured_text or ""
                    sm = difflib.SequenceMatcher(None, it["description"], old, autojunk=False)
                    kept = sum(b.size for b in sm.get_matching_blocks()) / max(1, len(old))
                    r = max(sm.ratio(), 0.9 * kept)  # an appended note keeps most of the old text
                    if r >= 0.6:
                        pairs.append((r, id(it), it, d))
            used_items, used_docs = set(), set()
            for r, _, it, d in sorted(pairs, key=lambda p: -p[0]):
                if id(it) in used_items or d.id in used_docs:
                    continue
                used_items.add(id(it))
                used_docs.add(d.id)
                d.source_ref = f"{lab}#{it['key']}"
                self._apply(d, it, text_changed=True)
                reembed.append(d)
                counts["updated"] += 1
            for d in gone:
                if d.id not in used_docs and d.source_status != "missing":
                    d.source_status = "missing"
                    counts["missing"] += 1
            for it in new_items:
                if id(it) in used_items:
                    continue
                when = _date(it["created"]) or _date(it["completed"]) or now.replace(tzinfo=None)
                d = Document(vault_id=vault.id, created_by=vault.owner_id, url=f"todo://{lab}#{it['key']}",
                             title=it["title"], captured_text=it["description"], full_text=it["description"],
                             captured_at=when, source_kind=KIND, source_ref=f"{lab}#{it['key']}",
                             source_hash=it["key"], source_status="ok", source_meta=_meta(it), imported_at=now)
                db.add(d)
                fresh.append(d)
                counts["new"] += 1
        if reembed:
            db.query(EMBED_TABLE).filter(EMBED_TABLE.document_id.in_([d.id for d in reembed])).delete(
                synchronize_session=False)
        db.commit()
        batch = fresh + reembed
        for i in range(0, len(batch), 200):
            embed_docs_bulk(batch[i:i + 200])
        for f in changed:
            self._stat[f] = stats[f]
        return counts

    @staticmethod
    def _apply(d, it: dict, *, text_changed: bool) -> bool:
        """Bring a document in line with its todo line; True if anything changed."""
        meta = _meta(it)
        before = (d.title, d.captured_text, json.dumps(d.source_meta, sort_keys=True), d.source_status)
        d.title = it["title"]
        if text_changed or d.captured_text != it["description"]:
            d.captured_text = d.full_text = it["description"]
        d.source_meta = meta
        d.source_hash = it["key"]
        d.source_status = "ok"
        return before != (d.title, d.captured_text, json.dumps(meta, sort_keys=True), "ok")
