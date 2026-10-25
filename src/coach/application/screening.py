"""Screening of athlete text for accidentally disclosed secrets (SEC-SEC-005).

Runs before the text is stored or sent to the LLM. The athlete notice is fixed text produced
in code, so the coach never has to (or can) repeat the secret.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from coach.application.events import EventName, check_fields
from coach.application.ports import EventSink
from coach.application.redaction import redact

_NOTICES: Final = {
    "es": (
        "Detecté algo que parece una credencial (clave, token o contraseña) en tu mensaje. "
        "No la guardé ni la envié a nadie, y no la voy a repetir. Trátala como comprometida: "
        "revócala o cámbiala (si es tu clave de Garmin, bórrala en Garmin Connect y crea una "
        "nueva). Para guardar una clave de forma segura, usa el comando `coach secret set`."
    ),
    "en": (
        "I detected what looks like a credential (key, token or password) in your message. "
        "It was not stored or sent anywhere, and I will not repeat it. Treat it as "
        "compromised: revoke or rotate it (for a Garmin key, delete it in Garmin Connect and "
        "create a new one). To store a key safely, use the `coach secret set` command."
    ),
}
_DEFAULT_LANGUAGE: Final = "es"


def disclosure_notice(language: str) -> str:
    primary = language.split("-", 1)[0].lower()
    return _NOTICES.get(primary, _NOTICES[_DEFAULT_LANGUAGE])


@dataclass(frozen=True, slots=True)
class ScreenedText:
    text: str
    secret_detected: bool
    notice: str | None


class SecretScreen:
    def __init__(self, events: EventSink) -> None:
        self._events = events

    def screen(
        self, text: str, *, language: str = _DEFAULT_LANGUAGE, source: str = "chat"
    ) -> ScreenedText:
        fields = check_fields({"source": source})
        redacted = redact(text)
        if redacted == text:
            return ScreenedText(text, secret_detected=False, notice=None)
        self._events.emit(EventName.SECRET_DETECTED, fields)
        return ScreenedText(redacted, secret_detected=True, notice=disclosure_notice(language))
