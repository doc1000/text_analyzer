"""Thin seam to orchard-db (treedb). Phase 1: stubs only."""
from __future__ import annotations

from typing import Optional, Sequence


def connect(path: str, *, read_only: bool = False):
    """Would call treedb.store.connect_with_retry(path, read_only=...)."""
    raise NotImplementedError("orchard-db API not wired yet")


def upsert_documents(con, docs: Sequence[dict]):
    """Would call treedb.documents.upsert_documents(con, docs)."""
    raise NotImplementedError("orchard-db API not wired yet")


def find_by_url(con, normalized_url: str) -> Optional[dict]:
    """Would call treedb.documents.find_by_url(con, url); returns doc dict or None."""
    raise NotImplementedError("orchard-db API not wired yet")
