import asyncio
import csv
from datetime import timedelta
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from app import auth, models, reports, schemas
from app.db import SessionLocal
from app.limits import BodyLimitMiddleware, MIB
from app.main import app
from conftest import PASSWORD
from test_api import csv_file


def test_schedule_field_limits_apply_to_api_batch_and_csv(clients, entry_payload):
    alpha = clients["alpha"]
    for name, field in schemas.ScheduleEntryCreate.model_fields.items():
        maximum = next((m.max_length for m in field.metadata if hasattr(m, "max_length")), None)
        if maximum is None:
            continue
        payload = {**entry_payload, field.alias: "x" * (maximum + 1)}
        assert alpha.post("/api/schedule", json=payload).status_code == 422
        assert alpha.post("/api/schedule/batch", json={"operations": [{"method": "POST", "entry": payload}]}).status_code == 422
        response = alpha.post("/api/file/import-csv?program_id=1", files=csv_file([payload]))
        assert response.status_code == 422
        # The maximum itself is accepted by the shared schema (semantic checks
        # for resource selection/time are independently tested).
        schemas.ScheduleEntryCreate.model_validate({**entry_payload, field.alias: "x" * maximum})
    assert alpha.get("/api/schedule").json() == []
    assert clients["admin"].post("/api/schedule", params={"override_reason": "x" * 1201}, json=entry_payload).status_code == 422
    assert alpha.post("/api/schedule", json={**entry_payload, "Course Code": "bad\x00"}).status_code == 422
    assert clients["admin"].post("/api/rooms", json={"name": "ß" * 200}).status_code == 422
    assert clients["admin"].post("/api/admin/users", json={"username": "ß" * 100, "password": PASSWORD}).status_code == 422


def test_settings_limits_and_malformed_curricula_are_atomic(clients):
    alpha = clients["alpha"]
    values = [
        ({"curriculumState": {"curricula": [], "extra": ["x" * 4000] * 70}}, 413),
        ({"customize": {"extra": ["x" * 4000] * 5}}, 413),
        ({"curriculumState": {"curricula": "bad"}}, 422),
        ({"curriculumState": {"curricula": [None]}}, 422),
        ({"curriculumState": {"curricula": [{"courses": [None]}]}}, 422),
        ({"curriculumState": {"curricula": [{"courses": [{"program": "P1", "courseCode": "x" * 101}]}]}}, 422),
        ({"curriculumState": {"curricula": [{}] * 101}}, 422),
        ({"curriculumState": {"curricula": [{"courses": [{"program": "P1"}] * 2001}]}}, 422),
        ({"customize": {"extra": "x" * 4097}}, 422),
        ({"customize": []}, 422),
    ]
    deep = {}
    for _ in range(14):
        deep = {"child": deep}
    values.append(({"customize": deep}, 422))
    for settings, status in values:
        response = alpha.put("/api/settings?program_id=1", json={"settings": settings, "version": 1})
        assert response.status_code == status, response.text
    assert alpha.get("/api/settings?program_id=1").json()["version"] == 1
    assert alpha.get("/api/activity").json() == []


def test_body_size_limits_and_png_validation(clients):
    alpha = clients["alpha"]
    for path, size in [("/api/settings", 384 * 1024), ("/api/export/png", 15 * MIB),
                       ("/api/file/import-csv?program_id=1", 6 * MIB), ("/api/schedule", MIB)]:
        # A declared oversize body is rejected without reading the supplied bytes.
        response = alpha.post(path, content=b"{}", headers={"Content-Length": str(size + 1)})
        assert response.status_code == 413
    assert alpha.post("/api/export/png", json={"png_base64": []}).status_code == 422
    assert alpha.post("/api/export/png", json={"png_base64": "x" * (14 * MIB + 1)}).status_code == 413


def test_chunked_body_limit_stops_reading():
    # Use the real app for FastAPI's HTTPException->413 handling, without a
    # Content-Length header. No arbitrarily large body is assembled by the test.
    async def check():
        calls = 0
        sent = []
        async def receive():
            nonlocal calls
            calls += 1
            return {"type": "http.request", "body": b" " * (256 * 1024), "more_body": True}
        async def send(message):
            sent.append(message)
        await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
            "scheme": "http", "path": "/api/export/png", "raw_path": b"/api/export/png", "query_string": b"",
            "root_path": "", "headers": [(b"content-type", b"application/json")],
            "client": ("tunnel", 123), "server": ("localhost", 80)}, receive, send)
        assert sent[0]["status"] == 413
        assert calls == 61
    asyncio.run(check())


def test_csv_formula_injection_and_ordinary_values(clients, entry_payload):
    dangerous = ["=1+1", "+SUM(1,2)", "-1+2", "@SUM(A1)", "  =HYPERLINK(1)", "\t=1", "\r=1", "\n=1"]
    ordinary = ["C-101", "Course + lab", "8:00a-9:00a", "M,W", "3", "", "O'Brien"]
    rows = list(csv.reader(io.StringIO(reports.write_csv([dangerous, ordinary]).decode())))
    assert rows[0] == ["'" + value for value in dangerous]
    assert rows[1] == ordinary
    assert clients["alpha"].post("/api/schedule", json={**entry_payload, "Course Code": "=1+1"}).status_code == 200
    exported = clients["alpha"].get("/api/reports/text.csv")
    assert list(csv.reader(io.StringIO(exported.text)))[1][2] == "'=1+1"


def test_origin_csrf_and_security_headers(clients, entry_payload, monkeypatch):
    monkeypatch.setenv("PUBLIC_ORIGIN", "https://scheduler.camp.edu")
    alpha = clients["alpha"]
    for headers in [{"Origin": "https://evil.example"}, {"Origin": "null"}, {"X-CSRF-Token": "wrong"}]:
        assert alpha.post("/api/schedule", json=entry_payload, headers=headers).status_code == 403
    assert alpha.post("/api/schedule", json=entry_payload, headers={"Origin": "https://scheduler.camp.edu"}).status_code == 200
    assert alpha.post("/api/auth/login", json={"username": "alpha", "password": PASSWORD}, headers={"Origin": "https://evil.example"}).status_code == 403
    response = alpha.get("/api/reports/faculty-load.html?faculty=F1")
    csp = response.headers["content-security-policy"]
    assert "script-src 'self';" in csp and "connect-src 'self'" in csp
    assert "object-src 'none'" in csp and "frame-ancestors 'none'" in csp
    assert "camera=()" in response.headers["permissions-policy"]
    assert response.headers["x-frame-options"] == "DENY"


def test_shared_tunnel_peer_does_not_lock_out_other_accounts(clients, monkeypatch):
    # Seed >100 recent failures at the shared peer, including legacy peer rows.
    with SessionLocal() as db:
        db.add(models.LoginAttempt(key=auth.digest("peer:testclient"), attempts=500, window_start=auth.now()))
        for i in range(110):
            db.add(models.LoginAttempt(key=auth.digest(f"account:wrong{i}"), attempts=1, window_start=auth.now()))
        db.commit()
    stranger = TestClient(app)
    for _ in range(10):
        assert stranger.post("/api/auth/login", json={"username": "alpha", "password": "wrong"}).status_code == 401
    # Spoofing forwarded headers neither bypasses account throttling nor causes
    # a different account behind the same connector to be throttled.
    assert stranger.post("/api/auth/login", json={"username": "alpha", "password": PASSWORD}, headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 429
    assert stranger.post("/api/auth/login", json={"username": "beta", "password": PASSWORD}).status_code == 200
    with SessionLocal() as db:
        attempt = db.get(models.LoginAttempt, auth.digest("account:alpha"))
        attempt.window_start = auth.now() - timedelta(minutes=16)
        db.commit()
    assert stranger.post("/api/auth/login", json={"username": "alpha", "password": PASSWORD}).status_code == 200


def test_expired_auth_rows_cleanup_on_failure_and_periodic_job(clients):
    def seed():
        with SessionLocal() as db:
            db.add(models.LoginAttempt(key="stale", attempts=10, window_start=auth.now() - timedelta(hours=25)))
            db.add(models.LoginSession(token_hash="expired", user_id=2, csrf_token="unused", expires_at=auth.now() - timedelta(seconds=1), last_seen=auth.now()))
            db.commit()
    def assert_clean():
        with SessionLocal() as db:
            assert db.get(models.LoginAttempt, "stale") is None
            assert db.get(models.LoginSession, "expired") is None
            assert db.scalar(select(func.count()).select_from(models.LoginSession)) == 3
    seed()
    assert TestClient(app).post("/api/auth/login", json={"username": "wrong", "password": "wrong"}).status_code == 401
    assert_clean()
    seed()
    auth.cleanup_expired()
    assert_clean()


@pytest.mark.parametrize("kind,field", [("rooms", "Room"), ("faculty", "Faculty")])
def test_shared_catalog_creation_and_import_permissions(clients, entry_payload, kind, field):
    alpha, admin = clients["alpha"], clients["admin"]
    assert alpha.post(f"/api/{kind}", json={"name": "New shared"}).status_code == 403
    assert alpha.post(f"/api/catalog/{kind}", json={"name": "New shared"}).status_code == 403
    original = alpha.post("/api/schedule", json=entry_payload).json()
    imported = {**entry_payload, "Section": "Imported", field: "New shared"}
    assert alpha.post("/api/file/import-csv?program_id=1&replace=true", files=csv_file([imported])).status_code == 422
    assert alpha.get("/api/schedule").json()[0]["id"] == original["id"]
    assert len(alpha.get("/api/sections?program_id=1").json()) == 1
    assert admin.post("/api/file/import-csv?program_id=1&replace=true", files=csv_file([imported])).status_code == 200
    shared = next(item for item in alpha.get(f"/api/{kind}").json() if item["name"] == "New shared")
    assert alpha.put(f'/api/{kind}/{shared["id"]}', json={"name": "Renamed", "version": 1}).status_code == 403
    assert alpha.delete(f'/api/{kind}/{shared["id"]}?version=1').status_code == 403
    assert admin.put(f'/api/{kind}/{shared["id"]}', json={"name": "Renamed", "version": 1}).status_code == 200
