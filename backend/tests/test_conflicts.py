from app import time_utils


def test_overlap_logic():
    assert time_utils.overlap(60, 120, 110, 180) is True
    assert time_utils.overlap(60, 120, 120, 180) is False
    assert time_utils.overlap(60, 120, 0, 59) is False


def test_multiday_overlap_is_rejected_and_tba_resources_do_not_conflict(clients, entry_payload):
    alpha, beta = clients["alpha"], clients["beta"]
    assert alpha.post("/api/schedule", json={**entry_payload, "Days": "M,W"}).status_code == 200
    conflict = beta.post("/api/schedule", json={**entry_payload, "Program": "P2", "Days": "W,F"})
    assert conflict.status_code == 409
    assert {c["conflict_type"] for c in conflict.json()["detail"]["conflicts"]} == {"room", "faculty"}
    assert beta.post("/api/schedule", json={**entry_payload, "Program": "P2", "Days": "T", "Room": "TBA", "Faculty": "TBA"}).status_code == 200
    assert alpha.post("/api/schedule", json={**entry_payload, "Days": "T", "Room": "TBA", "Faculty": "TBA"}).status_code == 200
