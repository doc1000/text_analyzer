"""Seam to orchard-db (treedb). One short-lived read-write connection per batch (spec A7).

treedb / orchard are imported lazily, so importing this module needs neither.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Callable, Optional, Sequence

from .ids import normalize_url


def connect(path, *, read_only: bool = False):
    """treedb.store.connect_with_retry (backoff up to 2 s, then StoreBusyError)."""
    from treedb import store as tstore

    return tstore.connect_with_retry(str(path), read_only=read_only)


@contextmanager
def connection(path, *, read_only: bool = False):
    con = connect(path, read_only=read_only)
    try:
        yield con
    finally:
        con.close()


def upsert_documents(con, docs: Sequence[dict]):
    from treedb import documents as tdocs

    return tdocs.upsert_documents(con, list(docs))


def get_doc(con, item_id: str) -> Optional[dict]:
    row = con.execute(
        "SELECT item_id, title, text, metadata FROM treedb.documents WHERE item_id = ?", [item_id]
    ).fetchone()
    if row is None:
        return None
    md = row[3]
    if isinstance(md, str):
        md = json.loads(md)
    return {"item_id": row[0], "title": row[1] or "", "text": row[2] or "", "metadata": dict(md or {})}


def find_by_url(con, url: str) -> Optional[dict]:
    """Existing doc for a url (normalized url match on metadata first, then the vb: id)."""
    from treedb import documents as tdocs

    from .ids import capture_item_id

    ids = tdocs.find_by_url(con, normalize_url(url))
    for i in ids or []:
        d = get_doc(con, i)
        if d:
            return d
    return get_doc(con, capture_item_id(url))


def make_lookup(path) -> Callable[[str, str], Optional[dict]]:
    """CaptureWriter lookup(norm_url, item_id) -> existing doc or None; opens a connection per miss."""

    def lookup(norm_url: str, item_id: str):
        try:
            with connection(path) as con:
                return find_by_url(con, norm_url)
        except Exception:  # noqa: BLE001 - empty/new database: nothing to merge with
            return None

    return lookup


def make_write_batch(path, *, alias: str = "vb/topic", insert_on_capture: bool = True,
                     attach: bool = True, encoder=None, stats: Optional[dict] = None):
    """Build the CaptureWriter write_batch: open, write one batch, close.

    With insert_on_capture (default) and an existing alias the batch goes through
    orchard.cultivate_db.insert_items (which upserts the documents and places the items in
    the tree at once); otherwise documents are only upserted. Idempotent, so retries are safe.
    `stats` (optional dict) accumulates placed / upserted_only / attach_failed counters.
    """
    stats = stats if stats is not None else {}

    def write_batch(docs: Sequence[dict]) -> None:
        docs = list(docs)
        with connection(path) as con:
            placed = False
            if insert_on_capture:
                from treedb import registry

                try:
                    registry.resolve(con, alias)
                    have_alias = True
                except Exception:  # noqa: BLE001
                    have_alias = False
                if have_alias:
                    from orchard.cultivate_db import insert_items

                    res = insert_items(con, alias, docs, encoder=encoder, attach=attach)
                    stats["placed"] = stats.get("placed", 0) + len(res.new_item_ids)
                    stats["recaptured"] = stats.get("recaptured", 0) + len(res.recaptured_item_ids)
                    stats["attach_failed"] = stats.get("attach_failed", 0) + sum(
                        1 for v in res.attach.values() if v is not None)
                    placed = True
            if not placed:
                upsert_documents(con, docs)
                stats["upserted_only"] = stats.get("upserted_only", 0) + len(docs)

    return write_batch
