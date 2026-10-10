"""Real PostgreSQL races and migrations. Uses a randomly named isolated schema."""
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from app import auth, models
from app.main import app
from test_migrations import apply_revisions


def test_postgres_browser_backup_restore_and_sequences(pg_clients, entry_payload):
    dependency = app.dependency_overrides[auth.get_db]()
    try:
        db = next(dependency)
        db.get(models.User, 1).is_admin = True
        db.commit()
    finally:
        dependency.close()
    admin = pg_clients[0]
    assert admin.post("/api/schedule", json={**entry_payload, "Program": "P0", "Room": "R", "Faculty": "F"}).status_code == 200
    backup = admin.post("/api/admin/database/backup")
    assert backup.status_code == 200
    assert admin.post("/api/rooms", json={"name": "After snapshot"}).status_code == 200
    restored = admin.post("/api/admin/database/restore", content=backup.content, headers={
        "X-Admin-Password": "Test-password-123!", "X-Restore-Confirmation": "REPLACE DATABASE"})
    assert restored.status_code == 200, restored.text
    assert restored.json()["ready"] is True
    assert admin.get("/api/schedule").status_code == 401
    login = admin.post("/api/auth/login", json={"username": "user0", "password": "Test-password-123!"})
    assert login.status_code == 200
    admin.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    assert len(admin.get("/api/schedule").json()) == 1
    assert admin.post("/api/rooms", json={"name": "After restored snapshot"}).status_code == 200


@pytest.fixture
def pg_clients():
    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set TEST_POSTGRES_URL to run PostgreSQL transaction tests")
    schema = "scheduler_test_" + uuid4().hex
    root = create_engine(url)
    with root.begin() as db:
        db.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    session_factory = sessionmaker(engine, autoflush=False)
    try:
        with engine.begin() as connection:
            apply_revisions(connection)
        with session_factory() as db:
            for i in range(2):
                user = models.User(username=f"user{i}", password_hash=auth.password_hash("Test-password-123!"),
                    is_admin=False, disabled=False, must_change_password=False)
                db.add(user)
                db.flush()
                program = models.Program(name=f"P{i}", assigned_user_id=user.id)
                db.add(program)
                db.flush()
                db.add(models.Section(name="A", normalized_name="a", program_id=program.id))
            db.add_all([models.Room(name="R", normalized_name="r"), models.Faculty(name="F", normalized_name="f")])
            db.commit()
        def db_dependency():
            with session_factory() as db:
                yield db
        app.dependency_overrides[auth.get_db] = db_dependency
        clients = [TestClient(app), TestClient(app)]
        for i, client in enumerate(clients):
            response = client.post("/api/auth/login", json={"username": f"user{i}", "password": "Test-password-123!"})
            assert response.status_code == 200, response.text
            client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        yield clients
        for client in clients:
            client.close()
    finally:
        app.dependency_overrides.pop(auth.get_db, None)
        engine.dispose()
        with root.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        root.dispose()


@pytest.mark.parametrize("resource", ["room", "faculty", "section"])
def test_simultaneous_bookings_only_one_commits(pg_clients, entry_payload, resource):
    barrier = Barrier(2)
    def book(index):
        barrier.wait(timeout=10)
        payload = {**entry_payload, "Program": f"P{index}", "Room": "TBA", "Faculty": "TBA"}
        if resource == "section":
            payload["Program"] = "P0"
        else:
            payload[resource.title()] = "R" if resource == "room" else "F"
        client = pg_clients[0] if resource == "section" else pg_clients[index]
        return client.post("/api/schedule", json=payload)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(book, (0, 1)))
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert len(pg_clients[0].get("/api/schedule").json()) == 1
    assert len([e for e in pg_clients[0].get("/api/activity").json() if e["entity_type"] == "schedule"]) == 1


def test_simultaneous_same_version_updates_only_one_commits(pg_clients, entry_payload):
    entry = pg_clients[0].post("/api/schedule", json={**entry_payload, "Program": "P0", "Room": "R", "Faculty": "F"}).json()
    barrier = Barrier(2)
    def update(index):
        barrier.wait(timeout=10)
        return pg_clients[0].put(f'/api/schedule/{entry["id"]}', json={**entry, "Course Description": f"Update {index}"})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(update, (0, 1)))
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert pg_clients[0].get(f'/api/schedule/{entry["id"]}').json()["version"] == 2


@pytest.mark.parametrize("resource", ["room", "faculty"])
def test_simultaneous_meeting_moves_recheck_shared_conflicts_and_undo(pg_clients, entry_payload, resource):
    entries = []
    for index, client in enumerate(pg_clients):
        response = client.post('/api/schedule', json={**entry_payload, 'Program': f'P{index}',
            'Days': 'M,W' if index == 0 else 'Th,F', 'Room': 'R' if resource == 'room' else 'TBA',
            'Faculty': 'F' if resource == 'faculty' else 'TBA'})
        assert response.status_code == 200, response.text
        entries.append(response.json())
    barrier = Barrier(2)
    def move(index):
        barrier.wait(timeout=10)
        return pg_clients[index].post(f"/api/schedule/{entries[index]['id']}/move", json={
            'source_day': 'M' if index == 0 else 'Th', 'destination_day': 'T', 'start_minutes': 600,
            'expected': entries[index]})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(move, (0, 1)))
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(index for index, response in enumerate(responses) if response.status_code == 200)
    assert len(pg_clients[0].get('/api/schedule').json()) == 3
    response = pg_clients[winner].post(f"/api/schedule/{entries[winner]['id']}/move/revert", json=responses[winner].json()['snapshot'])
    assert response.status_code == 200, response.text
    restored = pg_clients[0].get('/api/schedule').json()
    assert len(restored) == 2
    assert [row['Days'] for row in restored] == [row['Days'] for row in entries]


def test_simultaneous_moves_of_same_meeting_reject_stale_source(pg_clients, entry_payload):
    client = pg_clients[0]
    source = client.post('/api/schedule', json={**entry_payload, 'Program': 'P0', 'Room': 'R', 'Faculty': 'F', 'Days': 'M,W'}).json()
    barrier = Barrier(2)
    def move(index):
        barrier.wait(timeout=10)
        return client.post(f"/api/schedule/{source['id']}/move", json={
            'source_day': 'M', 'destination_day': 'T' if index == 0 else 'F', 'start_minutes': 600,
            'expected': source})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(move, (0, 1)))
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert len(client.get('/api/schedule').json()) == 2
