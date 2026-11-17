"""Item ids and URL normalization (spec A8)."""
from __future__ import annotations

import hashlib
from urllib.parse import urlsplit, urlunsplit


def normalize_url(url: str) -> str:
    """Lowercase scheme+host, strip fragment, strip trailing '/' on a path-only URL."""
    url = (url or "").strip()
    if not url:
        return ""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path
    if path == "/" and not parts.query:
        path = ""
    if not scheme and not netloc:
        return url.split("#", 1)[0]
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def capture_item_id(url: str) -> str:
    return "vb:" + hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()[:16]
