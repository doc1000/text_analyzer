"""A MiniLM encoder that stays loaded (same file as services/vb-orchard/src/vb_orchard/resident.py).

orchard.backends.minilm.MiniLMEmbeddingBackend reloads the tokenizer and model from disk on every
encode call that misses its on-disk cache (1-7 s each) and writes one cache file per call. Here the
model is loaded once per process and passed in as ``encode_fn``; the math is the same (pinned
revision, max_length 256, attention-mask mean pooling, deterministic torch), so the vectors match.
"""
from __future__ import annotations

import os
import threading

_lock = threading.Lock()
_loaded = None


def _load():
    global _loaded
    if _loaded is None:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        import torch
        from orchard.backends.minilm import MINILM_MODEL_ID, MINILM_REVISION
        from transformers import AutoModel, AutoTokenizer

        torch.use_deterministic_algorithms(True, warn_only=True)
        tok = AutoTokenizer.from_pretrained(MINILM_MODEL_ID, revision=MINILM_REVISION, local_files_only=True)
        model = AutoModel.from_pretrained(MINILM_MODEL_ID, revision=MINILM_REVISION, local_files_only=True)
        model.eval()
        _loaded = (tok, model)
    return _loaded


def encode(texts, *, batch_size: int = 32, max_length: int = 256):
    import numpy as np
    import torch

    texts = list(texts)
    if not texts:
        return np.zeros((0, 384), dtype=np.float32)
    with _lock:
        tok, model = _load()
        rows = []
        for start in range(0, len(texts), batch_size):
            enc = tok(texts[start:start + batch_size], padding=True, truncation=True,
                      max_length=max_length, return_tensors="pt")
            with torch.no_grad():
                hidden = model(**enc).last_hidden_state
            mask = enc["attention_mask"].unsqueeze(-1).expand(hidden.size()).float()
            pooled = torch.sum(hidden * mask, dim=1) / torch.clamp(mask.sum(dim=1), min=1e-9)
            rows.append(pooled.cpu().numpy().astype(np.float32))
    return np.concatenate(rows, axis=0)


def backend():
    """A MiniLMEmbeddingBackend whose encode() uses the resident model (no per-call reload)."""
    from orchard.backends.minilm import SENTENCE_CONFIG, MiniLMEmbeddingBackend

    b = MiniLMEmbeddingBackend(local_files_only=True)
    b.encode_fn = lambda texts: encode(texts, batch_size=int(SENTENCE_CONFIG["batch_size"]),
                                       max_length=int(SENTENCE_CONFIG["max_length"]))
    _load()
    return b
