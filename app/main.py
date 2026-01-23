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
from sqlalchemy.exc import IntegrityError
#Internal imports
from .db import init_db, get_db, EMBED_TABLE, SENTENCE_TABLE, TOPIC_TABLE, DOCUMENT_TABLE
from .schemas import (
    IngestPayload, DocumentDetailResponse, DocumentUpdateRequest, 
    SettingsResponse, SettingsUpdateRequest, TopicOption, TopicsListResponse,
    TopicAssignmentRequest,
    CreateApiKeyRequest, CreateApiKeyResponse, WhoAmIResponse,
    ListApiKeysResponse, ApiKeyInfo
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
from .auth import (
    ApiKeyAuthMiddleware,
    get_current_user,
    require_bootstrap_token,
    generate_api_key,
    hash_api_key,
    API_KEY_PREFIX,
)



async def _fill_embeddings_async():
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        fill_empty_embed_docs,
    )

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize DB schemas/extensions (lightweight, no network calls)
    init_db()
    
    # Initialize embedding tables (uses EMBEDDING_DIM env var, no network probe)
    from .db import initialize_embedding_tables
    initialize_embedding_tables()
    
    # Background task: fill any missing embeddings
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

# API key auth (protects all endpoints except explicit allowlist in middleware)
app.add_middleware(ApiKeyAuthMiddleware)

@app.get("/")
def root():
    """Serve the bubble map visualization at root."""
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/test-db")
def test_db(db: Session = Depends(get_db)):
    # quick sanity query
    result = db.execute(text('SELECT 1;'))
    return {"db_ok": bool(list(result))}

from . import models

@app.post("/auth/bootstrap/create-key", response_model=CreateApiKeyResponse)
def bootstrap_create_api_key(
    payload: CreateApiKeyRequest,
    _: None = Depends(require_bootstrap_token),
    db: Session = Depends(get_db),
):
    """
    Create an API key for a user (admin/bootstrap only).
    Protect this endpoint with VB_BOOTSTRAP_TOKEN via X-Bootstrap-Token header.
    """
    email = (payload.email or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email")

    user = db.query(models.User).filter(models.User.email == email).first()
    if not user:
        user = models.User(email=email)
        db.add(user)
        db.commit()
        db.refresh(user)

    # Generate + store hashed key (retry in the extremely unlikely case of hash collision)
    for _attempt in range(3):
        raw_key = generate_api_key(prefix=API_KEY_PREFIX)
        key_hash = hash_api_key(raw_key)
        key_hint = raw_key[-6:] if len(raw_key) >= 6 else None
        row = models.ApiKey(
            user_id=user.id,
            key_hash=key_hash,
            prefix=API_KEY_PREFIX,
            name=payload.name,
            key_hint=key_hint,
        )
        db.add(row)
        try:
            db.commit()
            return CreateApiKeyResponse(api_key=raw_key, user_id=str(user.id), email=user.email)
        except IntegrityError:
            db.rollback()
            continue

    raise HTTPException(status_code=500, detail="Could not create API key (retry)")


@app.get("/auth/whoami", response_model=WhoAmIResponse)
def whoami(user: models.User = Depends(get_current_user)):
    return WhoAmIResponse(user_id=str(user.id), email=user.email)


@app.get("/auth/api-keys", response_model=ListApiKeysResponse)
def list_api_keys(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = (
        db.query(models.ApiKey)
        .filter(models.ApiKey.user_id == user.id)
        .order_by(models.ApiKey.created_at.desc())
        .all()
    )
    return ListApiKeysResponse(
        keys=[
            ApiKeyInfo(
                id=str(r.id),
                name=r.name,
                prefix=r.prefix,
                key_hint=r.key_hint,
                created_at=r.created_at,
                last_used_at=r.last_used_at,
                revoked_at=r.revoked_at,
            )
            for r in rows
        ]
    )


@app.post("/auth/api-keys/{api_key_id}/revoke")
def revoke_api_key(api_key_id: str, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    from uuid import UUID as PyUUID
    try:
        key_uuid = PyUUID(api_key_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid api_key_id")

    row = (
        db.query(models.ApiKey)
        .filter(models.ApiKey.id == key_uuid)
        .filter(models.ApiKey.user_id == user.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="API key not found")
    if row.revoked_at is None:
        row.revoked_at = datetime.utcnow()
        db.commit()
    return {"status": "ok", "revoked_at": row.revoked_at}


@app.post("/ingest")
def ingest(payload: IngestPayload, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
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

    result = {
        "status": "ok",
        "document_id": str(doc.id),
        "num_chunks": int(chunk_len),
    }
    
    # Include user_id only if auth is enabled and user is present
    if user:
        result["user_id"] = str(user.id)
    
    return result

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
def get_topics_hierarchy(days: int = 30, db: Session = Depends(get_db)):
    """
    Return a D3-friendly hierarchy representation of topics with 3 levels:
    
    - Level 2 (Categories): Largest circles
    - Level 1 (Topics): Medium circles within categories
    - Level 0 (Fine): Smallest topic circles within topics
    - Documents: Leaf nodes within Level 0 topics
    
    Uses the hierarchical topic structure from TOPIC_TABLE with parent_id relationships.
    """
    from .topics import build_hierarchical_topics_for_d3
    return build_hierarchical_topics_for_d3(db, days=days)

@app.get("/topics/clear_cache")
def clear_cache():
    clear_topics_cache()
    return {"status": "ok", "message": "Topics cache cleared"}


@app.post("/topics/backfill")
def backfill_document_embeddings_endpoint(
    generate_summaries: bool = False,
    db: Session = Depends(get_db)
):
    """
    Comprehensive backfill for chunk summaries, document embeddings, and document summaries.
    
    When generate_summaries=True, this runs three phases:
    1. Generate summaries for chunks that don't have them
    2. Create document embedding records (with summaries) for documents that don't have them
    3. Update existing document records that have null summary_text
    
    When generate_summaries=False, only creates document embedding records without summaries.
    
    Args:
        generate_summaries: If True, generate chunk and document summaries via LLM
    
    Returns:
        Statistics about the backfill operation for each phase
    """
    from .helpers import backfill_document_embeddings
    
    stats = backfill_document_embeddings(generate_summaries=generate_summaries)
    
    return {
        "status": "ok",
        "message": "Backfill complete" if not generate_summaries else "Backfill with summaries complete",
        "stats": stats
    }


@app.post("/topics/backfill/chunks")
def backfill_chunk_summaries_endpoint(
    batch_size: int = 50,
    max_workers: int = None,
    db: Session = Depends(get_db)
):
    """
    Backfill summaries for chunks that don't have them using parallel batch processing.
    
    This is useful if you want to only generate chunk summaries without
    running the full document backfill.
    
    Args:
        batch_size: Number of chunks per batch (default: 50)
        max_workers: Number of parallel workers (default: 4 for Ollama, 10 for OpenAI)
    
    Returns:
        Statistics about the chunk summary backfill
    """
    from .helpers import backfill_chunk_summaries
    
    stats = backfill_chunk_summaries(batch_size=batch_size, max_workers=max_workers)
    
    return {
        "status": "ok",
        "message": "Chunk summary backfill complete",
        "stats": stats
    }


@app.post("/topics/recluster")
def recluster_topics(
    days: int = 30,
    clear_assignments: bool = False,
    db: Session = Depends(get_db)
):
    """
    Recluster documents using hierarchical agglomerative clustering.
    
    Creates topics at 3 levels:
    - Level 0: Fine-grained (cosine sim >= 0.85)
    - Level 1: Topics (cosine sim >= 0.75)
    - Level 2: Super-topics/Categories (cosine sim >= 0.60)
    
    Topics at each level are linked to their parent at the next level up.
    
    Args:
        days: Number of days of documents to include
        clear_assignments: If True, clear ALL existing topic assignments and do full recluster.
                          If False (default), only process uncategorized documents.
    
    Returns:
        Statistics about created topics at each level
    """
    from .topics import compute_hierarchical_topics, clear_topics_cache
    
    # Optionally clear existing topic assignments (for full recluster)
    if clear_assignments:
        updated = (
            db.query(Document)
            .filter(Document.assigned_topic_id != None)
            .update({
                Document.assigned_topic_id: None,
                Document.assigned_topic_title: None
            }, synchronize_session=False)
        )
        db.commit()
        print(f"Cleared topic assignments from {updated} documents")
    
    # Clear cache first
    clear_topics_cache()
    
    # Compute hierarchical topics (pass clear_assignments to control behavior)
    result = compute_hierarchical_topics(db, days=days, full_recluster=clear_assignments)
    
    return result


@app.get("/topics/stats")
def get_clustering_stats(days: int = 30, db: Session = Depends(get_db)):
    """
    Get statistics about the current clustering without recomputing.
    
    Returns info about document counts, cluster distribution, etc.
    """
    from .db import DOCUMENT_TABLE
    from datetime import timedelta
    
    cutoff = datetime.utcnow() - timedelta(days=days)
    
    # Count documents
    total_docs = db.query(func.count(Document.id)).filter(Document.captured_at >= cutoff).scalar()
    
    # Count documents with embeddings in DOCUMENT_TABLE
    docs_with_embeddings = db.query(func.count(DOCUMENT_TABLE.id)).scalar()
    
    # Count documents with topic assignments
    docs_with_topics = (
        db.query(func.count(Document.id))
        .filter(Document.captured_at >= cutoff)
        .filter(Document.assigned_topic_id != None)
        .scalar()
    )
    
    # Count topics at each level (0=fine, 1=topics, 2=categories)
    topic_counts = {}
    level_names = {0: "fine_topics", 1: "topics", 2: "categories"}
    for level in range(3):
        count = (
            db.query(func.count(TOPIC_TABLE.id))
            .filter(TOPIC_TABLE.level_index == level)
            .scalar()
        )
        topic_counts[level_names.get(level, f"level_{level}")] = count
    
    return {
        "time_range_days": days,
        "total_documents": total_docs,
        "documents_with_embeddings": docs_with_embeddings,
        "documents_with_topics": docs_with_topics,
        "topic_counts_by_level": topic_counts,
        "agglomerative_enabled": PREFERENCES.agglomerative.enabled,
        "clustering_thresholds": PREFERENCES.agglomerative.level_thresholds
    }

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
    
    Uses efficient SQL with pgvector's cosine distance operator.
    """
    # Verify document exists
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Get table names for raw SQL
    embed_table_name = EMBED_TABLE.__tablename__
    topic_table_name = TOPIC_TABLE.__tablename__
    
    # Use raw SQL for efficient pgvector operations:
    # 1. Compute average embedding for document's chunks
    # 2. Order topics by cosine distance (similarity = 1 - distance)
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
        AND de.avg_emb IS NOT NULL
        ORDER BY t.embedding <=> de.avg_emb ASC
        LIMIT 100
    """)
    
    result = db.execute(sql, {"doc_id": document_id})
    rows = result.fetchall()
    
    if not rows:
        # Check if it's because document has no embeddings
        check_sql = text(f"""
            SELECT COUNT(*) FROM embedding.{embed_table_name}
            WHERE document_id = :doc_id AND embedding IS NOT NULL
        """)
        count_result = db.execute(check_sql, {"doc_id": document_id}).scalar()
        if count_result == 0:
            raise HTTPException(status_code=400, detail="Document has no embeddings. Please wait for embedding processing.")
        # Otherwise, just no topics exist
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
            topic_uuid = PyUUID(payload.topic_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid topic_id format")
        
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
