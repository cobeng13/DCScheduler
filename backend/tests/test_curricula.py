from copy import deepcopy


def state():
    return {"curricula": [
        {"id": "old", "name": "2023-24", "sourceFileName": "old.csv", "courses": [
            {"program": "P1", "yearLevel": "Fourth Year", "semester": "First Semester", "courseCode": "MTAC 1 LAB",
             "courseDescription": "Assessment LAB", "labUnits": 3, "lecUnits": 0, "totalUnits": 3, "hours": 9,
             "unitNotes": "LAB (RLE)", "prerequisite": ""}]},
        {"id": "new", "name": "2026-27", "sourceFileName": "new.csv", "courses": [
            {"program": "P1", "yearLevel": "Fourth Year", "semester": "First Semester", "courseCode": "MTAC 1 LAB",
             "courseDescription": "Assessment LAB", "labUnits": 2, "lecUnits": 0, "totalUnits": 2, "hours": 6,
             "unitNotes": "LAB (RLE)", "prerequisite": ""}]},
    ], "selectedTerm": "First Semester", "sectionYearLevels": {"a": "Fourth Year"},
        "yearLevelCurriculumIds": {"fourth year": "old"}, "sectionCurriculumIds": {"a": "new"}}


def test_multiple_curricula_persist_with_assignments_versions_and_program_permissions(clients):
    alpha, beta = clients["alpha"], clients["beta"]
    payload = {"version": 1, "settings": {"curriculumState": state()}}
    saved = alpha.put("/api/settings?program_id=1", json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 2
    loaded = beta.get("/api/settings?program_id=1").json()["settings"]["curriculumState"]
    assert loaded == state()
    assert alpha.get("/api/schedule").json() == []  # A curriculum is not a timetable import.
    assert beta.put("/api/settings?program_id=1", json={**payload, "version": 2}).status_code == 403
    assert alpha.put("/api/settings?program_id=1", json=payload).status_code == 409
    changed = deepcopy(payload)
    changed["version"] = 2
    changed["settings"]["curriculumState"]["curricula"][1]["courses"][0]["program"] = "P2"
    assert alpha.put("/api/settings?program_id=1", json=changed).status_code == 403
    assert alpha.get("/api/settings?program_id=1").json()["settings"]["curriculumState"] == state()
    events = alpha.get("/api/activity").json()
    assert len(events) == 1 and events[0]["entity_type"] == "curriculum"


def test_missing_or_duplicate_curriculum_assignments_are_rejected_atomically(clients):
    alpha = clients["alpha"]
    for kind in ("yearLevelCurriculumIds", "sectionCurriculumIds", "duplicate", "removed", "invalid_map"):
        value = state()
        if kind == "duplicate":
            value["curricula"][1]["id"] = "old"
        elif kind == "removed":
            value["curricula"].pop()
        elif kind == "invalid_map":
            value["sectionYearLevels"] = []
        else:
            value[kind] = {"a": "nonexistent"}
        response = alpha.put("/api/settings?program_id=1", json={"version": 1, "settings": {"curriculumState": value}})
        assert response.status_code == 422, response.text
    assert alpha.get("/api/settings?program_id=1").json()["version"] == 1
    assert alpha.get("/api/activity").json() == []
