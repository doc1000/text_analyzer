"""Hopper actions (batch28): work on a hand-picked set of documents from ONE vault.

    POST /hopper/summarize  {vault_id, doc_ids, focus?}     -> {summary, llm, items}
    POST /hopper/bundle     {vault_id, doc_ids, max_chars?} -> {markdown, filename, items}
    POST /hopper/save       {vault_id, doc_ids, name}       -> {vault_id, name, copied}
    POST /hopper/promote    {vault_id, doc_ids}             -> {vault_id, copied, skipped}

The hopper itself lives in the browser (v1). Every call checks access to the source vault and that
all documents belong to it, so a hopper never mixes vaults.

- save: a new collection (mini-vault) owned by the caller, with copies of the items
  (source_kind = 'copy', pointing at the originals) and their embeddings; it gets its own tree.
- promote: copies collection items into the caller's personal vault (source_kind = 'promoted'),
  skipping URLs already there. Only from a collection; the copy is a snapshot, later changes to the
  original do not flow into it (VB_VAULTS_SOURCES_COLLECTIONS.md).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import models
from .auth import get_current_user, require_vault_access
from .db import get_db
from .models import Document, Vault, VaultMembership

log = logging.getLogger(__name__)
router = APIRouter(prefix="/hopper", tags=["hopper"])
bearer_scheme = HTTPBearer(auto_error=False)
MAX_ITEMS = 200


class HopperRequest(BaseModel):
    vault_id: str
    doc_ids: List[str] = Field(..., min_length=1)
    focus: Optional[str] = None       # summarize: optional question/angle
    max_chars: int = 6000             # bundle: text per item
    name: Optional[str] = None        # save: name of the new collection


# ---------------------------------------------------------------- helpers
def _vault_kind(v: Vault) -> str:
    return getattr(v, "kind", None) or ("personal" if v.is_personal else "shared")


def _load(db: Session, user, req: HopperRequest, min_role: str = "viewer"):
    """The source vault and its documents, in the requested order; 400/403/404 otherwise."""
    if len(req.doc_ids) > MAX_ITEMS:
        raise HTTPException(status_code=400, detail=f"at most {MAX_ITEMS} items")
    try:
        vid = UUID(req.vault_id)
        ids = [UUID(d) for d in dict.fromkeys(req.doc_ids)]
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid vault_id or doc_ids")
    require_vault_access(user, vid, db, min_role=min_role)
    vault = db.query(Vault).filter(Vault.id == vid).first()
    if vault is None:
        raise HTTPException(status_code=404, detail="vault not found")
    docs = {d.id: d for d in db.query(Document).filter(Document.id.in_(ids)).all()}
    if any(d.vault_id != vid for d in docs.values()) or len(docs) != len(ids):
        raise HTTPException(status_code=400, detail="all hopper items must exist and belong to the given vault")
    return vault, [docs[i] for i in ids]


def _text(d: Document) -> str:
    return (d.captured_text or d.full_text or d.extracted_text or "").strip()


def _summaries(db: Session, ids) -> dict:
    """Stored document-level summaries (embedding doc table), where they exist."""
    from .db import DOCUMENT_TABLE

    rows = (db.query(DOCUMENT_TABLE.document_id, DOCUMENT_TABLE.summary_text)
            .filter(DOCUMENT_TABLE.document_id.in_(ids)).all())
    return {r[0]: r[1] for r in rows if r[1]}


def _opening(t: str, n_sent: int = 2, max_chars: int = 400) -> str:
    t = re.sub(r"^#.*$", "", t, flags=re.M)            # drop Markdown headings
    t = re.sub(r"\s+", " ", t).strip()
    parts = re.split(r"(?<=[.!?])\s+", t)
    return " ".join(parts[:n_sent])[:max_chars].strip()


def _chat(prompt: str) -> str:
    from .config import PREFERENCES
    from .helpers import _ollama_chat, _openai_chat

    if getattr(PREFERENCES.models, "chat_provider", "openai") == "ollama":
        return _ollama_chat(prompt)
    return _openai_chat(prompt)


def _item(d: Document) -> dict:
    return {"doc_id": str(d.id), "title": d.title or "(no title)", "url": d.url}


# ---------------------------------------------------------------- summarize
@router.post("/summarize")
def summarize(req: HopperRequest, db: Session = Depends(get_db), user: models.User = Depends(get_current_user),
              _: HTTPAuthorizationCredentials = Depends(bearer_scheme)):
    vault, docs = _load(db, user, req)
    sums = _summaries(db, [d.id for d in docs])
    budget = 24000
    per = max(600, budget // max(1, len(docs)))
    blocks = []
    for n, d in enumerate(docs, 1):
        body = sums.get(d.id) or _text(d)[:per]
        blocks.append(f"[{n}] {d.title or '(no title)'}\n{body[:per]}")
    focus = (req.focus or "").strip()
    prompt = (
        "You are summarising a hand-picked set of saved documents for their owner.\n"
        "Write a short synthesis: the main themes across the set, where the items agree or differ, and "
        "anything notable. Refer to items by their [number]. Use only the material below.\n"
        + (f"Focus on: {focus}\n" if focus else "")
        + "\n" + "\n\n".join(blocks)
    )
    try:
        summary, llm = _chat(prompt), True
    except Exception as exc:  # noqa: BLE001 - provider down or key rejected: extractive fallback
        log.warning("hopper summarize: chat failed: %s", type(exc).__name__)
        lines = [f"[{n}] {d.title or '(no title)'}: {_opening(sums.get(d.id) or _text(d))}" for n, d in enumerate(docs, 1)]
        summary = (f"(No AI summary: the chat provider failed with {type(exc).__name__}. Opening lines instead.)\n\n"
                   + "\n".join(lines))
        llm = False
    return {"summary": summary, "llm": llm, "vault_id": str(vault.id), "items": [_item(d) for d in docs]}


# ---------------------------------------------------------------- bundle (Copy for Claude)
@router.post("/bundle")
def bundle(req: HopperRequest, db: Session = Depends(get_db), user: models.User = Depends(get_current_user),
           _: HTTPAuthorizationCredentials = Depends(bearer_scheme)):
    vault, docs = _load(db, user, req)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    max_chars = max(500, min(req.max_chars, 50000))
    out = [f"# Hopper: {len(docs)} item{'s' if len(docs) != 1 else ''} from \"{vault.name}\"",
           f"Exported from Vault Bubbles {now}. Each item has its title, link, provenance and text"
           f" (cut at {max_chars} characters).", ""]
    for n, d in enumerate(docs, 1):
        t = _text(d)
        cut = len(t) > max_chars
        captured = d.captured_at.strftime("%Y-%m-%d") if d.captured_at else "unknown"
        tags = ", ".join(d.user_tags or [])
        out += [f"## [{n}] {d.title or '(no title)'}",
                f"- URL: {d.url}",
                f"- Captured: {captured}; source: {d.source_kind or 'extension'}"
                + (f" ({d.source_ref})" if d.source_ref else "") + (f"; tags: {tags}" if tags else ""),
                "", t[:max_chars] + (" [...]" if cut else ""), ""]
    slug = re.sub(r"[^a-z0-9]+", "-", (vault.name or "vault").lower()).strip("-")[:40] or "vault"
    return {"markdown": "\n".join(out), "filename": f"hopper-{slug}-{datetime.now():%Y%m%d-%H%M}.md",
            "items": [_item(d) for d in docs]}


# ---------------------------------------------------------------- copies (save / promote)
def _copy_documents(db: Session, docs, dest_vault_id, user_id, source_kind: str) -> list:
    """Copy documents with their chunk/sentence/document embeddings into dest; returns new ids.
    One transaction; the caller commits. Copies point back at the originals (provenance)."""
    from .db import DOCUMENT_TABLE, EMBED_TABLE, SENTENCE_TABLE

    chunk_t = EMBED_TABLE.__table__.fullname
    sent_t = SENTENCE_TABLE.__table__.fullname
    doc_t = DOCUMENT_TABLE.__table__.fullname
    now = datetime.now(timezone.utc)
    new_ids = []
    for d in docs:
        nid = uuid4()
        db.add(Document(id=nid, vault_id=dest_vault_id, created_by=user_id, url=d.url, title=d.title,
                        captured_text=d.captured_text, extracted_text=d.extracted_text, full_text=d.full_text,
                        captured_at=d.captured_at, user_tags=list(d.user_tags or []),
                        source_kind=source_kind, source_ref=d.source_ref,
                        source_vault_id=d.vault_id, source_document_id=d.id, imported_at=now))
        db.flush()
        db.execute(text(f"""
            CREATE TEMP TABLE IF NOT EXISTS _hop_chunk_map (old_id uuid, new_id uuid) ON COMMIT DROP;
            DELETE FROM _hop_chunk_map;
            INSERT INTO _hop_chunk_map SELECT id, gen_random_uuid() FROM {chunk_t} WHERE document_id = :old;
            INSERT INTO {chunk_t} (id, document_id, chunk_index, chunk_text, embedding, created_at, summary_text)
              SELECT m.new_id, :new, c.chunk_index, c.chunk_text, c.embedding, c.created_at, c.summary_text
              FROM {chunk_t} c JOIN _hop_chunk_map m ON m.old_id = c.id;
            INSERT INTO {sent_t} (id, chunk_id, sent_index, sent_text, embedding, created_at)
              SELECT gen_random_uuid(), m.new_id, s.sent_index, s.sent_text, s.embedding, s.created_at
              FROM {sent_t} s JOIN _hop_chunk_map m ON m.old_id = s.chunk_id;
            INSERT INTO {doc_t} (id, document_id, summary_text, embedding, created_at)
              SELECT gen_random_uuid(), :new, summary_text, embedding, created_at FROM {doc_t} WHERE document_id = :old;
        """), {"old": d.id, "new": nid})
        new_ids.append(nid)
    return new_ids


@router.post("/save")
def save_as_collection(req: HopperRequest, db: Session = Depends(get_db), user: models.User = Depends(get_current_user),
                       _: HTTPAuthorizationCredentials = Depends(bearer_scheme)):
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    src, docs = _load(db, user, req)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    vault = Vault(name=name[:200], owner_id=user.id, is_personal=False, kind="collection", created_at=now)
    db.add(vault)
    db.flush()
    db.add(VaultMembership(vault_id=vault.id, user_id=user.id, role="owner", created_at=now))
    new_ids = _copy_documents(db, docs, vault.id, user.id, "copy")
    db.commit()
    return {"vault_id": str(vault.id), "name": vault.name, "kind": "collection", "copied": len(new_ids),
            "source_vault_id": str(src.id)}


@router.post("/promote")
def promote_to_personal(req: HopperRequest, db: Session = Depends(get_db), user: models.User = Depends(get_current_user),
                        _: HTTPAuthorizationCredentials = Depends(bearer_scheme)):
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    src, docs = _load(db, user, req)
    if _vault_kind(src) != "collection":
        raise HTTPException(status_code=400, detail="promotion copies items from a collection into your vault")
    personal = (db.query(Vault).join(VaultMembership, VaultMembership.vault_id == Vault.id)
                .filter(VaultMembership.user_id == user.id, VaultMembership.role == "owner",
                        Vault.kind == "personal", Vault.archived_at == None)  # noqa: E711
                .order_by(Vault.created_at.asc()).first())
    if personal is None:
        raise HTTPException(status_code=404, detail="no personal vault")
    have = {u for (u,) in db.query(Document.url).filter(Document.vault_id == personal.id,
                                                        Document.url.in_([d.url for d in docs])).all()}
    todo = [d for d in docs if d.url not in have]
    new_ids = _copy_documents(db, todo, personal.id, user.id, "promoted")
    db.commit()
    return {"vault_id": str(personal.id), "copied": len(new_ids),
            "skipped": [{**_item(d), "reason": "URL already in your vault"} for d in docs if d.url in have]}
