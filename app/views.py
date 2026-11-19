"""Alternative views over one vault for the bubble map (batch31), in the /topics/hierarchy shape.

- projects_tree: todo items grouped by +project, then Open / Done
- prune_by_source: the topic tree with only documents of the given source kinds
"""
from __future__ import annotations

from datetime import datetime, timedelta

SOURCE_GROUPS = {
    "captures": ["extension", "import", "copy", "promoted"],
    "vault_files": ["vault_file"],
    "todos": ["todo"],
}


def kinds_for(sources: str | None) -> list | None:
    """'captures,todos' -> source kinds; None or 'all' -> no filter."""
    if not sources or sources == "all":
        return None
    out = []
    for s in sources.split(","):
        s = s.strip()
        out += SOURCE_GROUPS.get(s, [s] if s else [])
    return out or None


def _doc_node(d) -> dict:
    return {"name": d.title or "Untitled", "doc_id": str(d.id), "url": d.url,
            "captured_at": d.captured_at.isoformat() + "Z" if d.captured_at else None,
            "size": 1, "type": "document"}


def projects_tree(db, vault_id, days: int | None) -> dict:
    from .models import Document

    q = db.query(Document.id, Document.title, Document.url, Document.captured_at, Document.source_meta,
                 Document.source_status).filter(Document.vault_id == vault_id, Document.source_kind == "todo")
    if days and days < 36500:
        q = q.filter(Document.captured_at >= datetime.utcnow() - timedelta(days=days))
    groups: dict = {}
    for d in q.all():
        meta = d.source_meta or {}
        status = "done" if meta.get("status") == "done" else "open"
        for p in (meta.get("projects") or ["(no project)"]):
            groups.setdefault(p, {"open": [], "done": []})[status].append(d)
    children = []
    for p, g in sorted(groups.items(), key=lambda kv: (-len(kv[1]["open"]), kv[0].lower())):
        sub = []
        for status, label in (("open", "Open"), ("done", "Done")):
            docs = sorted(g[status], key=lambda d: d.captured_at or datetime.min, reverse=True)
            if docs:
                sub.append({"name": f"{label} ({len(docs)})", "topic_id": f"project:{p}:{status}", "level": 0,
                            "level_name": label, "summary": f"{len(docs)} {label.lower()} item(s) in +{p}",
                            "doc_count": len(docs), "children": [_doc_node(d) for d in docs],
                            "size": None, "type": "topic"})
        n = len(g["open"]) + len(g["done"])
        children.append({"name": f"+{p}" if p != "(no project)" else p, "topic_id": f"project:{p}", "level": 1,
                         "level_name": "Project", "summary": f"{len(g['open'])} open, {len(g['done'])} done",
                         "doc_count": n, "children": sub, "size": None, "type": "topic"})
    return {"name": "Projects", "type": "root", "time_range_days": days, "children": children,
            "source": "projects", "vault_id": str(vault_id)}


def prune_by_source(db, tree: dict, kinds: list) -> dict:
    """Drop document leaves whose source kind is not in kinds; drop topics left empty; recount."""
    from .models import Document

    ids = []

    def collect(n):
        if n.get("type") == "document" and n.get("doc_id"):
            ids.append(n["doc_id"])
        for c in n.get("children") or []:
            collect(c)
    collect(tree)
    keep = {str(i) for (i,) in db.query(Document.id).filter(Document.id.in_(ids), Document.source_kind.in_(kinds))} if ids else set()

    def prune(n):
        if n.get("type") == "document":
            return n if n.get("doc_id") in keep else None
        kids = [k for k in (prune(c) for c in n.get("children") or []) if k is not None]
        if not kids and n.get("type") != "root":
            return None
        out = dict(n, children=kids)
        if "doc_count" in n:
            out["doc_count"] = sum(1 if k.get("type") == "document" else k.get("doc_count", 0) for k in kids)
        return out
    return prune(tree) or dict(tree, children=[])
