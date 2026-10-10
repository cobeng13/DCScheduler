"""Authenticated meeting moves: permissions, splitting, rollback and Undo."""
import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app import models, moves
from app.db import SessionLocal
from app.main import app


def create(client, payload):
    response = client.post('/api/schedule', json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def request_move(client, entry, **changes):
    payload = {'source_day': 'M', 'destination_day': 'T', 'start_minutes': 600, 'expected': entry, **changes}
    return client.post(f"/api/schedule/{entry['id']}/move", json=payload)


def undo(client, entry, result):
    return client.post(f"/api/schedule/{entry['id']}/move/revert", json=result['snapshot'])


@pytest.mark.parametrize('kind,name', [('section', 'B'), ('faculty', 'F2'), ('room', 'R2')])
@pytest.mark.parametrize('days', ['M', 'M,W'])
def test_reassignment_only_moves_one_meeting_and_undo_is_atomic(clients, entry_payload, kind, name, days):
    alpha = clients['alpha']
    assert alpha.post('/api/sections', json={'name': 'B', 'program_id': 1}).status_code == 200
    entry = create(alpha, {**entry_payload, 'Days': days})
    result = request_move(alpha, entry, assignment={'kind': kind, 'name': name})
    assert result.status_code == 200, result.text
    result = result.json()
    rows = alpha.get('/api/schedule?program_id=1').json()
    moved = next(row for row in rows if row['id'] == result['moved_entry_id'])
    assert moved[kind.title()] == name and moved['Days'] == 'T'
    assert moved['Time (24 Hrs)'] == '10:00-11:00'
    assert moved['Program'] == entry['Program'] and moved['program_id'] == 1
    for field in ['Units', 'Course Code', 'Course Description']:
        assert moved[field] == entry[field]
    if days == 'M,W':
        original = next(row for row in rows if row['id'] == entry['id'])
        assert original['Days'] == 'W' and original[kind.title()] == entry[kind.title()]
        assert original['Time (24 Hrs)'] == entry['Time (24 Hrs)']
        assert original['version'] > entry['version']
    reverted = undo(alpha, entry, result)
    assert reverted.status_code == 200, reverted.text
    restored = alpha.get('/api/schedule?program_id=1').json()
    assert len(restored) == 1
    assert {key: value for key, value in restored[0].items() if key != 'version'} == {key: value for key, value in entry.items() if key != 'version'}
    assert restored[0]['version'] > entry['version']
    assert undo(alpha, entry, result).status_code == 409
    actions = [event['action'] for event in alpha.get('/api/activity').json()]
    assert 'meeting_moved' in actions and 'meeting_move_reverted' in actions


@pytest.mark.parametrize('changes', [
    {'start_minutes': 419}, {'start_minutes': 1230}, {'destination_day': 'X'}, {'source_day': 'F'},
    {'assignment': {'kind': 'room', 'name': 'Deleted'}}, {'assignment': {'kind': 'section', 'name': 'Only P2'}},
    {'assignment': {'kind': 'room', 'name': 'TBA'}}, {'start_minutes': 480.5}])
def test_invalid_move_has_no_side_effects(clients, entry_payload, changes):
    alpha = clients['alpha']
    clients['beta'].post('/api/sections', json={'program_id': 2, 'name': 'Only P2'})
    entry = create(alpha, {**entry_payload, 'Days': 'M,W'})
    response = request_move(alpha, entry, **changes)
    assert response.status_code == 422, response.text
    assert alpha.get('/api/schedule?program_id=1').json() == [entry]
    assert not any(event['action'] == 'meeting_moved' for event in alpha.get('/api/activity').json())


def test_authentication_csrf_permissions_stale_source_and_noop(clients, entry_payload):
    alpha = clients['alpha']
    entry = create(alpha, entry_payload)
    with TestClient(app) as anonymous:
        assert request_move(anonymous, entry).status_code == 401
    csrf = alpha.headers.pop('X-CSRF-Token')
    assert request_move(alpha, entry).status_code == 403
    alpha.headers['X-CSRF-Token'] = csrf
    assert request_move(clients['beta'], entry).status_code == 403
    noop = request_move(alpha, entry, destination_day='M', start_minutes=480)
    assert noop.status_code == 200 and noop.json()['snapshot'] is None
    assert alpha.get('/api/schedule?program_id=1').json() == [entry]
    changed = alpha.put(f"/api/schedule/{entry['id']}", json={**entry, 'Course Description': 'Changed'})
    assert changed.status_code == 200
    assert request_move(alpha, entry).status_code == 409
    forged = {**changed.json(), 'Units': 10}
    assert request_move(alpha, forged).status_code == 409


@pytest.mark.parametrize('kind', ['section', 'room', 'faculty'])
def test_server_conflicts_roll_back_split_even_if_client_ignores_rules(clients, entry_payload, kind):
    alpha = clients['alpha']
    source = create(alpha, {**entry_payload, 'Days': 'M,W'})
    assert alpha.post('/api/sections', json={'name': 'B', 'program_id': 1}).status_code == 200
    other = create(alpha, {**entry_payload, 'Section': 'A' if kind == 'section' else 'B',
        'Room': 'R1' if kind == 'room' else 'R2', 'Faculty': 'F1' if kind == 'faculty' else 'F2',
        'Course Code': 'OTHER', 'Days': 'T', 'Time (LPU Std)': '10:00a-11:00a'})
    response = request_move(alpha, source, conflict_settings={'ignore_room': True, 'ignore_faculty': True})
    assert response.status_code == 409 and response.json()['detail']['code'] == 'scheduling_conflict'
    assert {item['conflict_type'] for item in response.json()['detail']['conflicts']} == {kind}
    assert alpha.get('/api/schedule?program_id=1').json() == [source, other]


def test_shared_resource_conflict_and_same_named_sections_are_program_scoped(clients, entry_payload):
    alpha, beta = clients['alpha'], clients['beta']
    source = create(alpha, {**entry_payload, 'Days': 'M,W'})
    other = create(beta, {**entry_payload, 'Program': 'P2', 'Days': 'T', 'Time (LPU Std)': '10:00a-11:00a'})
    assert request_move(alpha, source).status_code == 409
    current = alpha.get('/api/schedule').json()
    assert current == [source, other]
    result = request_move(alpha, source, assignment={'kind': 'room', 'name': 'R2'}, start_minutes=660)
    assert result.status_code == 200
    assert alpha.get(f"/api/schedule/{other['id']}").json() == other
    assert undo(beta, source, result.json()).status_code == 409
    assert undo(alpha, source, result.json()).status_code == 200
    assert alpha.get(f"/api/schedule/{other['id']}").json() == other


def test_undo_rejects_stale_group_and_rolls_back_on_new_conflict(clients, entry_payload):
    alpha = clients['alpha']
    source = create(alpha, {**entry_payload, 'Days': 'M,W'})
    result = request_move(alpha, source).json()
    restored_slot = create(alpha, {**entry_payload, 'Days': 'M', 'Course Code': 'NEW'})
    before = alpha.get('/api/schedule?program_id=1').json()
    assert undo(alpha, source, result).status_code == 409
    assert alpha.get('/api/schedule?program_id=1').json() == before
    assert alpha.delete(f"/api/schedule/{restored_slot['id']}?version={restored_slot['version']}").status_code == 200
    moved = alpha.get(f"/api/schedule/{result['moved_entry_id']}").json()
    assert alpha.put(f"/api/schedule/{moved['id']}", json={**moved, 'Course Description': 'Changed'}).status_code == 200
    before = alpha.get('/api/schedule?program_id=1').json()
    assert undo(alpha, source, result).status_code == 409
    assert alpha.get('/api/schedule?program_id=1').json() == before


def test_exception_rolls_back_split_hours_and_activity(clients, entry_payload, monkeypatch):
    alpha = clients['alpha']
    source = create(alpha, {**entry_payload, 'Days': 'M,W'})
    def fail(*args, **kwargs):
        raise RuntimeError('Injected failure after split')
    monkeypatch.setattr(moves, 'curriculum_hours', fail)
    with pytest.raises(RuntimeError):
        request_move(alpha, source)
    assert alpha.get('/api/schedule?program_id=1').json() == [source]
    assert not any(event['action'] == 'meeting_moved' for event in alpha.get('/api/activity').json())


def test_curriculum_hours_are_from_saved_program_state(clients, entry_payload):
    alpha = clients['alpha']
    source = create(alpha, {**entry_payload, 'Days': 'M,W'})
    with SessionLocal() as db:
        program = db.get(models.Program, 1)
        program.settings_json = json.dumps({'curriculumState': {'selectedTerm': 'First Semester', 'curricula': [{'id': 'c', 'courses': [{'semester': 'First Semester', 'yearLevel': '1', 'courseCode': 'C101', 'hours': 5}]}]}})
        db.commit()
    result = request_move(alpha, source, curriculum_hours={'a': 999})
    assert result.status_code == 200, result.text
    assert {row['# of Hours'] for row in result.json()['entries']} == {5}
    assert undo(alpha, source, result.json()).status_code == 200


def test_undo_requires_current_program_permission(clients, entry_payload):
    alpha = clients['alpha']
    source = create(alpha, entry_payload)
    result = request_move(alpha, source).json()
    with SessionLocal() as db:
        db.get(models.Program, 1).assigned_user_id = 3
        db.commit()
    assert undo(alpha, source, result).status_code == 403


@pytest.mark.parametrize('changed', [False, True])
def test_undo_retains_only_unchanged_preexisting_conflicts(clients, entry_payload, changed):
    from test_rules import save_rules
    alpha, beta, admin = clients['alpha'], clients['beta'], clients['admin']
    assert save_rules(admin, room=True, faculty=True).status_code == 200
    source = create(alpha, {**entry_payload, 'Days': 'M,W'})
    other = create(beta, {**entry_payload, 'Program': 'P2'})
    assert save_rules(admin, version=2).status_code == 200
    result = request_move(alpha, source).json()
    if changed:
        # Admin change preserves the resource conflict but makes it a new booking state.
        response = admin.put(f"/api/schedule/{other['id']}?override_reason=Shared", json={**other, 'Course Description': 'Changed'})
        assert response.status_code == 200
    before = alpha.get('/api/schedule').json()
    reverted = undo(alpha, source, result)
    assert reverted.status_code == (409 if changed else 200), reverted.text
    if changed:
        assert alpha.get('/api/schedule').json() == before


def test_admin_move_override_is_audited_and_section_conflicts_stay_blocked(clients, entry_payload):
    from test_rules import save_rules
    admin, alpha, beta = clients['admin'], clients['alpha'], clients['beta']
    source = create(alpha, {**entry_payload, 'Days': 'M,W'})
    create(beta, {**entry_payload, 'Program': 'P2', 'Days': 'T', 'Time (LPU Std)': '10:00a-11:00a'})
    body = {'source_day': 'M', 'destination_day': 'T', 'start_minutes': 600, 'expected': source}
    assert request_move(alpha, source).status_code == 409
    response = admin.post(f"/api/schedule/{source['id']}/move?override_reason=Shared lecture", json=body)
    assert response.status_code == 200, response.text
    event = next(item for item in admin.get('/api/activity').json() if item['action'] == 'meeting_moved')
    assert event['reason'] == 'Shared lecture'
    assert 'Days' in event['changed_fields']
    assert undo(admin, source, response.json()).status_code == 200
    create(alpha, {**entry_payload, 'Course Code': 'OTHER', 'Days': 'T', 'Time (LPU Std)': '10:00a-11:00a', 'Room': 'R2', 'Faculty': 'F2'})
    latest = alpha.get(f"/api/schedule/{source['id']}").json()
    body['expected'] = latest
    assert admin.post(f"/api/schedule/{source['id']}/move?override_reason=Shared", json=body).status_code == 409


def test_undo_rejects_new_entry_in_affected_course_group(clients, entry_payload):
    alpha = clients['alpha']
    source = create(alpha, entry_payload)
    result = request_move(alpha, source).json()
    create(alpha, {**entry_payload, 'Days': 'F'})
    before = alpha.get('/api/schedule?program_id=1').json()
    assert undo(alpha, source, result).status_code == 409
    assert alpha.get('/api/schedule?program_id=1').json() == before
