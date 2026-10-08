import asyncio
import pytest
from starlette.requests import Request
from app import models
from app.db import SessionLocal
from app.main import events
from conftest import PASSWORD


@pytest.mark.parametrize("invalidation", ["disable", "reset", "logout", "restore"])
def test_stream_replays_committed_changes_and_revokes_live_sessions(clients, entry_payload, invalidation):
    alpha = clients["alpha"]
    token = alpha.cookies.get("scheduler_session")
    created = alpha.post("/api/schedule", json=entry_payload)
    assert created.status_code == 200
    first_event = alpha.get("/api/activity").json()[0]
    updated = alpha.put(f'/api/schedule/{created.json()["id"]}', json={**created.json(), "Course Description": "Replay"})
    assert updated.status_code == 200

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def check():
        request = Request({"type": "http", "method": "GET", "path": "/api/events", "headers": [
            (b"cookie", f"scheduler_session={token}".encode()),
            (b"last-event-id", str(first_event["id"]).encode())]}, receive)
        with SessionLocal() as db:
            user = db.get(models.User, 2)
            response = await events(request, after=0, db=db, user=user)
        stream = response.body_iterator
        assert "retry:" in await anext(stream)
        replay = await anext(stream)
        assert "event: activity" in replay and "Replay" in replay
        assert "Course Description" in replay
        assert "event: cursor" in await anext(stream)
        # Revoking the database session terminates an already-open stream.
        if invalidation == "logout":
            result = alpha.post("/api/auth/logout")
        elif invalidation == "restore":
            admin = clients["admin"]
            backup = admin.post("/api/admin/database/backup")
            assert backup.status_code == 200
            result = admin.post("/api/admin/database/restore", content=backup.content, headers={
                "X-Admin-Password": PASSWORD, "X-Restore-Confirmation": "REPLACE DATABASE"})
        else:
            payload = {"disabled": True} if invalidation == "disable" else {"password": "Reset-password-123!"}
            result = clients["admin"].put("/api/admin/users/2", json=payload)
        assert result.status_code == 200
        assert "session-expired" in await anext(stream)
        await stream.aclose()
    asyncio.run(check())
