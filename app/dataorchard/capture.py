"""Capture normalization, merge rule (spec 6.3) and the single writer queue (A7).

Extension field mapping (extension/background.js -> app/schemas.IngestPayload):
  url, title, captured_at, tags     -> same names
  text                              -> full_text (spec name `full_text` is also accepted)
  mode == "page"                    -> text is the page body (full_text)
  mode == "selection"               -> text is "[Header] ...\\n\\n[captured]\\n<sel>"; <sel> -> highlight, full_text empty
  mode == "note" (popup; url may be note://...) -> text -> notes, full_text empty
  pdf_to_parse, linked_pdf_urls     -> ignored (VB server-side extras)
"""
from __future__ import annotations

import queue
import threading
from concurrent.futures import Future
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence

from .ids import capture_item_id, normalize_url

_SEL_MARK = "[captured]\n"


def to_utc_z(value) -> Optional[str]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        if s[-1:] in ("Z", "z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond:06d}Z"


def now_z() -> str:
    return to_utc_z(datetime.now(timezone.utc))


def normalize_payload(payload: dict) -> dict:
    """Extension/IngestPayload-shaped dict -> {url,title,full_text,highlight,notes,tags,captured_at}."""
    p = dict(payload)
    url = (p.get("url") or "").strip()
    if not url:
        raise ValueError("payload has no url")
    mode = p.get("mode") or "page"
    body = p.get("full_text")
    if body is None:
        body = p.get("text")
    body = body or ""
    highlight = p.get("highlight") or ""
    notes = p.get("notes") or ""
    if "full_text" not in p and "text" in p:  # real extension shape: interpret by mode
        if mode == "selection":
            idx = body.find(_SEL_MARK)
            highlight = highlight or (body[idx + len(_SEL_MARK):] if idx >= 0 else body).strip()
            body = ""
        elif mode == "note":
            notes = notes or body.strip()
            body = ""
    tags = p.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    tags = sorted({str(t).strip() for t in tags if str(t).strip()})
    return {
        "url": url,
        "title": (p.get("title") or "").strip(),
        "full_text": body,
        "highlight": highlight.strip(),
        "notes": notes.strip(),
        "tags": tags,
        "captured_at": to_utc_z(p.get("captured_at")),
    }


def _join_unique(existing: str, new: str) -> str:
    if not new:
        return existing
    parts = [x for x in existing.split("\n") if x] if existing else []
    if new in parts or new == existing:
        return existing
    return (existing + "\n" + new) if existing else new


def _ts_min(a, b):
    xs = [x for x in (a, b) if x]
    return min(xs) if xs else None


def _ts_max(a, b):
    xs = [x for x in (a, b) if x]
    return max(xs) if xs else None


def merge(existing_doc: Optional[dict], payload: dict, *, now: Optional[str] = None) -> dict:
    """Merge a (normalized or raw) payload into an existing doc (or None) per spec 6.3."""
    n = normalize_payload(payload)
    now = now or now_z()
    captured = n["captured_at"] or now
    if existing_doc is None:
        return {
            "item_id": capture_item_id(n["url"]),
            "title": n["title"],
            "text": n["full_text"],
            "metadata": {
                "url": n["url"], "captured_at": captured, "updated_at": now,
                "highlights": [n["highlight"]] if n["highlight"] else [],
                "notes": n["notes"], "tags": list(n["tags"]),
                "source": "vaultbubbles",
            },
        }
    md = dict(existing_doc.get("metadata") or {})
    highlights = list(md.get("highlights") or [])
    if n["highlight"] and n["highlight"] not in highlights:
        highlights.append(n["highlight"])
    text = existing_doc.get("text") or ""
    if n["full_text"] and len(n["full_text"]) > len(text):
        text = n["full_text"]
    md.update({
        "url": md.get("url") or n["url"],
        "captured_at": _ts_min(md.get("captured_at"), captured),
        "updated_at": _ts_max(md.get("updated_at"), now),
        "highlights": highlights,
        "notes": _join_unique(md.get("notes") or "", n["notes"]),
        "tags": sorted(set(md.get("tags") or []) | set(n["tags"])),
        "source": md.get("source") or "vaultbubbles",
    })
    return {
        "item_id": existing_doc["item_id"],
        "title": existing_doc.get("title") or n["title"],
        "text": text,
        "metadata": md,
    }


_STOP = object()


class CaptureWriter:
    """Single writer queue: many submitters, one background thread, batched write_batch(docs).

    write_batch(docs) must be idempotent (at-least-once: a failing batch is retried
    `retries` times, then every future in it gets the exception). `lookup(norm_url, item_id)`
    optionally finds an existing doc not yet in the writer's own cache of written docs.
    submit() returns a Future resolving to the item_id (also available as fut.item_id at once).
    """

    def __init__(self, write_batch: Callable[[Sequence[dict]], None], *,
                 lookup: Optional[Callable[[str, str], Optional[dict]]] = None,
                 max_batch: int = 64, linger: float = 0.02, retries: int = 3, clock=now_z):
        self._write = write_batch
        self._lookup = lookup
        self._max_batch = max_batch
        self._linger = linger
        self._retries = retries
        self._clock = clock
        self._q: "queue.Queue" = queue.Queue()
        self._cache: dict = {}
        self._closed = False
        self._lock = threading.Lock()
        self._idle = threading.Condition()
        self._pending = 0
        self._thread = threading.Thread(target=self._run, name="capture-writer", daemon=True)
        self._thread.start()

    def seed(self, docs: Sequence[dict]) -> None:
        for d in docs:
            self._cache[d["item_id"]] = d

    def submit(self, payload: dict) -> "Future":
        fut: Future = Future()
        n = normalize_payload(payload)
        fut.item_id = capture_item_id(n["url"])  # type: ignore[attr-defined]
        with self._lock:
            if self._closed:
                raise RuntimeError("CaptureWriter is closed")
            with self._idle:
                self._pending += 1
            self._q.put((n, fut))
        return fut

    def _existing(self, n: dict) -> Optional[dict]:
        iid = capture_item_id(n["url"])
        if iid in self._cache:
            return self._cache[iid]
        if self._lookup:
            return self._lookup(normalize_url(n["url"]), iid)
        return None

    def _run(self):
        while True:
            item = self._q.get()
            if item is _STOP:
                return
            batch = [item]
            stop = False
            while len(batch) < self._max_batch:
                try:
                    nxt = self._q.get(timeout=self._linger)
                except queue.Empty:
                    break
                if nxt is _STOP:
                    stop = True
                    break
                batch.append(nxt)
            self._process(batch)
            if stop:
                return

    def _process(self, batch):
        staged: dict = {}
        err = None
        try:
            for n, _ in batch:
                iid = capture_item_id(n["url"])
                base = staged.get(iid) or self._existing(n)
                staged[iid] = merge(base, n, now=self._clock())
            docs = list(staged.values())
            for _ in range(max(1, self._retries)):
                try:
                    self._write(docs)
                    err = None
                    break
                except Exception as e:  # noqa: BLE001
                    err = e
        except Exception as e:  # noqa: BLE001
            err = e
        if err is None:
            self._cache.update(staged)
        for n, fut in batch:
            if err is None:
                fut.set_result(capture_item_id(n["url"]))
            else:
                fut.set_exception(err)
        with self._idle:
            self._pending -= len(batch)
            self._idle.notify_all()

    def flush(self, timeout: Optional[float] = None) -> bool:
        with self._idle:
            return self._idle.wait_for(lambda: self._pending == 0, timeout)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._q.put(_STOP)
        self._thread.join()
