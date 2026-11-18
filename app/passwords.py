"""Password sign-in (batch29): email + password -> the same long-lived token the code flow issues.

    POST /auth/login        {email, password}                    -> {token, email}   (web app)
    POST /extension/login   {email, password}                    -> 302 /extension/success?token=...
    POST /auth/password     {new_password, current_password?}    -> {ok}   (signed in; sets or changes it)

Passwords are stored as PBKDF2-SHA256 (600 000 iterations, 16-byte random salt) in
users.password_hash (migration 009). No sign-up by password: accounts still start from the
emailed-code flow, which stays available. Repeated failures for one email are locked out briefly.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import threading
import time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy.orm import Session

from . import models
from .auth import generate_extension_token, get_current_user, hash_api_key
from .db import get_db

router = APIRouter(tags=["auth"])
bearer_scheme = HTTPBearer(auto_error=False)

ITERATIONS = 600_000
MIN_LENGTH = 8
MAX_FAILS = 5          # failures per email before a lockout
LOCK_SECONDS = 60      # lockout length, doubling while failures continue

_fails: dict = {}      # email -> (count, locked_until)
_fails_lock = threading.Lock()


# ---------------------------------------------------------------- hashing
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(ITERATIONS, base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_password(password: str, stored: str | None) -> bool:
    try:
        algo, iters, salt_b64, hash_b64 = (stored or "").split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), base64.b64decode(salt_b64), int(iters))
        return hmac.compare_digest(dk, base64.b64decode(hash_b64))
    except Exception:  # noqa: BLE001 - malformed or missing hash
        return False


# ---------------------------------------------------------------- lockout
def _check_locked(email: str) -> None:
    with _fails_lock:
        count, until = _fails.get(email, (0, 0.0))
    if until > time.monotonic():
        raise HTTPException(status_code=429, detail=f"Too many attempts; try again in {int(until - time.monotonic()) + 1} s")


def _record(email: str, ok: bool) -> None:
    with _fails_lock:
        if ok:
            _fails.pop(email, None)
            return
        count, _ = _fails.get(email, (0, 0.0))
        count += 1
        until = time.monotonic() + LOCK_SECONDS * (2 ** (count - MAX_FAILS)) if count >= MAX_FAILS else 0.0
        _fails[email] = (count, until)


# ---------------------------------------------------------------- endpoints
class LoginRequest(BaseModel):
    email: str
    password: str


class SetPasswordRequest(BaseModel):
    new_password: str
    current_password: str | None = None


def _authenticate(db: Session, req: LoginRequest) -> models.User:
    email = (req.email or "").strip().lower()
    if not email or not req.password:
        raise HTTPException(status_code=400, detail="Email and password are required")
    _check_locked(email)
    user = db.query(models.User).filter(models.User.email == email).first()
    ok = bool(user) and verify_password(req.password, getattr(user, "password_hash", None))
    _record(email, ok)
    if not ok:
        raise HTTPException(status_code=401, detail="Email or password is incorrect")
    return user


def _issue_token(db: Session, user: models.User, name: str) -> str:
    raw = generate_extension_token()
    db.add(models.ExtensionToken(user_id=user.id, token_hash=hash_api_key(raw), name=name))
    db.commit()
    return raw


@router.post("/auth/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = _authenticate(db, req)
    return {"token": _issue_token(db, user, "Web sign-in (password)"), "email": user.email}


@router.post("/extension/login")
def extension_login(req: LoginRequest, db: Session = Depends(get_db)):
    """Password sign-in for the browser extensions: ends on the same success URL as the code flow."""
    user = _authenticate(db, req)
    return RedirectResponse(url=f"/extension/success?token={_issue_token(db, user, 'Browser Extension (password)')}",
                            status_code=302)


@router.post("/auth/password")
def set_password(req: SetPasswordRequest, db: Session = Depends(get_db), user: models.User = Depends(get_current_user),
                 _: HTTPAuthorizationCredentials = Depends(bearer_scheme)):
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if len(req.new_password or "") < MIN_LENGTH:
        raise HTTPException(status_code=400, detail=f"Use at least {MIN_LENGTH} characters")
    u = db.query(models.User).filter(models.User.id == user.id).first()
    if u.password_hash:
        if not verify_password(req.current_password or "", u.password_hash):
            raise HTTPException(status_code=403, detail="Current password is incorrect")
    u.password_hash = hash_password(req.new_password)
    u.password_set_at = datetime.utcnow()
    db.commit()
    return {"ok": True}
