"""Settings from environment variables, optionally seeded from a KEY=VALUE config file.

Precedence: real environment > config file > defaults. The config file is
``$VB_ORCHARD_CONFIG`` or ``~/.vb-orchard/config.env`` (outside any repo; it may hold the
Postgres URL, so never commit it).

    VB_DATABASE_URL        postgresql://user:pass@host:port/db   (required for sync)
    ORCHARD_DB_PATH        orchard-db DuckDB file      (default ~/.vb-orchard/vb.duckdb)
    ORCHARD_ALIAS          cultivar alias              (default vb/topic)
    VB_OWNER_EMAIL         sync the vaults this user belongs to
    VB_VAULT_ROLES         membership roles that count (default owner)
    VB_VAULT_IDS           explicit vault ids, comma separated (overrides email/roles)
    VB_ORCHARD_POLL_S      fallback poll interval in seconds (default 60)
    VB_ORCHARD_RECUT_AFTER full re-cut after this many incremental inserts (default 25; 0 = never)
    VB_ORCHARD_PORT        viewer / API port on 127.0.0.1 (default 8766)
    ORCHARD_ENCODER        minilm (default) or fake (tests only)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

HOME = Path.home() / ".vb-orchard"


def config_file() -> Path:
    return Path(os.environ.get("VB_ORCHARD_CONFIG") or HOME / "config.env")


def read_config_file(path: Optional[Path] = None) -> dict:
    p = path or config_file()
    out: dict = {}
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def _csv(v: Optional[str]) -> list:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


@dataclass
class Settings:
    database_url: Optional[str] = None
    db_path: Path = HOME / "vb.duckdb"
    alias: str = "vb/topic"
    owner_email: Optional[str] = None
    vault_roles: list = field(default_factory=lambda: ["owner"])
    vault_ids: list = field(default_factory=list)
    poll_seconds: float = 60.0
    recut_after: int = 25
    port: int = 8766
    encoder: str = "minilm"

    @classmethod
    def load(cls, env: Optional[dict] = None, config_path: Optional[Path] = None) -> "Settings":
        e = dict(read_config_file(config_path))
        e.update(os.environ if env is None else env)
        s = cls()
        s.database_url = e.get("VB_DATABASE_URL") or None
        if e.get("ORCHARD_DB_PATH"):
            s.db_path = Path(e["ORCHARD_DB_PATH"]).expanduser()
        s.alias = e.get("ORCHARD_ALIAS") or s.alias
        s.owner_email = e.get("VB_OWNER_EMAIL") or None
        s.vault_roles = _csv(e.get("VB_VAULT_ROLES")) or s.vault_roles
        s.vault_ids = _csv(e.get("VB_VAULT_IDS"))
        s.poll_seconds = float(e.get("VB_ORCHARD_POLL_S") or s.poll_seconds)
        s.recut_after = int(e.get("VB_ORCHARD_RECUT_AFTER") or s.recut_after)
        s.port = int(e.get("VB_ORCHARD_PORT") or s.port)
        s.encoder = (e.get("ORCHARD_ENCODER") or s.encoder).lower()
        return s

    def require_database(self) -> str:
        if not self.database_url:
            raise SystemExit(f"VB_DATABASE_URL is not set (environment or {config_file()})")
        if not (self.vault_ids or self.owner_email):
            raise SystemExit("set VB_OWNER_EMAIL or VB_VAULT_IDS to choose which vaults to sync")
        return self.database_url

    def describe(self) -> dict:
        """Settings without the database URL (safe to print)."""
        return {"db_path": str(self.db_path), "alias": self.alias, "owner_email": self.owner_email,
                "vault_roles": self.vault_roles, "vault_ids": self.vault_ids, "poll_seconds": self.poll_seconds,
                "recut_after": self.recut_after, "port": self.port, "encoder": self.encoder,
                "database_url_set": bool(self.database_url), "config_file": str(config_file())}
