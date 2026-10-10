"""Online meeting moves and activity-backed atomic Undo. Callers commit."""
import json
import math
from fastapi import HTTPException
from sqlalchemy import select
from . import auth, models, online_service as service, schemas, time_utils


def group_key(row):
    return row.section_id, row.course_code.strip().lower()


def group_rows(db, program_id, keys):
    return [row for row in db.scalars(select(models.ScheduleEntry).where(models.ScheduleEntry.program_id == program_id).order_by(models.ScheduleEntry.id)) if group_key(row) in keys]


def curriculum_hours(program, section, code):
    state = json.loads(program.settings_json).get('curriculumState', {})
    normalize = lambda value: value.strip().lower()
    year = state.get('sectionYearLevels', {}).get(normalize(section), '')
    identity = state.get('sectionCurriculumIds', {}).get(normalize(section)) or state.get('yearLevelCurriculumIds', {}).get(normalize(year))
    courses = [course for curriculum in state.get('curricula', []) if not identity or curriculum['id'] == identity for course in curriculum.get('courses', []) if course['semester'] == state.get('selectedTerm', 'First Semester')]
    matching = [course for course in courses if normalize(course['yearLevel']) == normalize(year)]
    if year and (matching or identity):
        courses = matching
    course = next((course for course in courses if normalize(course['courseCode']) == normalize(code)), None)
    return course['hours'] if course else None


def move(db, user, entry_id, request, override_reason=None):
    auth.lock(db)
    source = db.get(models.ScheduleEntry, entry_id)
    if source is None:
        raise HTTPException(404, 'Class no longer exists.')
    program = auth.editable(db, user, source.program_id)
    auth.check_version(source, request.expected.version)
    if service.serialize(source) != request.expected.model_dump(by_alias=True):
        raise HTTPException(409, 'Class changed. Refresh before moving it.')
    days = list(dict.fromkeys(time_utils.normalize_days_string(source.days).split(',')))
    if request.source_day not in days or source.start_minutes is None or source.end_minutes is None:
        raise HTTPException(422, 'Invalid source meeting.')
    if request.destination_day not in {'M', 'T', 'W', 'Th', 'F', 'Sa', 'Su'}:
        raise HTTPException(422, 'Invalid destination day.')
    end = request.start_minutes + source.end_minutes - source.start_minutes
    if request.start_minutes < 420 or end > 1260 or end <= request.start_minutes:
        raise HTTPException(422, 'Move must fit between 7:00 AM and 9:00 PM.')
    assignment, entity = request.assignment, None
    if assignment:
        entity = service.resolve(db, {'section': models.Section, 'faculty': models.Faculty, 'room': models.Room}[assignment.kind], assignment.name, source.program_id)
        if entity is None:
            raise HTTPException(422, 'Select an existing destination assignment.')
    if request.source_day == request.destination_day and request.start_minutes == source.start_minutes and (not assignment or getattr(source, assignment.kind + '_id') == entity.id):
        return {'entries': [], 'snapshot': None, 'moved_entry_id': entry_id}
    keys = {group_key(source)}
    if assignment and assignment.kind == 'section':
        keys.add((entity.id, source.course_code.strip().lower()))
    rows = group_rows(db, source.program_id, keys)
    before = [service.serialize(row) for row in rows]
    original_conflicts = sorted({(row.id, item['entry']['id'], item['conflict_type']) for row in rows for item in service.candidate_conflicts(db, row, row.id)})
    conflict_entries = {str(item['entry']['id']): item['entry'] for row in rows for item in service.candidate_conflicts(db, row, row.id)}
    if len(days) > 1:
        values = {column.name: getattr(source, column.name) for column in models.ScheduleEntry.__table__.columns if column.name not in {'id', 'version', 'created_at', 'updated_at'}}
        source.days = ','.join(day for day in days if day != request.source_day)
        target = models.ScheduleEntry(**values, version=1)
        db.add(target)
    else:
        target = source
    target.days = request.destination_day
    target.start_minutes, target.end_minutes = request.start_minutes, end
    target.time_lpu = f'{time_utils.format_time_lpu(request.start_minutes)}-{time_utils.format_time_lpu(end)}'
    target.time_24 = f'{time_utils.format_time_24(request.start_minutes)}-{time_utils.format_time_24(end)}'
    if assignment:
        setattr(target, assignment.kind, entity.name)
        setattr(target, assignment.kind + '_id', entity.id)
    db.flush()
    service.validate_conflicts(db, user, target, target.id, override_reason)
    rows = group_rows(db, source.program_id, keys)
    for key in keys:
        group = [row for row in rows if group_key(row) == key]
        if not group:
            continue
        total = curriculum_hours(program, group[0].section, group[0].course_code)
        if total is None:
            total = round(sum((row.end_minutes - row.start_minutes) / 60 * len(time_utils.normalize_days(row.days)) for row in group if row.start_minutes is not None and row.end_minutes is not None), 2)
        if not math.isfinite(total) or total < 0:
            raise HTTPException(422, 'Invalid curriculum hours.')
        for row in group:
            row.hours = total
    previous = {row['id']: row for row in before}
    for row in rows:
        if row.id in previous and service.serialize(row) != previous[row.id]:
            row.version += 1
    db.flush()
    after = [service.serialize(row) for row in rows]
    event = auth.audit(db, user, 'meeting_moved', 'schedule', source.id, source.program_id, before={**next(row for row in before if row['id'] == source.id), 'entries': before, 'original_conflicts': original_conflicts, 'conflict_entries': conflict_entries}, after={**service.serialize(target), 'entries': after, 'keys': [list(key) for key in sorted(keys)]}, reason=override_reason)
    db.flush()
    return {'entries': after, 'snapshot': {'move_activity_id': event.id}, 'moved_entry_id': target.id}


def revert(db, user, entry_id, request):
    auth.lock(db)
    event = db.get(models.Activity, request.move_activity_id)
    if not event or event.action != 'meeting_moved' or event.entity_id != entry_id or event.actor_id != user.id:
        raise HTTPException(409, 'Move snapshot is unavailable or does not belong to you.')
    auth.editable(db, user, event.program_id)
    saved_before, saved_after = json.loads(event.before_json), json.loads(event.after_json)
    before = {row['id']: row for row in saved_before['entries']}
    after = {row['id']: row for row in saved_after['entries']}
    keys = {tuple(key) for key in saved_after['keys']}
    rows = {row.id: row for row in group_rows(db, event.program_id, keys)}
    if {row_id: service.serialize(row) for row_id, row in rows.items()} != after:
        raise HTTPException(409, 'Classes changed since the move. Undo is unavailable.')
    for row_id in after.keys() - before.keys():
        db.delete(rows[row_id])
    for row_id, saved in before.items():
        row = rows[row_id]
        version = row.version + 1
        for name, value in schemas.ScheduleEntry.model_validate(saved).model_dump().items():
            if name not in {'id', 'version'}:
                setattr(row, name, value)
        row.version = version
        if time_utils.is_tba(row.time_lpu):
            row.start_minutes = row.end_minutes = None
        else:
            _, _, row.start_minutes, row.end_minutes = time_utils.parse_time_lpu(row.time_lpu)
    db.flush()
    permitted = set()
    for item in saved_before['original_conflicts']:
        other = db.get(models.ScheduleEntry, item[1])
        saved = saved_before['conflict_entries'].get(str(item[1]))
        if other and saved:
            current = service.serialize(other)
            if other.id in before:
                current.pop('version', None)
                saved = {key: value for key, value in saved.items() if key != 'version'}
            if current == saved:
                permitted.add(tuple(item))
    for row_id in before:
        found = [item for item in service.candidate_conflicts(db, rows[row_id], row_id) if (row_id, item['entry']['id'], item['conflict_type']) not in permitted]
        if found:
            raise HTTPException(409, {'code': 'undo_conflict', 'message': 'Undo blocked by a new schedule conflict.', 'conflicts': found})
    restored = [service.serialize(rows[row_id]) for row_id in before]
    auth.audit(db, user, 'meeting_move_reverted', 'schedule', entry_id, event.program_id, before={'entries': list(after.values())}, after={'entries': restored, 'move_activity_id': event.id})
    return {'entries': restored, 'removed_ids': sorted(after.keys() - before.keys())}
