"""Read side on 127.0.0.1: the orchard-harvest viewer plus VB-shaped ``/do`` routes, one tree per vault.

    GET /                         packed-circles viewer (live refresh via /api/version)
    GET /api/tree | /api/find | /api/version          (orchard-harvest; ?vault=<id>, default: personal)
    GET /do/topics/hierarchy?vault=<id>&days=30       VB /topics/hierarchy shape
    GET /do/find?vault=<id>&q=...&k=10[&days=N]       hybrid search
    GET /do/recent?vault=<id>&days=7&limit=20         newest items
    GET /do/health                                    {"ok", "vaults": [ids], "vault_info": [...], "default_vault"}

Each vault is its own orchard-db file (``<ORCHARD_DB_DIR>/<vault_id>.duckdb``), opened per request,
read-only. Trees are never merged. This server does no authorization: it is bound to 127.0.0.1 and
VB asks it only for vaults the signed-in user may read. The sync service is the only writer.
"""
from __future__ import annotations

import re
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .adapter import to_vb_hierarchy

CUT = "topics"
_VAULT_FILE = re.compile(r"^([0-9a-fA-F-]{36})\.duckdb$")


def _days(params, default=None):
    v = (params.get("days") or [None])[0]
    if v in (None, ""):
        return default
    if v == "all":
        return None
    return float(v)


class VaultStores:
    """HarvestStore per vault file, created on first use; vault kind/name from vb_sync.meta."""

    def __init__(self, db_dir):
        self.db_dir = Path(db_dir)
        self._stores: dict = {}
        self._lock = threading.Lock()

    def files(self) -> dict:
        out = {}
        if self.db_dir.exists():
            for p in self.db_dir.iterdir():
                m = _VAULT_FILE.match(p.name)
                if m:
                    out[m.group(1).lower()] = p
        return out

    def store(self, vault_id):
        from orchard_harvest.store import HarvestStore

        path = self.files().get(str(vault_id).lower())
        if path is None:
            return None
        with self._lock:
            s = self._stores.get(str(path))
            if s is None:
                s = self._stores[str(path)] = HarvestStore(str(path))
            return s

    def meta(self, vault_id) -> dict:
        s = self.store(vault_id)
        if s is None:
            return {}
        try:
            with s.connection() as con:
                rows = con.execute("SELECT key, value FROM vb_sync.meta").fetchall()
            return dict(rows)
        except Exception:  # noqa: BLE001 - no state yet, or the file is busy
            return {}

    def info(self) -> list:
        out = []
        for vid in sorted(self.files()):
            m = self.meta(vid)
            out.append({"id": vid, "kind": m.get("vault_kind") or None, "name": m.get("vault_name") or None,
                        "last_insert": m.get("last_insert"), "last_rebuild": m.get("last_rebuild")})
        return out

    def default_vault(self):
        info = self.info()
        personal = [v["id"] for v in info if v["kind"] == "personal"]
        if personal:
            return personal[0]
        return info[0]["id"] if info else None


def make_server(settings, *, port=None, stores: VaultStores | None = None) -> ThreadingHTTPServer:
    from orchard_harvest import queries
    from orchard_harvest.store import AliasNotFound
    from orchard_harvest.view.server import make_handler

    stores = stores or VaultStores(settings.db_dir)
    alias = settings.alias
    handlers: dict = {}  # vault_id -> harvest handler class bound to that vault's store

    def harvest_handler(vault_id):
        store = stores.store(vault_id)
        if store is None:
            return None
        key = id(store)
        if key not in handlers:
            handlers[key] = make_handler(store, alias, CUT)
        return handlers[key]

    class Handler(make_handler(None, alias, CUT)):
        def _vault(self, params):
            v = (params.get("vault") or [None])[0]
            return (v or stores.default_vault() or "").lower() or None

        def do_GET(self):  # noqa: N802
            url = urlparse(self.path)
            params = parse_qs(url.query)
            if url.path == "/do/health":
                return self._json(200, {"ok": True, "alias": alias, "vaults": sorted(stores.files()),
                                        "vault_info": stores.info(), "default_vault": stores.default_vault()})
            vault = self._vault(params)
            if not url.path.startswith("/do/"):
                # viewer and /api/*: the harvest handler of the requested (or default) vault
                h = harvest_handler(vault) if vault else None
                if h is None:
                    if url.path in ("/", "/index.html") or url.path.startswith("/static/"):
                        h = make_handler(None, alias, CUT)  # page shell; its API calls report the error
                    else:
                        return self._json(404, {"error": f"no orchard-db for vault {vault!r}"})
                return h.do_GET(self)
            store = stores.store(vault) if vault else None
            if store is None:
                return self._json(404, {"error": f"no orchard-db for vault {vault!r}"})
            try:
                if url.path == "/do/topics/hierarchy":
                    days = _days(params, 30)
                    tree = to_vb_hierarchy(queries.packed_tree(store, alias, CUT, days=days), days)
                    tree["vault_id"] = vault
                    return self._json(200, tree)
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
