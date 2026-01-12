# save as app/main.py
## IMPORTS
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
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
from .db import init_db, get_db, EMBED_TABLE, SENTENCE_TABLE, TOPIC_TABLE
from .schemas import (
    IngestPayload, DocumentDetailResponse, DocumentUpdateRequest, 
    SettingsResponse, SettingsUpdateRequest, TopicOption, TopicsListResponse,
    TopicAssignmentRequest
)
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

@app.get("/")
def read_root():
    """Serve the main index.html page."""
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    raise HTTPException(status_code=404, detail="index.html not found")

@app.get("/index.html")
def read_index():
    """Serve the main index.html page."""
    return read_root()

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
        assigned_topic_id=str(doc.assigned_topic_id) if doc.assigned_topic_id else None,
        assigned_topic_title=doc.assigned_topic_title,
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

@app.delete("/documents/{document_id}")
def delete_document(document_id: str, db: Session = Depends(get_db)):
    """Delete a document from the database. Chunks and sentences will be deleted via CASCADE."""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Store document info for response
    doc_title = doc.title or "Untitled"
    
    # Delete the document (CASCADE will delete chunks and sentences automatically)
    db.delete(doc)
    db.commit()
    
    return {"status": "ok", "message": f"Document '{doc_title}' deleted successfully"}

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
    """Update settings including providers, model names, and OpenAI API key."""
    # Update chat provider if provided
    if payload.chat_provider is not None:
        if payload.chat_provider not in ["openai", "ollama"]:
            raise HTTPException(status_code=400, detail="Invalid chat_provider. Must be 'openai' or 'ollama'")
        PREFERENCES.models.chat_provider = payload.chat_provider
    
    # Update embedding provider if provided
    if payload.embedding_provider is not None:
        if payload.embedding_provider not in ["openai", "ollama"]:
            raise HTTPException(status_code=400, detail="Invalid embedding_provider. Must be 'openai' or 'ollama'")
        PREFERENCES.models.embedding_provider = payload.embedding_provider
    
    # Update topic provider if provided
    if payload.topic_provider is not None:
        if payload.topic_provider not in ["openai", "ollama"]:
            raise HTTPException(status_code=400, detail="Invalid topic_provider. Must be 'openai' or 'ollama'")
        PREFERENCES.models.topic_provider = payload.topic_provider
    
    # Update Ollama model names if provided
    if payload.chat_model is not None:
        PREFERENCES.models.ollama.chat_model = payload.chat_model
    
    if payload.topic_model is not None:
        PREFERENCES.models.ollama.topic_model = payload.topic_model
    
    if payload.embed_model is not None:
        PREFERENCES.models.ollama.embed_model = payload.embed_model
    
    # Update OpenAI model names if provided
    if payload.llm_model is not None:
        PREFERENCES.models.llm_model = payload.llm_model
    
    if payload.embedding_model is not None:
        PREFERENCES.models.embedding_model = payload.embedding_model
    
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
    
    # Persist settings to file
    from .config import save_settings_to_file
    save_settings_to_file(PREFERENCES)
    
    # Return updated settings
    return get_settings()


# ---------- Topic Assignment Endpoints ----------

@app.get("/documents/{document_id}/topics", response_model=TopicsListResponse)
def get_topics_for_document(document_id: str, db: Session = Depends(get_db)):
    """
    Get all existing topics sorted by semantic similarity to the document's embedding.
    Most similar topics appear first.
    
    If the document has an assigned topic, it will be included in the results.
    Uses efficient SQL with pgvector's cosine distance operator.
    """
    # Verify document exists
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Get table names for raw SQL
    embed_table_name = EMBED_TABLE.__tablename__
    topic_table_name = TOPIC_TABLE.__tablename__
    
    # Check if document has embeddings
    check_sql = text(f"""
        SELECT COUNT(*) FROM embedding.{embed_table_name}
        WHERE document_id = :doc_id AND embedding IS NOT NULL
    """)
    count_result = db.execute(check_sql, {"doc_id": document_id}).scalar()
    if count_result == 0:
        raise HTTPException(status_code=400, detail="Document has no embeddings. Please wait for embedding processing.")
    
    # Check if any topics exist at all
    topic_check_sql = text(f"""
        SELECT COUNT(*) FROM embedding.{topic_table_name}
        WHERE level_index = 0 AND embedding IS NOT NULL
    """)
    topic_count = db.execute(topic_check_sql).scalar()
    if topic_count == 0:
        # No topics exist yet - user needs to generate topics first
        return TopicsListResponse(topics=[])
    
    # Use raw SQL for efficient pgvector operations:
    # 1. Compute average embedding for document's chunks
    # 2. Order topics by cosine distance (similarity = 1 - distance)
    # 3. Include assigned topic even if not most similar (using UNION)
    sql = text(f"""
        WITH doc_embedding AS (
            SELECT AVG(embedding) as avg_emb
            FROM embedding.{embed_table_name}
            WHERE document_id = :doc_id
            AND embedding IS NOT NULL
        )
        SELECT 
            t.id,
            t.title_text,
            t.document_count,
            1 - (t.embedding <=> de.avg_emb) as similarity
        FROM embedding.{topic_table_name} t
        CROSS JOIN doc_embedding de
        WHERE t.level_index = 0
        AND t.embedding IS NOT NULL
        AND de.avg_emb IS NOT NULL
        ORDER BY t.embedding <=> de.avg_emb ASC
        LIMIT 100
    """)
    
    result = db.execute(sql, {"doc_id": document_id})
    rows = result.fetchall()
    
    # If document has an assigned topic, ensure it's in the results
    assigned_topic_id = doc.assigned_topic_id
    if assigned_topic_id:
        # Check if assigned topic is already in results
        assigned_in_results = any(str(row.id) == str(assigned_topic_id) for row in rows)
        
        if not assigned_in_results:
            # Fetch the assigned topic separately
            assigned_sql = text(f"""
                SELECT id, title_text, document_count
                FROM embedding.{topic_table_name}
                WHERE id = :topic_id AND level_index = 0
            """)
            assigned_result = db.execute(assigned_sql, {"topic_id": assigned_topic_id}).fetchone()
            
            if assigned_result:
                # Calculate similarity for assigned topic
                similarity_sql = text(f"""
                    WITH doc_embedding AS (
                        SELECT AVG(embedding) as avg_emb
                        FROM embedding.{embed_table_name}
                        WHERE document_id = :doc_id
                        AND embedding IS NOT NULL
                    )
                    SELECT 1 - (t.embedding <=> de.avg_emb) as similarity
                    FROM embedding.{topic_table_name} t
                    CROSS JOIN doc_embedding de
                    WHERE t.id = :topic_id AND t.embedding IS NOT NULL AND de.avg_emb IS NOT NULL
                """)
                similarity_result = db.execute(similarity_sql, {"doc_id": document_id, "topic_id": assigned_topic_id}).scalar()
                
                if similarity_result is not None:
                    # Create a row-like object for the assigned topic
                    from collections import namedtuple
                    TopicRow = namedtuple('TopicRow', ['id', 'title_text', 'document_count', 'similarity'])
                    assigned_row = TopicRow(
                        id=assigned_result.id,
                        title_text=assigned_result.title_text,
                        document_count=assigned_result.document_count,
                        similarity=float(similarity_result)
                    )
                    # Add to beginning of results (most relevant)
                    rows = [assigned_row] + list(rows)
    
    if not rows:
        return TopicsListResponse(topics=[])
    
    topic_options = [
        TopicOption(
            id=str(row.id),
            title=row.title_text,
            similarity=round(float(row.similarity), 4) if row.similarity else 0.0,
            document_count=row.document_count or 0
        )
        for row in rows
    ]
    
    return TopicsListResponse(topics=topic_options)


@app.post("/documents/{document_id}/topic/generate")
def generate_topic_for_document(document_id: str, db: Session = Depends(get_db)):
    """
    Generate a topic title for a document using AI, but don't assign it yet.
    Returns the generated title for user preview/approval.
    """
    from .topics import compute_document_embeddings, _generate_title_and_summary
    
    # Get the document
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Compute document embedding
    doc_embeddings = compute_document_embeddings(db, [doc])
    if doc.id not in doc_embeddings:
        raise HTTPException(status_code=400, detail="Document has no embeddings")
    
    # Generate title using the topic model
    title, summary = _generate_title_and_summary([doc], db=db, doc_embeddings=doc_embeddings)
    
    return {
        "status": "ok",
        "generated_title": title,
        "generated_summary": summary
    }


@app.put("/documents/{document_id}/topic")
def update_document_topic(
    document_id: str, 
    payload: TopicAssignmentRequest,
    db: Session = Depends(get_db)
):
    """
    Update a document's topic assignment.
    
    Options:
    1. topic_id provided: Assign to existing topic
    2. custom_title provided: Create new topic with custom title
    3. generate_new=True: Use AI to generate a new topic for this document
    """
    from .topics import (
        compute_document_embeddings, _save_topic_to_db, 
        _generate_title_and_summary, clear_topics_cache
    )
    from .mmr import compute_centroid
    from uuid import UUID as PyUUID
    
    # Get the document
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Compute document embedding (needed for new topic creation)
    doc_embeddings = compute_document_embeddings(db, [doc])
    
    if payload.topic_id:
        # Option 1: Assign to existing topic
        try:
            # Handle both string UUIDs and UUID objects
            if isinstance(payload.topic_id, str):
                topic_uuid = PyUUID(payload.topic_id)
            else:
                topic_uuid = payload.topic_id
        except (ValueError, TypeError) as e:
            raise HTTPException(status_code=400, detail=f"Invalid topic_id format: {e}")
        
        topic = db.query(TOPIC_TABLE).filter(TOPIC_TABLE.id == topic_uuid).first()
        if not topic:
            raise HTTPException(status_code=404, detail="Topic not found")
        
        doc.assigned_topic_id = topic.id
        doc.assigned_topic_title = topic.title_text
        
        db.commit()
        clear_topics_cache()
        
        return {
            "status": "ok",
            "message": f"Document assigned to topic: {topic.title_text}",
            "assigned_topic_id": str(topic.id),
            "assigned_topic_title": topic.title_text
        }
    
    elif payload.custom_title:
        # Option 2: Create new topic with custom title
        if doc.id not in doc_embeddings:
            raise HTTPException(status_code=400, detail="Document has no embeddings")
        
        # Use document embedding as centroid for the new topic
        centroid = doc_embeddings[doc.id]
        
        topic_id = _save_topic_to_db(
            db=db,
            title=payload.custom_title,
            centroid=centroid,
            document_count=1,
            summary=None,
            level_index=0
        )
        
        doc.assigned_topic_id = topic_id
        doc.assigned_topic_title = payload.custom_title
        
        db.commit()
        clear_topics_cache()
        
        return {
            "status": "ok",
            "message": f"Created new topic: {payload.custom_title}",
            "assigned_topic_id": str(topic_id),
            "assigned_topic_title": payload.custom_title
        }
    
    elif payload.generate_new:
        # Option 3: Generate new topic using AI
        if doc.id not in doc_embeddings:
            raise HTTPException(status_code=400, detail="Document has no embeddings")
        
        # Generate title using the topic model
        title, summary = _generate_title_and_summary([doc], db=db, doc_embeddings=doc_embeddings)
        
        # Use document embedding as centroid
        centroid = doc_embeddings[doc.id]
        
        topic_id = _save_topic_to_db(
            db=db,
            title=title,
            centroid=centroid,
            document_count=1,
            summary=summary,
            level_index=0
        )
        
        doc.assigned_topic_id = topic_id
        doc.assigned_topic_title = title
        
        db.commit()
        clear_topics_cache()
        
        return {
            "status": "ok",
            "message": f"Generated new topic: {title}",
            "assigned_topic_id": str(topic_id),
            "assigned_topic_title": title
        }
    
    else:
        # Clear topic assignment
        doc.assigned_topic_id = None
        doc.assigned_topic_title = None
        
        db.commit()
        clear_topics_cache()
        
        return {
            "status": "ok",
            "message": "Topic assignment cleared",
            "assigned_topic_id": None,
            "assigned_topic_title": None
        }


if __name__ == "__main__":
	#pip install -r requirements.txt
	uvicorn.run(app, host="127.0.0.1", port=8000)
