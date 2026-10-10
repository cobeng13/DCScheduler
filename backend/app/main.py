from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
import csv
from datetime import timedelta
import io
import json
import os
from pathlib import Path
import secrets

from fastapi import APIRouter, Depends, FastAPI, File, HTTPException, Request, Response, UploadFile, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, StrictBool, StrictInt, ConfigDict
from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from . import auth, models, moves, online_service as service, reports, schemas
from . import database_archive as archive
from .db import SessionLocal
from .limits import BodyLimitMiddleware, bounded_json, PREFERENCES_BYTES

@asynccontextmanager
async def lifespan(app):
    async def housekeeping():
        while True:
            try:
                await asyncio.to_thread(auth.cleanup_expired)
            except Exception:
                logging.getLogger(__name__).error("Authentication housekeeping failed; check database availability")
            await asyncio.sleep(900)
    task = asyncio.create_task(housekeeping())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="CAMP Online Scheduler", lifespan=lifespan)
app.add_middleware(BodyLimitMiddleware)
api = APIRouter(prefix="/api")
Db = Depends(auth.get_db)
User = Depends(auth.current_user)
Admin = Depends(auth.admin)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; script-src-attr 'none'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
        "font-src 'self' data:; connect-src 'self'; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    )
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    if request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-store, no-transform" if request.url.path == "/api/events" else "no-store"
    return response


@app.exception_handler(IntegrityError)
async def duplicate_error(request, exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=409, content={"detail": "A record with that name already exists, or this record is still in use."})


@app.exception_handler(ValidationError)
async def invalid_payload(request, exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=422, content={"detail": "Invalid batch entry. Check required fields, units, hours and version."})


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=256)
    password: str = Field(min_length=12, max_length=256)


class UserCreate(Credentials):
    is_admin: bool = False


class UserUpdate(BaseModel):
    disabled: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=256)


class ProgramCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    assigned_user_id: int | None = None


class ProgramUpdate(BaseModel):
    assigned_user_id: int | None = None
    version: int


class EntityPayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    program_id: int | None = None
    version: int | None = None


class BatchPayload(BaseModel):
    operations: list[dict] = Field(min_length=1, max_length=200)


@api.post("/schedule/batch")
def schedule_batch(payload: BatchPayload, override_reason: str | None = Query(default=None, max_length=1200), db: Session = Db, user=User):
    results = []
    for operation in payload.operations:
        kind = operation.get("method")
        if kind == "POST":
            item = service.save(db, user, schemas.ScheduleEntryCreate.model_validate(operation.get("entry", {})), override_reason=override_reason)
            results.append(service.serialize(item))
        elif kind == "PUT":
            item = service.save(db, user, schemas.ScheduleEntryUpdate.model_validate(operation.get("entry", {})), operation.get("id"), override_reason)
            results.append(service.serialize(item))
        elif kind == "DELETE":
            item = db.get(models.ScheduleEntry, operation.get("id"))
            if not item:
                raise HTTPException(404, "Schedule entry not found")
            service.remove(db, user, item, operation.get("version"))
            db.flush()
        else:
            raise HTTPException(422, "Invalid batch operation")
    db.commit()
    return results


def program_json(program):
    return {"id": program.id, "name": program.name, "assigned_user_id": program.assigned_user_id, "version": program.version}


def require_program(db, program_id):
    if program_id is None:
        raise HTTPException(422, "Select a program")
    item = db.get(models.Program, program_id)
    if item is None:
        raise HTTPException(404, "Program not found")
    return item


@api.get("/health")
def health(db: Session = Db):
    db.execute(text("SELECT 1"))
    # Readiness checks the migrated schema as well as the database connection.
    db.scalar(select(models.User.id).limit(1))
    return {"ok": True, "revision": os.getenv("APP_REVISION", "unknown")}


@api.post("/auth/login")
def login(payload: Credentials, request: Request, response: Response, db: Session = Db):
    auth.validate_origin(request)
    auth.lock(db)
    username = payload.username.strip().casefold()
    # The direct peer is normally a shared tunnel connector, not an end user.
    # Never use forwarded headers or that peer to lock out unrelated accounts.
    auth.cleanup_rows(db)
    key = auth.digest("account:" + username)
    attempt = db.get(models.LoginAttempt, key)
    if not attempt:
        attempt = models.LoginAttempt(key=key, attempts=0, window_start=auth.now())
        db.add(attempt)
    elif auth.aware(attempt.window_start) < auth.now() - timedelta(minutes=15):
        attempt.attempts, attempt.window_start = 0, auth.now()
    if attempt.attempts >= 10:
        db.commit()  # Persist cleanup even for throttled requests.
        raise HTTPException(429, "Too many sign-in attempts. Try again in 15 minutes.")
    user = db.scalar(select(models.User).where(models.User.username == username))
    valid = auth.verify_password(payload.password, user.password_hash if user else DUMMY_PASSWORD)
    if not user or user.disabled or not valid:
        attempt.attempts += 1
        auth.audit(db, None, "login_failed", "authentication", security=True)
        db.commit()
        raise HTTPException(401, "Invalid username or password")
    attempt.attempts = 0
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    db.execute(delete(models.LoginSession).where(models.LoginSession.expires_at < auth.now()))
    session = models.LoginSession(token_hash=auth.digest(token), user_id=user.id, csrf_token=csrf,
        expires_at=auth.now() + timedelta(hours=12), last_seen=auth.now())
    db.add(session)
    auth.audit(db, user, "logged_in", "authentication", security=True)
    db.commit()
    response.set_cookie(auth.COOKIE, token, httponly=True, secure=os.getenv("COOKIE_SECURE", "true").lower() == "true",
        samesite="lax", max_age=43200, path="/api")
    return {"user": auth.user_json(user), "csrf_token": csrf}


DUMMY_PASSWORD = auth.password_hash(secrets.token_urlsafe(32))


@api.get("/auth/me")
def me(request: Request, user=User):
    return {"user": auth.user_json(user), "csrf_token": request.state.session.csrf_token}


@api.post("/auth/logout")
def logout(request: Request, response: Response, db: Session = Db, user=User):
    db.delete(request.state.session)
    auth.audit(db, user, "logged_out", "authentication", security=True)
    db.commit()
    response.delete_cookie(auth.COOKIE, path="/api")
    return {"ok": True}


@api.post("/auth/password")
def change_password(payload: PasswordChange, request: Request, response: Response, db: Session = Db, user=User):
    if not auth.verify_password(payload.current_password, user.password_hash):
        raise HTTPException(403, "Current password is incorrect")
    user.password_hash = auth.password_hash(payload.password)
    user.must_change_password = False
    auth.revoke(db, user.id)
    auth.audit(db, user, "password_changed", "authentication", security=True)
    db.commit()
    response.delete_cookie(auth.COOKIE, path="/api")
    return {"ok": True, "sign_in_again": True}


@api.get("/admin/users")
def users(db: Session = Db, user=Admin):
    return [auth.user_json(item) for item in db.scalars(select(models.User).order_by(models.User.username))]


@api.post("/admin/users")
def create_user(payload: UserCreate, db: Session = Db, user=Admin):
    username = service.label(payload.username).casefold()
    if len(username) > 100:
        raise HTTPException(422, "Normalized username exceeds 100 characters")
    item = models.User(username=username, password_hash=auth.password_hash(payload.password),
        is_admin=payload.is_admin, disabled=False, must_change_password=True)
    db.add(item)
    db.flush()
    auth.audit(db, user, "created", "user", item.id, after=auth.user_json(item))
    db.commit()
    return auth.user_json(item)


@api.put("/admin/users/{user_id}")
def update_user(user_id: int, payload: UserUpdate, db: Session = Db, user=Admin):
    item = db.get(models.User, user_id)
    if not item:
        raise HTTPException(404, "User not found")
    if item.id == user.id and payload.disabled:
        raise HTTPException(409, "You cannot disable your own account")
    before = auth.user_json(item)
    if payload.disabled is not None:
        item.disabled = payload.disabled
    if payload.password:
        item.password_hash = auth.password_hash(payload.password)
        item.must_change_password = True
    auth.revoke(db, item.id)
    auth.audit(db, user, "password_reset" if payload.password else "updated", "user", item.id,
               before=before, after=auth.user_json(item))
    db.commit()
    return auth.user_json(item)


@api.get("/programs")
def programs(db: Session = Db, user=User):
    return [program_json(p) for p in db.scalars(select(models.Program).order_by(models.Program.name))]


def assigned_user(db, user_id):
    if user_id is not None:
        item = db.get(models.User, user_id)
        if not item or item.disabled:
            raise HTTPException(422, "Assign an enabled user")


@api.post("/admin/programs")
def create_program(payload: ProgramCreate, db: Session = Db, user=Admin):
    assigned_user(db, payload.assigned_user_id)
    item = models.Program(name=service.label(payload.name), assigned_user_id=payload.assigned_user_id)
    db.add(item)
    db.flush()
    auth.audit(db, user, "created", "program", item.id, item.id, after=program_json(item))
    db.commit()
    return program_json(item)


@api.put("/admin/programs/{program_id}")
def assign_program(program_id: int, payload: ProgramUpdate, db: Session = Db, user=Admin):
    item = require_program(db, program_id)
    auth.check_version(item, payload.version)
    assigned_user(db, payload.assigned_user_id)
    before = program_json(item)
    item.assigned_user_id, item.version = payload.assigned_user_id, item.version + 1
    auth.audit(db, user, "assigned", "program", item.id, item.id, before, program_json(item))
    db.commit()
    return program_json(item)


@api.get("/schedule", response_model=list[schemas.ScheduleEntry])
def list_schedule(program_id: int | None = None, section: str | None = None, faculty: str | None = None,
                  room: str | None = None, db: Session = Db, user=User):
    query = select(models.ScheduleEntry).order_by(models.ScheduleEntry.id)
    for field, value in (("program_id", program_id), ("section", section), ("faculty", faculty), ("room", room)):
        if value is not None:
            query = query.where(getattr(models.ScheduleEntry, field) == value)
    return list(db.scalars(query))


@api.get("/schedule/{entry_id}", response_model=schemas.ScheduleEntry)
def get_schedule(entry_id: int, db: Session = Db, user=User):
    item = db.get(models.ScheduleEntry, entry_id)
    if not item:
        raise HTTPException(404, "Schedule entry not found")
    return item


@api.post("/schedule", response_model=schemas.ScheduleEntry)
def create_schedule(payload: schemas.ScheduleEntryCreate, override_reason: str | None = Query(default=None, max_length=1200), db: Session = Db, user=User):
    item = service.save(db, user, payload, override_reason=override_reason)
    db.commit()
    return item


@api.put("/schedule/{entry_id}", response_model=schemas.ScheduleEntry)
def update_schedule(entry_id: int, payload: schemas.ScheduleEntryUpdate, override_reason: str | None = Query(default=None, max_length=1200), db: Session = Db, user=User):
    item = service.save(db, user, payload, entry_id, override_reason)
    db.commit()
    return item


@api.delete("/schedule/{entry_id}")
def delete_schedule(entry_id: int, version: int, db: Session = Db, user=User):
    item = db.get(models.ScheduleEntry, entry_id)
    if not item:
        raise HTTPException(404, "Schedule entry not found")
    service.remove(db, user, item, version)
    db.commit()
    return {"ok": True}


@api.post("/schedule/{entry_id}/move", response_model=schemas.MeetingMoveResult)
def move_meeting(entry_id: int, payload: schemas.MeetingMove, override_reason: str | None = Query(default=None, max_length=1200), db: Session = Db, user=User):
    result = moves.move(db, user, entry_id, payload, override_reason)
    db.commit()
    return result


@api.post("/schedule/{entry_id}/move/revert")
def revert_meeting_move(entry_id: int, payload: schemas.MeetingMoveSnapshot, db: Session = Db, user=User):
    result = moves.revert(db, user, entry_id, payload)
    db.commit()
    return result


@api.post("/schedule/{entry_id}/move-check")
def move_check(entry_id: int, payload: schemas.ScheduleEntryCreate, db: Session = Db, user=User):
    if entry_id:
        old = db.get(models.ScheduleEntry, entry_id)
        if not old:
            raise HTTPException(404, "Schedule entry not found")
        auth.editable(db, user, old.program_id)
    candidate = service.build_candidate(db, user, payload)
    found = service.candidate_conflicts(db, candidate, entry_id)
    return {"ok": not found, "reason": "conflict" if found else None, "conflicts": found}


ENTITY_TYPES = {"sections": models.Section, "faculty": models.Faculty, "rooms": models.Room}


def entity_class(kind):
    if kind not in ENTITY_TYPES:
        raise HTTPException(404, "Unknown catalog")
    return ENTITY_TYPES[kind]


@api.get("/catalog/{kind}")
def list_entities(kind: str, program_id: int | None = None, db: Session = Db, user=User):
    cls = entity_class(kind)
    query = select(cls).order_by(cls.name)
    if cls is models.Section and program_id is not None:
        query = query.where(cls.program_id == program_id)
    return [service.entity_json(item) for item in db.scalars(query)]


@api.post("/catalog/{kind}")
def create_entity(kind: str, payload: EntityPayload, db: Session = Db, user=User):
    cls = entity_class(kind)
    if cls is models.Section:
        auth.editable(db, user, require_program(db, payload.program_id).id)
    if cls is models.Room and not user.is_admin:
        raise HTTPException(403, service.ROOM_CREATE_ADMIN_MESSAGE)
    name = service.label(payload.name)
    if cls is not models.Section and service.normalized(name) == "tba":
        raise HTTPException(422, "TBA is an unassigned resource")
    query = select(cls).where(cls.normalized_name == service.normalized(name))
    if cls is models.Section:
        query = query.where(cls.program_id == payload.program_id)
    if db.scalar(query):
        raise HTTPException(409, "That name already exists")
    item = service.resolve(db, cls, name, payload.program_id, create=True)
    auth.audit(db, user, "created", kind, item.id, payload.program_id if cls is models.Section else None,
               after=service.entity_json(item))
    db.commit()
    return service.entity_json(item)


@api.put("/catalog/{kind}/{entity_id}")
def update_entity(kind: str, entity_id: int, payload: EntityPayload, merge: bool = False, db: Session = Db, user=User):
    cls = entity_class(kind)
    item = db.get(cls, entity_id)
    if not item:
        raise HTTPException(404, "Record not found")
    if cls is models.Section:
        auth.editable(db, user, item.program_id)
    elif not user.is_admin:
        raise HTTPException(403, "Only administrators can rename or merge shared resources")
    auth.check_version(item, payload.version)
    name = service.label(payload.name)
    if cls is not models.Section and service.normalized(name) == "tba":
        raise HTTPException(422, "TBA is an unassigned resource")
    before = service.entity_json(item)
    field = {"sections": "section", "faculty": "faculty", "rooms": "room"}[kind]
    affected = list(db.scalars(select(models.ScheduleEntry).where(getattr(models.ScheduleEntry, field + "_id") == item.id)))
    target = service.resolve(db, cls, name, item.program_id if cls is models.Section else None, False) if merge else item
    if merge and cls is models.Section:
        raise HTTPException(422, "Sections cannot be merged")
    for entry in affected:
        old = service.serialize(entry)
        setattr(entry, field, target.name if merge else name)
        setattr(entry, field + "_id", target.id)
        entry.version += 1
        # Flush before checking every changed row; the whole operation rolls back on conflict.
        db.flush()
        service.validate_conflicts(db, user, entry, entry.id)
        auth.audit(db, user, "updated", "schedule", entry.id, entry.program_id, old, service.serialize(entry))
    if merge and target.id != item.id:
        service.remove_rule_exception(db, user, kind, item.id)
        db.delete(item)
    else:
        item.name, item.normalized_name, item.version = name, service.normalized(name), item.version + 1
    auth.audit(db, user, "merged" if merge else "renamed", kind, entity_id,
               item.program_id if cls is models.Section else None, before, service.entity_json(target))
    db.commit()
    return service.entity_json(target)


@api.delete("/catalog/{kind}/{entity_id}")
def delete_entity(kind: str, entity_id: int, version: int, force: bool = False, db: Session = Db, user=User):
    cls = entity_class(kind)
    item = db.get(cls, entity_id)
    if not item:
        raise HTTPException(404, "Record not found")
    if cls is models.Section:
        auth.editable(db, user, item.program_id)
    elif not user.is_admin:
        raise HTTPException(403, "Only administrators can delete shared resources")
    auth.check_version(item, version)
    field = {"sections": "section_id", "faculty": "faculty_id", "rooms": "room_id"}[kind]
    entries = list(db.scalars(select(models.ScheduleEntry).where(getattr(models.ScheduleEntry, field) == item.id)))
    if entries and not force:
        raise HTTPException(409, "Record has scheduled classes")
    # Shared resources are never force-deleted with other programs' schedules.
    if entries and cls is not models.Section:
        raise HTTPException(409, "Remove resource assignments before deleting a shared resource")
    for entry in entries:
        service.remove(db, user, entry, entry.version)
    auth.audit(db, user, "deleted", kind, item.id, item.program_id if cls is models.Section else None,
               before=service.entity_json(item))
    service.remove_rule_exception(db, user, kind, item.id)
    db.delete(item)
    db.commit()
    return {"ok": True}


# Explicit aliases retain the local UI's list operations without exposing legacy routes.
for kind in ENTITY_TYPES:
    def install_alias(kind):
        @api.get(f"/{kind}")
        def get_alias(program_id: int | None = None, db: Session = Db, user=User):
            return list_entities(kind, program_id, db, user)
        @api.post(f"/{kind}")
        def post_alias(payload: EntityPayload, db: Session = Db, user=User):
            return create_entity(kind, payload, db, user)
        @api.put(f"/{kind}/{{entity_id}}")
        def put_alias(entity_id: int, payload: EntityPayload, merge: bool = False, db: Session = Db, user=User):
            return update_entity(kind, entity_id, payload, merge, db, user)
        @api.delete(f"/{kind}/{{entity_id}}")
        def delete_alias(entity_id: int, version: int, force: bool = False, db: Session = Db, user=User):
            return delete_entity(kind, entity_id, version, force, db, user)
        @api.post(f"/{kind}/{{entity_id}}/remove")
        def remove_alias(entity_id: int, version: int, force: bool = False, db: Session = Db, user=User):
            return delete_entity(kind, entity_id, version, force, db, user)
    install_alias(kind)


@api.get("/conflicts")
def conflicts(db: Session = Db, user=User):
    return {"conflicts": service.all_conflicts(db)}


@api.get("/settings")
def get_settings(program_id: int | None = None, db: Session = Db, user=User):
    settings = json.loads(user.preferences_json)
    version = None
    if program_id is not None:
        program = require_program(db, program_id)
        settings.update(json.loads(program.settings_json))
        version = program.version
    settings["conflictIgnore"], _ = service.scheduling_rules(db)
    return {"settings": settings, "version": version}


@api.put("/settings")
def put_settings(payload: dict, program_id: int | None = None, db: Session = Db, user=User):
    bounded_json(payload)
    settings = payload.get("settings", {})
    if not isinstance(settings, dict):
        raise HTTPException(422, "Invalid settings")
    if "curriculumState" in settings:
        program = auth.editable(db, user, require_program(db, program_id).id)
        auth.check_version(program, payload.get("version"))
        state = settings["curriculumState"]
        if not isinstance(state, dict):
            raise HTTPException(422, "Invalid curriculum")
        curricula = state.get("curricula", [])
        if not isinstance(curricula, list) or len(curricula) > 100:
            raise HTTPException(422, "Curriculum limit is 100 curricula")
        total_courses = 0
        for curriculum in curricula:
            if not isinstance(curriculum, dict) or not isinstance(curriculum.get("courses", []), list):
                raise HTTPException(422, "Invalid curriculum courses")
            total_courses += len(curriculum.get("courses", []))
            if total_courses > 2000:
                raise HTTPException(422, "Curriculum limit is 2000 courses")
            for course in curriculum.get("courses", []):
                if not isinstance(course, dict):
                    raise HTTPException(422, "Invalid curriculum course")
                for key, maximum in {"program": 200, "courseCode": 100, "courseDescription": 2000,
                                     "yearLevel": 100, "semester": 100, "unitNotes": 200,
                                     "prerequisite": 2000}.items():
                    if key in course and (not isinstance(course[key], str) or len(course[key]) > maximum):
                        raise HTTPException(422, f"Invalid curriculum course {key}; maximum {maximum} characters")
                if course.get("program") != program.name:
                    raise HTTPException(403, "Curriculum contains a different program")
        ids = []
        for curriculum in curricula:
            identity = curriculum.get("id")
            if not isinstance(identity, str) or not identity or len(identity) > 200 or identity in ids:
                raise HTTPException(422, "Curricula require unique identifiers of 1–200 characters")
            ids.append(identity)
            for field, maximum in {"name": 200, "sourceFileName": 255}.items():
                if field in curriculum and (not isinstance(curriculum[field], str) or len(curriculum[field]) > maximum):
                    raise HTTPException(422, f"Invalid curriculum {field}")
        for field in ("yearLevelCurriculumIds", "sectionCurriculumIds", "sectionYearLevels"):
            mapping = state.get(field, {})
            if not isinstance(mapping, dict) or any(not isinstance(value, str) or len(value) > 200 for value in mapping.values()):
                raise HTTPException(422, "Invalid curriculum assignments")
            if field != "sectionYearLevels" and any(value and value not in ids for value in mapping.values()):
                raise HTTPException(422, "Curriculum assignment references a missing curriculum")
        old = json.loads(program.settings_json)
        program.settings_json = json.dumps({"curriculumState": state})
        program.version += 1
        auth.audit(db, user, "updated", "curriculum", program.id, program.id, old, {"curriculumState": state})
    if "customize" in settings:
        if not isinstance(settings["customize"], dict):
            raise HTTPException(422, "Invalid personal preferences")
        user.preferences_json = bounded_json({"customize": settings["customize"]}, PREFERENCES_BYTES)
    # Legacy conflictIgnore values are not writable here; global rules use the admin API.
    db.commit()
    return get_settings(program_id, db, user)


class SchedulingRulesPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1, strict=True)
    ignoreRoom: StrictBool
    ignoreFaculty: StrictBool
    ignoreRoomIds: list[StrictInt] | None = Field(default=None, max_length=500)
    ignoreFacultyIds: list[StrictInt] | None = Field(default=None, max_length=500)


@api.get("/rules")
def public_rules(db: Session = Db, user=User):
    rules, version = service.scheduling_rules(db)
    return {"rules": rules, "version": version, "section_override": False}


@api.get("/admin/rules")
def get_rules(db: Session = Db, user=Admin):
    return public_rules(db, user)


@api.put("/admin/rules")
def put_rules(payload: SchedulingRulesPayload, db: Session = Db, user=Admin):
    before, version = service.scheduling_rules(db)
    if payload.version != version:
        raise HTTPException(409, {"code": "stale_version", "message": "Scheduling rules changed. Review the current settings and try again."})
    after = {**before, **payload.model_dump(exclude={"version"}, exclude_none=True)}
    for field, cls in (("ignoreRoomIds", models.Room), ("ignoreFacultyIds", models.Faculty)):
        ids = after[field]
        if any(value <= 0 for value in ids) or len(set(ids)) != len(ids):
            raise HTTPException(422, "Exception lists require unique positive resource IDs")
        if ids and set(db.scalars(select(cls.id).where(cls.id.in_(ids)))) != set(ids):
            raise HTTPException(422, "Select existing rooms and faculty for conflict exceptions")
        after[field] = sorted(ids)
    if after != before:
        item = db.get(models.AppSettings, 1)
        if item is None:
            item = models.AppSettings(id=1)
            db.add(item)
        item.settings_json = json.dumps({**after, "rulesVersion": version + 1})
        auth.audit(db, user, "updated", "scheduling_rules", 1, before=before, after=after)
        db.commit()
    return public_rules(db, user)


@api.post("/file/import-csv")
async def import_csv(file: UploadFile = File(...), program_id: int | None = None, replace: bool = False,
                     preview: bool = False, db: Session = Db, user=User):
    program = auth.editable(db, user, require_program(db, program_id).id)
    raw = await file.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(413, "CSV limit is 5 MB")
    try:
        decoded = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        decoded = raw.decode("cp1252")
    reader = csv.DictReader(io.StringIO(decoded))
    headers = {h.strip().casefold(): h for h in (reader.fieldnames or []) if h}
    required = [h for h in schemas.CANONICAL_HEADERS if h != "Time (24 Hrs)"]
    missing = [h for h in required if h.casefold() not in headers]
    summary = {"rows_total": 0, "rows_imported": 0, "rows_skipped": 0, "missing_columns": missing, "errors": []}
    if missing:
        raise HTTPException(422, summary)
    # The outer request transaction owns the advisory lock. A savepoint allows
    # preview rollback while retaining that lock and checking rows against rows.
    with db.begin_nested() as batch:
        if replace:
            for entry in list(db.scalars(select(models.ScheduleEntry).where(models.ScheduleEntry.program_id == program.id))):
                service.remove(db, user, entry, entry.version)
            db.flush()
        for index, row in enumerate(reader, start=2):
            summary["rows_total"] += 1
            if summary["rows_total"] > 10000:
                raise HTTPException(413, "CSV limit is 10,000 rows")
            values = {h: (row.get(headers.get(h.casefold(), "")) or "").strip() for h in schemas.CANONICAL_HEADERS}
            try:
                if values["Program"] != program.name:
                    raise HTTPException(403, "Every CSV row must belong to the selected program")
                values["Units"] = float(values["Units"] or 0)
                values["# of Hours"] = float(values["# of Hours"] or 0)
                payload = schemas.ScheduleEntryCreate.model_validate(values)
                service.save(db, user, payload, allow_catalog_add=True)
                summary["rows_imported"] += 1
            except (ValueError, HTTPException) as exc:
                summary["errors"].append({"row_index": index, "reason": str(exc.detail if isinstance(exc, HTTPException) else exc)})
                # Stop at the first invalid row: no partially valid replacement.
                summary["rows_imported"] = 0
                summary["rows_skipped"] = summary["rows_total"]
                batch.rollback()
                if preview:
                    return summary
                raise HTTPException(422, summary) from exc
        if preview:
            batch.rollback()
        elif not summary["rows_total"]:
            batch.rollback()
            raise HTTPException(422, "Empty CSV cannot replace a timetable. Ask an administrator to clear classes in Administration.")
        else:
            auth.audit(db, user, "imported", "csv", program_id=program.id, after={"rows": summary["rows_imported"], "replace": replace})
    db.commit()
    return summary


class PasswordConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=256)


@api.post("/admin/database/backup")
def backup_database(db: Session = Db, user=Admin):
    auth.audit(db, user, "backup_created", "database", security=True)
    db.flush()
    file = archive.create(db)
    try:
        db.commit()
    except Exception:
        file.close()
        raise
    async def chunks():
        try:
            while chunk := file.read(64 * 1024):
                yield chunk
        finally:
            file.close()
    filename = f"scheduler-{auth.now():%Y%m%dT%H%M%SZ}.scheduler-backup"
    return StreamingResponse(chunks(), media_type="application/octet-stream",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@api.post("/admin/database/restore")
async def restore_database(request: Request, db: Session = Db, user=Admin):
    archive.reauthenticate(db, user, request.headers.get("X-Admin-Password"))
    if request.headers.get("X-Restore-Confirmation") != "REPLACE DATABASE":
        raise HTTPException(422, "Confirm replacement of the entire database")
    actor = user.username
    with archive.temporary_file() as file:
        size = 0
        try:
            async for chunk in request.stream():
                size += len(chunk)
                if size > archive.MAX_BYTES:
                    raise HTTPException(413, "Browser restores support files up to 12 MiB; use the server restore script for larger backups")
                file.write(chunk)
            work = asyncio.create_task(asyncio.to_thread(archive.restore, db, file, actor))
            try:
                rows = await asyncio.shield(work)
            except asyncio.CancelledError:
                # Do not close the file/session while the SQL worker still uses them.
                try:
                    await work
                finally:
                    db.rollback()
                raise
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(422, "Backup contains invalid or duplicate records. The current database was not changed.")
        except OSError:
            db.rollback()
            raise HTTPException(507, "Temporary restore storage is full. The current database was not changed.")
        except SQLAlchemyError:
            try:
                db.rollback()
            except SQLAlchemyError:
                pass
            raise HTTPException(503, "Database restore could not be confirmed. Check server health before trying again.")
        except HTTPException:
            db.rollback()
            raise
    try:
        health(db)
    except SQLAlchemyError:
        raise HTTPException(503, "Database restored, but readiness check failed. Check server health before signing in.")
    response = Response(content=json.dumps({"ok": True, "rows": rows, "ready": True, "sign_in_required": True}), media_type="application/json")
    response.delete_cookie(auth.COOKIE, path="/api")
    return response


@api.post("/admin/timetable/clear")
def clear_timetable(payload: PasswordConfirmation, db: Session = Db, user=Admin):
    archive.reauthenticate(db, user, payload.password)
    count = 0
    for entry in db.scalars(select(models.ScheduleEntry).execution_options(yield_per=200)):
        service.remove(db, user, entry, entry.version)
        count += 1
        if count % 200 == 0:
            db.flush()
    auth.audit(db, user, "cleared", "timetable", before={"classes": count}, after={"classes": 0})
    db.commit()
    return {"ok": True, "deleted": count}


@api.post("/file/reset")
def clear_program(payload: PasswordConfirmation, program_id: int, db: Session = Db, user=Admin):
    archive.reauthenticate(db, user, payload.password)
    auth.editable(db, user, program_id)
    for entry in list(db.scalars(select(models.ScheduleEntry).where(models.ScheduleEntry.program_id == program_id))):
        service.remove(db, user, entry, entry.version)
    auth.audit(db, user, "cleared", "program_schedule", program_id, program_id)
    db.commit()
    return {"ok": True}


@api.api_route("/file/import", methods=["POST"])
@api.api_route("/file/export", methods=["GET"])
def database_files(user=User):
    raise HTTPException(410, "Legacy database-file Open/Save is unavailable online. Admins can use Administration for full backups and restores; schedules can be exported as CSV.")


@api.get("/reports/text.csv")
@api.get("/reports/timetable/{group}.csv")
def csv_export(group: str = "section", program_id: int | None = None, filter_value: str | None = None, db: Session = Db, user=User):
    if group not in {"section", "faculty", "room"}:
        raise HTTPException(422, "Invalid group")
    entries = list_schedule(program_id, db=db, user=user)
    if filter_value:
        entries = [entry for entry in entries if getattr(entry, group) == filter_value]
    content = reports.write_csv(reports.build_text_rows([service.serialize(e) for e in entries]))
    return Response(content, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="schedule.csv"'})


@api.get("/reports/faculty-load.html")
def faculty_export(faculty: str, db: Session = Db, user=User):
    entries = list_schedule(faculty=faculty, db=db, user=user)
    return Response(reports.build_faculty_load_html(faculty, [service.serialize(e) for e in entries]), media_type="text/html",
        headers={"Content-Disposition": 'attachment; filename="faculty-load.html"'})


@api.post("/export/png")
def png_export(payload: dict, user=User):
    import base64
    import binascii
    encoded = payload.get("png_base64", "")
    if not isinstance(encoded, str):
        raise HTTPException(422, "Invalid PNG")
    encoded = encoded.split(",", 1)[-1]
    if len(encoded) > 14 * 1024 * 1024:
        raise HTTPException(413, "PNG too large")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(422, "Invalid PNG")
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(422, "Invalid PNG")
    return Response(data, media_type="image/png", headers={"Content-Disposition": 'attachment; filename="timetable.png"'})


def activity_json(event):
    before = json.loads(event.before_json) if event.before_json else None
    after = json.loads(event.after_json) if event.after_json else None
    changed = [key for key in set(before or {}) | set(after or {}) if (before or {}).get(key) != (after or {}).get(key)]
    return {"id": event.id, "actor_id": event.actor_id, "actor": event.actor, "program_id": event.program_id,
        "action": event.action, "entity_type": event.entity_type, "entity_id": event.entity_id,
        "before": before, "after": after, "changed_fields": sorted(changed), "reason": event.reason,
        "created_at": auth.aware(event.created_at).isoformat()}


@api.get("/activity")
def activity(after: int = 0, before: int | None = None, program_id: int | None = None, actor_id: int | None = None, q: str = "",
             limit: int = 100, security: bool = False, db: Session = Db, user=User):
    if security and not user.is_admin:
        raise HTTPException(403, "Administrator access required")
    query = select(models.Activity).where(models.Activity.security == security, models.Activity.id > after)
    if before is not None:
        query = query.where(models.Activity.id < before)
    if program_id is not None:
        query = query.where(models.Activity.program_id == program_id)
    if actor_id is not None:
        query = query.where(models.Activity.actor_id == actor_id)
    if q:
        pattern = "%" + q[:200] + "%"
        query = query.where(or_(models.Activity.actor.ilike(pattern), models.Activity.action.ilike(pattern),
            models.Activity.before_json.ilike(pattern), models.Activity.after_json.ilike(pattern)))
    query = query.order_by(models.Activity.id.desc()).limit(max(1, min(limit, 200)))
    return [activity_json(event) for event in db.scalars(query)]


@api.get("/activity/actors")
def activity_actors(db: Session = Db, user=User):
    query = select(models.Activity.actor_id, models.Activity.actor).where(models.Activity.security == False).distinct()
    return [{"id": actor_id, "username": name} for actor_id, name in db.execute(query) if actor_id is not None]


@api.post("/presence/heartbeat")
def heartbeat(request: Request, db: Session = Db, user=User):
    request.state.session.last_seen = auth.now()
    db.commit()
    return {"ok": True}


@api.get("/presence")
def presence(db: Session = Db, user=User):
    query = select(models.User).join(models.LoginSession).where(models.User.disabled == False,
        models.LoginSession.last_seen > auth.now() - timedelta(seconds=90), models.LoginSession.expires_at > auth.now()).distinct()
    return [{"id": item.id, "username": item.username} for item in db.scalars(query)]


@api.get("/events")
async def events(request: Request, after: int = 0, db: Session = Db, user=User):
    try:
        cursor = int(request.headers.get("Last-Event-ID", after))
    except ValueError:
        raise HTTPException(422, "Invalid event cursor")
    token_hash = auth.digest(request.cookies.get(auth.COOKIE, ""))
    user_id = user.id
    # Release the request dependency's connection before a long-lived stream.
    db.close()

    async def stream():
        nonlocal cursor
        yield "retry: 2000\n\n"
        while not await request.is_disconnected():
            with SessionLocal() as db:
                session = db.get(models.LoginSession, token_hash)
                account = db.get(models.User, user_id)
                if not session or auth.aware(session.expires_at) <= auth.now() or not account or account.disabled or account.must_change_password:
                    yield 'event: session-expired\ndata: {}\n\n'
                    return
                rows = list(db.scalars(select(models.Activity).where(models.Activity.id > cursor).order_by(models.Activity.id).limit(200)))
                for event in rows:
                    cursor = event.id
                    if not event.security:
                        yield f"id: {cursor}\nevent: activity\ndata: {json.dumps(activity_json(event))}\n\n"
                if rows:
                    yield f"id: {cursor}\nevent: cursor\ndata: {{}}\n\n"
                else:
                    yield ": heartbeat\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache, no-transform"})


app.include_router(api)
web_dist = Path(__file__).resolve().parent / "web" / "dist"
if (web_dist / "assets").exists():
    app.mount("/assets", StaticFiles(directory=web_dist / "assets"), name="assets")


@app.get("/{path:path}")
def frontend(path: str):
    if path == "api" or path.startswith("api/"):
        raise HTTPException(404, "API route not found")
    index = web_dist / "index.html"
    if not index.exists():
        raise HTTPException(404, "Build the frontend first, or use the Vite development server")
    return FileResponse(index)
