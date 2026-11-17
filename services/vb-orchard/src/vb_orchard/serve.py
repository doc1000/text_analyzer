"""Read side on 127.0.0.1: the orchard-harvest viewer plus VB-shaped ``/do`` routes.

    GET /                         packed-circles viewer (live refresh via /api/version)
    GET /api/tree | /api/find | /api/version          (orchard-harvest)
    GET /do/topics/hierarchy?days=30                  VB /topics/hierarchy shape
    GET /do/find?q=...&k=10[&days=N]                  hybrid search
    GET /do/recent?days=7&limit=20                    newest items
    GET /do/health                                    {"ok": true, ...}

Only reads the orchard-db file (opened per request); the sync service is the writer.
"""
from __future__ import annotations

from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .adapter import to_vb_hierarchy

CUT = "topics"


def _days(params, default=None):
    v = (params.get("days") or [None])[0]
    if v in (None, ""):
        return default
    if v == "all":
        return None
    return float(v)


def _synced_vaults(store) -> list:
    """Vault ids the sync service last synced (vb_sync.meta 'vaults'); [] if unknown."""
    try:
        with store.connection() as con:
            row = con.execute("SELECT value FROM vb_sync.meta WHERE key = 'vaults'").fetchone()
        return [v for v in (row[0] if row else "").split(",") if v]
    except Exception:  # noqa: BLE001 - no state table yet, or the file is busy
        return []


def make_server(settings, *, port=None, store=None) -> ThreadingHTTPServer:
    from orchard_harvest import queries
    from orchard_harvest.store import AliasNotFound, HarvestStore
    from orchard_harvest.view.server import make_handler

    store = store or HarvestStore(str(settings.db_path))
    alias = settings.alias
    Base = make_handler(store, alias, CUT)

    class Handler(Base):
        def do_GET(self):  # noqa: N802
            url = urlparse(self.path)
            if not url.path.startswith("/do/"):
                return super().do_GET()
            params = parse_qs(url.query)
            try:
                if url.path == "/do/health":
                    snap = store.snapshot(alias, CUT)
                    return self._json(200, {"ok": True, "alias": alias, "items": len(snap.items) if hasattr(snap, "items") else None,
                                            "as_of": snap.as_of(), "stale": bool(snap.stale),
                                            "vaults": _synced_vaults(store)})
                if url.path == "/do/topics/hierarchy":
                    days = _days(params, 30)
                    return self._json(200, to_vb_hierarchy(queries.packed_tree(store, alias, CUT, days=days), days))
                if url.path == "/do/find":
                    q = (params.get("q") or [""])[0]
                    k = int((params.get("k") or ["10"])[0])
                    return self._json(200, queries.to_json_payload(
                        queries.find(store, alias, q, k=k, days=_days(params), cut=CUT)))
                if url.path == "/do/recent":
                    limit = int((params.get("limit") or ["20"])[0])
                    return self._json(200, queries.to_json_payload(
                        queries.recent(store, alias, days=_days(params, 7), limit=limit, cut=CUT)))
                return self._json(404, {"error": "not found"})
            except ValueError as exc:
                return self._json(400, {"error": f"bad request: {exc}"})
            except AliasNotFound as exc:
                return self._json(404, {"error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                return self._json(503, {"error": f"{type(exc).__name__}: {exc}"})

    return ThreadingHTTPServer(("127.0.0.1", settings.port if port is None else port), Handler)
