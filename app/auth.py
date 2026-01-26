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
from .models import ApiKey, User, Vault, VaultMembership


API_KEY_PREFIX = "vb_live_"


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
    """Create a personal vault for a user with owner membership."""
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
    db.commit()
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

