"""Accidentally disclosed secrets are caught before storage or the LLM (SEC-SEC-005, AT-SEC-001)."""

import pytest

from coach.application.events import EventName
from coach.application.screening import SecretScreen, disclosure_notice
from coach.infrastructure.memory import RecordingEventSink

pytestmark = [pytest.mark.req("SEC-SEC-005"), pytest.mark.acceptance("AT-SEC-001")]

PASTED_KEY = "kX9vQ2mL7pR4tY8wZ1nB6cF3hJ5sD0gA2eU7"  # pragma: allowlist secret
PASSWORD_MESSAGE = "mi contraseña: hunter2"  # pragma: allowlist secret
MESSAGE = f"Hoy corrí 10 km. Por las dudas, mi clave de Garmin es {PASTED_KEY}"


def test_secret_is_redacted_and_the_athlete_is_warned() -> None:
    events = RecordingEventSink()
    screened = SecretScreen(events).screen(MESSAGE, language="es")
    assert screened.secret_detected
    assert PASTED_KEY not in screened.text
    assert screened.text.startswith("Hoy corrí 10 km.")
    assert screened.notice == disclosure_notice("es")
    assert events.events == [(EventName.SECRET_DETECTED, {"source": "chat"})]


def test_spanish_notice_recommends_rotation_and_secure_storage() -> None:
    notice = disclosure_notice("es")
    assert "comprometida" in notice
    assert "Garmin Connect" in notice
    assert "coach secret set" in notice
    assert PASTED_KEY not in notice


def test_english_notice_and_fallback_language() -> None:
    assert "compromised" in disclosure_notice("en")
    assert "compromised" in disclosure_notice("en-GB")
    assert disclosure_notice("pt-BR") == disclosure_notice("es")


def test_ordinary_message_passes_unchanged() -> None:
    events = RecordingEventSink()
    text = "Hoy corrí 10 km a ritmo suave, RPE 4. ¿Mañana hago series?"
    screened = SecretScreen(events).screen(text, language="es")
    assert (screened.text, screened.secret_detected, screened.notice) == (text, False, None)
    assert events.events == []


def test_spanish_password_assignments_are_detected() -> None:
    events = RecordingEventSink()
    screened = SecretScreen(events).screen(PASSWORD_MESSAGE, language="es")
    assert "hunter2" not in screened.text
    assert screened.secret_detected


def test_source_is_a_code() -> None:
    with pytest.raises(ValueError, match="event field"):
        SecretScreen(RecordingEventSink()).screen(MESSAGE, language="es", source="free text")
