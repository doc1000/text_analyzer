"""Deterministic hashed bag-of-words encoder (384-d). Test stand-in for MiniLM only."""
from __future__ import annotations

import hashlib
import re

import numpy as np

_TOK = re.compile(r"[A-Za-z0-9À-￿]{2,}")


class FakeEncoder:
    dim = 384

    def encode(self, texts):
        out = np.zeros((len(texts), self.dim), dtype=np.float64)
        for r, t in enumerate(texts):
            for w in _TOK.findall((t or "").lower()):
                h = int.from_bytes(hashlib.blake2b(w.encode("utf-8"), digest_size=8).digest(), "little")
                out[r, h % self.dim] += 1.0 if (h >> 40) & 1 else -1.0
            out[r, 0] += 1e-3  # never an all-zero row
        return out
