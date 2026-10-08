from datetime import timedelta
import csv
import io

from fastapi.testclient import TestClient
from sqlalchemy import select
from app import auth, models, reports, schemas
from app.db import SessionLocal
from app.main import app
from conftest import PASSWORD


def create(client, payload):
    response = client.post("/api/schedule", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_authentication_csrf_and_legacy_routes(clients, entry_payload):
    anonymous = TestClient(app)
    for path in ("/api/schedule", "/api/activity", "/api/presence", "/api/settings", "/api/reports/text.csv"):
        assert anonymous.get(path).status_code == 401
    assert anonymous.post("/api/file/reset?program_id=1").status_code == 401
    assert clients["alpha"].post("/api/schedule", json=entry_payload, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert clients["alpha"].get("/api/admin/users").status_code == 403
    assert clients["alpha"].get("/api/file/export").status_code == 410
    assert clients["alpha"].post("/file/reset").status_code == 405
    assert clients["alpha"].get("/api/schedule").headers["cache-control"] == "no-store"


def test_own_program_edit_global_read_and_destination_permission(clients, entry_payload):
    entry = create(clients["alpha"], entry_payload)
    assert clients["beta"].get("/api/schedule").json()[0]["id"] == entry["id"]
    assert clients["beta"].put(f'/api/schedule/{entry["id"]}', json=entry).status_code == 403
    assert clients["beta"].delete(f'/api/schedule/{entry["id"]}?version=1').status_code == 403
    assert clients["alpha"].put(f'/api/schedule/{entry["id"]}', json={**entry, "Program": "P2"}).status_code == 403
    assert clients["alpha"].post("/api/schedule", json={**entry_payload, "Program": "P2"}).status_code == 403


def test_stale_save_delete_and_atomic_audit(clients, entry_payload):
    entry = create(clients["alpha"], entry_payload)
    path = f'/api/schedule/{entry["id"]}'
    changed = clients["admin"].put(path, json={**entry, "Course Description": "Updated"})
    assert changed.status_code == 200
    assert changed.json()["version"] == 2
    stale = clients["alpha"].put(path, json=entry)
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale_version"
    assert clients["alpha"].delete(path + "?version=1").status_code == 409
    assert clients["alpha"].put(path, json=entry_payload).status_code == 422
    events = clients["alpha"].get("/api/activity").json()
    changes = [e for e in events if e["entity_type"] == "schedule"]
    assert len(changes) == 2
    assert changes[0]["before"]["Course Description"] == "Course"
    assert changes[0]["after"]["Course Description"] == "Updated"
    assert "Course Description" in changes[0]["changed_fields"]


def test_cross_program_conflicts_overrides_and_section_isolation(clients, entry_payload):
    create(clients["alpha"], entry_payload)
    second = {**entry_payload, "Program": "P2", "Faculty": "F2"}
    blocked = clients["beta"].post("/api/schedule", json=second)
    assert blocked.status_code == 409
    assert [c["conflict_type"] for c in blocked.json()["detail"]["conflicts"]] == ["room"]
    assert clients["beta"].post("/api/schedule?override_reason=needed", json=second).status_code == 403
    overridden = clients["admin"].post("/api/schedule?override_reason=Shared%20lecture", json=second)
    assert overridden.status_code == 200
    assert clients["alpha"].get("/api/activity").json()[0]["reason"] == "Shared lecture"
    assert clients["admin"].post("/api/schedule?override_reason=needed", json=entry_payload).status_code == 409
    assert clients["alpha"].get("/api/conflicts").json()["conflicts"]


def test_faculty_conflict_tba_and_adjacent_times(clients, entry_payload):
    create(clients["alpha"], entry_payload)
    assert clients["beta"].post("/api/schedule", json={**entry_payload, "Program": "P2", "Room": "R2"}).status_code == 409
    create(clients["beta"], {**entry_payload, "Program": "P2", "Time (LPU Std)": "9:00a-10:00a"})
    create(clients["beta"], {**entry_payload, "Program": "P2", "Time (LPU Std)": "TBA", "Room": "TBA", "Faculty": "TBA"})
    tba = clients["beta"].get("/api/schedule").json()[-1]
    assert tba["room_id"] is None and tba["faculty_id"] is None


def test_catalog_ownership_normalization_and_force_delete(clients, entry_payload):
    alpha, beta, admin = clients["alpha"], clients["beta"], clients["admin"]
    room = admin.post("/api/rooms", json={"name": "  Lab   2 "}).json()
    assert room["name"] == "Lab 2"
    assert admin.post("/api/rooms", json={"name": "LAB 2"}).status_code == 409
    assert alpha.put(f'/api/rooms/{room["id"]}', json={"name": "Other", "version": 1}).status_code == 403
    assert beta.post("/api/sections", json={"name": "B", "program_id": 1}).status_code == 403
    assert beta.put("/api/sections/1", json={"name": "B", "version": 1}).status_code == 403
    entry = create(alpha, entry_payload)
    renamed = alpha.put("/api/sections/1", json={"name": "B", "version": 1})
    assert renamed.status_code == 200
    assert beta.get(f'/api/schedule/{entry["id"]}').json()["Section"] == "B"
    assert beta.get("/api/sections?program_id=2").json()[0]["name"] == "A"
    assert admin.delete("/api/rooms/1?version=1&force=true").status_code == 409
    assert alpha.delete("/api/sections/1?version=2").status_code == 409
    assert alpha.delete("/api/sections/1?version=2&force=true").status_code == 200
    assert not alpha.get("/api/schedule?program_id=1").json()


def test_merge_conflicts_rollback_and_version_bump(clients, entry_payload):
    alpha, beta, admin = clients["alpha"], clients["beta"], clients["admin"]
    create(alpha, entry_payload)
    second = create(beta, {**entry_payload, "Program": "P2", "Room": "R2", "Faculty": "F2"})
    response = admin.put("/api/rooms/2?merge=true", json={"name": "R1", "version": 1})
    assert response.status_code == 409
    assert beta.get(f'/api/schedule/{second["id"]}').json()["Room"] == "R2"
    assert len(beta.get("/api/rooms").json()) == 2


def test_reassignment_disabling_password_reset_and_security_visibility(clients, entry_payload):
    admin, alpha, beta = clients["admin"], clients["alpha"], clients["beta"]
    p = admin.get("/api/programs").json()[0]
    assert admin.put("/api/admin/programs/1", json={"assigned_user_id": 3, "version": p["version"]}).status_code == 200
    assert alpha.post("/api/schedule", json=entry_payload).status_code == 403
    create(beta, entry_payload)
    assert admin.put("/api/admin/users/3", json={"disabled": True}).status_code == 200
    assert beta.get("/api/schedule").status_code == 401
    assert admin.put("/api/admin/users/2", json={"password": "New-password-123!"}).status_code == 200
    assert alpha.get("/api/auth/me").status_code == 401
    assert admin.get("/api/activity?security=true").status_code == 200
    other = TestClient(app)
    login = other.post("/api/auth/login", json={"username": "alpha", "password": "New-password-123!"})
    other.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    assert other.get("/api/auth/me").json()["user"]["must_change_password"] is True
    assert other.get("/api/schedule").status_code == 403
    assert other.post("/api/auth/password", json={"current_password": "New-password-123!", "password": "Changed-password-123!"}).status_code == 200
    assert other.get("/api/auth/me").status_code == 401
    assert other.post("/api/auth/login", json={"username": "alpha", "password": "Changed-password-123!"}).status_code == 200
    assert other.get("/api/activity?security=true").status_code == 403
    logs = admin.get("/api/activity?security=true").text
    assert PASSWORD not in logs and "Changed-password" not in logs


def csv_file(rows):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, schemas.CANONICAL_HEADERS)
    writer.writeheader()
    for row in rows:
        writer.writerow({**row, "Time (24 Hrs)": row.get("Time (24 Hrs)", "")})
    return {"file": ("schedule.csv", buffer.getvalue().encode(), "text/csv")}


def test_csv_preview_and_replace_are_atomic_and_program_scoped(clients, entry_payload):
    alpha, beta = clients["alpha"], clients["beta"]
    original = create(alpha, entry_payload)
    other = create(beta, {**entry_payload, "Program": "P2", "Room": "R2", "Faculty": "F2"})
    good = {**entry_payload, "Section": "New section", "Time (LPU Std)": "10:00a-11:00a"}
    bad = {**good, "Units": "not numeric"}
    preview = alpha.post("/api/file/import-csv?program_id=1&replace=true&preview=true", files=csv_file([good]))
    assert preview.status_code == 200 and preview.json()["rows_imported"] == 1
    assert alpha.get("/api/schedule?program_id=1").json()[0]["id"] == original["id"]
    assert alpha.post("/api/file/import-csv?program_id=1&replace=true", files=csv_file([good, bad])).status_code == 422
    assert len(alpha.get("/api/sections?program_id=1").json()) == 1
    assert alpha.get("/api/schedule?program_id=1").json()[0]["id"] == original["id"]
    assert alpha.post("/api/file/import-csv?program_id=1&replace=true", files=csv_file([{**good, "Program": "P2"}])).status_code == 422
    assert alpha.post("/api/file/import-csv?program_id=1&replace=true", files=csv_file([good])).status_code == 200
    assert beta.get("/api/schedule?program_id=2").json()[0]["id"] == other["id"]
    assert beta.post("/api/file/reset?program_id=1").status_code == 403
    assert alpha.post("/api/file/reset?program_id=1", json={"password": PASSWORD}).status_code == 403
    assert clients["admin"].post("/api/file/reset?program_id=1", json={"password": PASSWORD}).status_code == 200
    assert len(beta.get("/api/schedule").json()) == 1


def test_csv_internal_conflicts_and_empty_replace_rollback(clients, entry_payload):
    entry = create(clients["alpha"], entry_payload)
    files = csv_file([entry_payload, entry_payload])
    assert clients["alpha"].post("/api/file/import-csv?program_id=1&replace=true", files=files).status_code == 422
    assert clients["alpha"].post("/api/file/import-csv?program_id=1&replace=true", files=csv_file([])).status_code == 422
    assert clients["alpha"].get("/api/schedule").json()[0]["id"] == entry["id"]


def test_batch_move_and_stale_undo_rollback(clients, entry_payload):
    alpha = clients["alpha"]
    entry = create(alpha, {**entry_payload, "Days": "M,W"})
    operations = [{"method": "PUT", "id": entry["id"], "entry": {**entry, "Days": "W"}},
        {"method": "POST", "entry": {**entry_payload, "Days": "M", "Time (LPU Std)": "10:00a-11:00a"}}]
    changed = alpha.post("/api/schedule/batch", json={"operations": operations})
    assert changed.status_code == 200, changed.text
    original, moved = changed.json()
    newer = clients["admin"].put(f'/api/schedule/{original["id"]}', json={**original, "Course Description": "Newer"})
    assert newer.status_code == 200
    undo = [{"method": "DELETE", "id": moved["id"], "version": moved["version"]},
        {"method": "PUT", "id": original["id"], "entry": {**entry, "version": original["version"]}}]
    assert alpha.post("/api/schedule/batch", json={"operations": undo}).status_code == 409
    assert alpha.get(f'/api/schedule/{moved["id"]}').status_code == 200
    assert len(alpha.get("/api/schedule").json()) == 2


def test_settings_are_personal_and_program_owned(clients):
    alpha, beta = clients["alpha"], clients["beta"]
    payload = {"settings": {"customize": {"classBlockFontSizePx": 12}, "curriculumState": {"curricula": []}}, "version": 1}
    assert alpha.put("/api/settings?program_id=1", json=payload).status_code == 200
    assert beta.put("/api/settings?program_id=1", json=payload).status_code == 403
    assert alpha.put("/api/settings?program_id=1", json=payload).status_code == 409
    assert beta.get("/api/settings?program_id=2").json()["settings"].get("customize") is None
    assert alpha.get("/api/settings?program_id=1").json()["settings"]["customize"]["classBlockFontSizePx"] == 12
    payload["version"] = 2
    payload["settings"]["curriculumState"] = {"curricula": [{"courses": [{"program": "P2"}]}]}
    assert alpha.put("/api/settings?program_id=1", json=payload).status_code == 403


def test_presence_expiry_activity_filter_and_logout(clients, entry_payload):
    alpha = clients["alpha"]
    create(alpha, entry_payload)
    assert len(alpha.get("/api/presence").json()) == 3
    with SessionLocal() as db:
        session = db.scalar(select(models.LoginSession).where(models.LoginSession.user_id == 3))
        session.last_seen = auth.now() - timedelta(seconds=91)
        db.commit()
    assert len(alpha.get("/api/presence").json()) == 2
    assert clients["beta"].post("/api/presence/heartbeat").status_code == 200
    assert len(alpha.get("/api/presence").json()) == 3
    assert len(alpha.get("/api/activity?program_id=1&actor_id=2&q=C101").json()) == 1
    assert alpha.post("/api/auth/logout").status_code == 200
    assert alpha.get("/api/schedule").status_code == 401


def test_login_throttle_and_secure_cookie(clients, monkeypatch):
    stranger = TestClient(app)
    for attempt in range(10):
        assert stranger.post("/api/auth/login", json={"username": "wrong", "password": "wrong"}).status_code == 401
    assert stranger.post("/api/auth/login", json={"username": "wrong", "password": "wrong"}).status_code == 429
    monkeypatch.setenv("COOKIE_SECURE", "true")
    response = stranger.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie and "samesite=lax" in cookie


def test_reports_and_finite_validation(clients, entry_payload):
    create(clients["alpha"], entry_payload)
    for path in ("/api/reports/text.csv", "/api/reports/timetable/section.csv", "/api/reports/faculty-load.html?faculty=F1"):
        assert clients["alpha"].get(path).status_code == 200
    assert clients["alpha"].post("/api/schedule", json={**entry_payload, "Units": -1}).status_code == 422
    assert clients["alpha"].post("/api/export/png", json={"png_base64": "not png"}).status_code == 422
    content = reports.build_faculty_load_html("F1", [entry_payload]).decode()
    assert "Faculty Load" in content
