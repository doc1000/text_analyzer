"""Collections: isolated, read-only vaults loaded from a folder of static files (batch27).

    python -m app.collections new "Forte" --from <folder> [--owner EMAIL]
    python -m app.collections list [--owner EMAIL]
    python -m app.collections delete <vault_id> --yes

A collection is a vault with kind = 'collection'. Its documents are read-only in VB (tags allowed),
it gets its own Data Orchard tree, and it never mixes with the personal vault. Promotion of
selected items into the personal vault is a separate, explicit step (see
VB_VAULTS_SOURCES_COLLECTIONS.md in the workspace root).

Settings come from the environment, falling back to ~/.vaultbubbles/config.env (DATABASE_URL, ...).
The owner is --owner or VB_OWNER_EMAIL (also read from ~/.vb-orchard/config.env).

File types: .md/.markdown (YAML-style front matter: title, source_url/url, published/date),
.txt, .html/.htm (main text via trafilatura), .pdf (pdfplumber). Other files are skipped.
"""
from __future__ import annotations

import argparse
import html as htmllib
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

TEXT_TYPES = {".md", ".markdown", ".txt", ".html", ".htm", ".pdf"}
_FM = re.compile(r"\A﻿?---\r?\n(.*?)\r?\n---\r?\n?", re.S)


# ---------------------------------------------------------------- settings
def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_settings() -> None:
    _load_env_file(Path(os.environ.get("VB_APP_CONFIG") or Path.home() / ".vaultbubbles" / "config.env"))
    _load_env_file(Path(os.environ.get("VB_ORCHARD_CONFIG") or Path.home() / ".vb-orchard" / "config.env"))
    os.environ.setdefault("HF_HUB_OFFLINE", "1")


# ---------------------------------------------------------------- file parsing
def _front_matter(raw: str):
    m = _FM.match(raw)
    if not m:
        return {}, raw
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t", "-")):
            k, v = line.split(":", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1].replace("''", "'") if v[0] == "'" else v[1:-1]
            meta[k.strip().lower()] = v
    return meta, raw[m.end():]


def _parse_time(value):
    if not value:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d"):
        try:
            dt = datetime.fromisoformat(s) if fmt is None else datetime.strptime(s[:10], fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def read_item(path: Path, root: Path) -> dict | None:
    """One file -> {title, url, text, captured_at, source_ref}; None if empty or unsupported."""
    ext = path.suffix.lower()
    rel = path.relative_to(root).as_posix()
    mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    title = url = None
    captured = None
    if ext in (".md", ".markdown", ".txt"):
        raw = path.read_text(encoding="utf-8", errors="replace")
        meta, body = _front_matter(raw) if ext != ".txt" else ({}, raw)
        title = meta.get("title")
        url = meta.get("source_url") or meta.get("url") or meta.get("link")
        captured = _parse_time(meta.get("published") or meta.get("date") or meta.get("created"))
        if not title:
            h = re.search(r"^#\s+(.+)$", body, re.M)
            title = h.group(1).strip() if h else None
        text = (htmllib.unescape(body) if ext != ".txt" else body).strip()
    elif ext in (".html", ".htm"):
        raw = path.read_text(encoding="utf-8", errors="replace")
        import trafilatura

        text = (trafilatura.extract(raw) or "").strip()
        t = re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I)
        title = t.group(1).strip() if t else None
    elif ext == ".pdf":
        import pdfplumber

        with pdfplumber.open(str(path)) as pdf:
            text = "\n\n".join((p.extract_text() or "") for p in pdf.pages).strip()
    else:
        return None
    if not text:
        return None
    title = htmllib.unescape(title or path.stem.replace("-", " ").replace("_", " "))
    return {"title": title[:500], "url": url or path.resolve().as_uri(), "text": text,
            "captured_at": captured or mtime, "source_ref": rel}


def scan(folder: Path) -> list:
    return sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in TEXT_TYPES)


# ---------------------------------------------------------------- commands
def _owner(db, email):
    from .models import User

    if not email:
        raise SystemExit("set --owner or VB_OWNER_EMAIL (e.g. in ~/.vb-orchard/config.env)")
    u = db.query(User).filter(User.email.ilike(email)).first()
    if not u:
        raise SystemExit("owner not found in users")
    return u


def cmd_new(args) -> None:
    from .db import get_db
    from .helpers import embed_docs_bulk
    from .models import Document, Vault, VaultMembership

    folder = Path(args.folder).expanduser().resolve()
    if not folder.is_dir():
        raise SystemExit(f"not a folder: {folder}")
    files = scan(folder)
    if args.limit:
        files = files[:args.limit]
    print(f"{len(files)} files in {folder}", flush=True)
    db = next(get_db())
    owner = _owner(db, args.owner)
    now = datetime.now(timezone.utc)
    vault = Vault(name=args.name, owner_id=owner.id, is_personal=False, kind="collection",
                  created_at=now.replace(tzinfo=None))
    db.add(vault)
    db.flush()
    db.add(VaultMembership(vault_id=vault.id, user_id=owner.id, role="owner", created_at=now.replace(tzinfo=None)))

    # 1) all documents in one transaction: the orchard sync then builds the tree in one pass
    docs, skipped, seen = [], [], set()
    for p in files:
        try:
            item = read_item(p, folder)
        except Exception as exc:  # noqa: BLE001
            skipped.append((p.name, f"{type(exc).__name__}: {exc}"[:120]))
            continue
        if item is None:
            skipped.append((p.name, "empty or unsupported"))
            continue
        if item["url"] in seen:
            skipped.append((p.name, "duplicate url"))
            continue
        seen.add(item["url"])
        doc = Document(vault_id=vault.id, created_by=owner.id, url=item["url"], title=item["title"],
                       captured_text=item["text"], full_text=item["text"],
                       captured_at=item["captured_at"].astimezone(timezone.utc).replace(tzinfo=None),
                       source_kind="import", source_ref=item["source_ref"], imported_at=now)
        db.add(doc)
        docs.append(doc)
    db.commit()
    print(f"collection {args.name!r} = vault {vault.id}: {len(docs)} documents, {len(skipped)} skipped", flush=True)
    for name, why in skipped[:20]:
        print(f"  skipped {name}: {why}")

    # 2) chunk + sentence embeddings for Ask / sentence search (local MiniLM when EMBEDDING_PROVIDER=local)
    if args.no_embed:
        return
    from .helpers import PREFERENCES, warm_local_encoder

    if getattr(PREFERENCES.models, "embedding_provider", "") == "local":
        warm_local_encoder()
    t0 = time.perf_counter()
    step = 25  # documents per batch: one encode call for their chunks, one for their sentences
    for i in range(0, len(docs), step):
        embed_docs_bulk(docs[i:i + step])
        print(f"  embedded {min(i + step, len(docs))}/{len(docs)} in {time.perf_counter() - t0:.0f} s", flush=True)


def cmd_list(args) -> None:
    from sqlalchemy import func

    from .db import get_db
    from .models import Document, Vault

    db = next(get_db())
    q = (db.query(Vault, func.count(Document.id)).outerjoin(Document, Document.vault_id == Vault.id)
         .filter(Vault.kind == "collection").group_by(Vault.id).order_by(Vault.created_at))
    if args.owner:
        q = q.filter(Vault.owner_id == _owner(db, args.owner).id)
    for v, n in q.all():
        print(f"{v.id}  {v.name}  {n} documents  created {v.created_at:%Y-%m-%d}")


def cmd_delete(args) -> None:
    from .db import get_db
    from .models import Document, Vault

    db = next(get_db())
    v = db.query(Vault).filter(Vault.id == args.vault_id).first()
    if v is None:
        raise SystemExit("no such vault")
    if v.kind != "collection":
        raise SystemExit(f"refusing: vault {v.name!r} is kind {v.kind!r}, not a collection")
    if not args.yes:
        raise SystemExit(f"would delete collection {v.name!r} and all its documents; add --yes")
    # documents.vault_id has no foreign key in the live schema, so delete the documents explicitly
    # (their chunks, sentences and doc vectors cascade from documents)
    n = db.query(Document).filter(Document.vault_id == v.id).delete(synchronize_session=False)
    db.delete(v)  # memberships cascade
    db.commit()
    print(f"removed {n} documents")
    print(f"deleted collection {v.name!r} ({args.vault_id}); its orchard tree file goes on the next sync pass")


def main(argv=None) -> None:
    load_settings()
    p = argparse.ArgumentParser(prog="python -m app.collections", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new", help="create a collection from a folder of files")
    n.add_argument("name")
    n.add_argument("--from", dest="folder", required=True)
    n.add_argument("--owner", default=os.environ.get("VB_OWNER_EMAIL"))
    n.add_argument("--limit", type=int, default=0, help="import only the first N files (testing)")
    n.add_argument("--no-embed", action="store_true", help="skip chunk/sentence embeddings")
    ls = sub.add_parser("list")
    ls.add_argument("--owner", default=None)
    d = sub.add_parser("delete")
    d.add_argument("vault_id")
    d.add_argument("--yes", action="store_true")
    a = p.parse_args(argv)
    {"new": cmd_new, "list": cmd_list, "delete": cmd_delete}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
