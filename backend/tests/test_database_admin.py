import hashlib
import json

import pytest
from sqlalchemy import select
from app import database_archive as archive, models
from app.db import SessionLocal
from conftest import PASSWORD


def upload(client, data, password=PASSWORD, **headers):
    return client.post("/api/admin/database/restore", content=data, headers={
        "Content-Type": "application/octet-stream", "X-Admin-Password": password,
        "X-Restore-Confirmation": "REPLACE DATABASE", **headers})


def reseal(data, change):
    records = [json.loads(line) for line in data.splitlines()]
    change(records)
    body = b"".join((json.dumps(row) + "\n").encode() for row in records[:-1])
    records[-1]["sha256"] = hashlib.sha256(body).hexdigest()
    return body + (json.dumps(records[-1]) + "\n").encode()


def test_all_database_controls_require_admin_csrf_and_origin(clients, monkeypatch):
    monkeypatch.setenv("PUBLIC_ORIGIN", "https://scheduler.example")
    for path in ("/api/admin/database/backup", "/api/admin/database/restore", "/api/admin/timetable/clear", "/api/file/reset?program_id=1"):
        assert clients["alpha"].post(path, json={"password": PASSWORD}).status_code == 403
        assert clients["admin"].post(path, json={"password": PASSWORD}, headers={"X-CSRF-Token": "wrong"}).status_code == 403
        assert clients["admin"].post(path, json={"password": PASSWORD}, headers={"Origin": "https://evil.example"}).status_code == 403


def test_full_backup_round_trip_restores_all_tables_and_revokes_sessions(clients, entry_payload):
    admin, alpha, beta = (clients[k] for k in ("admin", "alpha", "beta"))
    assert alpha.post("/api/schedule", json=entry_payload).status_code == 200
    assert admin.put("/api/admin/rules", json={"version": 1, "ignoreRoom": False, "ignoreFaculty": False, "ignoreRoomIds": [1]}).status_code == 200
    assert alpha.put("/api/settings?program_id=1", json={"version": 1, "settings": {"curriculumState": {"curricula": []}, "customize": {"label": "saved"}}}).status_code == 200
    backup = admin.post("/api/admin/database/backup")
    assert backup.status_code == 200
    assert ".scheduler-backup" in backup.headers["content-disposition"]
    assert backup.headers["cache-control"] == "no-store"
    header = json.loads(backup.content.splitlines()[0])
    assert set(header["tables"]) == set(archive.BY_NAME)
    assert admin.post("/api/rooms", json={"name": "After backup"}).status_code == 200
    assert admin.post("/api/admin/timetable/clear", json={"password": PASSWORD}).status_code == 200
    restored = upload(admin, backup.content)
    assert restored.status_code == 200, restored.text
    assert restored.json()["ready"] is True
    for client in (admin, alpha, beta):
        assert client.get("/api/auth/me").status_code == 401
    login = admin.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    assert login.status_code == 200
    admin.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    assert len(admin.get("/api/schedule").json()) == 1
    assert "After backup" not in [r["name"] for r in admin.get("/api/rooms").json()]
    assert admin.get("/api/rules").json()["rules"]["ignoreRoomIds"] == [1]
    assert admin.get("/api/settings?program_id=1").json()["settings"]["curriculumState"] == {"curricula": []}
    events = admin.get("/api/activity?security=true").json()
    assert any(e["action"] == "restored" and e["actor"] == "admin" for e in events)
    # New IDs remain usable after replacement, rather than colliding with restored rows.
    assert admin.post("/api/rooms", json={"name": "After restore"}).status_code == 200


def test_clear_requires_current_password_keeps_catalogs_and_audits(clients, entry_payload):
    admin, alpha, beta = (clients[k] for k in ("admin", "alpha", "beta"))
    assert alpha.post("/api/schedule", json=entry_payload).status_code == 200
    assert beta.post("/api/schedule", json={**entry_payload, "Program": "P2", "Room": "R2", "Faculty": "F2"}).status_code == 200
    assert admin.post("/api/admin/timetable/clear", json={}).status_code == 422
    assert admin.post("/api/admin/timetable/clear", json={"password": "wrong"}).status_code == 403
    assert len(alpha.get("/api/schedule").json()) == 2
    result = admin.post("/api/admin/timetable/clear", json={"password": PASSWORD})
    assert result.status_code == 200 and result.json()["deleted"] == 2
    assert alpha.get("/api/schedule").json() == []
    assert len(alpha.get("/api/sections").json()) == 2
    assert len(alpha.get("/api/rooms").json()) == 2
    assert len(alpha.get("/api/faculty").json()) == 2
    assert len(alpha.get("/api/programs").json()) == 2
    events = alpha.get("/api/activity").json()
    assert sum(e["action"] == "deleted" and e["entity_type"] == "schedule" for e in events) == 2
    assert any(e["entity_type"] == "timetable" and e["action"] == "cleared" for e in events)
    assert PASSWORD not in json.dumps(events)


def test_wrong_password_is_throttled_and_restore_needs_explicit_confirmation(clients):
    admin = clients["admin"]
    for _ in range(5):
        assert upload(admin, b"untrusted", password="wrong").status_code == 403
    assert upload(admin, b"untrusted").status_code == 429
    assert admin.post("/api/admin/timetable/clear", json={"password": PASSWORD}).status_code == 429


@pytest.mark.parametrize("damage", ["truncated", "checksum", "sql", "schema", "unknown_table", "duplicate", "no_admin", "bad_fk", "ownership", "bad_hash"])
def test_invalid_restore_is_atomic(clients, entry_payload, damage):
    admin, alpha = clients["admin"], clients["alpha"]
    assert alpha.post("/api/schedule", json=entry_payload).status_code == 200
    data = admin.post("/api/admin/database/backup").content
    before = alpha.get("/api/schedule").json()
    if damage == "truncated":
        data = data[:-100]
    elif damage == "checksum":
        data = data.replace(b"Course", b"Edited", 1)
    elif damage == "sql":
        data = b"DROP TABLE users;"
    else:
        def change(rows):
            if damage == "schema":
                rows[0]["schema"] = "unknown"
            elif damage == "unknown_table":
                rows[1]["table"] = "unknown"
            elif damage == "duplicate":
                users = [v["row"] for v in rows if v.get("table") == "users"]
                users[1]["id"] = users[0]["id"]
            elif damage in {"no_admin", "bad_hash"}:
                for value in rows:
                    if value.get("table") == "users":
                        if damage == "no_admin":
                            value["row"]["is_admin"] = False
                        else:
                            value["row"]["password_hash"] = "invalid"
            else:
                entry = next(v["row"] for v in rows if v.get("table") == "schedule_entries")
                entry["section_id" if damage == "bad_fk" else "program_id"] = 999 if damage == "bad_fk" else 2
        data = reseal(data, change)
    result = upload(admin, data)
    assert result.status_code == 422, result.text
    assert alpha.get("/api/schedule").json() == before
    assert admin.get("/api/auth/me").status_code == 200


def test_restore_size_and_confirmation_limits(clients):
    admin = clients["admin"]
    assert admin.post("/api/admin/database/restore", content=b"x", headers={"X-Admin-Password": PASSWORD}).status_code == 422
    assert upload(admin, b"x", **{"Content-Length": str(archive.MAX_BYTES + 1)}).status_code == 413


def test_backup_size_failure_does_not_leave_an_audit_success(clients, monkeypatch):
    monkeypatch.setattr(archive, "MAX_BYTES", 100)
    assert clients["admin"].post("/api/admin/database/backup").status_code == 413
    with SessionLocal() as db:
        assert not db.scalar(select(models.Activity.id).where(models.Activity.action == "backup_created"))


def test_post_restore_readiness_failure_is_reported_as_failure(clients, monkeypatch):
    import importlib
    from sqlalchemy.exc import OperationalError
    main = importlib.import_module("app.main")
    admin = clients["admin"]
    data = admin.post("/api/admin/database/backup").content
    def unavailable(db):
        raise OperationalError("readiness", {}, Exception("unavailable"))
    monkeypatch.setattr(main, "health", unavailable)
    result = upload(admin, data)
    assert result.status_code == 503
    assert "Database restored, but readiness check failed" in result.json()["detail"]
    assert admin.get("/api/auth/me").status_code == 401
