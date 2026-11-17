"""Bridge to the vb-orchard service (Data Orchard), which serves a derived topic tree and hybrid
search over this app's documents (services/vb-orchard; it reads Postgres, never writes it).

Enabled with ``TOPIC_SOURCE=orchard``. ``ORCHARD_URL`` defaults to http://127.0.0.1:8766.
Every call has a short timeout and returns ``None`` on any failure, so callers fall back to the
built-in topic code. Orchard is used only for users who can see a vault that orchard syncs
(reported by its /do/health).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from typing import Iterable, Optional

log = logging.getLogger(__name__)

_health = {"at": 0.0, "vaults": None}
_lock = threading.Lock()


def enabled() -> bool:
    return os.getenv("TOPIC_SOURCE", "").strip().lower() == "orchard"


def base_url() -> str:
    return os.getenv("ORCHARD_URL", "http://127.0.0.1:8766").rstrip("/")


def _get(path: str, params: Optional[dict] = None, timeout: float = 5.0):
    url = base_url() + path + ("?" + urllib.parse.urlencode(params) if params else "")
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def synced_vaults(max_age_s: float = 30.0) -> Optional[set]:
    """Vault ids orchard syncs (cached briefly); None if orchard is unreachable."""
    with _lock:
        if time.monotonic() - _health["at"] < max_age_s:
            return _health["vaults"]
    try:
        h = _get("/do/health", timeout=2.0)
        vaults = set(h.get("vaults") or [])
    except Exception as exc:  # noqa: BLE001
        log.info("orchard unreachable: %s", exc)
        vaults = None
    with _lock:
        _health.update(at=time.monotonic(), vaults=vaults)
    return vaults


def covers(vault_ids: Iterable) -> bool:
    if not enabled():
        return False
    vs = synced_vaults()
    return bool(vs) and any(str(v) in vs for v in vault_ids)


def hierarchy(days) -> Optional[dict]:
    try:
        tree = _get("/do/topics/hierarchy", {"days": "all" if not days else days}, timeout=10.0)
        tree["source"] = "orchard"
        return tree
    except Exception as exc:  # noqa: BLE001
        log.warning("orchard hierarchy failed, using built-in topics: %s", exc)
        return None


def find(query: str, k: int = 10, days=None) -> Optional[list]:
    """Hybrid search hits [{item_id, title, url, score, snippet?...}] or None on failure."""
    params = {"q": query, "k": k}
    if days:
        params["days"] = days
    try:
        r = _get("/do/find", params, timeout=30.0)
        return r if isinstance(r, list) else r.get("results")
    except Exception as exc:  # noqa: BLE001
        log.warning("orchard find failed: %s", exc)
        return None
