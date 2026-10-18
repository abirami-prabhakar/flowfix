import json
from unittest.mock import Mock

import pytest
import requests
from pydantic import ValidationError

from fixflow.database import DatabaseError, Store, duplicates, metrics
from fixflow.extraction import InferenceError, extract
from fixflow.models import Complaint, Extraction, Location, Status
from fixflow.monitoring import sanitize_event, sanitize_transaction
from fixflow.seed import seed


@pytest.fixture
def complaint():
    return Complaint(description="The bathroom tap keeps leaking even when fully closed.",
                     location=Location(building="Cedar", floor="1", area="Room 103"))


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "tickets.db")


def test_demo_validation(complaint):
    draft = extract(complaint, "demo")
    assert draft.source == "demo"
    assert draft.extraction.category.value == "Plumbing"
    assert draft.extraction.missing_information == []
    assert draft.latency_ms >= 0


@pytest.mark.parametrize("description", ["", "   ", "short", "a" * 3001])
def test_bad_input(description):
    with pytest.raises(ValidationError):
        Complaint(description=description, location=Location())


def test_missing_fields_never_invented():
    draft = extract(Complaint(description="The tap is leaking.", location=Location(floor="0")), "demo")
    assert draft.extraction.location.building == ""
    assert draft.extraction.location.floor == "0"
    assert draft.extraction.missing_information == ["building", "area"]
    assert len(draft.extraction.follow_up_questions) == 2


def test_ollama_contract_and_untrusted_location(monkeypatch, complaint):
    result = extract(complaint, "demo").extraction.model_dump(mode="json")
    result["location"] = {"building": "Invented", "floor": "99", "area": "Secret"}
    post = Mock(return_value=Mock(json=lambda: {"message": {"content": json.dumps(result)}}))
    monkeypatch.setattr("fixflow.extraction.requests.post", post)
    draft = extract(complaint, "ollama")
    assert draft.source == "ollama"
    assert draft.extraction.location == complaint.location
    payload = post.call_args.kwargs
    assert payload["json"]["format"]["additionalProperties"] is False
    assert payload["json"]["stream"] is False
    assert payload["timeout"][0] == 5


@pytest.mark.parametrize("content", ["not JSON", "{}", '{"category":"Diagnosis"}', "[]"])
def test_malformed_model_response(monkeypatch, complaint, content):
    monkeypatch.setattr("fixflow.extraction.requests.post", Mock(return_value=Mock(json=lambda: {"message": {"content": content}})))
    with pytest.raises(InferenceError, match="No ticket was saved"):
        extract(complaint, "ollama")


@pytest.mark.parametrize("error", [requests.Timeout(), requests.ConnectionError(), requests.HTTPError()])
def test_inference_network_errors(monkeypatch, complaint, error):
    monkeypatch.setattr("fixflow.extraction.requests.post", Mock(side_effect=error))
    with pytest.raises(InferenceError):
        extract(complaint, "ollama")


def test_urgent_human_review():
    draft = extract(Complaint(description="The socket has sparks but I am not sure why.", location=Location()), "demo")
    assert draft.urgent_review
    assert draft.extraction.suggested_priority.value == "Urgent review"


def test_save_idempotent_and_persistent(store, complaint):
    draft = extract(complaint, "demo")
    first = store.save(draft, "repeat")
    assert store.save(draft, "repeat") == first
    assert len(Store(store.path).tickets()) == 1
    assert len(store.history(first)) == 1


def test_duplicates_locations_and_resolved(store, complaint):
    draft = extract(complaint, "demo")
    ticket_id = store.save(draft, "first")
    assert duplicates(draft, store.tickets()) == [(ticket_id, 1.0)]
    other = complaint.model_copy(deep=True)
    other.location.area = "Room 104"
    assert duplicates(extract(other, "demo"), store.tickets()) == []
    other.location.area = ""
    assert duplicates(extract(other, "demo"), store.tickets()) == []
    store.update(ticket_id, "In progress", "Demo Staff", "New")
    store.update(ticket_id, "Resolved", "Demo Staff", "In progress")
    assert duplicates(draft, store.tickets()) == []


def test_similarity_with_paraphrased_words(store, complaint):
    draft = extract(complaint, "demo")
    store.save(draft, "first")
    complaint.description = "Bathroom tap keeps leaking when fully closed."
    assert duplicates(extract(complaint, "demo"), store.tickets())


def test_transitions_history_and_reopening(store, complaint):
    ticket_id = store.save(extract(complaint, "demo"), "first")
    with pytest.raises(ValueError, match="Invalid status"):
        store.update(ticket_id, "Closed", "", "New")
    with pytest.raises(ValueError, match="staff member"):
        store.update(ticket_id, "Assigned", "", "New")
    for old, new in [("New", "Assigned"), ("Assigned", "In progress"), ("In progress", "Resolved"), ("Resolved", "Closed")]:
        store.update(ticket_id, new, "Demo Staff", old)
    assert store.get(ticket_id)["resolved_at"]
    assert metrics(store.tickets())["resolved_sample_count"] == 1
    store.update(ticket_id, "New", "Demo Staff", "Closed")
    assert store.get(ticket_id)["resolved_at"] is None
    assert len(store.history(ticket_id)) == 6


def test_conflicting_status_changes(store, complaint):
    ticket_id = store.save(extract(complaint, "demo"), "first")
    store.update(ticket_id, "Assigned", "Demo Staff", "New")
    with pytest.raises(ValueError, match="another session"):
        store.update(ticket_id, "In progress", "Demo Staff", "New")


def test_parameterized_values(store, complaint):
    complaint.location.area = "Room 1'; DROP TABLE tickets; --"
    ticket_id = store.save(extract(complaint, "demo"), "sql")
    assert store.get(ticket_id)["draft"].complaint.location == complaint.location


def test_db_error_is_clear(store, monkeypatch):
    import sqlite3
    monkeypatch.setattr("fixflow.database.sqlite3.connect", Mock(side_effect=sqlite3.OperationalError("sensitive path")))
    with pytest.raises(DatabaseError, match="Database operation failed"):
        store.tickets()


def test_seed_idempotent_and_empty_metrics(store):
    assert metrics([])["average_resolution_hours"] is None
    assert seed(store) == seed(store)
    assert len(store.tickets()) == 6


def test_monitoring_allowlist():
    assert sanitize_event({"message": "private complaint"}) is None
    event = sanitize_event({"message": "inference_failed", "exception": {"secret": "Room 103"}, "user": {}, "request": {}, "breadcrumbs": []})
    assert event == {"message": "inference_failed"}
    transaction = sanitize_transaction({"transaction": "fixflow.extract", "request": {"private": "text"},
                                        "spans": [{"description": "secret"}], "contexts": {"trace": {"trace_id": "abc", "data": "secret"}}})
    assert transaction == {"transaction": "fixflow.extract", "contexts": {"trace": {"trace_id": "abc"}}, "spans": []}
