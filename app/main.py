# save as app/main.py
from fastapi import FastAPI, Depends
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import spacy
#import zlib
import gzip
import math
import uvicorn
import os
from sqlalchemy.orm import Session
from sqlalchemy import select
from .db import SessionLocal, init_db
from .schemas import IngestPayload
from . import models
from .models import Chunk, Document
from .helpers import chunk_text, get_embedding, EMBED_DIM, _answer_from_hits
from .helpers import DocumentOut, QueryRequest, ChunkHit, QueryResponse
from datetime import datetime
from typing import List
from fastapi.staticfiles import StaticFiles


nlp = spacy.load("en_core_web_sm")

app = FastAPI()

init_db()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

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
    result = db.execute("SELECT 1;")
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
    db.flush() # get doc.id without committing yet

    # 2) Chunk full text
    chunks = chunk_text(payload.text)

    # 3) For each chunk, compute embedding and create Chunk row
    for idx, chunk_text_value in enumerate(chunks):
        try:
            embedding = get_embedding(chunk_text_value)
        except NotImplementedError:
            embedding = None  # let you develop embeddings later

        chunk = Chunk(
            document_id=doc.id,
            chunk_index=idx,
            chunk_text=chunk_text_value,
            embedding=embedding,
            score_info=payload.score_info,
            score_ai_slop=payload.score_ai_slop,
        )
        db.add(chunk)
    # Commit everything
    db.commit()
    db.refresh(doc)

    return {
        "status": "ok",
        "document_id": str(doc.id),
        "num_chunks": len(chunks),
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

@app.get("/documents/{doc_id}", response_model=DocumentOut)
def get_document(doc_id: str, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc

@app.post("/query", response_model=QueryResponse)
def query_docs(payload: QueryRequest, db: Session = Depends(get_db)):
    # 1) Embed the query
    q_emb = get_embedding(payload.query)

    # 2) cosine distance → similarity
    similarity_expr = 1 - Chunk.embedding.cosine_distance(q_emb)

    rows = (
        db.query(Chunk, Document, similarity_expr.label("similarity"))
        .join(Document, Chunk.document_id == Document.id)
        .filter(Chunk.embedding != None)  # ← ignore NULL embeddings
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

if __name__ == "__main__":
	#pip install -r requirements.txt
	uvicorn.run(app, host="127.0.0.1", port=8000)
