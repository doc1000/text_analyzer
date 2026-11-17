"""VB-facing engine: wraps orchard (cultivate), orchard-db and orchard_harvest. Read paths open
the DuckDB file read-only per request (via HarvestStore); writers open, write one batch, close."""
from __future__ import annotations

import threading
from typing import Optional, Sequence

from .adapter import to_vb_hierarchy

DEFAULT_ALIAS = "vb/topic"
_stores: dict = {}
_lock = threading.Lock()


def harvest_store(path, *, encoder=None):
    from orchard_harvest.store import HarvestStore

    key = str(path)
    with _lock:
        s = _stores.get(key)
        if s is None:
            s = _stores[key] = HarvestStore(key, encoder=encoder)
        return s


def rebuild(path, docs: Sequence[dict], *, alias: str = DEFAULT_ALIAS, perspective: Optional[dict] = None,
            encoder=None, name: str = "vb-topic"):
    """Full cultivate of `docs` into the DuckDB file at `path`; moves `alias` to the new tree."""
    from orchard.cultivate_db import cultivate_into_db
    from orchard.perspectives import topic_perspective

    return cultivate_into_db(list(docs), perspective or topic_perspective(), name=name, db=str(path),
                             alias=alias, encoder=encoder)


def ingest_new(path, docs: Sequence[dict], *, alias: str = DEFAULT_ALIAS, encoder=None, attach: bool = True):
    """Incremental insert (upsert + embed + place + display refresh)."""
    from orchard.cultivate_db import insert_items

    return insert_items(str(path), alias, list(docs), encoder=encoder, attach=attach)


def packed(path, days=None, *, alias: str = DEFAULT_ALIAS):
    from orchard_harvest import packed_tree

    return packed_tree(harvest_store(path), alias, days=days)


def hierarchy(path, days, *, alias: str = DEFAULT_ALIAS) -> dict:
    return to_vb_hierarchy(packed(path, days, alias=alias), days)


def find(path, q: str, *, k: int = 10, days=None, alias: str = DEFAULT_ALIAS, encoder=None):
    from orchard_harvest import find as hfind

    return hfind(harvest_store(path), alias, q, k=k, days=days, encoder=encoder)


def recent(path, days=7, *, limit: int = 20, alias: str = DEFAULT_ALIAS):
    from orchard_harvest import recent as hrecent

    return hrecent(harvest_store(path), alias, days=days, limit=limit)
