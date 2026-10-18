"""Optional monitoring must remain private and cannot prevent ticket entry."""
from unittest.mock import MagicMock, Mock

import pytest
import sentry_sdk
from sentry_sdk.transport import Transport

from fixflow.monitoring import failure, init_monitoring, trace


def test_disabled_monitoring_does_not_initialize_sdk(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    initialize = Mock()
    monkeypatch.setattr("fixflow.monitoring.sentry_sdk.init", initialize)
    assert init_monitoring() is None
    initialize.assert_not_called()


@pytest.mark.parametrize("rate", ["NaN", "inf", "-0.1", "1.1", "invalid"])
def test_bad_sample_config_reports_generic_warning(monkeypatch, rate):
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.test/1")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", rate)
    initialize = Mock()
    monkeypatch.setattr("fixflow.monitoring.sentry_sdk.init", initialize)
    warning = init_monitoring()
    assert "Ticket management is still available" in warning
    assert "example.test" not in warning
    initialize.assert_not_called()


def test_bad_dsn_does_not_crash_application(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "bad-private-dsn")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")
    monkeypatch.setattr("fixflow.monitoring.sentry_sdk.init", Mock(side_effect=ValueError("bad-private-dsn")))
    warning = init_monitoring()
    assert "Sentry monitoring could not be configured" in warning
    assert "bad-private-dsn" not in warning


def test_monitoring_configuration_disables_implicit_capture(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.test/1")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.25")
    initialize = Mock()
    monkeypatch.setattr("fixflow.monitoring.sentry_sdk.init", initialize)
    assert init_monitoring() is None
    options = initialize.call_args.kwargs
    assert options["traces_sample_rate"] == 0.25
    for key in ("default_integrations", "auto_enabling_integrations", "auto_session_tracking",
                "send_client_reports", "enable_logs"):
        assert options[key] is False
    assert options["data_collection"]["user_info"] is False
    assert options["data_collection"]["http_bodies"] == []
    assert options["data_collection"]["gen_ai"] == {"inputs": False, "outputs": False}
    assert options["data_collection"]["stack_frame_variables"] is False
    assert options["max_request_body_size"] == "never"
    assert options["max_breadcrumbs"] == 0


@pytest.mark.parametrize("stage", ["start", "enter", "finish"])
@pytest.mark.parametrize("body_fails", [False, True])
def test_tracing_failure_never_changes_application_result(monkeypatch, stage, body_fails):
    transaction = MagicMock()
    start = Mock(return_value=transaction)
    if stage == "start":
        start.side_effect = RuntimeError("telemetry unavailable")
    elif stage == "enter":
        transaction.__enter__.side_effect = RuntimeError("telemetry unavailable")
    else:
        transaction.__exit__.side_effect = RuntimeError("telemetry unavailable")
    monkeypatch.setattr("fixflow.monitoring.sentry_sdk.start_transaction", start)

    def operation():
        with trace("fixflow.database"):
            if body_fails:
                raise ValueError("original database failure")
            return "saved"

    if body_fails:
        with pytest.raises(ValueError, match="original database failure"):
            operation()
    else:
        assert operation() == "saved"


def test_error_reporting_failure_preserves_clear_inference_error(monkeypatch):
    import requests
    from fixflow.extraction import InferenceError, extract
    from fixflow.models import Complaint, Location

    monkeypatch.setattr("fixflow.extraction.requests.post", Mock(side_effect=requests.Timeout()))
    monkeypatch.setattr("fixflow.monitoring.sentry_sdk.capture_message", Mock(side_effect=RuntimeError("unavailable")))
    with pytest.raises(InferenceError, match="No ticket was saved"):
        extract(Complaint(description="The bathroom tap is leaking.", location=Location()), "ollama")


def test_actual_sdk_envelopes_exclude_resident_content(monkeypatch):
    """Exercise SDK hooks using an in-memory transport; no Sentry network calls."""
    envelopes = []

    class MemoryTransport(Transport):
        def capture_envelope(self, envelope):
            envelopes.append(envelope)

    client = None
    previous_client = sentry_sdk.get_client()

    def initialize(**options):
        nonlocal client
        client = sentry_sdk.Client(transport=MemoryTransport, **options)
        sentry_sdk.get_global_scope().set_client(client)

    monkeypatch.setenv("SENTRY_DSN", "https://public@example.test/1")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "1")
    monkeypatch.setattr("fixflow.monitoring.sentry_sdk.init", initialize)
    secret = "Resident Alice Room 103: tap leaking"
    try:
        assert init_monitoring() is None
        with sentry_sdk.new_scope() as scope:
            scope.set_user({"username": secret})
            scope.set_extra("complaint", secret)
            scope.set_tag("room", secret)
            with trace("fixflow.extract") as _:
                with sentry_sdk.start_span(op="private", name=secret):
                    failure("inference_failed")
                sentry_sdk.capture_message(secret)
        sentry_sdk.flush()
    finally:
        sentry_sdk.get_global_scope().set_client(previous_client)
        if client is not None:
            client.close()
    items = [item for envelope in envelopes for item in envelope.items]
    assert {item.type for item in items} == {"event", "transaction"}
    events = [item.payload.json for item in items]
    assert sum(event.get("message") == "inference_failed" for event in events) == 1
    transaction = next(event for event in events if event.get("type") == "transaction")
    assert transaction["transaction"] == "fixflow.extract"
    assert transaction["spans"] == []
    assert all(secret not in str(event) for event in events)
