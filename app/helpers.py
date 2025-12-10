## \app\helpers.py

from typing import List
import spacy
import os
from openai import OpenAI
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


EMBED_DIM = 1536  # keep in sync with DB/vector size

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def get_embedding(text: str)  -> List[float]:
    """
    Calls OpenAI's embedding model and returns a 1536-dimensional vector.
    """
    response = client.embeddings.create(
        model="text-embedding-3-small",   # or "text-embedding-3-large"
        input=text
    )
    return response.data[0].embedding

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

    resp = client.chat.completions.create(
        model="gpt-4.1-nano", #"gpt-4.1-mini",
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content.strip()
