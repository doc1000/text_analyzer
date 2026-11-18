"""Read side of Vault Bubbles' Postgres (the source of truth). Every sync connection is read-only.

The only writes are the explicit ``install_trigger`` / ``uninstall_trigger`` commands, which add or
drop a NOTIFY trigger on ``documents`` (phase 2 fast path) and touch no data.
"""
from __future__ import annotations

from typing import Iterable

from .copy import pg_row_to_doc

CHANNEL = "vb_documents"

_FINGERPRINT = ("md5(coalesce(url,'') || chr(31) || coalesce(title,'') || chr(31) || coalesce(full_text,'') "
                "|| chr(31) || coalesce(extract(epoch FROM captured_at)::text,'') "
                "|| chr(31) || coalesce(array_to_string(user_tags, chr(30)),''))")

TRIGGER_SQL = f"""
CREATE OR REPLACE FUNCTION vb_orchard_notify() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  PERFORM pg_notify('{CHANNEL}', COALESCE(NEW.id, OLD.id)::text);
  RETURN NULL;
END $$;
DROP TRIGGER IF EXISTS vb_orchard_notify ON documents;
CREATE TRIGGER vb_orchard_notify AFTER INSERT OR UPDATE OR DELETE ON documents
  FOR EACH ROW EXECUTE FUNCTION vb_orchard_notify();
"""

UNTRIGGER_SQL = """
DROP TRIGGER IF EXISTS vb_orchard_notify ON documents;
DROP FUNCTION IF EXISTS vb_orchard_notify();
"""


def connect(url: str, *, read_only: bool = True):
    import psycopg

    opts = "-c default_transaction_read_only=on" if read_only else None
    return psycopg.connect(url, autocommit=True, options=opts, application_name="vb-orchard")


def _kind_sql(con) -> str:
    """vaults.kind if migration 008 ran, else derived from is_personal."""
    has = con.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'vaults' "
                      "AND column_name = 'kind'").fetchone()
    return "v.kind" if has else "CASE WHEN v.is_personal THEN 'personal' ELSE 'shared' END"


def resolve_vaults(con, settings) -> list:
    """Vaults to sync, each {"id", "kind", "name"}: explicit VB_VAULT_IDS, else the vaults where
    VB_OWNER_EMAIL holds one of VB_VAULT_ROLES. Each gets its own orchard-db file."""
    kind = _kind_sql(con)
    if settings.vault_ids:
        rows = con.execute(f"SELECT v.id::text, {kind}, v.name FROM vaults v WHERE v.id = ANY(%s::uuid[]) "
                           "ORDER BY 1", [list(settings.vault_ids)]).fetchall()
    else:
        rows = con.execute(
            f"SELECT DISTINCT v.id::text, {kind}, v.name FROM vaults v JOIN vault_memberships vm ON vm.vault_id = v.id "
            "JOIN users u ON u.id = vm.user_id WHERE lower(u.email) = lower(%s) AND vm.role = ANY(%s) "
            "AND v.archived_at IS NULL ORDER BY 1",
            [settings.owner_email, list(settings.vault_roles)]).fetchall()
    return [{"id": r[0], "kind": r[1], "name": r[2]} for r in rows]


def fingerprints(con, vault_ids: Iterable[str]) -> dict:
    """{document id: content fingerprint} for every document in the vaults."""
    rows = con.execute(f"SELECT id::text, {_FINGERPRINT} FROM documents WHERE vault_id = ANY(%s::uuid[])",
                       [list(vault_ids)]).fetchall()
    return {r[0]: r[1] for r in rows}


def fetch_docs(con, ids: Iterable[str]) -> list:
    ids = list(ids)
    if not ids:
        return []
    rows = con.execute("SELECT id::text, url, title, full_text, captured_at, user_tags FROM documents "
                       "WHERE id = ANY(%s::uuid[]) ORDER BY captured_at NULLS FIRST, id", [ids]).fetchall()
    return [pg_row_to_doc(*r) for r in rows]


def trigger_installed(con) -> bool:
    row = con.execute("SELECT 1 FROM pg_trigger WHERE tgname = 'vb_orchard_notify' AND NOT tgisinternal").fetchone()
    return row is not None


def install_trigger(url: str) -> None:
    with connect(url, read_only=False) as con:
        con.execute(TRIGGER_SQL)


def uninstall_trigger(url: str) -> None:
    with connect(url, read_only=False) as con:
        con.execute(UNTRIGGER_SQL)
