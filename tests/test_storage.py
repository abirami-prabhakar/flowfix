"""Storage regressions use temporary files and fictional complaint data only."""

import pytest

from fixflow.database import DatabaseError, Store, duplicates, metrics
from fixflow.extraction import extract
from fixflow.models import Complaint, Location
from fixflow.seed import seed


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "storage.db")


@pytest.fixture
def draft():
    return extract(Complaint(description="The bathroom tap keeps leaking when closed.",
                             location=Location(building="Cedar", floor="1", area="Room 103")), "demo")


@pytest.mark.parametrize("key", [None, 123, "", "  ", "x" * 121])
def test_submission_key_must_be_nonblank_text(store, draft, key):
    with pytest.raises(ValueError, match="submission key"):
        store.save(draft, key)
    assert store.tickets() == []


@pytest.mark.parametrize("column,value", [
    ("draft", '{"private": "fictional resident detail"}'),
    ("status", "fictional resident detail"),
    ("created_at", "fictional resident detail"),
    ("updated_at", "2026-01-01T12:00:00"),  # Missing timezone.
    ("resolved_at", "fictional resident detail"),
])
def test_corrupt_rows_fail_with_a_safe_actionable_error(store, draft, column, value):
    ticket_id = store.save(draft, "corrupt-record")
    with store.connection() as db:
        db.execute(f"UPDATE tickets SET {column}=? WHERE id=?", (value, ticket_id))
    with pytest.raises(DatabaseError, match="restore a valid database backup") as error:
        store.get(ticket_id)
    assert "fictional resident detail" not in str(error.value)
    with pytest.raises(DatabaseError):
        store.tickets()


def test_staff_only_edit_rejects_a_stale_ticket_version(store, draft):
    ticket_id = store.save(draft, "staff-version")
    second_session = Store(store.path)
    stale = second_session.get(ticket_id)
    store.update(ticket_id, "New", "Demo Staff A", "New", expected_updated_at=stale["updated_at"])
    with pytest.raises(ValueError, match="another session"):
        second_session.update(ticket_id, "New", "Demo Staff B", "New", expected_updated_at=stale["updated_at"])
    assert store.get(ticket_id)["staff"] == "Demo Staff A"
    assert len(store.history(ticket_id)) == 2


def test_no_op_update_preserves_ticket_version_and_history(store, draft):
    ticket_id = store.save(draft, "no-op")
    original = store.get(ticket_id)
    store.update(ticket_id, "New", "  ", "New", expected_updated_at=original["updated_at"])
    assert store.get(ticket_id) == original
    assert len(store.history(ticket_id)) == 1


@pytest.mark.parametrize("staff", [None, 123, "x" * 121])
def test_invalid_staff_never_modifies_a_ticket(store, draft, staff):
    ticket_id = store.save(draft, "invalid-staff")
    original = store.get(ticket_id)
    with pytest.raises(ValueError, match="Staff"):
        store.update(ticket_id, "Assigned", staff, "New")
    assert store.get(ticket_id) == original
    assert len(store.history(ticket_id)) == 1


def reject_history_writes(store):
    with store.connection() as db:
        db.execute("""CREATE TRIGGER reject_history BEFORE INSERT ON status_changes
                      BEGIN SELECT RAISE(ABORT, 'fictional private failure'); END""")


def test_new_ticket_and_initial_history_are_atomic(store, draft):
    reject_history_writes(store)
    with pytest.raises(DatabaseError, match="Database operation failed"):
        store.save(draft, "failed-save")
    assert store.tickets() == []


def test_ticket_update_and_history_are_atomic(store, draft):
    ticket_id = store.save(draft, "failed-update")
    original = store.get(ticket_id)
    reject_history_writes(store)
    with pytest.raises(DatabaseError, match="Database operation failed"):
        store.update(ticket_id, "Assigned", "Demo Staff", "New")
    assert store.get(ticket_id) == original
    assert len(store.history(ticket_id)) == 1


@pytest.mark.parametrize("location", [
    {"building": "---", "floor": "1", "area": "Room 103"},
    {"building": "Cedar", "floor": "...", "area": "Room 103"},
    {"building": "Cedar", "floor": "1", "area": "???"},
    {"building": "Cedar", "floor": "1", "area": "___"},
])
def test_duplicate_matching_requires_meaningful_location(store, location):
    complaint = Complaint(description="The bathroom tap keeps leaking when closed.", location=Location(**location))
    draft = extract(complaint, "demo")
    store.save(draft, "unknown-location")
    assert duplicates(draft, store.tickets()) == []


def test_punctuation_only_complaints_do_not_match(store):
    draft = extract(Complaint(description="!!!!!!!!", location=Location(building="Cedar", floor="1", area="Room 103")), "demo")
    store.save(draft, "no-text-evidence")
    assert duplicates(draft, store.tickets()) == []


def test_duplicates_normalize_case_spacing_and_punctuation(store, draft):
    ticket_id = store.save(draft, "normalized-match")
    complaint = draft.complaint.model_copy(deep=True)
    complaint.location = Location(building="CEDAR", floor="1", area="room-103")
    assert duplicates(extract(complaint, "demo"), store.tickets()) == [(ticket_id, 1.0)]


def test_resolution_metric_uses_elapsed_time_and_excludes_reopened_tickets(store, draft, monkeypatch):
    timestamps = iter([
        "2026-01-01T10:00:00+00:00", "2026-01-01T11:00:00+00:00",
        "2026-01-01T13:00:00+00:00", "2026-01-01T14:00:00+00:00",
    ])
    monkeypatch.setattr("fixflow.database.now", lambda: next(timestamps))
    ticket_id = store.save(draft, "elapsed-time")
    store.update(ticket_id, "In progress", "Demo Staff", "New")
    store.update(ticket_id, "Resolved", "Demo Staff", "In progress")
    assert metrics(store.tickets())["average_resolution_hours"] == 3
    store.update(ticket_id, "In progress", "Demo Staff", "Resolved")
    assert metrics(store.tickets())["average_resolution_hours"] is None
    assert metrics(store.tickets())["resolved_sample_count"] == 0


def test_reseeding_preserves_manager_changes_and_history(store):
    ticket_id = seed(store)[0]
    store.update(ticket_id, "In progress", "Demo Staff", "New")
    original = store.get(ticket_id)
    history = store.history(ticket_id)
    assert seed(store)[0] == ticket_id
    assert store.get(ticket_id) == original
    assert store.history(ticket_id) == history
    assert len(store.tickets()) == 6
