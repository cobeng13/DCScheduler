"""Bounded data-only backups. Uploaded files never supply SQL or schema names."""
import hashlib
import json
import math
import re
import tempfile
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import Boolean, DateTime, Float, Integer, String, and_, func, inspect, or_, select, text

from . import auth, models

MAX_BYTES = 12 * 1024 * 1024
MAX_LINE = 4 * 1024 * 1024
MAX_ROWS = 100000
TABLES = list(models.Base.metadata.sorted_tables)
BY_NAME = {table.name: table for table in TABLES}
FINGERPRINT = hashlib.sha256(json.dumps({table.name: [(c.name, str(c.type), c.nullable)
    for c in table.columns] for table in TABLES}, sort_keys=True).encode()).hexdigest()


def reauthenticate(db, user, password):
    if not isinstance(password, str) or not 1 <= len(password) <= 256:
        raise HTTPException(422, "Enter your current administrator password")
    key = auth.digest(f"admin-confirmation:{user.id}")
    attempt = db.get(models.LoginAttempt, key)
    if attempt is None:
        attempt = models.LoginAttempt(key=key, attempts=0, window_start=auth.now())
        db.add(attempt)
    elif auth.aware(attempt.window_start) < auth.now() - timedelta(minutes=15):
        attempt.attempts, attempt.window_start = 0, auth.now()
    if attempt.attempts >= 5:
        raise HTTPException(429, "Too many password confirmations. Try again in 15 minutes.")
    if not auth.verify_password(password, user.password_hash):
        attempt.attempts += 1
        auth.audit(db, user, "password_confirmation_failed", "database", security=True)
        db.commit()
        raise HTTPException(403, "Incorrect administrator password. Nothing was changed.")
    attempt.attempts = 0
    db.flush()


def manifest(db):
    names = set(inspect(db.connection()).get_table_names())
    if names - set(BY_NAME) - {"alembic_version"}:
        raise HTTPException(409, "Database has additional tables; use the server PostgreSQL backup/restore scripts")
    revisions = sorted(db.scalars(text("SELECT version_num FROM alembic_version"))) if "alembic_version" in names else []
    return {"format": "camp-scheduler-database", "version": 1, "schema": FINGERPRINT,
            "migrations": revisions, "tables": list(BY_NAME)}


def temporary_file():
    try:
        return tempfile.TemporaryFile(mode="w+b")
    except OSError:
        raise HTTPException(507, "Temporary backup storage is unavailable")


def create(db):
    file = temporary_file()
    digest = hashlib.sha256()
    counts = {name: 0 for name in BY_NAME}
    total = 0
    def write(value, hashed=True):
        encoded = (json.dumps(value, default=lambda item: item.isoformat(), allow_nan=False, separators=(",", ":")) + "\n").encode()
        if len(encoded) > MAX_LINE or file.tell() + len(encoded) > MAX_BYTES:
            raise HTTPException(413, "Browser backups support up to 12 MiB; use deploy/backup.sh for larger databases")
        file.write(encoded)
        if hashed:
            digest.update(encoded)
    try:
        write(manifest(db))
        for table in TABLES:
            for row in db.execute(select(table).execution_options(yield_per=200)).mappings():
                total += 1
                if total > MAX_ROWS:
                    raise HTTPException(413, "Browser backup row limit exceeded; use deploy/backup.sh")
                write({"table": table.name, "row": dict(row)})
                counts[table.name] += 1
        write({"end": counts, "sha256": digest.hexdigest()}, False)
        file.seek(0)
        return file
    except OSError:
        file.close()
        raise HTTPException(507, "Temporary backup storage is full; use deploy/backup.sh")
    except Exception:
        file.close()
        raise


def decode_row(table, row):
    if not isinstance(row, dict) or set(row) != set(table.columns.keys()):
        raise ValueError("Invalid backup columns")
    result = {}
    for column in table.columns:
        value = row[column.name]
        if value is None:
            if not column.nullable:
                raise ValueError("Missing required backup value")
        elif isinstance(column.type, Boolean):
            if type(value) is not bool:
                raise ValueError("Invalid backup boolean")
        elif isinstance(column.type, Integer):
            if type(value) is not int or not -(2**31) <= value < 2**31:
                raise ValueError("Invalid backup integer")
            if (column.primary_key or column.name == "version") and value < 1:
                raise ValueError("Invalid backup identifier/version")
        elif isinstance(column.type, Float):
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError("Invalid backup number")
        elif isinstance(column.type, DateTime):
            if not isinstance(value, str) or len(value) > 64:
                raise ValueError("Invalid backup date")
            value = datetime.fromisoformat(value)
        elif isinstance(column.type, String):
            if not isinstance(value, str) or "\x00" in value or (column.type.length and len(value) > column.type.length):
                raise ValueError("Invalid backup text")
            value.encode("utf-8")
            if column.name == "username" and (not value or value != value.strip().casefold()):
                raise ValueError("Invalid backup username")
            if column.name.endswith("_json"):
                parsed = json.loads(value)
                if column.name in {"preferences_json", "settings_json"} and not isinstance(parsed, dict):
                    raise ValueError("Invalid backup settings")
            if column.name == "password_hash" and not re.fullmatch(r"scrypt\$[0-9a-f]{32}\$[0-9a-f]{128}", value):
                raise ValueError("Invalid backup password hash")
        result[column.name] = value
    return result


def records(file, expected):
    file.seek(0)
    digest = hashlib.sha256()
    counts = {name: 0 for name in BY_NAME}
    total = 0
    try:
        first = file.readline(MAX_LINE + 1)
        if len(first) > MAX_LINE or json.loads(first) != expected:
            raise ValueError("Backup format or database schema does not match this application")
        digest.update(first)
        while True:
            line = file.readline(MAX_LINE + 1)
            if not line or len(line) > MAX_LINE or file.tell() > MAX_BYTES:
                raise ValueError("Backup is incomplete or exceeds limits")
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("Invalid backup record")
            if "end" in value:
                if value != {"end": counts, "sha256": digest.hexdigest()} or file.read(1):
                    raise ValueError("Backup checksum/counts do not match or contains trailing data")
                break
            if set(value) != {"table", "row"} or not isinstance(value["table"], str) or value["table"] not in BY_NAME:
                raise ValueError("Unknown backup table")
            total += 1
            if total > MAX_ROWS:
                raise ValueError("Backup row limit exceeded")
            digest.update(line)
            counts[value["table"]] += 1
            yield BY_NAME[value["table"]], decode_row(BY_NAME[value["table"]], value["row"])
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError) as exc:
        raise HTTPException(422, "Invalid browser database backup. Use an unchanged .scheduler-backup downloaded from this application with the same schema version.") from exc


def restore(db, file, actor):
    expected = manifest(db)
    # Validate the entire stream and checksum before deleting any current rows.
    for _ in records(file, expected):
        pass
    db.expunge_all()
    for table in reversed(TABLES):
        db.execute(table.delete())
    counts = {name: 0 for name in BY_NAME}
    batch, batch_table = [], None
    for table, row in records(file, expected):
        if batch and (table is not batch_table or len(batch) >= 200):
            db.execute(batch_table.insert(), batch)
            batch = []
        batch_table = table
        batch.append(row)
        counts[table.name] += 1
    if batch:
        db.execute(batch_table.insert(), batch)
    if not db.scalar(select(models.User.id).where(models.User.is_admin.is_(True), models.User.disabled.is_(False)).limit(1)):
        raise HTTPException(422, "Backup must contain an enabled administrator account")
    entry, program, section, room, faculty = models.ScheduleEntry, models.Program, models.Section, models.Room, models.Faculty
    invalid = select(entry.id).join(program, entry.program_id == program.id).join(section, entry.section_id == section.id).outerjoin(
        room, entry.room_id == room.id).outerjoin(faculty, entry.faculty_id == faculty.id).where(or_(
            entry.program_id != section.program_id, entry.program != program.name, entry.section != section.name,
            and_(entry.room_id.is_not(None), entry.room != room.name),
            and_(entry.faculty_id.is_not(None), entry.faculty != faculty.name))).limit(1)
    if db.scalar(invalid):
        raise HTTPException(422, "Backup schedule ownership or resource labels do not match")
    settings = db.get(models.AppSettings, 1)
    if settings:
        rules = json.loads(settings.settings_json)
        for field in ("ignoreRoom", "ignoreFaculty"):
            if field in rules and type(rules[field]) is not bool:
                raise HTTPException(422, "Invalid backup scheduling rules")
        for field, cls in (("ignoreRoomIds", room), ("ignoreFacultyIds", faculty)):
            ids = rules.get(field, [])
            if not isinstance(ids, list) or len(ids) > 500 or any(type(v) is not int or v <= 0 for v in ids) or len(set(ids)) != len(ids):
                raise HTTPException(422, "Invalid backup scheduling exceptions")
            if ids and set(db.scalars(select(cls.id).where(cls.id.in_(ids)))) != set(ids):
                raise HTTPException(422, "Backup scheduling exceptions refer to missing resources")
        if type(rules.get("rulesVersion", 1)) is not int or rules.get("rulesVersion", 1) < 1:
            raise HTTPException(422, "Invalid backup scheduling rules version")
    # Never reactivate sessions or brute-force state copied from a backup.
    db.execute(models.LoginSession.__table__.delete())
    db.execute(models.LoginAttempt.__table__.delete())
    if db.bind.dialect.name == "postgresql":
        for table in TABLES:
            column = table.c.get("id")
            if column is not None and isinstance(column.type, Integer):
                sequence = db.scalar(text("SELECT pg_get_serial_sequence(:table, 'id')"), {"table": table.name})
                if sequence:
                    maximum = db.scalar(select(func.max(column))) or 1
                    # Never move sequences backward, including after a rollback.
                    db.execute(text("SELECT setval(CAST(:seq AS regclass), GREATEST(nextval(CAST(:seq AS regclass)), :maximum), true)"),
                               {"seq": sequence, "maximum": maximum})
    db.add(models.Activity(actor=actor, actor_id=None, action="restored", entity_type="database",
                          after_json=json.dumps({"rows": counts}), security=True))
    db.flush()
    return counts
