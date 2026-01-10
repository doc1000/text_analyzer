# save as app/main.py
## IMPORTS
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import asyncio
from pydantic import BaseModel
import uvicorn
import os
from datetime import datetime
from typing import List
import numpy as np
from sqlalchemy.orm import Session
from sqlalchemy import asc, func, text
#Internal imports
from .db import init_db, get_db, EMBED_TABLE, SENTENCE_TABLE
from .schemas import IngestPayload, DocumentDetailResponse, DocumentUpdateRequest, SettingsResponse, SettingsUpdateRequest
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
from .helpers import update_openai_client



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

from . import models

@app.post("/ingest")
def ingest(payload: IngestPayload, db: Session = Depends(get_db)):
    # Use captured_at from payload if provided, else now
    captured_at = payload.captured_at or datetime.utcnow()

    doc = models.Document(
        url=payload.url,
        title=payload.title,
        full_text=payload.text,
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
    # Query sentence embeddings instead of chunk embeddings
    similarity_expr = 1 - SENTENCE_TABLE.embedding.cosine_distance(q_emb)

    # base query: sentences with non-null embeddings joined to chunks and documents
    # Join path: SENTENCE_TABLE → EMBED_TABLE → Document
    base_query = (
        db.query(SENTENCE_TABLE, EMBED_TABLE, Document, similarity_expr.label("similarity"))
        .join(EMBED_TABLE, SENTENCE_TABLE.chunk_id == EMBED_TABLE.id)
        .join(Document, EMBED_TABLE.document_id == Document.id)
        .filter(SENTENCE_TABLE.embedding != None)  # ← ignore NULL embeddings
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
    seen_sentence_texts = set()
    
    for sentence, chunk, doc, similarity in rows:
        # Deduplicate by sentence text content to avoid showing same text multiple times
        sent_text_normalized = (sentence.sent_text or "").strip()
        
        if sent_text_normalized and sent_text_normalized not in seen_sentence_texts:
            seen_sentence_texts.add(sent_text_normalized)
            hits.append(
                ChunkHit(
                    document_id=str(doc.id),
                    document_title=doc.title,
                    url=doc.url,
                    chunk_index=chunk.chunk_index,
                    chunk_text=chunk.chunk_text,
                    sent_text=sentence.sent_text,
                    sent_index=sentence.sent_index,
                    similarity=float(similarity),
                )
            )
        elif not sent_text_normalized:
            # If no sentence text, still add but deduplicate by position
            pos_key = (str(doc.id), chunk.chunk_index, sentence.sent_index)
            if pos_key not in seen_sentence_texts:
                seen_sentence_texts.add(pos_key)
                hits.append(
                    ChunkHit(
                        document_id=str(doc.id),
                        document_title=doc.title,
                        url=doc.url,
                        chunk_index=chunk.chunk_index,
                        chunk_text=chunk.chunk_text,
                        sent_text=sentence.sent_text,
                        sent_index=sentence.sent_index,
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
        text=full_text,
    )

@app.put("/documents/{document_id}", response_model=DocumentDetailResponse)
def update_document(document_id: str, payload: DocumentUpdateRequest, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Update title if provided
    if payload.title is not None:
        doc.title = payload.title

    # Update text if provided - need to re-chunk and re-embed
    if payload.text is not None:
        doc.full_text = payload.text
        
        # Delete old chunks (CASCADE will delete sentences automatically)
        db.query(EMBED_TABLE).filter(EMBED_TABLE.document_id == doc.id).delete()
        db.commit()
        
        # Re-chunk and re-embed
        from .helpers import embed_doc_chunks
        embed_doc_chunks(doc, chunk_type="chunk")
        
        # Re-embed sentences for each chunk
        chunks = db.query(EMBED_TABLE).filter(EMBED_TABLE.document_id == doc.id).all()
        for chunk in chunks:
            embed_doc_chunks(chunk, chunk_type="sent")

    db.commit()
    
    # Return updated document
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
        text=full_text,
    )

@app.get("/model_configs")
def get_embedding_length(prompt: str = "this is a test"):
    """
    return embedding length and current model configurations
    """
    emb = len(get_embedding(prompt))
    
    # Determine actual models being used based on providers
    chat_model = PREFERENCES.models.ollama.chat_model if PREFERENCES.models.chat_provider == "ollama" else PREFERENCES.models.llm_model
    embedding_model = PREFERENCES.models.ollama.embed_model if PREFERENCES.models.embedding_provider == "ollama" else PREFERENCES.models.embedding_model
    topic_model = PREFERENCES.models.ollama.topic_model if PREFERENCES.models.topic_provider == "ollama" else PREFERENCES.models.llm_model
    
    return {
        "len": emb,
        "chat_model": chat_model,
        "topic_model": topic_model,
        "embedding_model": embedding_model,
        "chat_provider": PREFERENCES.models.chat_provider,
        "embedding_provider": PREFERENCES.models.embedding_provider,
        "topic_provider": PREFERENCES.models.topic_provider
    }

@app.get("/settings", response_model=SettingsResponse)
def get_settings():
    """Get current settings including models and providers."""
    # Determine actual models being used based on providers
    chat_model = PREFERENCES.models.ollama.chat_model if PREFERENCES.models.chat_provider == "ollama" else PREFERENCES.models.llm_model
    embedding_model = PREFERENCES.models.ollama.embed_model if PREFERENCES.models.embedding_provider == "ollama" else PREFERENCES.models.embedding_model
    topic_model = PREFERENCES.models.ollama.topic_model if PREFERENCES.models.topic_provider == "ollama" else PREFERENCES.models.llm_model
    
    has_openai_key = bool(os.getenv("OPENAI_API_KEY"))
    
    return SettingsResponse(
        chat_model=chat_model,
        topic_model=topic_model,
        embedding_model=embedding_model,
        chat_provider=PREFERENCES.models.chat_provider,
        embedding_provider=PREFERENCES.models.embedding_provider,
        topic_provider=PREFERENCES.models.topic_provider,
        has_openai_key=has_openai_key
    )

@app.put("/settings", response_model=SettingsResponse)
def update_settings(payload: SettingsUpdateRequest):
    """Update settings including chat provider and OpenAI API key."""
    # Update chat provider if provided
    if payload.chat_provider is not None:
        if payload.chat_provider not in ["openai", "ollama"]:
            raise HTTPException(status_code=400, detail="Invalid chat_provider. Must be 'openai' or 'ollama'")
        PREFERENCES.models.chat_provider = payload.chat_provider
    
    # Update OpenAI API key if provided
    if payload.openai_api_key is not None:
        if payload.openai_api_key.strip():
            update_openai_client(payload.openai_api_key.strip())
            # Optionally save to .env file for persistence
            # For now, just update the environment variable
        else:
            # Empty string means remove the key
            os.environ.pop("OPENAI_API_KEY", None)
            import app.helpers as helpers_module
            helpers_module._client_instance = None
    
    # Return updated settings
    return get_settings()

if __name__ == "__main__":
	#pip install -r requirements.txt
	uvicorn.run(app, host="127.0.0.1", port=8000)
