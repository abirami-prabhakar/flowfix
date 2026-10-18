from pathlib import Path

from streamlit.testing.v1 import AppTest

from fixflow.database import Store
from fixflow.extraction import InferenceError

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def start_app(tmp_path, monkeypatch, *, public_demo="false", password="test-manager-password"):
    monkeypatch.setenv("FIXFLOW_DB", str(tmp_path / "ui.db"))
    monkeypatch.setenv("MANAGER_PASSWORD", password)
    monkeypatch.setenv("PUBLIC_DEMO", public_demo)
    monkeypatch.setenv("FIXFLOW_MODE", "demo")
    monkeypatch.setenv("SENTRY_DSN", "")
    return AppTest.from_file(APP_PATH, default_timeout=30).run()


def button(app, label):
    return next(widget for widget in app.button if widget.label == label)


def review(app, *, description="The bathroom tap keeps dripping all night.", area="Room 103"):
    app.text_area(key="complaint_description").set_value(description)
    for key, value in {"complaint_building": "Cedar", "complaint_floor": "1", "complaint_area": area}.items():
        app.text_input(key=key).set_value(value)
    button(app, "Review request").click().run()
    assert not app.exception


def test_complaint_to_dashboard(tmp_path, monkeypatch):
    app = start_app(tmp_path, monkeypatch)
    assert not app.exception
    review(app)
    assert any("SAMPLE RESULT" in warning.value for warning in app.warning)
    app.checkbox[0].check().run()
    button(app, "Save reviewed ticket").click().run()
    assert any("saved" in x.value for x in app.success)
    app.sidebar.radio[0].set_value("Manager Dashboard").run()
    assert not app.dataframe  # No private records before authentication.
    app.text_input(key="manager_password_input").set_value("test-manager-password")
    button(app, "Sign in").click().run()
    assert not app.exception
    assert app.dataframe
    app.text_input[0].set_value("Demo Staff")
    next(w for w in app.selectbox if w.label == "Update status").set_value("Assigned")
    next(b for b in app.button if b.label == "Save changes").click().run()
    assert not app.exception
    assert Store().get(1)["status"] == "Assigned"
    button(app, "Sign out").click().run()
    assert not app.dataframe
    assert not app.exception


def test_manager_disabled_without_password(tmp_path, monkeypatch):
    app = start_app(tmp_path, monkeypatch, password="")
    app.sidebar.radio[0].set_value("Manager Dashboard").run()
    assert not app.dataframe
    assert any("disabled" in x.value for x in app.info)


def test_follow_up_answers_require_a_new_review(tmp_path, monkeypatch):
    app = start_app(tmp_path, monkeypatch)
    review(app, area="")
    assert any("Missing information" in warning.value for warning in app.warning)
    assert any("Which room or common area" in text.value for text in app.text)
    assert "area" in app.session_state.draft.extraction.missing_information
    app.checkbox[0].check().run()
    assert not button(app, "Save reviewed ticket").disabled

    review(app, area="Room 103")
    assert not app.session_state.draft.extraction.missing_information
    assert not app.session_state.draft.extraction.follow_up_questions
    assert not app.checkbox[0].value
    assert button(app, "Save reviewed ticket").disabled
    assert not Store().tickets()


def test_failed_inference_clears_previous_draft_and_saves_nothing(tmp_path, monkeypatch):
    app = start_app(tmp_path, monkeypatch)
    review(app)

    def unavailable(*args, **kwargs):
        raise InferenceError("Extraction failed. Check Ollama is running. No ticket was saved.")

    monkeypatch.setattr("fixflow.extraction.extract", unavailable)
    button(app, "Review request").click().run()
    assert not app.exception
    assert any("No ticket was saved" in error.value for error in app.error)
    assert not any(widget.label == "Save reviewed ticket" for widget in app.button)
    assert not Store().tickets()


def test_urgent_warning_survives_inference_failure(tmp_path, monkeypatch):
    def unavailable(*args, **kwargs):
        raise InferenceError("Extraction failed. No ticket was saved.")

    monkeypatch.setattr("fixflow.extraction.extract", unavailable)
    app = start_app(tmp_path, monkeypatch)
    review(app, description="There are sparks coming from the socket.")
    assert any("Possible urgent issue" in error.value for error in app.error)
    assert any("No ticket was saved" in error.value for error in app.error)
    assert not Store().tickets()


def test_public_demo_never_bypasses_manager_authentication(tmp_path, monkeypatch):
    app = start_app(tmp_path, monkeypatch, public_demo="true")
    review(app)
    app.checkbox[0].check().run()
    button(app, "Save reviewed ticket").click().run()
    app.sidebar.radio[0].set_value("Manager Dashboard").run()
    assert not app.dataframe
    assert any(widget.label == "Manager password" for widget in app.text_input)
    app.text_input(key="manager_password_input").set_value("incorrect-password")
    button(app, "Sign in").click().run()
    assert not app.dataframe
    assert any("Incorrect password" in error.value for error in app.error)
    assert not app.exception


def test_resident_text_is_rendered_literally(tmp_path, monkeypatch):
    app = start_app(tmp_path, monkeypatch)
    complaint = "Tap leaks. ![private](https://example.invalid/track.png) **urgent**"
    review(app, description=complaint)
    assert any(text.value == complaint for text in app.text)
    assert not any(complaint in markdown.value for markdown in app.markdown)
    assert not app.exception
