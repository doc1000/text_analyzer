"""Data copies for the overnight run (spec 6.3, D4). Pure file readers; no DB, no network."""
from __future__ import annotations

import csv
import hashlib
import html
import random
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .capture import to_utc_z
from .ids import capture_item_id, normalize_url

_FM = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.S)
_BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)


def _split_front_matter(raw: str):
    m = _FM.match(raw)
    if not m:
        return {}, raw
    try:
        import yaml
        meta = yaml.safe_load(m.group(1)) or {}
    except Exception:  # noqa: BLE001 - minimal fallback parser
        meta = {}
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip().strip("'\"")
    return meta, raw[m.end():]


def _first_bold_sentence(body: str) -> str:
    m = _BOLD.search(body)
    if not m:
        return ""
    return " ".join(m.group(1).split())


def load_forte_copy(dir) -> list:
    """Read <dir>/posts/*.md (YAML front matter + body) into VB document dicts."""
    root = Path(dir)
    docs = []
    for f in sorted((root / "posts").glob("*.md")):
        meta, body = _split_front_matter(f.read_text(encoding="utf-8"))
        url = str(meta.get("source_url") or "").strip()
        if not url:
            continue
        title = html.unescape(str(meta.get("title") or "")).strip()
        body = body.strip()
        captured = to_utc_z(meta.get("published")) or to_utc_z(meta.get("retrieved_at"))
        cat = meta.get("source_category")
        highlight = _first_bold_sentence(body)
        docs.append({
            "item_id": capture_item_id(url),
            "title": title,
            "text": body,
            "metadata": {
                "url": url,
                "captured_at": captured,
                "updated_at": captured,
                "highlights": [highlight] if highlight else [],
                "notes": "",
                "tags": [str(cat)] if cat else [],
                "source": "forte-copy",
            },
        })
    return docs


_PG_TS = re.compile(r"^(.*[+-]\d\d)$")


def _pg_ts(value: str) -> Optional[str]:
    v = (value or "").strip()
    if not v:
        return None
    if _PG_TS.match(v):  # postgres "+00" offsets
        v += ":00"
    return to_utc_z(v.replace(" ", "T", 1))


def load_pg_csv(path) -> list:
    """Read a human-made `\\copy (SELECT id,url,title,full_text,captured_at FROM documents) TO ... CSV HEADER`."""
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
    docs = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            url = (row.get("url") or "").strip()
            captured = _pg_ts(row.get("captured_at"))
            docs.append({
                "item_id": "vb:" + str(row["id"]).strip(),
                "title": (row.get("title") or "").strip(),
                "text": row.get("full_text") or "",
                "metadata": {
                    "url": url,
                    "captured_at": captured,
                    "updated_at": captured,
                    "highlights": [],
                    "notes": "",
                    "tags": [],
                    "source": "vaultbubbles",
                },
            })
    return docs


def synthetic_scale(docs, n: int = 10000, seed: int = 20261116) -> list:
    """n perturbed copies of docs, captured_at spread over the 2 years ending 2026-11-16."""
    if not docs:
        return []
    rng = random.Random(seed)
    end = datetime(2026, 11, 16, tzinfo=timezone.utc)
    span = 730 * 86400
    out = []
    for i in range(n):
        src = docs[i % len(docs)]
        words = src["text"].split()
        keep = [w for w in words if rng.random() > 0.08]
        if len(keep) > 20 and rng.random() < 0.5:  # swap two blocks
            cut = rng.randrange(5, len(keep) - 5)
            keep = keep[cut:] + keep[:cut]
        text = " ".join(keep)
        ts = end - timedelta(seconds=rng.randrange(span))
        url = f"{src['metadata']['url'].rstrip('/')}/syn-{i}"
        md = dict(src["metadata"])
        md.update({
            "url": url,
            "captured_at": to_utc_z(ts),
            "updated_at": to_utc_z(ts),
            "highlights": list(md.get("highlights") or []),
            "tags": list(md.get("tags") or []),
            "source": "synthetic",
        })
        out.append({
            "item_id": "vb:" + hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()[:16],
            "title": f"{src['title']} ({i})" if i >= len(docs) else src["title"],
            "text": text,
            "metadata": md,
        })
    return out
