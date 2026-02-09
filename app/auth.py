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


def create_personal_vault(user: User, db: Session) -> Vault:
    """Create a personal vault for a user with owner membership.
    
    Also adds a "Getting Started" document to help new users.
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
    
    # Add Getting Started document to help new users
    getting_started_doc = Document(
        vault_id=vault.id,
        created_by=user.id,
        url="internal://getting-started",
        title="Getting Started with VaultBubbles",
        captured_text=GETTING_STARTED_CONTENT,
        extracted_text=GETTING_STARTED_CONTENT,
        full_text=GETTING_STARTED_CONTENT,
    )
    db.add(getting_started_doc)
    # Commit the document first so embed_doc_chunks can see it in its own session
    db.commit()
    db.refresh(getting_started_doc)
    
    # Create embeddings for the document and assign to topic hierarchy
    try:
        # First, create chunks and embeddings for the document
        chunk_count = embed_doc_chunks(getting_started_doc)
        
        # Now compute the document-level embedding
        doc_embeddings = compute_document_embeddings(db, [getting_started_doc])
        
        if getting_started_doc.id in doc_embeddings:
            centroid = doc_embeddings[getting_started_doc.id]
            
            # Create Level 1 topic (Documentation)
            level1_topic_id = _save_topic_to_db(
                db=db,
                title="Documentation",
                centroid=centroid,
                document_count=1,
                summary=None,
                level_index=1,
                parent_id=None
            )
            
            # Create Level 0 topic (Getting Started Guides)
            level0_topic_id = _save_topic_to_db(
                db=db,
                title="Getting Started Guides",
                centroid=centroid,
                document_count=1,
                summary=None,
                level_index=0,
                parent_id=level1_topic_id
            )
            
            # Assign document to Level 0 topic
            getting_started_doc.assigned_topic_id = level0_topic_id
            getting_started_doc.assigned_topic_title = "Getting Started Guides"
            getting_started_doc.topic_manually_assigned = True
            
            # Commit the topic assignments
            db.commit()
    except Exception as e:
        # If topic creation fails, still create the vault with the document
        # (topic assignment is optional, not critical for vault creation)
        print(f"Warning: Failed to create topics for Getting Started document: {e}")
        import traceback
        traceback.print_exc()
    
    db.refresh(vault)
    return vault


def get_user_vault(user: User, db: Session) -> Optional[Vault]:
    """
    Get the user's personal vault (create if doesn't exist).
    
    Returns None if user is None (auth disabled mode).
    """
    if user is None:
        return None
    
    # Look for user's personal vault through membership
    vault = (
        db.query(Vault)
        .join(VaultMembership, VaultMembership.vault_id == Vault.id)
        .filter(VaultMembership.user_id == user.id)
        .filter(Vault.is_personal == True)  # noqa: E712
        .filter(Vault.archived_at == None)  # noqa: E711
        .first()
    )
    
    if not vault:
        vault = create_personal_vault(user, db)
    
    return vault


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

