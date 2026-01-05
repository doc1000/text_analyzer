## \app\helpers.py

from typing import List, Optional
import spacy
import os
from openai import OpenAI
import json
import urllib.request
import urllib.error
from .db import get_db
from .config import PREFERENCES
from uuid import UUID
from pydantic import BaseModel
from datetime import datetime
nlp = spacy.load("en_core_web_sm")
from .models import Document, get_or_create_embedding_class

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


def split_long_sentence(sent, max_tokens=MAX_CHARS_PER_CHUNK):
    if len(sent) <= max_tokens:
        return [sent]

    chunks = []
    offset = 0
    while offset < len(sent):
        chunk = sent[offset: offset + max_tokens]  # token-based slice
        chunks.append(chunk)
        offset += max_tokens
    return chunks

def split_doc_sentences(doc, max_tokens=MAX_CHARS_PER_CHUNK):
    for sent in doc.sents:
        s = sent.text.strip()
        if len(s) <= max_tokens:
            yield s
        else:
            # yield sub-spans of the long sentence
            yield from split_long_sentence(s, max_tokens)


def chunk_text(text: str) -> List[str]:
    """
    Very simple sentence-based chunker: walks spaCy sentences
    and groups them up to ~MAX_CHARS_PER_CHUNK.
    """
    doc = nlp(text)
    doc = list(split_doc_sentences(doc, max_tokens=MAX_CHARS_PER_CHUNK))
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0

    for s in doc: #.sents:
        #s = sent.text.strip()
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

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def _openai_chat(prompt: str) -> str:
    model_name = PREFERENCES.models.llm_model
    resp = client.chat.completions.create(
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


def _post_json(url: str, payload: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))

OLLAMA_EMBED_CHAR_LIMIT = int(os.getenv("OLLAMA_EMBED_CHAR_LIMIT", "12000"))

def _clean_embed_input(text: str) -> str:
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    # remove null bytes (common DB artifact)
    text = text.replace("\x00", "")
    # trim whitespace
    text = text.strip()
    # cap size to avoid 400 from oversized inputs
    if len(text) > OLLAMA_EMBED_CHAR_LIMIT:
        text = text[:OLLAMA_EMBED_CHAR_LIMIT]
    return text

def _ollama_embed(text: str) -> list[float]:
    base = PREFERENCES.models.ollama.base_url.rstrip("/")
    model = PREFERENCES.models.ollama.embed_model

    cleaned = _clean_embed_input(text)

    url = f"{base}/api/embed"
    payload = {"model": model, "input": cleaned}

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(f"Ollama embed HTTP {e.code}: {e.reason}. Body: {body[:500]}") from e

    # Response commonly includes "embeddings": [[...]]
    vec = None
    if "embeddings" in data and data["embeddings"]:
        vec = data["embeddings"][0]
    elif "embedding" in data:
        vec = data["embedding"]

    if not isinstance(vec, list) or not vec:
        raise RuntimeError(f"Unexpected Ollama embed response: {data}")

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
    #if len(vec) != EMBED_DIM:
    #    raise ValueError(f"Embedding dim mismatch: got {len(vec)} expected {EMBED_DIM} (model={model_name})")
    return vec

#EMBED_DIM = PREFERENCES.models.embedding_dim  # keep in sync with DB/vector size
EMBED_DIM = len(get_embedding("this is a test"))
EMBED_MODEL = PREFERENCES.models.embedding_model

EMBED_TABLE = get_or_create_embedding_class(
        model_name=EMBED_MODEL,
        version="v1",
        dim=EMBED_DIM,
        db=get_db()
    )

def fill_empty_embed_docs():
    #pull documents that do not have chunks with current embeddings
    #1) pull name of table assigned to current embedding
    #   create table if not exists

    # pull documents that do not have embeddings in batches
    # use ORM syntax
    db_gen = get_db()
    db: Session = next(db_gen)
    subq = (
        db.query(EMBED_TABLE.id)
        .filter(EMBED_TABLE.document_id == Document.id)
        .exists()
    )
    query = (
        db.query(Document.id, Document.full_text)
        .filter(~subq)  # ← find NULL embeddings
        .limit(100)
    )
    while True:
        rows = query.all()
        print(f"number of rows: {len(rows)}")
        if not rows:
            break
        for doc in rows:
            #   create and load embeddings for each document
            #try:
            embed_doc_chunks(doc)
            #except Exception as e:
                

def embed_doc_chunks(doc:Document):
    # create temp table class for structured loading
    db_gen = get_db()
    db: Session = next(db_gen)
    # 2) Chunk full text
    chunks = chunk_text(doc.full_text)

    # 3) For each chunk, compute embedding and create Chunk row
    for idx, chunk_text_value in enumerate(chunks):
        print(f"chunk {idx} length: {len(chunk_text_value)}")
        try:
            embedding = get_embedding(chunk_text_value)
            chunk = EMBED_TABLE(
                document_id=doc.id,
                chunk_index=idx,
                chunk_text=chunk_text_value,
                embedding=embedding
            )
            db.add(chunk)
        except Exception as e: #NotImplementedError:
            embedding = None  # let you develop embeddings later
            # Continue on error; you can change to `raise` if you prefer strict fail-fast
            preview = (doc.full_text or "")[:200].replace("\n", " ")
            print(f"[migrate_embeddings][WARN] chunk {doc.id} failed: {e} (len={len(doc.full_text or '')}) preview='{preview}'")

    # Commit everything
    db.commit()
    return len(chunks)


def new_embedding_model_example():
    # Example: after containers are up and DB is reachable
    from .db import engine
    from .models import get_or_create_embedding_class

    meta = get_or_create_embedding_class(
        model_name="bge-small-en",
        version="v1",
        dim=512,
        db = get_db
    )

    print("Created/registered embedding table at:", meta.table_location)







