# save as app/main.py
## IMPORTS
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from contextlib import asynccontextmanager
import asyncio
from pydantic import BaseModel
import uvicorn
import os
from datetime import datetime
from typing import List
import numpy as np
from sqlalchemy.orm import Session
from sqlalchemy import asc, func, or_, text
from sqlalchemy.exc import IntegrityError
#Internal imports
from .db import get_db, EMBED_TABLE, SENTENCE_TABLE, TOPIC_TABLE, DOCUMENT_TABLE
from .schemas import (
    IngestPayload, DocumentDetailResponse, DocumentUpdateRequest, 
    SettingsResponse, SettingsUpdateRequest, TopicOption, TopicsListResponse,
    TopicAssignmentRequest,
    CreateApiKeyRequest, CreateApiKeyResponse, WhoAmIResponse,
    ListApiKeysResponse, ApiKeyInfo,
    VaultResponse, VaultCreate, VaultListResponse,
)
from . import models
from .models import Document, Vault, VaultMembership
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
    generate_extension_token,
    hash_api_key,
    API_KEY_PREFIX,
    EXTENSION_TOKEN_PREFIX,
    get_user_vault,
    create_personal_vault,
    check_vault_access,
    get_user_accessible_vault_ids,
    require_vault_access,
)
from .email_service import send_verification_email



@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup is intentionally minimal for fast Fly boots
    # - DB migrations run via run_migration.py at deploy time
    # - Embedding tables are lazy-initialized on first use (via __getattr__ in db.py)
    # - Background fill moved to /admin/backfill endpoint
    
    # Initialize scheduler for periodic tasks (deduplication)
    from .scheduler import init_scheduler, shutdown_scheduler
    if PREFERENCES.deduplication.enabled:
        init_scheduler(schedule_times=PREFERENCES.deduplication.schedule_times)
    
    yield
    
    # Shutdown scheduler gracefully
    if PREFERENCES.deduplication.enabled:
        shutdown_scheduler()

app = FastAPI(lifespan=lifespan)

# Security scheme for Swagger UI (shows Authorize button)
bearer_scheme = HTTPBearer(auto_error=False)

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
    """Liveness probe - fast, no DB. Used by Fly health checks."""
    return {"status": "ok"}

@app.get("/ready")
def readiness_check(db: Session = Depends(get_db)):
    """
    Readiness probe - checks if DB is reachable and embedding tables exist.
    Use this for internal checks, NOT for Fly health checks.
    """
    try:
        # Check DB connectivity
        db.execute(text("SELECT 1"))
        
        # Check if embedding tables are initialized (lazy init if needed)
        from .db import ensure_embedding_ready
        ensure_embedding_ready()
        
        return {"ready": True, "db": "connected", "embeddings": "initialized"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Not ready: {e}")

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
        
        # Create personal vault with template content for new user
        create_personal_vault(user, db)

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
def whoami(
    user: models.User = Depends(get_current_user),
    _: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
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


def format_linked_pdfs_as_markdown(pdf_urls: list, exclude: str = None) -> str:
    """Format PDF URLs as markdown links for reference."""
    if not pdf_urls:
        return ""
    refs = []
    for url in pdf_urls:
        if url == exclude:
            continue
        # Extract filename from URL or use truncated URL
        if '/' in url:
            filename = url.split('/')[-1]
            # Clean up query params
            if '?' in filename:
                filename = filename.split('?')[0]
        else:
            filename = url[:50] + "..." if len(url) > 50 else url
        refs.append(f"- [{filename}]({url})")
    return "\n".join(refs)


def extract_arxiv_id(url: str) -> str:
    """Extract arXiv paper ID from URL (works for both /abs/ and /pdf/ URLs)."""
    import re
    # Match both /abs/ID and /pdf/ID patterns
    # Paper IDs can contain dots (e.g., 2601.15299) so don't exclude them
    # But stop at .pdf extension if present
    match = re.search(r'arxiv\.org/(?:abs|pdf)/([^/?#]+?)(?:\.pdf)?$', url)
    return match.group(1) if match else None


def normalize_pdf_url(url: str) -> str:
    """Normalize PDF URL to ensure it's properly formatted for extraction."""
    if not url:
        return url
    
    # Special handling for arxiv.org/pdf/ URLs - ensure they have .pdf extension
    # arxiv.org/pdf/2601.15299 -> arxiv.org/pdf/2601.15299.pdf
    if 'arxiv.org/pdf/' in url and not url.endswith('.pdf'):
        paper_id = extract_arxiv_id(url)
        if paper_id:
            return f"https://arxiv.org/pdf/{paper_id}.pdf"
    
    return url


def is_pdf_url(url: str) -> bool:
    """Check if URL is a direct PDF."""
    if not url:
        return False
    url_lower = url.lower()
    return (
        url_lower.endswith('.pdf') or 
        '/pdf/' in url_lower or
        url_lower.endswith('/pdf')
    )


@app.post("/ingest")
def ingest(
    payload: IngestPayload,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
    _: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    # Use captured_at from payload if provided, else now
    captured_at = payload.captured_at or datetime.utcnow()
    
    # Get user's vault (creates personal vault if doesn't exist)
    vault = get_user_vault(user, db)

    # 1. Start with extension text as captured_text
    captured_text = payload.text or ""
    title = payload.title
    extracted_text = None
    pdf_parsed = False
    
    # 2. Determine if we should parse a PDF (URL is PDF or arXiv)
    pdf_to_parse = payload.pdf_to_parse  # New field from extension
    
    # Check URL patterns if pdf_to_parse not explicitly set
    if not pdf_to_parse and payload.url:
        if is_pdf_url(payload.url):
            pdf_to_parse = payload.url
        elif 'arxiv.org/abs/' in payload.url:
            # arXiv abstract page - the PDF IS the content
            paper_id = extract_arxiv_id(payload.url)
            if paper_id:
                pdf_to_parse = f"https://arxiv.org/pdf/{paper_id}.pdf"
    
    # Legacy support: if old pdf_urls field used, check if any should be parsed
    if not pdf_to_parse and payload.pdf_urls and len(payload.pdf_urls) > 0:
        # Only parse if URL itself is a PDF (old behavior for direct PDF pages)
        if is_pdf_url(payload.url):
            pdf_to_parse = payload.url
    
    # 3. Parse the PDF if needed (PDF IS the captured content)
    if pdf_to_parse:
        # Normalize URL (e.g., add .pdf to arxiv URLs)
        pdf_to_parse = normalize_pdf_url(pdf_to_parse)
        print(f"[INFO] Parsing PDF as primary content: {pdf_to_parse}")
        try:
            from .extraction import extract_from_pdf_urls
            pdf_text = extract_from_pdf_urls([pdf_to_parse], max_pages_per_pdf=50)
            if pdf_text:
                captured_text = pdf_text  # PDF content IS the captured content
                pdf_parsed = True
                print(f"[INFO] PDF parsed successfully: {len(pdf_text)} chars")
            else:
                print(f"[WARN] No text extracted from PDF: {pdf_to_parse}")
        except Exception as e:
            print(f"[ERROR] Failed to parse PDF {pdf_to_parse}: {e}")
            import traceback
            traceback.print_exc()
            # Keep extension text as fallback
    
    # 4. Format linked PDFs as markdown references (don't parse them)
    linked_pdfs = payload.linked_pdf_urls or []
    # Legacy support: use old pdf_urls as linked refs if new field not provided
    if not linked_pdfs and payload.pdf_urls:
        linked_pdfs = payload.pdf_urls
    
    if linked_pdfs:
        refs = format_linked_pdfs_as_markdown(linked_pdfs, exclude=pdf_to_parse)
        if refs:
            captured_text += f"\n\n---\n\n**Linked Documents:**\n{refs}"
            print(f"[INFO] Added {len(linked_pdfs)} linked PDF references")
    
    # 5. Run trafilatura for NLP (optional, don't overwrite captured)
    if payload.mode == "page" and not pdf_parsed and payload.url and not payload.url.startswith("note://"):
        try:
            from .extraction import extract_from_url
            extraction_result = extract_from_url(payload.url)
            if extraction_result and extraction_result.get("text"):
                extracted_text = extraction_result["text"]
                print(f"[INFO] Trafilatura extraction for NLP: {len(extracted_text)} chars")
                # Only use extracted title if user didn't provide one
                if not payload.title and extraction_result.get("title"):
                    title = extraction_result.get("title")
            else:
                print(f"[INFO] Trafilatura extraction returned no text")
        except Exception as e:
            print(f"[WARN] Trafilatura extraction error (non-fatal): {e}")
            # Trafilatura failure is fine - we still have captured_text
    
    # 6. Ensure we have some text
    if not captured_text or not captured_text.strip():
        # Build a helpful error message
        if pdf_to_parse and not pdf_parsed:
            detail = f"PDF extraction failed for: {pdf_to_parse}. The PDF may be inaccessible, password-protected, or contain only images."
        elif is_pdf_url(payload.url):
            detail = f"Could not extract text from PDF URL: {payload.url}. Try accessing the PDF directly."
        else:
            detail = "No text content found. Please ensure the page has text content."
        
        print(f"[ERROR] Ingest failed - no text content. URL: {payload.url}, pdf_to_parse: {pdf_to_parse}, pdf_parsed: {pdf_parsed}")
        raise HTTPException(status_code=400, detail=detail)

    # 6b. URL duplicate check BEFORE creating document (only for full page captures)
    has_extracted_text = (extracted_text is not None)
    if has_extracted_text and vault:
        from .topics import find_url_duplicate
        existing_doc = find_url_duplicate(db, vault.id, payload.url, has_extracted_text)
        if existing_doc:
            return {
                "status": "duplicate",
                "document_id": str(existing_doc.id),
                "message": "Document already exists (matching URL)",
                "is_duplicate": True,
                "duplicate_reason": "url",
                "user_id": str(user.id) if user else None,
                "vault_id": str(vault.id) if vault else None,
            }

    # 7. Create document with separated text fields
    doc = models.Document(
        vault_id=vault.id if vault else None,
        created_by=user.id if user else None,
        url=payload.url,
        title=title,
        captured_text=captured_text,
        extracted_text=extracted_text,
        full_text=captured_text,  # Deprecated field - keep for backward compat
        captured_at=captured_at,
    )

    db.add(doc)
    db.commit()

    chunk_len = embed_doc_chunks(doc)

    # 7b. Semantic duplicate check AFTER embedding (only for full page captures)
    if has_extracted_text and vault:
        from .topics import find_semantic_duplicate
        existing_doc = find_semantic_duplicate(db, doc)
        if existing_doc:
            # Delete the new doc we just created, return the existing one
            db.delete(doc)
            db.commit()
            return {
                "status": "duplicate",
                "document_id": str(existing_doc.id),
                "message": "Document already exists (semantically similar content)",
                "is_duplicate": True,
                "duplicate_reason": "semantic",
                "user_id": str(user.id) if user else None,
                "vault_id": str(vault.id) if vault else None,
            }

    result = {
        "status": "ok",
        "document_id": str(doc.id),
        "num_chunks": int(chunk_len),
        "pdf_parsed": pdf_parsed,
        "linked_pdfs_count": len(linked_pdfs) if linked_pdfs else 0,
        "has_extracted_text": has_extracted_text,
    }
    
    # Include user_id and vault_id if auth is enabled and user is present
    if user:
        result["user_id"] = str(user.id)
    if vault:
        result["vault_id"] = str(vault.id)
    
    return result

@app.get("/documents", response_model=List[DocumentOut])
def list_documents(
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
    _: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    # Get vault IDs the user can access
    vault_ids = get_user_accessible_vault_ids(user, db)
    
    # Base query
    query = db.query(Document).order_by(Document.captured_at.desc())
    
    # Filter by vault membership if user is authenticated
    if user and vault_ids:
        query = query.filter(Document.vault_id.in_(vault_ids))
    elif user:
        # User has no vaults - return empty
        return []
    # If user is None (auth disabled), return all documents for backward compatibility
    
    docs = query.offset(offset).limit(limit).all()
    return docs


@app.post("/query", response_model=QueryResponse)
def query_docs(
    payload: QueryRequest, 
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
    _: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
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
    
    # 2a) Filter by vault membership - only search documents user can access
    vault_ids = get_user_accessible_vault_ids(user, db)
    if user and vault_ids:
        # If specific vault_id provided, verify access and filter to just that vault
        if payload.vault_id:
            from uuid import UUID as PyUUID
            try:
                requested_vault_id = PyUUID(payload.vault_id)
                if requested_vault_id not in vault_ids:
                    # User doesn't have access to this vault
                    raise HTTPException(status_code=403, detail="Access denied to this vault")
                base_query = base_query.filter(Document.vault_id == requested_vault_id)
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="Invalid vault_id format")
        else:
            # No specific vault - search all accessible vaults
            base_query = base_query.filter(Document.vault_id.in_(vault_ids))
    elif user:
        # User has no vaults - return empty results
        return QueryResponse(answer=None, hits=[])
    # If user is None (auth disabled), search all documents for backward compatibility

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
SCREENSHOTS_DIR = os.path.join(os.path.dirname(__file__), "..", "Screenshots")
#print("STATIC DIR:", STATIC_DIR)  # optional debug

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
# Mount Screenshots folder for extension install page images
if os.path.exists(SCREENSHOTS_DIR):
    app.mount("/Screenshots", StaticFiles(directory=SCREENSHOTS_DIR), name="screenshots")

@app.get("/topics", response_model=TopicsResponse)
def get_topics(
    days: int = 10, 
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
    _: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    """
    Return hierarchical topics for documents in the last `days` days.
    Only returns documents from vaults the user has access to.
    """
    vault_ids = get_user_accessible_vault_ids(user, db)
    return get_topics_with_cache(db, days=days, vault_ids=vault_ids)

@app.get("/topics/hierarchy")
def get_topics_hierarchy(
    days: int = 30, 
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
    _: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    """
    Return a D3-friendly hierarchy representation of topics with 3 levels:
    
    - Level 2 (Categories): Largest circles
    - Level 1 (Topics): Medium circles within categories
    - Level 0 (Fine): Smallest topic circles within topics
    - Documents: Leaf nodes within Level 0 topics
    
    Uses the hierarchical topic structure from TOPIC_TABLE with parent_id relationships.
    Only returns documents from vaults the user has access to.
    """
    from .topics import build_hierarchical_topics_for_d3
    vault_ids = get_user_accessible_vault_ids(user, db)
    return build_hierarchical_topics_for_d3(db, days=days, vault_ids=vault_ids)

@app.get("/topics/clear_cache")
def clear_cache():
    clear_topics_cache()
    return {"status": "ok", "message": "Topics cache cleared"}


@app.post("/topics/backfill")
def backfill_document_embeddings_endpoint(
    generate_summaries: bool = False,
    db: Session = Depends(get_db),
    _: None = Depends(require_bootstrap_token),
):
    """
    Comprehensive backfill for chunk summaries, document embeddings, and document summaries.
    Requires admin bootstrap token.
    
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
    db: Session = Depends(get_db),
    _: None = Depends(require_bootstrap_token),
):
    """
    Backfill summaries for chunks that don't have them using parallel batch processing.
    Requires admin bootstrap token.
    
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
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
    _: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    """
    Recluster documents using hierarchical agglomerative clustering.
    Requires authenticated user - only reclusters documents in user's vaults.
    
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
    
    # Get vault IDs the user can access
    vault_ids = get_user_accessible_vault_ids(user, db)
    print(f"topic vault ideas: {vault_ids}")
    # Optionally clear existing topic assignments (for full recluster)
    # Only clear AUTO-ASSIGNED topics, preserve manual assignments
    # Only clear assignments for documents in user's vaults
    if clear_assignments:
        query = db.query(Document).filter(
            Document.assigned_topic_id != None,
            Document.topic_manually_assigned != True  # Preserve manual assignments
        )
        if vault_ids:
            query = query.filter(Document.vault_id.in_(vault_ids))
        updated = query.update({
            Document.assigned_topic_id: None,
            Document.assigned_topic_title: None
        }, synchronize_session=False)
        db.commit()
        print(f"Cleared topic assignments from {updated} auto-assigned documents (preserved manual assignments)")
    
    # Clear cache first
    clear_topics_cache()
    
    # Compute hierarchical topics (pass clear_assignments to control behavior)
    # Pass vault_ids to scope reclustering to user's documents
    
    result = compute_hierarchical_topics(db, days=days, full_recluster=clear_assignments, vault_ids=vault_ids)
    
    return result


@app.get("/topics/stats")
def get_clustering_stats(
    days: int = 30, 
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
    _: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    """
    Get statistics about the current clustering without recomputing.
    
    Returns info about document counts, cluster distribution, etc.
    Only counts documents from vaults the user has access to.
    """
    from .db import DOCUMENT_TABLE
    from datetime import timedelta
    
    cutoff = datetime.utcnow() - timedelta(days=days)
    vault_ids = get_user_accessible_vault_ids(user, db)
    
    # Base query with vault filtering
    base_query = db.query(func.count(Document.id)).filter(Document.captured_at >= cutoff)
    if user and vault_ids:
        base_query = base_query.filter(Document.vault_id.in_(vault_ids))
    elif user:
        # User has no vaults - return zeros
        return {
            "time_range_days": days,
            "total_documents": 0,
            "documents_with_embeddings": 0,
            "documents_with_topics": 0,
            "topic_counts_by_level": {"fine_topics": 0, "topics": 0, "categories": 0},
            "agglomerative_enabled": PREFERENCES.agglomerative.enabled,
            "clustering_thresholds": PREFERENCES.agglomerative.level_thresholds
        }
    
    # Count documents
    total_docs = base_query.scalar()
    
    # Count documents with embeddings in DOCUMENT_TABLE (join with vault filter)
    embed_query = db.query(func.count(DOCUMENT_TABLE.id))
    if user and vault_ids:
        embed_query = embed_query.join(Document, Document.id == DOCUMENT_TABLE.id).filter(Document.vault_id.in_(vault_ids))
    docs_with_embeddings = embed_query.scalar()
    
    # Count documents with topic assignments
    topics_query = (
        db.query(func.count(Document.id))
        .filter(Document.captured_at >= cutoff)
        .filter(Document.assigned_topic_id != None)
    )
    if user and vault_ids:
        topics_query = topics_query.filter(Document.vault_id.in_(vault_ids))
    docs_with_topics = topics_query.scalar()
    
    # Count topics at each level (0=fine, 1=topics, 2=categories)
    # Filter topics by user's vaults for proper isolation
    topic_counts = {}
    level_names = {0: "fine_topics", 1: "topics", 2: "categories"}
    for level in range(3):
        query = db.query(func.count(TOPIC_TABLE.id)).filter(TOPIC_TABLE.level_index == level)
        # Filter by user's vaults
        if user and vault_ids:
            query = query.filter(TOPIC_TABLE.vault_id.in_(vault_ids))
        count = query.scalar()
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
def get_document_detail(
    document_id: str, 
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
    _: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Check vault access
    if doc.vault_id:
        require_vault_access(user, doc.vault_id, db, min_role="viewer")

    # Return captured_text for display (what the user captured)
    # Fall back to full_text for backward compatibility with older documents
    display_text = doc.captured_text or doc.full_text or ""

    return DocumentDetailResponse(
        id=str(doc.id),
        vault_id=str(doc.vault_id) if doc.vault_id else None,
        url=doc.url,
        title=doc.title,
        captured_at=doc.captured_at,
        text=display_text,
        assigned_topic_id=str(doc.assigned_topic_id) if doc.assigned_topic_id else None,
        assigned_topic_title=doc.assigned_topic_title,
        created_by=str(doc.created_by) if doc.created_by else None,
    )

@app.put("/documents/{document_id}", response_model=DocumentDetailResponse)
def update_document(
    document_id: str, 
    payload: DocumentUpdateRequest, 
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
    _: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Check vault access - need editor role to modify
    if doc.vault_id:
        require_vault_access(user, doc.vault_id, db, min_role="editor")

    # Update title if provided
    if payload.title is not None:
        doc.title = payload.title

    # Update text if provided - need to re-chunk and re-embed
    if payload.text is not None:
        doc.captured_text = payload.text
        doc.full_text = payload.text  # Keep deprecated field in sync
        
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
    
    # Return updated document - use captured_text for display
    display_text = doc.captured_text or doc.full_text or ""

    return DocumentDetailResponse(
        id=str(doc.id),
        vault_id=str(doc.vault_id) if doc.vault_id else None,
        url=doc.url,
        title=doc.title,
        captured_at=doc.captured_at,
        text=display_text,
        assigned_topic_id=str(doc.assigned_topic_id) if doc.assigned_topic_id else None,
        assigned_topic_title=doc.assigned_topic_title,
        created_by=str(doc.created_by) if doc.created_by else None,
    )

@app.delete("/documents/{document_id}")
def delete_document(
    document_id: str, 
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
    _: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    """Delete a document from the database. Chunks and sentences will be deleted via CASCADE."""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Check vault access - need editor role to delete
    if doc.vault_id:
        require_vault_access(user, doc.vault_id, db, min_role="editor")
    
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
def update_settings(
    payload: SettingsUpdateRequest,
    user: models.User = Depends(get_current_user),
    _: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    """Update settings including providers, model names, and OpenAI API key.
    Requires authentication."""
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
def get_topics_for_document(
    document_id: str, 
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
    _: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    """
    Get all existing topics sorted by semantic similarity to the document's embedding.
    Most similar topics appear first.
    
    Uses efficient SQL with pgvector's cosine distance operator.
    """
    # Verify document exists
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Check vault access
    if doc.vault_id:
        require_vault_access(user, doc.vault_id, db, min_role="viewer")
    
    # Get user's accessible vault IDs for filtering topics
    vault_ids = get_user_accessible_vault_ids(user, db)
    
    # Get table names for raw SQL
    embed_table_name = EMBED_TABLE.__tablename__
    topic_table_name = TOPIC_TABLE.__tablename__
    
    # Use raw SQL for efficient pgvector operations:
    # 1. Compute average embedding for document's chunks
    # 2. Order topics by cosine distance (similarity = 1 - distance)
    # 3. Filter topics to only those with documents in user's accessible vaults AND topics in user's vaults
    sql = text(f"""
        WITH doc_embedding AS (
            SELECT AVG(embedding) as avg_emb
            FROM embedding.{embed_table_name}
            WHERE document_id = :doc_id
            AND embedding IS NOT NULL
        ),
        accessible_topics AS (
            SELECT DISTINCT d.assigned_topic_id
            FROM documents d
            WHERE d.vault_id::text = ANY(:vault_ids)
            AND d.assigned_topic_id IS NOT NULL
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
        AND t.id IN (SELECT assigned_topic_id FROM accessible_topics)
        AND t.vault_id::text = ANY(:vault_ids)
        ORDER BY t.embedding <=> de.avg_emb ASC
        LIMIT 100
    """)
    
    # Convert vault_ids to list of strings for PostgreSQL array
    vault_id_strs = [str(v) for v in vault_ids] if vault_ids else []
    result = db.execute(sql, {"doc_id": document_id, "vault_ids": vault_id_strs})
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
def generate_topic_for_document(
    document_id: str, 
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
    _: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    """
    Generate a topic title for a document using AI, but don't assign it yet.
    Returns the generated title for user preview/approval.
    """
    from .topics import compute_document_embeddings, _generate_title_and_summary
    
    # Get the document
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Check vault access
    if doc.vault_id:
        require_vault_access(user, doc.vault_id, db, min_role="viewer")
    
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
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
    _: HTTPAuthorizationCredentials = Depends(bearer_scheme),
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
    
    # Check vault access - need editor role to modify topic assignment
    if doc.vault_id:
        require_vault_access(user, doc.vault_id, db, min_role="editor")
    
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
        doc.topic_manually_assigned = True
        
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
            level_index=0,
            vault_id=doc.vault_id
        )
        
        doc.assigned_topic_id = topic_id
        doc.assigned_topic_title = payload.custom_title
        doc.topic_manually_assigned = True
        
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
        doc.topic_manually_assigned = True
        
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
        doc.topic_manually_assigned = False
        
        db.commit()
        clear_topics_cache()
        
        return {
            "status": "ok",
            "message": "Topic assignment cleared",
            "assigned_topic_id": None,
            "assigned_topic_title": None
        }


# ---------- Vault Management Endpoints ----------

@app.get("/vaults", response_model=VaultListResponse)
def list_vaults(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
    _: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    """
    List all vaults the current user has access to.
    Returns vaults with the user's role in each.
    """
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    # Get all vaults user has membership in
    results = (
        db.query(Vault, VaultMembership.role, func.count(Document.id).label("doc_count"))
        .join(VaultMembership, VaultMembership.vault_id == Vault.id)
        .outerjoin(Document, Document.vault_id == Vault.id)
        .filter(VaultMembership.user_id == user.id)
        .filter(Vault.archived_at == None)  # noqa: E711
        .group_by(Vault.id, VaultMembership.role)
        .order_by(Vault.is_personal.desc(), Vault.created_at.asc())
        .all()
    )
    
    vaults = [
        VaultResponse(
            id=str(vault.id),
            name=vault.name,
            owner_id=str(vault.owner_id) if vault.owner_id else None,
            is_personal=vault.is_personal,
            created_at=vault.created_at,
            archived_at=vault.archived_at,
            role=role,
            document_count=doc_count,
        )
        for vault, role, doc_count in results
    ]
    
    return VaultListResponse(vaults=vaults)


@app.post("/vaults", response_model=VaultResponse)
def create_vault(
    payload: VaultCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
    _: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    """
    Create a new vault. The current user becomes the owner.
    """
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    vault = Vault(
        name=payload.name,
        owner_id=user.id,
        is_personal=payload.is_personal,
    )
    db.add(vault)
    db.flush()
    
    # Create owner membership
    membership = VaultMembership(
        vault_id=vault.id,
        user_id=user.id,
        role="owner",
    )
    db.add(membership)
    db.commit()
    db.refresh(vault)
    
    return VaultResponse(
        id=str(vault.id),
        name=vault.name,
        owner_id=str(vault.owner_id) if vault.owner_id else None,
        is_personal=vault.is_personal,
        created_at=vault.created_at,
        archived_at=vault.archived_at,
        role="owner",
        document_count=0,
    )


@app.get("/vaults/{vault_id}", response_model=VaultResponse)
def get_vault(
    vault_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
    _: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    """
    Get details of a specific vault.
    """
    from uuid import UUID as PyUUID
    
    try:
        vault_uuid = PyUUID(vault_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid vault_id format")
    
    vault = db.query(Vault).filter(Vault.id == vault_uuid).first()
    if not vault:
        raise HTTPException(status_code=404, detail="Vault not found")
    
    # Check access
    require_vault_access(user, vault_uuid, db, min_role="viewer")
    
    # Get user's role
    membership = (
        db.query(VaultMembership)
        .filter(VaultMembership.vault_id == vault_uuid)
        .filter(VaultMembership.user_id == user.id)
        .first()
    )
    
    # Get document count
    doc_count = (
        db.query(func.count(Document.id))
        .filter(Document.vault_id == vault_uuid)
        .scalar()
    )
    
    return VaultResponse(
        id=str(vault.id),
        name=vault.name,
        owner_id=str(vault.owner_id) if vault.owner_id else None,
        is_personal=vault.is_personal,
        created_at=vault.created_at,
        archived_at=vault.archived_at,
        role=membership.role if membership else None,
        document_count=doc_count,
    )


# ---------- Admin Endpoints ----------

@app.post("/admin/backfill")
def admin_backfill_embeddings(
    _: None = Depends(require_bootstrap_token),
):
    """
    Manually trigger background fill of missing embeddings.
    
    This was previously run automatically at startup but is now manual
    to keep startup fast for Fly.io.
    
    Requires bootstrap token via X-Bootstrap-Token header.
    """
    import asyncio
    
    async def _fill_embeddings_async():
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, fill_empty_embed_docs)
    
    # Run in background
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(_fill_embeddings_async())
            return {"status": "started", "message": "Backfill task started in background"}
        else:
            # Fallback for sync context
            fill_empty_embed_docs()
            return {"status": "completed", "message": "Backfill completed synchronously"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start backfill: {e}")


@app.post("/admin/init-db")
def admin_init_db(
    _: None = Depends(require_bootstrap_token),
):
    """
    Manually run database initialization (schemas, extensions, migrations).
    
    This was previously run automatically at startup but is now manual
    to keep startup fast for Fly.io.
    
    Requires bootstrap token via X-Bootstrap-Token header.
    """
    from .db import init_db
    
    try:
        init_db()
        return {"status": "ok", "message": "Database initialized successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB init failed: {e}")


@app.post("/admin/dedupe")
def admin_dedupe_all_vaults(
    _: None = Depends(require_bootstrap_token),
    db: Session = Depends(get_db),
):
    """
    Run batch deduplication across all vaults.
    
    This is a fallback for manual cleanup - normally dedupe runs on schedule (midnight and noon).
    Can be used for initial cleanup of existing data or on-demand maintenance.
    
    Requires bootstrap token via X-Bootstrap-Token header.
    """
    from .topics import deduplicate_vault_documents
    
    vault_ids = db.query(Vault.id).all()
    results = []
    total_removed = 0
    
    for (vault_id,) in vault_ids:
        result = deduplicate_vault_documents(db, vault_id)
        if result["documents_deleted"]:
            results.append(result)
            total_removed += len(result["documents_deleted"])
    
    return {
        "status": "ok",
        "vaults_processed": len(vault_ids),
        "total_duplicates_removed": total_removed,
        "vaults_with_duplicates": results,
    }


@app.get("/admin/scheduler/status")
def admin_scheduler_status(
    _: None = Depends(require_bootstrap_token),
):
    """
    Get the current status of the background scheduler.
    
    Shows whether the scheduler is running and when deduplication jobs are scheduled.
    
    Requires bootstrap token via X-Bootstrap-Token header.
    """
    from .scheduler import get_scheduler_status
    
    status = get_scheduler_status()
    status["deduplication_config"] = {
        "enabled": PREFERENCES.deduplication.enabled,
        "schedule_times": PREFERENCES.deduplication.schedule_times,
        "similarity_threshold": PREFERENCES.deduplication.similarity_threshold,
    }
    
    return status


@app.get("/admin/test-extraction")
def admin_test_extraction(
    url: str,
    _: None = Depends(require_bootstrap_token),
):
    """
    Test trafilatura extraction for a given URL.
    
    Returns extracted title and text preview.
    Requires bootstrap token via X-Bootstrap-Token header.
    """
    from .extraction import extract_from_url
    
    result = extract_from_url(url)
    if not result:
        raise HTTPException(status_code=404, detail="Extraction failed - could not fetch or extract content")
    
    return {
        "url": url,
        "title": result.get("title"),
        "author": result.get("author"),
        "date": result.get("date"),
        "language": result.get("language"),
        "text_preview": result.get("text", "")[:1000],
        "text_length": len(result.get("text", "")),
    }


@app.post("/admin/reviewer-code")
def admin_generate_reviewer_code(
    email: str,
    days: int = 14,
    _: None = Depends(require_bootstrap_token),
    db: Session = Depends(get_db),
):
    """
    Generate a reviewer verification code for extension testing.
    
    - email: Email to associate with the code
    - days: Expiration in days (default: 14)
    
    Requires bootstrap token via X-Bootstrap-Token header.
    """
    import random
    from datetime import timedelta
    
    code = "".join([str(random.randint(0, 9)) for _ in range(6)])
    expires_at = datetime.utcnow() + timedelta(days=days)
    
    db.execute(text("""
        INSERT INTO extension_verification_codes 
        (id, email, code, purpose, created_at, expires_at)
        VALUES (:email, :code, 'reviewer', now(), :expires_at)
    """), {"email": email.strip().lower(), "code": code, "expires_at": expires_at})
    db.commit()
    
    return {
        "email": email,
        "code": code,
        "expires_at": expires_at.isoformat(),
        "days": days,
    }


# ---------- Extension OAuth Connect Flow ----------

from .schemas import (
    ExtensionConnectRequest, ExtensionConnectResponse,
    ExtensionVerifyRequest, ExtensionTokenInfo, ExtensionTokensListResponse,
)
import secrets
import random
from datetime import timedelta
from fastapi.responses import RedirectResponse


@app.get("/extension/connect")
def extension_connect_page():
    """Serve the extension connect HTML page."""
    return FileResponse(os.path.join(STATIC_DIR, "extension-connect.html"))


@app.post("/extension/connect", response_model=ExtensionConnectResponse)
def extension_connect_send_code(
    payload: ExtensionConnectRequest,
    db: Session = Depends(get_db),
):
    """
    Send a verification code to the user's email.
    
    This starts the OAuth-style connection flow for the browser extension.
    """
    email = (payload.email or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email address")
    
    # Generate a 6-digit code
    code = "".join([str(random.randint(0, 9)) for _ in range(6)])
    
    # Calculate expiry (10 minutes from now)
    expires_at = datetime.utcnow() + timedelta(minutes=10)
    
    # Store the verification code
    verification = models.ExtensionVerificationCode(
        email=email,
        code=code,
        expires_at=expires_at,
    )
    db.add(verification)
    db.commit()
    
    # Send the email
    if not send_verification_email(email, code):
        raise HTTPException(
            status_code=500, 
            detail="Failed to send verification email. Please try again."
        )
    
    # Mask the email for the response (e.g., j***@example.com)
    parts = email.split("@")
    if len(parts[0]) > 2:
        masked = parts[0][0] + "***" + parts[0][-1] + "@" + parts[1]
    else:
        masked = parts[0][0] + "***@" + parts[1]
    
    return ExtensionConnectResponse(
        status="ok",
        message="Verification code sent to your email",
        email=masked,
    )


@app.post("/extension/verify")
def extension_verify_code(
    payload: ExtensionVerifyRequest,
    db: Session = Depends(get_db),
):
    """
    Verify the code and issue an extension token.
    
    On success, redirects to /extension/success?token=xxx
    """
    email = (payload.email or "").strip().lower()
    code = (payload.code or "").strip()
    
    if not email or not code:
        raise HTTPException(status_code=400, detail="Email and code are required")
    
    # Find the verification code
    # For 'user' codes: enforce single-use (used_at must be None)
    # For 'reviewer' codes: allow multi-use (skip used_at check)
    verification = (
        db.query(models.ExtensionVerificationCode)
        .filter(models.ExtensionVerificationCode.email == email)
        .filter(models.ExtensionVerificationCode.code == code)
        .filter(models.ExtensionVerificationCode.expires_at > datetime.utcnow())
        .filter(
            or_(
                models.ExtensionVerificationCode.purpose == 'reviewer',  # Reviewer codes can be reused
                models.ExtensionVerificationCode.used_at == None  # noqa: E711 - User codes must be unused
            )
        )
        .order_by(models.ExtensionVerificationCode.created_at.desc())
        .first()
    )
    
    if not verification:
        raise HTTPException(
            status_code=400, 
            detail="Invalid or expired verification code"
        )
    
    # Mark code as used (only for normal user codes, not reviewer codes)
    if verification.purpose == 'user':
        verification.used_at = datetime.utcnow()
    
    # Get or create user
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user:
        user = models.User(email=email)
        db.add(user)
        db.commit()
        db.refresh(user)
        
        # Create personal vault with template content for new user
        create_personal_vault(user, db)
    
    # Generate extension token
    raw_token = generate_extension_token()
    token_hash = hash_api_key(raw_token)  # Use same hashing as API keys
    
    # Store token
    ext_token = models.ExtensionToken(
        user_id=user.id,
        token_hash=token_hash,
        name="Browser Extension",  # Can be enhanced with browser info later
    )
    db.add(ext_token)
    db.commit()
    
    # Return the token in URL for the extension to capture
    return RedirectResponse(
        url=f"/extension/success?token={raw_token}",
        status_code=302,
    )


@app.get("/extension/success")
def extension_success_page():
    """
    Serve the success page.
    
    The token is in the URL query param for the extension to capture.
    """
    return FileResponse(os.path.join(STATIC_DIR, "extension-success.html"))


@app.get("/extension/tokens", response_model=ExtensionTokensListResponse)
def list_extension_tokens(
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    """
    List all extension tokens (connected devices) for the current user.
    """
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    tokens = (
        db.query(models.ExtensionToken)
        .filter(models.ExtensionToken.user_id == user.id)
        .order_by(models.ExtensionToken.created_at.desc())
        .all()
    )
    
    return ExtensionTokensListResponse(
        tokens=[
            ExtensionTokenInfo(
                id=str(t.id),
                name=t.name,
                created_at=t.created_at,
                last_used_at=t.last_used_at,
                revoked_at=t.revoked_at,
            )
            for t in tokens
        ]
    )


@app.post("/extension/tokens/{token_id}/revoke")
def revoke_extension_token(
    token_id: str,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    """
    Revoke an extension token (disconnect a device).
    """
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    from uuid import UUID as PyUUID
    try:
        token_uuid = PyUUID(token_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid token_id")
    
    token = (
        db.query(models.ExtensionToken)
        .filter(models.ExtensionToken.id == token_uuid)
        .filter(models.ExtensionToken.user_id == user.id)
        .first()
    )
    
    if not token:
        raise HTTPException(status_code=404, detail="Token not found")
    
    if token.revoked_at is None:
        token.revoked_at = datetime.utcnow()
        db.commit()
    
    return {"status": "ok", "revoked_at": token.revoked_at}


# -------- EXTENSION DOWNLOAD ENDPOINTS --------

@app.get("/download/extension-chrome.zip")
def download_chrome_extension():
    """Serve Chrome extension ZIP for download."""
    zip_path = os.path.join(os.path.dirname(__file__), "..", "extension-chrome.zip")
    if os.path.exists(zip_path):
        return FileResponse(
            zip_path, 
            filename="vaultbubble-chrome.zip",
            media_type="application/zip"
        )
    raise HTTPException(status_code=404, detail="Chrome extension not found. Please contact support.")


@app.get("/download/extension-firefox.zip")
def download_firefox_extension():
    """Serve Firefox extension ZIP for download."""
    zip_path = os.path.join(os.path.dirname(__file__), "..", "extension-firefox.zip")
    if os.path.exists(zip_path):
        return FileResponse(
            zip_path, 
            filename="vaultbubble-firefox.zip",
            media_type="application/zip"
        )
    raise HTTPException(status_code=404, detail="Firefox extension not found. Please contact support.")


if __name__ == "__main__":
	#pip install -r requirements.txt
	uvicorn.run(app, host="127.0.0.1", port=8000)
