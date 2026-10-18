"""Regression checks for untrusted inference responses and operator settings."""
import json
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from fixflow.extraction import InferenceError, extract
from fixflow.models import Complaint, Location, TicketDraft


@pytest.fixture
def complaint():
    return Complaint(description="The bathroom tap keeps dripping.",
                     location=Location(building="Cedar", floor="1", area="Bathroom"))


@pytest.mark.parametrize("payload", [None, [], "invalid", {}, {"message": None},
                                      {"message": {"content": 42}}, {"message": {"content": ""}}])
def test_invalid_response_envelope_is_a_clear_failure(monkeypatch, complaint, payload):
    monkeypatch.setattr("fixflow.extraction.requests.post", Mock(return_value=Mock(json=lambda: payload)))
    with pytest.raises(InferenceError, match="No ticket was saved"):
        extract(complaint, "ollama")


@pytest.mark.parametrize("metadata", [{"done": False}, {"done": True, "done_reason": "length"}])
def test_truncated_generation_is_rejected_even_when_json_is_valid(monkeypatch, complaint, metadata):
    content = extract(complaint, "demo").extraction.model_dump_json()
    payload = {"message": {"content": content}, **metadata}
    monkeypatch.setattr("fixflow.extraction.requests.post", Mock(return_value=Mock(json=lambda: payload)))
    with pytest.raises(InferenceError):
        extract(complaint, "ollama")


@pytest.mark.parametrize("name,value", [
    ("OLLAMA_TIMEOUT", "0"), ("OLLAMA_TIMEOUT", "-1"), ("OLLAMA_TIMEOUT", "NaN"),
    ("OLLAMA_TIMEOUT", "inf"), ("OLLAMA_TIMEOUT", "slow"),
    ("OLLAMA_MODEL", ""), ("OLLAMA_MODEL", "x" * 121),
    ("OLLAMA_URL", "file:///private"), ("OLLAMA_URL", "http://"),
    ("OLLAMA_URL", "http://localhost:99999"),
    ("OLLAMA_URL", "http://user:secret@localhost:11434"),
    ("OLLAMA_URL", "http://localhost:11434?secret=anything"),
])
def test_invalid_configuration_fails_before_network(monkeypatch, complaint, name, value):
    monkeypatch.setenv(name, value)
    post = Mock()
    monkeypatch.setattr("fixflow.extraction.requests.post", post)
    with pytest.raises(InferenceError):
        extract(complaint, "ollama")
    post.assert_not_called()


def test_custom_model_host_and_timeout_are_replaceable(monkeypatch, complaint):
    monkeypatch.setenv("OLLAMA_URL", "https://inference.example.test/ollama/")
    monkeypatch.setenv("OLLAMA_MODEL", "gemma3:4b")
    monkeypatch.setenv("OLLAMA_TIMEOUT", "120")
    content = extract(complaint, "demo").extraction.model_dump_json()
    post = Mock(return_value=Mock(json=lambda: {"done": True, "message": {"content": content}}))
    monkeypatch.setattr("fixflow.extraction.requests.post", post)
    draft = extract(complaint, "ollama")
    assert draft.model == "gemma3:4b"
    assert post.call_args.args == ("https://inference.example.test/ollama/api/chat",)
    assert post.call_args.kwargs["timeout"] == (5, 120)
    payload = post.call_args.kwargs["json"]
    assert payload["model"] == "gemma3:4b"
    assert json.dumps(payload["format"]) in payload["messages"][0]["content"]


def test_demo_does_not_depend_on_ollama_configuration(monkeypatch, complaint):
    monkeypatch.setenv("OLLAMA_MODEL", "x" * 121)
    monkeypatch.setenv("OLLAMA_TIMEOUT", "invalid")
    monkeypatch.setattr("fixflow.extraction.requests.post", Mock(side_effect=AssertionError("network called")))
    assert extract(complaint, "demo").source == "demo"


@pytest.mark.parametrize("text", ["The socket is sparking.", "I was shocked by the fan.",
                                 "Exposed wires hang by the door.", "There are live wires in the hall."])
def test_urgent_symptom_variations_require_human_review(text):
    draft = extract(Complaint(description=text, location=Location()), "demo")
    assert draft.urgent_review
    assert draft.extraction.suggested_priority.value == "Urgent review"


@pytest.mark.parametrize("latency", [float("nan"), float("inf")])
def test_nonfinite_latency_cannot_be_persisted(complaint, latency):
    payload = extract(complaint, "demo").model_dump()
    payload["latency_ms"] = latency
    with pytest.raises(ValidationError):
        TicketDraft.model_validate(payload)
