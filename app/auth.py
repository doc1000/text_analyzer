import hashlib
import os
import secrets
from datetime import datetime
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from sqlalchemy.orm import Session

from uuid import UUID

from .db import SessionLocal, get_db
from .models import ApiKey, Document, ExtensionToken, User, Vault, VaultMembership
from .topics import compute_document_embeddings, _save_topic_to_db
from .helpers import embed_doc_chunks


API_KEY_PREFIX = "vb_live_"
EXTENSION_TOKEN_PREFIX = "vb_ext_"

# Getting Started content - embedded to avoid file I/O on Fly
GETTING_STARTED_CONTENT = """# Getting Started with VaultBubbles

Welcome to VaultBubbles! This guide will help you set up and start using your personal knowledge vault.

## What is VaultBubbles?

VaultBubbles is a personal knowledge management system that:
- Captures web pages you read via a Chrome extension
- Automatically organizes content into topics using AI
- Lets you search and query your saved knowledge with natural language
- Visualizes your knowledge as an interactive bubble map

## Setup (5 minutes)

### Step 1: Get Your API Key

Your API key authenticates you with VaultBubbles. It looks like this:
```
vb_live_abc123xyz...
```

If you don't have one yet, contact your VaultBubbles administrator.

### Step 2: Install the Chrome Extension

1. Open Chrome and go to `chrome://extensions`
2. Enable "Developer mode" (toggle in top-right)
3. Click "Load unpacked" and select the `extension-chrome` folder
4. The VaultBubbles icon appears in your toolbar

### Step 3: Configure the Extension

1. Right-click the VaultBubbles icon
2. Click "Options"
3. Select "Cloud" to use the hosted service
4. Done! Settings save automatically

### Step 4: Configure the Website

1. Go to https://vaultbubbles.fly.dev
2. Click the Settings button (top-right)
3. Find "VaultBubble API Key"
4. Paste your `vb_live_...` key
5. Click "Save Key"
6. The page reloads - you're authenticated!

## Capturing Content

### From the Extension Popup
1. Navigate to any webpage
2. Click the VaultBubbles icon
3. Click "Capture Page"

### From the Context Menu
1. Right-click anywhere on a page
2. Select "Save to VaultBubbles"

### Saving Selected Text
1. Highlight text on any page
2. Right-click the selection
3. Choose "Save Selection to VaultBubbles"

## Using the Topics Map

The main page shows your knowledge as nested bubbles:

**Bubble Sizes:**
- Large = Categories (broad topics)
- Medium = Sub-topics (specific themes)
- Small = Individual documents

**How to Navigate:**
- Click a large bubble to zoom into that topic
- Click a small bubble to open the document
- Click outside bubbles to zoom back out
- Use "Days" filter to show recent content only

## Querying Your Vault

### Scoped Questions
1. Click on any topic bubble to zoom in
2. The "Ask within this scope" panel appears at the bottom
3. Type a question like "What are the key points here?"
4. Press Ctrl+Enter or click "Ask"
5. AI searches only documents in that topic

### Question Examples
- "Summarize what I've saved about machine learning"
- "What were the main arguments in these articles?"
- "Find mentions of specific names or dates"
- "What patterns do you see across these documents?"

## Managing Documents

### Opening a Document
Click any small bubble (document) to open the viewer.

### Editing
- Change the title by editing the text at the top
- Modify content in the text area
- Click "Save" to keep changes

### Assigning Topics
- Click "Show Topics" to expand topic options
- Select from existing topics, or
- Click "Generate with AI" for a suggestion
- Click "Accept" or "Reject" the suggestion

### Deleting
Click "Delete" and confirm to permanently remove a document.

## Settings Reference

Access via Settings button:

| Setting | What it does |
|---------|--------------|
| VaultBubble API Key | Your authentication (vb_live_...) |
| Chat Provider | AI for answering questions |
| Topic Provider | AI for generating topic names |
| Embedding Provider | How documents are compared |
| OpenAI API Key | Required for OpenAI features |

**Recommended defaults:**
- Chat: OpenAI
- Topics: OpenAI  
- Embeddings: HuggingFace

## Tips for Best Results

### Capture Quality Content
- Save articles, not homepages
- Longer content clusters better than short snippets
- Capture related content to build topic clusters

### Review Periodically
- Click "Reload" to re-cluster new documents
- Check for orphan documents (single-item topics)
- Merge similar topics by re-assigning documents

### Ask Good Questions
- Be specific: "What did X say about Y?" 
- Ask for summaries: "Summarize the main points"
- Compare: "What are the different views on X?"

## Troubleshooting

**"Unauthorized" error?**
-> Re-enter your API key in Settings

**Document not showing in topics?**
-> Click "Reload" to trigger re-clustering

**Extension not working?**
-> Check Options -> make sure "Cloud" is selected

**Page looks empty?**
-> Check the "Days" filter (try 365 for everything)

---

Happy knowledge building!
"""


def _get_pepper() -> str:
    # Optional server-side secret mixed into the hash so DB leaks don't enable offline guessing.
    # Set as a Fly secret (recommended).
    return os.getenv("VB_API_KEY_PEPPER", "")


def hash_api_key(raw_key: str) -> str:
    # Store only a hash (never raw keys).
    data = (_get_pepper() + raw_key).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def generate_api_key(prefix: str = API_KEY_PREFIX) -> str:
    # URL-safe, high entropy; shown only once to the user.
    return prefix + secrets.token_urlsafe(32)


def generate_extension_token() -> str:
    """Generate a new extension token with vb_ext_ prefix."""
    return EXTENSION_TOKEN_PREFIX + secrets.token_urlsafe(32)


class ApiKeyAuthMiddleware(BaseHTTPMiddleware):
    """
    Protects all endpoints by default using Authorization: Bearer <api_key>,
    except for a small public allowlist.
    """

    def __init__(self, app):
        super().__init__(app)
        # Don't cache auth check - read env var per-request for dynamic config

    async def dispatch(self, request: Request, call_next):
        # Check env var per-request (not cached at init time)
        # Default to false for dev/testing - can be overridden with VB_REQUIRE_AUTH=true
        require_auth_str = os.getenv("VB_REQUIRE_AUTH", "false").lower()
        require_auth = require_auth_str in ("1", "true", "yes", "y")
        
        path = request.url.path or ""

        # Public endpoints (never require auth)
        # Note: /admin/* endpoints use require_bootstrap_token for protection
        if (
            path == "/"
            or path == "/health"
            or path == "/ready"
            or path == "/test-db"
            or path == "/docs"
            or path == "/openapi.json"
            or path == "/redoc"
            or path.startswith("/static")
            or path.startswith("/auth/bootstrap")
            or path.startswith("/admin/")
            or path.startswith("/extension/")  # Extension OAuth flow endpoints
            or path.startswith("/download/")  # Extension ZIP downloads
            or path.startswith("/Screenshots/")  # Screenshot images for docs
        ):
            return await call_next(request)

        # Check for Bearer token
        auth = request.headers.get("authorization") or ""
        has_token = auth.lower().startswith("bearer ")
        
        # If auth is disabled and no token provided, pass through
        if not require_auth and not has_token:
            return await call_next(request)
        
        # If auth required but no token, reject
        if require_auth and not has_token:
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

        # Validate the token (auth required OR token was provided)
        token = auth.split(" ", 1)[1].strip()
        if not token:
            if require_auth:
                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
            return await call_next(request)

        token_hash = hash_api_key(token)

        # Use an independent sync session inside middleware.
        db = SessionLocal()
        try:
            row = None
            is_extension_token = token.startswith(EXTENSION_TOKEN_PREFIX)
            
            if is_extension_token:
                # Check extension_tokens table for vb_ext_* tokens
                row = (
                    db.query(ExtensionToken)
                    .filter(ExtensionToken.token_hash == token_hash)
                    .filter(ExtensionToken.revoked_at == None)  # noqa: E711
                    .first()
                )
            else:
                # Check api_keys table for vb_live_* tokens (and other prefixes)
                row = (
                    db.query(ApiKey)
                    .filter(ApiKey.key_hash == token_hash)
                    .filter(ApiKey.revoked_at == None)  # noqa: E711
                    .first()
                )
            
            if not row:
                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

            # Touch last_used_at for auditability.
            row.last_used_at = datetime.utcnow()
            db.commit()

            # Stash minimal identity on request.state; dependency can load full user.
            request.state.user_id = row.user_id
            if is_extension_token:
                request.state.extension_token_id = row.id
            else:
                request.state.api_key_id = row.id
        finally:
            db.close()

        return await call_next(request)


def require_bootstrap_token(x_bootstrap_token: Optional[str] = Header(None)) -> None:
    expected = os.getenv("VB_BOOTSTRAP_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=500, detail="Server misconfigured: VB_BOOTSTRAP_TOKEN not set")
    if not x_bootstrap_token or x_bootstrap_token != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Get current authenticated user, or None if auth is disabled."""
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        # If auth is disabled, return None instead of raising 401
        require_auth_str = os.getenv("VB_REQUIRE_AUTH", "false").lower()
        require_auth = require_auth_str in ("1", "true", "yes", "y")
        if not require_auth:
            return None
        raise HTTPException(status_code=401, detail="Unauthorized")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user


# ---------- Vault Helpers ----------

# Role hierarchy: owner > admin > editor > viewer
ROLE_LEVELS = {
    "owner": 100,
    "admin": 75,
    "editor": 50,
    "viewer": 25,
}


def role_level(role: str) -> int:
    """Get numeric level for a role. Higher = more permissions."""
    return ROLE_LEVELS.get(role, 0)


def copy_template_vault_to_user(template_vault: Vault, new_user: User, new_vault: Vault, db: Session) -> bool:
    """
    Copy all documents, chunks, topics, and embeddings from template vault to new user's vault.
    
    Returns True if successful, False if failed (caller should fall back to old behavior).
    """
    try:
        from .config import PREFERENCES
        from .models import get_or_create_embedding_class
        #import uuid
        
        # Get embedding model config
        embed_config = PREFERENCES.embedding
        model_config = PREFERENCES.models
        embed_model = model_config.embedding_model
        embed_dim = model_config.embedding_dim
        
        # Map model aliases to actual names and versions
        if embed_model == "all-minilm":
            model_name = "all-minilm"
            version = "v1"
            dim = 384
        elif embed_model == "bge-m3":
            model_name = "bge-m3"
            version = "v1"
            dim = 1024
        else:
            # Use provided dimensions or defaults
            model_name = embed_model
            version = "v1"
            dim = embed_dim if embed_dim else 384
        
        # Get embedding table classes
        ChunkEmbedding = get_or_create_embedding_class(model_name, version, dim, db, chunk_type="chunk")
        SentenceEmbedding = get_or_create_embedding_class(model_name, version, dim, db, chunk_type="sent")
        DocEmbedding = get_or_create_embedding_class(model_name, version, dim, db, chunk_type="doc")
        TopicEmbedding = get_or_create_embedding_class(model_name, version, dim, db, chunk_type="topic")
        
        # 1. Copy documents from template vault
        template_docs = (
            db.query(Document)
            .filter(Document.vault_id == template_vault.id)
            .all()
        )
        
        if not template_docs:
            print("Warning: Template vault has no documents")
            return False
        
        # Map old document IDs to new document IDs
        doc_id_map = {}
        new_docs = []
        
        for old_doc in template_docs:
            new_doc = Document(
                vault_id=new_vault.id,
                created_by=new_user.id,
                url=old_doc.url,
                title=old_doc.title,
                captured_text=old_doc.captured_text,
                extracted_text=old_doc.extracted_text,
                full_text=old_doc.full_text,
                captured_at=old_doc.captured_at,
                # Topic assignments will be updated after we copy topics
                assigned_topic_id=None,
                assigned_topic_title=None,
                topic_manually_assigned=old_doc.topic_manually_assigned,
            )
            db.add(new_doc)
            db.flush()  # Get new_doc.id
            doc_id_map[old_doc.id] = new_doc.id
            new_docs.append(new_doc)
        
        print(f"Copied {len(new_docs)} documents from template vault")
        
        # 2. Copy chunk embeddings (track chunk ID mapping for sentences)
        chunk_id_map = {}  # Map old chunk ID to new chunk ID
        for old_doc_id, new_doc_id in doc_id_map.items():
            old_chunks = (
                db.query(ChunkEmbedding)
                .filter(ChunkEmbedding.document_id == old_doc_id)
                .all()
            )
            
            for old_chunk in old_chunks:
                #new_chunk_id = uuid.uuid4()
                new_chunk = ChunkEmbedding(
                    #id=new_chunk_id,
                    document_id=new_doc_id,
                    chunk_index=old_chunk.chunk_index,
                    chunk_text=old_chunk.chunk_text,
                    summary_text=old_chunk.summary_text,
                    embedding=old_chunk.embedding,
                    created_at=old_chunk.created_at,
                )
                db.add(new_chunk)
                chunk_id_map[old_chunk.id] = new_chunk_id  # Track mapping
        
        print(f"Copied chunk embeddings for {len(doc_id_map)} documents")
        
        # 2b. Copy sentence embeddings (critical for query functionality)
        sentence_count = 0
        for old_chunk_id, new_chunk_id in chunk_id_map.items():
            old_sentences = (
                db.query(SentenceEmbedding)
                .filter(SentenceEmbedding.chunk_id == old_chunk_id)
                .all()
            )
            
            for old_sentence in old_sentences:
                new_sentence = SentenceEmbedding(
                    #id=uuid.uuid4(),
                    chunk_id=new_chunk_id,
                    sent_index=old_sentence.sent_index,
                    sent_text=old_sentence.sent_text,
                    embedding=old_sentence.embedding,
                    created_at=old_sentence.created_at,
                )
                db.add(new_sentence)
                sentence_count += 1
        
        print(f"Copied {sentence_count} sentence embeddings for {len(chunk_id_map)} chunks")
        
        # 3. Copy document-level embeddings
        for old_doc_id, new_doc_id in doc_id_map.items():
            old_doc_emb = (
                db.query(DocEmbedding)
                .filter(DocEmbedding.document_id == old_doc_id)
                .first()
            )
            
            if old_doc_emb:
                new_doc_emb = DocEmbedding(
                    #id=uuid.uuid4(),
                    document_id=new_doc_id,
                    summary_text=old_doc_emb.summary_text,
                    embedding=old_doc_emb.embedding,
                    created_at=old_doc_emb.created_at,
                )
                db.add(new_doc_emb)
        
        print(f"Copied document embeddings for {len(doc_id_map)} documents")
        
        # 4. Copy topics (maintaining hierarchy)
        # Get all topics from template vault's documents
        template_doc_ids = [doc.id for doc in template_docs]
        
        # Find all topics that have documents from template vault
        template_topic_ids = set()
        for doc in template_docs:
            if doc.assigned_topic_id:
                template_topic_ids.add(doc.assigned_topic_id)
        
        if template_topic_ids:
            # Get all topics and their parents (to preserve hierarchy)
            topics_to_copy = []
            topics_seen = set()
            
            def get_topic_with_parents(topic_id):
                """Recursively get topic and all its parents."""
                if topic_id in topics_seen:
                    return
                topics_seen.add(topic_id)
                
                topic = db.query(TopicEmbedding).filter(TopicEmbedding.id == topic_id).first()
                if topic:
                    topics_to_copy.append(topic)
                    if topic.parent_id:
                        get_topic_with_parents(topic.parent_id)
            
            for topic_id in template_topic_ids:
                get_topic_with_parents(topic_id)
            
            # Sort topics by level (highest first) to maintain parent-child order
            topics_to_copy.sort(key=lambda t: t.level_index, reverse=True)
            
            # Map old topic IDs to new topic IDs
            topic_id_map = {}
            
            for old_topic in topics_to_copy:
                new_parent_id = topic_id_map.get(old_topic.parent_id) if old_topic.parent_id else None
                
                new_topic = TopicEmbedding(
                    #id=uuid.uuid4(),
                    vault_id=new_vault.id,  # Set new vault_id for isolation
                    parent_id=new_parent_id,
                    level_index=old_topic.level_index,
                    title_text=old_topic.title_text,
                    summary_text=old_topic.summary_text,
                    document_count=old_topic.document_count,
                    embedding=old_topic.embedding,
                    last_matched_at=old_topic.last_matched_at,
                    match_count=old_topic.match_count,
                    created_at=old_topic.created_at,
                )
                db.add(new_topic)
                db.flush()  # Get new_topic.id
                topic_id_map[old_topic.id] = new_topic.id
            
            print(f"Copied {len(topic_id_map)} topics from template vault")
            
            # 5. Update document topic assignments
            for new_doc in new_docs:
                # Find corresponding old document
                old_doc = next((d for d in template_docs if doc_id_map[d.id] == new_doc.id), None)
                if old_doc and old_doc.assigned_topic_id:
                    new_topic_id = topic_id_map.get(old_doc.assigned_topic_id)
                    if new_topic_id:
                        new_doc.assigned_topic_id = new_topic_id
                        new_doc.assigned_topic_title = old_doc.assigned_topic_title
        
        db.commit()
        print(f"Successfully copied template vault to user {new_user.email}")
        return True
        
    except Exception as e:
        print(f"Error copying template vault: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
        return False


def create_personal_vault(user: User, db: Session) -> Vault:
    """Create a personal vault for a user with owner membership.
    
    Copies documents and embeddings from newuser@example.com template vault if available,
    otherwise falls back to creating a single "Getting Started" document.
    """
    vault = Vault(
        name="Personal Vault",
        owner_id=user.id,
        is_personal=True,
    )
    db.add(vault)
    db.flush()  # Get vault.id before creating membership
    
    membership = VaultMembership(
        vault_id=vault.id,
        user_id=user.id,
        role="owner",
    )
    db.add(membership)
    db.commit()  # Commit vault and membership first
    
    # give new users view access to the newuser@example.com vault.
    if not newuser_vault_access(user, db):  
        print("Failed to give new users view access to the newuser@example.com vault")
        # try to copy the newuser@example.com vault to the new user's vault.
        if not newuser_vault_copy(user, db):
            print("Failed to copy the newuser@example.com vault to the new user's vault")
        else:
            print("Successfully copied the newuser@example.com vault to the new user's vault")
    else:
        print("Successfully gave new users view access to the newuser@example.com vault")

    
    db.refresh(vault)
    return vault

def newuser_vault_access(user: User, db: Session) -> bool:
    """
    give new users view access to the newuser@example.com vault.
    """
    newuser_vault = db.query(Vault).join(User,Vault.owner_id==User.id).filter(User.email == "newuser@example.com").first()

    if newuser_vault:
        current_access = check_vault_access(user, newuser_vault.id, db, "viewer")
        if not current_access:
            current_access = grant_vault_access(user, newuser_vault.id, "viewer", db)
    return current_access

def newuser_vault_copy(user: User, db: Session) -> bool:
    """
    copy the newuser@example.com vault to the new user's vault.
    """
    newuser_vault = db.query(Vault).join(User,Vault.owner_id==User.id).filter(User.email == "newuser@example.com").first()
    if newuser_vault:
        return copy_template_vault_to_user(newuser_vault, user, vault, db)
    return False

def grant_vault_access(user: User, vault_id: UUID, role: str, db: Session) -> bool:
    """
    grant access to specific vault to a specific user.
    """
    membership = VaultMembership(
        vault_id=vault_id,
        user_id=user.id,
        role=role,
    )
    db.add(membership)
    db.commit()

    return check_vault_access(user, vault_id, db, role)


def check_vault_access(
    user: User, 
    vault_id: UUID, 
    db: Session, 
    min_role: str = "viewer"
) -> bool:
    """
    Check if user has at least min_role access to the specified vault.
    
    Returns False if user is None (auth disabled mode returns True for backward compat).
    """
    if user is None:
        # Auth disabled - allow access for backward compatibility
        return True
    
    membership = (
        db.query(VaultMembership)
        .filter(VaultMembership.vault_id == vault_id)
        .filter(VaultMembership.user_id == user.id)
        .first()
    )
    
    if not membership:
        return False
    
    return role_level(membership.role) >= role_level(min_role)


def get_user_accessible_vault_ids(user: User, db: Session, min_role: str = "viewer") -> list[UUID]:
    """
    Get all vault IDs the user can access with at least min_role.
    
    Returns empty list if user is None.
    """
    if user is None:
        return []
    
    memberships = (
        db.query(VaultMembership)
        .filter(VaultMembership.user_id == user.id)
        .all()
    )
    
    return [
        m.vault_id for m in memberships 
        if role_level(m.role) >= role_level(min_role)
    ]


def require_vault_access(
    user: User, 
    vault_id: UUID, 
    db: Session, 
    min_role: str = "viewer"
) -> None:
    """
    Raise HTTPException 403 if user doesn't have required access to vault.
    
    Use in endpoints that operate on specific vaults.
    """
    if not check_vault_access(user, vault_id, db, min_role):
        raise HTTPException(
            status_code=403, 
            detail=f"Access denied: requires at least '{min_role}' role on this vault"
        )

