# save as app/main.py
## IMPORTS
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import asyncio
from pydantic import BaseModel
import spacy
import gzip
import uvicorn
import os
from datetime import datetime
from typing import List
import numpy as np
from sqlalchemy.orm import Session
from sqlalchemy import asc, func, text
#Internal imports
from .db import init_db, get_db, EMBED_TABLE
from .schemas import IngestPayload, DocumentDetailResponse
from . import models
from .models import Document
from .helpers import (get_embedding,embed_doc_chunks,
    _answer_from_hits,DocumentOut, QueryRequest, ChunkHit,
    QueryResponse, fill_empty_embed_docs
)
from .topics import (
    TopicsResponse,
    build_topics_hierarchy,
    get_topics_with_cache,
    clear_topics_cache,
)
from .config import PREFERENCES



nlp = spacy.load("en_core_web_sm")

async def _fill_embeddings_async():
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        fill_empty_embed_docs,
    )

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    asyncio.create_task(_fill_embeddings_async())
    yield

app = FastAPI(lifespan=lifespan)

# Allow extension + localhost
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # for dev, you can later restrict this
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/test-db")
def test_db(db: Session = Depends(get_db)):
    # quick sanity query
    result = db.execute(text('SELECT 1;'))
    return {"db_ok": bool(list(result))}

class Input(BaseModel):
    text: str

@app.post("/analyzer")
def analyzer(data: Input):
    text = data.text
    doc = nlp(text)
    
    # Lexical density
    content_words = [t for t in doc if t.pos_ in ["NOUN","VERB","ADJ","ADV"]]
    lexical_density = len(content_words) / len([t for t in doc if t.is_alpha]) if text else 0

    # Specificity (rough)
    numbers = sum(1 for t in doc if t.like_num)
    entities = len(doc.ents)
    sents = list(doc.sents)
    specificity = min(1.0, (numbers + entities) / (len(sents) + 1))

    # Compression ratio
    compressed = len(gzip.compress(text.encode("utf-8")))
    #compressed = len(zlib.compress(text.encode("utf-8")))
    ratio = len(text.encode("utf-8")) / compressed if compressed else 1
    
    # Combine scores (weights adjustable)
    score = (
        lexical_density * 40 +
        specificity * 40 +
        ratio * 20
    )
    score = min(100, score)

    return {
        "score": score,
        "lexical_density": lexical_density,
        "specificity": specificity,
        "compression_ratio": ratio,
        "infoScore": score,
        "aiScore": ratio
    }

from . import models

@app.post("/ingest")
def ingest(payload: IngestPayload, db: Session = Depends(get_db)):
    # Use captured_at from payload if provided, else now
    captured_at = payload.captured_at or datetime.utcnow()

    doc = models.Document(
        url=payload.url,
        title=payload.title,
        full_text=payload.text,
        score_info=payload.score_info,
        score_ai_slop=payload.score_ai_slop,
        captured_at=captured_at,
    )

    db.add(doc)
    #db.flush() # get doc.id without committing yet
    db.commit()

    chunk_len = embed_doc_chunks(doc)
    #db.commit()

    return {
        "status": "ok",
        "document_id": str(doc.id),
        "num_chunks": int(chunk_len),
    }

@app.get("/documents", response_model=List[DocumentOut])
def list_documents(
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    docs = (
        db.query(Document)
        .order_by(Document.captured_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return docs


@app.post("/query", response_model=QueryResponse)
def query_docs(payload: QueryRequest, db: Session = Depends(get_db)):
    # 1) Embed the query
    q_emb = get_embedding(payload.query)
    # Convert to numpy array for pgvector compatibility
    q_emb = np.array(q_emb, dtype=np.float32)

    # 2) cosine distance → similarity
    # Use pgvector's cosine distance operator (<=>) directly
    # cosine_distance returns distance (0=same, 1=opposite), so similarity = 1 - distance
    similarity_expr = 1 - EMBED_TABLE.embedding.cosine_distance(q_emb)

    # base query: chunks with non-null embeddings joined to documents
    base_query = (
        db.query(EMBED_TABLE, Document, similarity_expr.label("similarity"))
        .join(Document, EMBED_TABLE.document_id == Document.id)
        .filter(EMBED_TABLE.embedding != None)  # ← ignore NULL embeddings
    )

    # 2b) Optional scoping by document IDs
    if payload.doc_ids:
        # Convert string UUIDs to UUID objects for proper filtering
        # Filter out invalid UUIDs gracefully
        from uuid import UUID
        uuid_list = []
        invalid_ids = []
        for doc_id in payload.doc_ids:
            try:
                uuid_list.append(UUID(doc_id))
            except (ValueError, TypeError):
                invalid_ids.append(doc_id)
        
        if invalid_ids:
            # Log warning but continue with valid UUIDs
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Invalid UUIDs in doc_ids, ignoring: {invalid_ids}")
        
        if uuid_list:
            base_query = base_query.filter(Document.id.in_(uuid_list))

    rows = (
        base_query
        .order_by(similarity_expr.desc())
        .limit(payload.top_k)
        .all()
    )

    hits: List[ChunkHit] = []
    for chunk, doc, similarity in rows:
        if similarity is None:
            # extra safety, shouldn't hit if filter above works
            continue

        hits.append(
            ChunkHit(
                document_id=str(doc.id),
                document_title=doc.title,
                url=doc.url,
                score_info=doc.score_info,
                score_ai_slop=doc.score_ai_slop,
                chunk_index=chunk.chunk_index,
                chunk_text=chunk.chunk_text,
                similarity=float(similarity),
            )
        )

    answer = None
    if payload.with_answer and hits:
        answer = _answer_from_hits(payload.query, hits)

    return QueryResponse(answer=answer, hits=hits)


# Path: /code/app/static inside container
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
#print("STATIC DIR:", STATIC_DIR)  # optional debug

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/topics", response_model=TopicsResponse)
def get_topics(days: int = 10, db: Session = Depends(get_db)):
    """
    Return hierarchical topics for documents in the last `days` days.
    """
    return get_topics_with_cache(db, days=days)

@app.get("/topics/hierarchy")
def get_topics_hierarchy(days: int = 10, db: Session = Depends(get_db)):
    """
    Return a D3-friendly hierarchy representation of topics and subtopics
    for the last `days` days of documents.
    """
    topics_resp = get_topics_with_cache(db, days=days)
    return build_topics_hierarchy(topics_resp)

@app.get("/topics/clear_cache")
def clear_cache():
    clear_topics_cache()

@app.get("/documents/{document_id}", response_model=DocumentDetailResponse)
def get_document_detail(document_id: str, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Reconstruct full captured text from chunks (ordered)
    chunks = (
        db.query(EMBED_TABLE)
        .filter(EMBED_TABLE.document_id == doc.id)
        .order_by(asc(EMBED_TABLE.chunk_index))
        .all()
    )
    full_text = "\n\n".join([c.chunk_text for c in chunks]) if chunks else ""

    return DocumentDetailResponse(
        id=str(doc.id),
        url=doc.url,
        title=doc.title,
        captured_at=doc.captured_at,
        score_info=doc.score_info,
        score_ai_slop=doc.score_ai_slop,
        text=full_text,
    )

@app.get("/model_configs")
def get_embedding_length(prompt: str = "this is a test"):
    """
    return embedding length
    """
    emb = len(get_embedding(prompt))
    return {"len": emb,
             "embed model":PREFERENCES.models.embedding_model,
            "chat model": PREFERENCES.models.llm_model}

if __name__ == "__main__":
	#pip install -r requirements.txt
	uvicorn.run(app, host="127.0.0.1", port=8000)
