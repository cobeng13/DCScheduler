import csv
import io

import pytest


def save_rules(client, version=1, room=False, faculty=False, room_ids=None, faculty_ids=None, **kwargs):
    payload = {"version": version, "ignoreRoom": room, "ignoreFaculty": faculty}
    if room_ids is not None:
        payload["ignoreRoomIds"] = room_ids
    if faculty_ids is not None:
        payload["ignoreFacultyIds"] = faculty_ids
    return client.put("/api/admin/rules", json=payload, **kwargs)


def test_rules_admin_only_versioned_validated_and_audited(clients, monkeypatch):
    monkeypatch.setenv("PUBLIC_ORIGIN", "https://scheduler.example")
    admin, alpha = clients["admin"], clients["alpha"]
    assert alpha.get("/api/rules").json()["rules"] == {"ignoreRoom": False, "ignoreFaculty": False, "ignoreRoomIds": [], "ignoreFacultyIds": []}
    assert save_rules(alpha, room=True).status_code == 403
    assert save_rules(admin, room=True, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert save_rules(admin, room=True, headers={"Origin": "https://evil.example"}).status_code == 403
    assert admin.put("/api/admin/rules", json={"version": 1, "ignoreRoom": "true", "ignoreFaculty": False}).status_code == 422
    assert admin.put("/api/admin/rules", json={"version": 1, "ignoreRoom": True, "ignoreFaculty": False, "ignoreSection": True}).status_code == 422
    result = save_rules(admin, room=True)
    assert result.status_code == 200 and result.json()["version"] == 2
    assert save_rules(admin, faculty=True).status_code == 409
    assert alpha.get("/api/rules").json() == result.json()
    # Legacy personal/program preferences cannot change shared booking rules.
    assert alpha.put("/api/settings", json={"settings": {"conflictIgnore": {"ignoreFaculty": True}}}).status_code == 200
    assert alpha.get("/api/rules").json() == result.json()
    events = alpha.get("/api/activity").json()
    assert len(events) == 1 and events[0]["entity_type"] == "scheduling_rules"
    assert events[0]["before"]["ignoreRoom"] is False
    assert events[0]["after"]["ignoreRoom"] is True


def test_rules_apply_to_save_move_checks_sections_and_reenabling(clients, entry_payload):
    admin, alpha, beta = (clients[k] for k in ("admin", "alpha", "beta"))
    assert alpha.post("/api/schedule", json=entry_payload).status_code == 200
    second = {**entry_payload, "Program": "P2"}
    assert save_rules(admin, room=True).status_code == 200
    blocked = beta.post("/api/schedule", json=second)
    assert blocked.status_code == 409
    assert {c["conflict_type"] for c in blocked.json()["detail"]["conflicts"]} == {"faculty"}
    assert save_rules(admin, 2, room=True, faculty=True).status_code == 200
    assert beta.post("/api/schedule/0/move-check", json=second).json()["ok"] is True
    assert beta.post("/api/schedule", json=second).status_code == 200
    assert alpha.get("/api/conflicts").json()["conflicts"] == []
    # Neither global settings nor a reason can override a section overlap.
    assert admin.post("/api/schedule?override_reason=required", json=entry_payload).status_code == 409
    assert save_rules(admin, 3).status_code == 200
    conflicts = alpha.get("/api/conflicts").json()["conflicts"]
    assert {c["conflict_type"] for c in conflicts} == {"room", "faculty"}
    assert len(alpha.get("/api/schedule").json()) == 2
    assert beta.post("/api/schedule/0/move-check", json=second).json()["ok"] is False


def test_rules_apply_to_atomic_csv_import(clients, entry_payload):
    admin, alpha, beta = (clients[k] for k in ("admin", "alpha", "beta"))
    assert alpha.post("/api/schedule", json=entry_payload).status_code == 200
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(entry_payload))
    writer.writeheader()
    writer.writerow({**entry_payload, "Program": "P2"})
    def imported():
        return beta.post("/api/file/import-csv?program_id=2", files={"file": ("schedule.csv", output.getvalue(), "text/csv")})
    assert imported().status_code == 422
    assert save_rules(admin, room=True, faculty=True).status_code == 200
    assert imported().status_code == 200
    assert len(beta.get("/api/schedule?program_id=2").json()) == 1


def test_faculty_ignore_does_not_disable_room_check(clients, entry_payload):
    admin, alpha, beta = (clients[k] for k in ("admin", "alpha", "beta"))
    assert alpha.post("/api/schedule", json=entry_payload).status_code == 200
    assert save_rules(admin, faculty=True).status_code == 200
    blocked = beta.post("/api/schedule", json={**entry_payload, "Program": "P2"})
    assert blocked.status_code == 409
    assert {c["conflict_type"] for c in blocked.json()["detail"]["conflicts"]} == {"room"}
    assert beta.post("/api/schedule", json={**entry_payload, "Program": "P2", "Room": "R2"}).status_code == 200


@pytest.mark.parametrize("kind", ["room", "faculty"])
def test_specific_exceptions_do_not_disable_other_resources_or_sections(clients, entry_payload, kind):
    admin, alpha, beta = (clients[k] for k in ("admin", "alpha", "beta"))
    first = alpha.post("/api/schedule", json=entry_payload).json()
    ids = {kind + "_ids": [1]}
    assert save_rules(admin, **ids).status_code == 200
    second = {**entry_payload, "Program": "P2", "Faculty" if kind == "room" else "Room": "F2" if kind == "room" else "R2"}
    assert beta.post("/api/schedule/0/move-check", json=second).json()["ok"] is True
    assert beta.post("/api/schedule", json=second).status_code == 200
    assert alpha.get("/api/conflicts").json()["conflicts"] == []
    assert admin.post("/api/schedule?override_reason=needed", json=entry_payload).status_code == 409
    # The exception follows the resource ID through a rename.
    catalog = "rooms" if kind == "room" else "faculty"
    assert admin.put(f"/api/{catalog}/1", json={"name": "Renamed", "version": 1}).status_code == 200
    assert alpha.get("/api/rules").json()["rules"]["ignoreRoomIds" if kind == "room" else "ignoreFacultyIds"] == [1]
    # Another actual room/faculty member is still checked.
    other = {**entry_payload, "Days": "T", "Room": "R2", "Faculty": "F2"}
    assert alpha.post("/api/schedule", json=other).status_code == 200
    blocked = beta.post("/api/schedule", json={**other, "Program": "P2", "Faculty" if kind == "room" else "Room": "F1" if kind == "room" else "R1"})
    assert blocked.status_code == 409
    assert {c["conflict_type"] for c in blocked.json()["detail"]["conflicts"]} == {kind}
    assert save_rules(admin, 2, **{kind + "_ids": []}).status_code == 200
    assert {c["conflict_type"] for c in alpha.get("/api/conflicts").json()["conflicts"]} == {kind}
    assert len(alpha.get("/api/schedule").json()) == 3


def test_exception_permissions_limits_and_legacy_updates(clients):
    admin, alpha = clients["admin"], clients["alpha"]
    assert save_rules(alpha, room_ids=[1]).status_code == 403
    for ids in ([0], [-1], [True], ["1"], [1, 1], [999], list(range(1, 502))):
        assert save_rules(admin, room_ids=ids).status_code == 422
        assert save_rules(admin, faculty_ids=ids).status_code == 422
    assert admin.get("/api/rules").json()["version"] == 1
    assert save_rules(admin, room_ids=[1], faculty_ids=[2]).status_code == 200
    assert save_rules(admin, room_ids=[]).status_code == 409
    # Older clients that omit lists must not erase another admin's selections.
    unchanged = save_rules(admin, 2)
    assert unchanged.status_code == 200 and unchanged.json()["version"] == 2
    assert unchanged.json()["rules"]["ignoreRoomIds"] == [1]
    assert unchanged.json()["rules"]["ignoreFacultyIds"] == [2]
    event = alpha.get("/api/activity").json()[0]
    assert "ignoreRoomIds" in event["changed_fields"]


def test_specific_exceptions_apply_to_csv_and_bulk_with_atomic_section_checks(clients, entry_payload):
    admin, alpha, beta = (clients[k] for k in ("admin", "alpha", "beta"))
    assert alpha.post("/api/schedule", json=entry_payload).status_code == 200
    assert save_rules(admin, room_ids=[1], faculty_ids=[1]).status_code == 200
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(entry_payload))
    writer.writeheader()
    writer.writerow({**entry_payload, "Program": "P2"})
    assert beta.post("/api/file/import-csv?program_id=2", files={"file": ("test.csv", output.getvalue(), "text/csv")}).status_code == 200
    before = alpha.get("/api/schedule").json()
    batch = {"operations": [{"method": "POST", "entry": {**entry_payload, "Days": "T"}},
                            {"method": "POST", "entry": entry_payload}]}
    assert admin.post("/api/schedule/batch", json=batch).status_code == 409
    assert alpha.get("/api/schedule").json() == before


def test_removed_or_merged_resources_clean_up_exceptions_atomically(clients):
    admin = clients["admin"]
    assert save_rules(admin, room_ids=[1], faculty_ids=[1]).status_code == 200
    assert admin.delete("/api/rooms/1?version=1").status_code == 200
    rules = admin.get("/api/rules").json()
    assert rules["rules"]["ignoreRoomIds"] == [] and rules["version"] == 3
    assert admin.put("/api/faculty/1?merge=true", json={"name": "F2", "version": 1}).status_code == 200
    rules = admin.get("/api/rules").json()
    assert rules["rules"]["ignoreFacultyIds"] == [] and rules["version"] == 4
