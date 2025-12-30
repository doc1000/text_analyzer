## \app\helpers.py

from typing import List, Optional
import spacy
import os
from openai import OpenAI
import json
import urllib.request
from .config import PREFERENCES
from uuid import UUID
from pydantic import BaseModel
from datetime import datetime
nlp = spacy.load("en_core_web_sm")

MAX_CHARS_PER_CHUNK = 1000  # tune this as you like

## BaseModel Classes - could be moved
## DocumentOut, QueryRequest, ChunkHit, QueryResponse
class DocumentOut(BaseModel):
    id: UUID
    url: str
    title: str | None
    score_info: float | None
    score_ai_slop: float | None
    captured_at: datetime

    class Config:
        #orm_mode = True
        from_attributes = True
        #model_config = ConfigDict(from_attributes=True)


class QueryRequest(BaseModel):
    query: str
    top_k: int = 5
    with_answer: bool = True
    doc_ids: Optional[List[str]] = None


class ChunkHit(BaseModel):
    document_id: str
    document_title: str | None
    url: str
    score_info: float | None
    score_ai_slop: float | None
    chunk_index: int
    chunk_text: str
    similarity: float


class QueryResponse(BaseModel):
    answer: str | None
    hits: List[ChunkHit]

def chunk_text(text: str) -> List[str]:
    """
    Very simple sentence-based chunker: walks spaCy sentences
    and groups them up to ~MAX_CHARS_PER_CHUNK.
    """
    doc = nlp(text)
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0

    for sent in doc.sents:
        s = sent.text.strip()
        if not s:
            continue

        if current_len + len(s) > MAX_CHARS_PER_CHUNK and current:
            chunks.append(" ".join(current))
            current = [s]
            current_len = len(s)
        else:
            current.append(s)
            current_len += len(s) + 1

    if current:
        chunks.append(" ".join(current))

    return chunks


EMBED_DIM = PREFERENCES.models.embedding_dim  # keep in sync with DB/vector size

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def _openai_chat(prompt: str) -> str:
    model_name = PREFERENCES.models.llm_model
    resp = _client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content.strip()

def _ollama_chat(prompt: str) -> str:
    base = PREFERENCES.models.ollama.base_url.rstrip("/")
    model = PREFERENCES.models.ollama.chat_model
    url = f"{base}/api/chat"

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    # Ollama returns message content under data["message"]["content"]
    return (data.get("message", {}) or {}).get("content", "").strip()

def _ollama_embed(text: str) -> List[float]:
    base = PREFERENCES.models.ollama.base_url.rstrip("/")
    model = PREFERENCES.models.ollama.embed_model
    url = f"{base}/api/embeddings"
    payload = {"model": model, "prompt": text}

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    vec = data["embedding"]

    if len(vec) != EMBED_DIM:
        raise ValueError(f"Embedding dim mismatch: got {len(vec)} expected {EMBED_DIM} (model={model})")

    return vec

def get_embedding(text: str) -> List[float]:
    """
    Get a single embedding vector for a text using the configured provider.
    """
    if getattr(PREFERENCES.models, "provider", "openai") == "ollama":
        return _ollama_embed(text)

    # OpenAI fallback (your existing behavior)
    model_name = PREFERENCES.models.embedding_model
    resp = client.embeddings.create(model=model_name, input=text)
    vec = resp.data[0].embedding
    if len(vec) != EMBED_DIM:
        raise ValueError(f"Embedding dim mismatch: got {len(vec)} expected {EMBED_DIM} (model={model_name})")
    return vec

def _answer_from_hits(query: str, hits: List[ChunkHit]) -> str:
    context = "\n\n".join(
        f"Source {i+1} ({h.url}):\n{h.chunk_text}"
        for i, h in enumerate(hits)
    )
    prompt = (
        "You are a helpful assistant. Using ONLY the context below, "
        "answer the user's question concisely.\n\n"
        f"Question: {query}\n\n"
        f"Context:\n{context}"
    )

    model_provider = getattr(PREFERENCES.models, "provider", "openai")

    if model_provider == "ollama":
        text = _ollama_chat(prompt)
    else:
        text = _openai_chat(prompt)
    return text


