import hashlib
import os
import secrets
from datetime import datetime
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from sqlalchemy.orm import Session

from .db import SessionLocal, get_db
from .models import ApiKey, User


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
        self._require_auth = os.getenv("VB_REQUIRE_AUTH", "true").lower() in ("1", "true", "yes", "y")

    async def dispatch(self, request: Request, call_next):
        if not self._require_auth:
            return await call_next(request)

        path = request.url.path or ""

        # Public endpoints
        if (
            path == "/"
            or path == "/health"
            or path == "/test-db"
            or path == "/docs"
            or path == "/openapi.json"
            or path == "/redoc"
            or path.startswith("/static")
            or path.startswith("/auth/bootstrap")
        ):
            return await call_next(request)

        auth = request.headers.get("authorization") or ""
        if not auth.lower().startswith("bearer "):
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

        token = auth.split(" ", 1)[1].strip()
        if not token:
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

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
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user

