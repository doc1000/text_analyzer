"""``vb-orchard`` command line.

    vb-orchard status              settings (no secrets), orchard-db and Postgres state
    vb-orchard sync [--once]       the sync service (the only orchard-db writer)
    vb-orchard rebuild             one full rebuild from Postgres, then exit
    vb-orchard serve               viewer + /do API on 127.0.0.1:$VB_ORCHARD_PORT
    vb-orchard mcp                 orchard-harvest MCP server on stdio
    vb-orchard install-trigger     add the NOTIFY trigger on documents (fast path; no data change)
    vb-orchard uninstall-trigger   drop it again
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from .config import Settings


def _log():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                        stream=sys.stdout)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="vb-orchard", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("sync")
    sp.add_argument("--once", action="store_true", help="one pass, then exit")
    rb = sub.add_parser("rebuild")
    rb.add_argument("--vault", default=None, help="one vault id (default: every synced vault)")
    sv = sub.add_parser("serve")
    sv.add_argument("--port", type=int, default=None)
    mc = sub.add_parser("mcp")
    mc.add_argument("--vault", default=None, help="vault id (default: the personal vault)")
    sub.add_parser("status")
    sub.add_parser("install-trigger")
    sub.add_parser("uninstall-trigger")
    a = p.parse_args(argv)
    s = Settings.load()

    if a.cmd == "sync":
        _log()
        from .sync import run

        run(s, once=a.once)
    elif a.cmd == "rebuild":
        _log()
        from . import pgsource
        from .lock import WriterLock
        from .sync import Syncer, get_encoder

        url = s.require_database()
        with WriterLock(s.lock_path), pgsource.connect(url) as pg:
            enc = get_encoder(s.encoder)
            for v in pgsource.resolve_vaults(pg, s):
                if a.vault and v["id"] != a.vault:
                    continue
                r = Syncer(s, v, encoder=enc).rebuild(pg, pgsource.fingerprints(pg, [v["id"]]), "manual")
                print(f"{v['kind']} {v['name']} {v['id']}: {r.line()}")
    elif a.cmd == "serve":
        from .serve import make_server

        srv = make_server(s, port=a.port)
        print(f"serving http://127.0.0.1:{srv.server_address[1]}/ for the vault files in {s.db_dir}", flush=True)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            srv.server_close()
    elif a.cmd == "mcp":
        from orchard_harvest.mcp.server import main as mcp_main

        from .serve import VaultStores

        vault = a.vault or VaultStores(s.db_dir).default_vault()
        if not vault:
            raise SystemExit(f"no orchard-db files in {s.db_dir}")
        mcp_main(["--db", str(s.db_for(vault)), "--alias", s.alias])
    elif a.cmd == "status":
        from .serve import VaultStores
        from .sync import read_state

        out = {"settings": s.describe(), "orchard_db_files": {}}
        for vid, path in VaultStores(s.db_dir).files().items():
            try:
                out["orchard_db_files"][vid] = len(read_state(path))
            except Exception as exc:  # noqa: BLE001
                out["orchard_db_files"][vid] = f"busy: {type(exc).__name__}"
        if s.database_url:
            from . import pgsource

            with pgsource.connect(s.database_url) as pg:
                vaults = pgsource.resolve_vaults(pg, s)
                out["vaults"] = [{**v, "postgres_documents": len(pgsource.fingerprints(pg, [v["id"]]))}
                                 for v in vaults]
                out["notify_trigger"] = pgsource.trigger_installed(pg)
        print(json.dumps(out, indent=2))
    elif a.cmd == "install-trigger":
        from . import pgsource

        pgsource.install_trigger(s.require_database())
        print("installed trigger vb_orchard_notify on documents (NOTIFY vb_documents)")
    elif a.cmd == "uninstall-trigger":
        from . import pgsource

        pgsource.uninstall_trigger(s.require_database())
        print("dropped trigger vb_orchard_notify")


if __name__ == "__main__":
    main()
