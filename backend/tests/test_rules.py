import csv
import io


def save_rules(client, version=1, room=False, faculty=False, **kwargs):
    return client.put("/api/admin/rules", json={"version": version, "ignoreRoom": room, "ignoreFaculty": faculty}, **kwargs)


def test_rules_admin_only_versioned_validated_and_audited(clients, monkeypatch):
    monkeypatch.setenv("PUBLIC_ORIGIN", "https://scheduler.example")
    admin, alpha = clients["admin"], clients["alpha"]
    assert alpha.get("/api/rules").json()["rules"] == {"ignoreRoom": False, "ignoreFaculty": False}
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
