"""Transactional scheduler operations. Callers own the commit."""
import json
from fastapi import HTTPException
from sqlalchemy import or_, select
from . import auth, models, schemas, time_utils


def normalized(name):
    result = " ".join(name.strip().casefold().split())
    if len(result) > 200 or "\x00" in result:
        raise HTTPException(422, "Normalized names must not exceed 200 characters or contain null characters")
    return result


def label(name):
    result = " ".join(name.strip().split())
    if not result or len(result) > 200 or "\x00" in result:
        raise HTTPException(422, "Names must contain 1–200 characters")
    return result


def serialize(entry):
    return schemas.ScheduleEntry.model_validate(entry).model_dump(by_alias=True)


def entity_json(entity):
    result = {"id": entity.id, "name": entity.name, "version": entity.version}
    if isinstance(entity, models.Section):
        result["program_id"] = entity.program_id
    return result


def resolve(db, cls, name, program_id=None, create=False):
    if cls is not models.Section and time_utils.is_tba(name):
        return None
    name = label(name)
    stmt = select(cls).where(cls.normalized_name == normalized(name))
    if cls is models.Section:
        stmt = stmt.where(cls.program_id == program_id)
    item = db.scalar(stmt)
    if item is None:
        if not create:
            raise HTTPException(422, f"Select an existing {cls.__name__.lower()}: {name}")
        item = cls(name=name, normalized_name=normalized(name), version=1)
        if cls is models.Section:
            item.program_id = program_id
        db.add(item)
        db.flush()
    return item


def scheduling_rules(db):
    item = db.get(models.AppSettings, 1)
    stored = json.loads(item.settings_json) if item else {}
    return {"ignoreRoom": stored.get("ignoreRoom") is True,
            "ignoreFaculty": stored.get("ignoreFaculty") is True}, stored.get("rulesVersion", 1)


def enforced_kinds(db):
    rules, _ = scheduling_rules(db)
    return ["section"] + ([] if rules["ignoreRoom"] else ["room"]) + ([] if rules["ignoreFaculty"] else ["faculty"])


def candidate_conflicts(db, candidate, entry_id=0):
    if candidate.start_minutes is None:
        return []
    kinds = enforced_kinds(db)
    days = time_utils.normalize_days(candidate.days)
    result = []
    matches = [models.ScheduleEntry.section_id == candidate.section_id]
    if candidate.room_id is not None:
        matches.append(models.ScheduleEntry.room_id == candidate.room_id)
    if candidate.faculty_id is not None:
        matches.append(models.ScheduleEntry.faculty_id == candidate.faculty_id)
    query = select(models.ScheduleEntry).where(models.ScheduleEntry.id != entry_id,
        models.ScheduleEntry.start_minutes < candidate.end_minutes,
        models.ScheduleEntry.end_minutes > candidate.start_minutes, or_(*matches))
    for other in db.scalars(query):
        if other.start_minutes is None or not days.intersection(time_utils.normalize_days(other.days)):
            continue
        if not time_utils.overlap(candidate.start_minutes, candidate.end_minutes, other.start_minutes, other.end_minutes):
            continue
        for kind in kinds:
            key = f"{kind}_id"
            if getattr(candidate, key) is not None and getattr(candidate, key) == getattr(other, key):
                result.append({"conflict_type": kind, "entry": serialize(other)})
    return result


def all_conflicts(db):
    """One database read; compare only bookings sharing a day and resource."""
    kinds = enforced_kinds(db)
    buckets = {}
    groups = {}
    for entry in db.scalars(select(models.ScheduleEntry)):
        if entry.start_minutes is None:
            continue
        for day in time_utils.normalize_days(entry.days):
            for kind in kinds:
                resource_id = getattr(entry, kind + "_id")
                if resource_id is not None:
                    buckets.setdefault((day, kind, resource_id), []).append(entry)
    for (_, kind, _), entries in buckets.items():
        active = []
        for entry in sorted(entries, key=lambda row: row.start_minutes):
            active = [other for other in active if other.end_minutes > entry.start_minutes]
            for other in active:
                groups.setdefault((entry.id, kind), set()).add(other.id)
                groups.setdefault((other.id, kind), set()).add(entry.id)
            active.append(entry)
    return [{"entry_id": entry_id, "conflict_type": kind, "conflicts_with": sorted(ids)}
        for (entry_id, kind), ids in sorted(groups.items())]


def build_candidate(db, user, payload, allow_catalog_add=False):
    data = payload.model_dump(by_alias=False)
    program = db.scalar(select(models.Program).where(models.Program.name == data["program"]))
    if program is None:
        raise HTTPException(422, "Select an existing program")
    auth.editable(db, user, program.id)
    section = resolve(db, models.Section, data["section"], program.id, allow_catalog_add)
    room = resolve(db, models.Room, data["room"], create=allow_catalog_add and user.is_admin)
    faculty = resolve(db, models.Faculty, data["faculty"], create=allow_catalog_add and user.is_admin)
    if time_utils.is_tba(data["time_lpu"]) or time_utils.is_tba(data["days"]):
        data.update(time_lpu="TBA", time_24=None, days="TBA", start_minutes=None, end_minutes=None)
    else:
        try:
            lpu, clock, start, end = time_utils.parse_time_lpu(data["time_lpu"])
            days = time_utils.normalize_days_string(data["days"])
            if not days:
                raise ValueError("Invalid days")
            data.update(time_lpu=lpu, time_24=clock, start_minutes=start, end_minutes=end, days=days)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    if data["units"] < 0 or data["hours"] < 0:
        raise HTTPException(422, "Units and hours cannot be negative")
    data.pop("version", None)
    data.update(program_id=program.id, program=program.name, section_id=section.id, section=section.name,
                room_id=room.id if room else None, room=room.name if room else "TBA",
                faculty_id=faculty.id if faculty else None, faculty=faculty.name if faculty else "TBA")
    return models.ScheduleEntry(**data, version=1)


def validate_conflicts(db, user, candidate, entry_id=0, override_reason=None):
    if override_reason is not None and (len(override_reason) > 1200 or "\x00" in override_reason):
        raise HTTPException(422, "Override reason exceeds 1200 characters")
    found = candidate_conflicts(db, candidate, entry_id)
    if override_reason and (not user.is_admin or not override_reason.strip()):
        raise HTTPException(403, "Only administrators may override with a reason")
    blocked = found and (any(c["conflict_type"] == "section" for c in found) or not (user.is_admin and override_reason and override_reason.strip()))
    if blocked:
        raise HTTPException(409, {"code": "scheduling_conflict", "message": "This booking overlaps an existing class.", "conflicts": found})
    return found


def save(db, user, payload, entry_id=None, override_reason=None, allow_catalog_add=False):
    old = db.get(models.ScheduleEntry, entry_id) if entry_id else None
    if entry_id and not old:
        raise HTTPException(404, "Schedule entry not found")
    before = serialize(old) if old else None
    if old:
        auth.editable(db, user, old.program_id)
        auth.check_version(old, payload.version)
    candidate = build_candidate(db, user, payload, allow_catalog_add)
    validate_conflicts(db, user, candidate, entry_id or 0, override_reason)
    if old:
        for column in models.ScheduleEntry.__table__.columns:
            if column.name not in {"id", "version", "created_at", "updated_at"}:
                setattr(old, column.name, getattr(candidate, column.name))
        old.version += 1
        result = old
    else:
        result = candidate
        db.add(result)
    db.flush()
    auth.audit(db, user, "updated" if old else "created", "schedule", result.id, result.program_id,
               before, serialize(result), override_reason)
    return result


def remove(db, user, entry, version):
    auth.editable(db, user, entry.program_id)
    auth.check_version(entry, version)
    auth.audit(db, user, "deleted", "schedule", entry.id, entry.program_id, before=serialize(entry))
    db.delete(entry)
