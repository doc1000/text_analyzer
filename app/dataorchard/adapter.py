"""packed_tree (spec 6.2) -> VB /topics/hierarchy shape (spec A10; app/topics.py build_hierarchical_topics_for_d3).

VB node fields: root {name,time_range_days,children,type:"root"};
topic {name,topic_id,level,level_name,summary,doc_count,children,size,type:"topic"};
document {name,doc_id,url,captured_at,size:1,type:"document"}.
`level` counts from the leaves: a cluster holding items directly is 0, its parent 1, etc.
"""
from __future__ import annotations

from typing import Optional

_LEVEL_NAMES = {0: "Fine", 1: "Topic", 2: "Category"}


def _doc_id(item_id: Optional[str]) -> Optional[str]:
    if item_id is None:
        return None
    return item_id[3:] if item_id.startswith("vb:") else item_id


def _height(node: dict) -> int:
    kids = [c for c in node.get("children") or [] if c.get("type") != "item"]
    return 0 if not kids else 1 + max(_height(c) for c in kids)


def _doc(node: dict) -> dict:
    return {
        "name": node.get("name") or "(no title)",
        "doc_id": _doc_id(node.get("item_id")),
        "url": node.get("url"),
        "captured_at": node.get("captured_at"),
        "size": 1,
        "type": "document",
    }


def _topic(node: dict) -> Optional[dict]:
    kids = []
    for c in node.get("children") or []:
        if c.get("type") == "item":
            kids.append(_doc(c))
        else:
            sub = _topic(c)
            if sub is not None:
                kids.append(sub)
    clusters = [k for k in kids if k["type"] == "topic"]
    docs = [k for k in kids if k["type"] == "document"]
    kids = clusters + docs  # VB order: topic children first, then documents
    total = sum(k["doc_count"] for k in clusters) + len(docs)
    if total == 0:
        return None
    level = _height(node)
    return {
        "name": node.get("title") or node.get("name") or f"Cluster {node.get('node_key')}",
        "topic_id": node.get("node_key"),
        "level": level,
        "level_name": _LEVEL_NAMES.get(level, ""),
        "summary": node.get("summary"),
        "doc_count": total,
        "children": kids or None,
        "size": total if not kids else None,
        "type": "topic",
    }


def to_vb_hierarchy(packed: dict, days) -> dict:
    root = {"name": "Topics", "time_range_days": days, "children": [], "type": "root"}
    top_docs = []
    for c in (packed or {}).get("children") or []:
        if c.get("type") == "item":
            top_docs.append(_doc(c))
        else:
            t = _topic(c)
            if t is not None:
                root["children"].append(t)
    if top_docs:  # items hanging directly under the cut root (spec A6)
        root["children"].append({
            "name": f"Uncategorized ({len(top_docs)})",
            "topic_id": "uncategorized",
            "level": -1,
            "level_name": "Uncategorized",
            "summary": "Documents without topic assignment",
            "doc_count": len(top_docs),
            "children": top_docs,
            "type": "topic",
        })
    return root
