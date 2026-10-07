import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# No test ever connects to the normal scheduler database.
_test_dir = tempfile.TemporaryDirectory(prefix="scheduler-tests-")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_test_dir.name) / "test.db")
os.environ["COOKIE_SECURE"] = "false"
os.environ.pop("DATABASE_PASSWORD_FILE", None)
from app import auth, models
from app.db import engine, SessionLocal
from app.main import app


@event.listens_for(engine, "connect")
def foreign_keys(connection, record):
    connection.execute("PRAGMA foreign_keys=ON")


PASSWORD = "Test-password-123!"


def pytest_sessionfinish(session, exitstatus):
    engine.dispose()
    _test_dir.cleanup()


@pytest.fixture
def clients():
    models.Base.metadata.drop_all(engine)
    models.Base.metadata.create_all(engine)
    with SessionLocal() as db:
        accounts = [models.User(username=name, password_hash=auth.password_hash(PASSWORD), is_admin=name == "admin",
            disabled=False, must_change_password=False) for name in ("admin", "alpha", "beta")]
        db.add_all(accounts)
        db.flush()
        db.add_all([models.Program(name="P1", assigned_user_id=accounts[1].id), models.Program(name="P2", assigned_user_id=accounts[2].id)])
        db.flush()
        for pid in (1, 2):
            db.add(models.Section(name="A", normalized_name="a", program_id=pid))
        db.add_all([models.Room(name="R1", normalized_name="r1"), models.Room(name="R2", normalized_name="r2"),
            models.Faculty(name="F1", normalized_name="f1"), models.Faculty(name="F2", normalized_name="f2")])
        db.commit()
    result = {}
    for name in ("admin", "alpha", "beta"):
        client = TestClient(app)
        login = client.post("/api/auth/login", json={"username": name, "password": PASSWORD})
        assert login.status_code == 200, login.text
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        result[name] = client
    yield result
    for client in result.values():
        client.close()


@pytest.fixture
def entry_payload():
    return {"Program": "P1", "Section": "A", "Course Code": "C101", "Course Description": "Course",
        "Units": 3, "# of Hours": 3, "Time (LPU Std)": "8:00a-9:00a", "Days": "M", "Room": "R1", "Faculty": "F1"}
