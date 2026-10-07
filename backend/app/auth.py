"""Cookie sessions, CSRF, password hashing and database-backed throttling."""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import os
import secrets
from fastapi import Depends, HTTPException, Request
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session
from .db import SessionLocal
from . import models

COOKIE = "scheduler_session"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def now():
    return datetime.now(timezone.utc)


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password):
    if not 12 <= len(password) <= 256:
        raise HTTPException(422, "Password must contain 12–256 characters")
    salt = secrets.token_hex(16)
    value = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
    return f"scrypt${salt}${value}"


def verify_password(password, stored):
    if len(password) > 256:
        return False
    try:
        _, salt, expected = stored.split("$")
        value = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
        return hmac.compare_digest(value, expected)
    except (ValueError, TypeError):
        return False


def get_db():
    with SessionLocal() as db:
        yield db


def lock(db: Session):
    # All mutation paths use this lock, acquired BEFORE reading mutable records.
    # A single transaction lock also gives committed activity IDs a stable order.
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(736243901)"))
    elif not db.info.get("locked"):
        db.execute(text("BEGIN IMMEDIATE"))
        db.info["locked"] = True


def current_user(request: Request, db: Session = Depends(get_db)):
    if request.method not in SAFE_METHODS:
        lock(db)
    token = request.cookies.get(COOKIE, "")
    session = db.get(models.LoginSession, digest(token)) if token else None
    if not session or aware(session.expires_at) <= now():
        raise HTTPException(401, "Sign in required")
    user = db.get(models.User, session.user_id)
    if not user or user.disabled:
        raise HTTPException(401, "Account unavailable")
    if request.method not in SAFE_METHODS:
        if not hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), session.csrf_token):
            raise HTTPException(403, "Invalid CSRF token")
        expected = os.environ.get("PUBLIC_ORIGIN")
        if expected and request.headers.get("origin") not in {None, expected}:
            raise HTTPException(403, "Invalid origin")
    if user.must_change_password and request.url.path not in {
        "/api/auth/me", "/api/auth/password", "/api/auth/logout"
    }:
        raise HTTPException(403, "Change your initial password first")
    request.state.session = session
    return user


def admin(user=Depends(current_user)):
    if not user.is_admin:
        raise HTTPException(403, "Administrator access required")
    return user


def user_json(user):
    return {"id": user.id, "username": user.username, "is_admin": user.is_admin,
            "disabled": user.disabled, "must_change_password": user.must_change_password}


def revoke(db, user_id):
    db.execute(delete(models.LoginSession).where(models.LoginSession.user_id == user_id))


def editable(db, user, program_id):
    program = db.get(models.Program, program_id)
    if not program:
        raise HTTPException(404, "Program not found")
    if not user.is_admin and program.assigned_user_id != user.id:
        raise HTTPException(403, "You can edit only your assigned programs")
    return program


def check_version(instance, version):
    if version is None:
        raise HTTPException(428, {"code": "version_required", "message": "Reload before saving"})
    if instance.version != version:
        raise HTTPException(409, {"code": "stale_version", "message": "This record changed. Reload and review your unsaved changes.", "version": instance.version})


def audit(db, user, action, kind, entity_id=None, program_id=None, before=None, after=None, reason=None, security=False):
    import json
    event = models.Activity(actor_id=user.id if user else None, actor=user.username if user else "system",
        action=action, entity_type=kind, entity_id=entity_id, program_id=program_id,
        before_json=json.dumps(before, ensure_ascii=False) if before is not None else None,
        after_json=json.dumps(after, ensure_ascii=False) if after is not None else None,
        reason=reason, security=security)
    db.add(event)
    return event
