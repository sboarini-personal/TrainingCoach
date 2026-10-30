"""Safety notices (safety spec section 3; SAFE-MED-004).

Fixed text produced in code: the athlete hears the safety reason and a recommendation of
appropriate healthcare evaluation, and for acute severe symptoms to contact local emergency
services. The text never names a cause, never says a symptom is normal and never gives
medical advice (SAFE-MED-001).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from coach.domain.recovery.checkin import RedFlag

# Red flags that are acute and severe: contact emergency services (safety spec 3).
ACUTE: Final = frozenset(
    {
        RedFlag.RF_CHEST_PAIN,
        RedFlag.RF_FAINTING,
        RedFlag.RF_BREATHING,
        RedFlag.RF_NEURO,
        RedFlag.RF_STRENGTH_LOSS,
        RedFlag.RF_SEVERE_ACUTE_PAIN,
    }
)
_HOLD: Final = {
    "es": (
        "Registraste una señal de alerta, así que pausé las recomendaciones de entrenamiento "
        "intenso (SAFETY_HOLD). Te recomiendo una evaluación con un profesional de la salud "
        "antes de volver a entrenar fuerte. Cuando un profesional te habilite, levantá la pausa "
        "con `coach safety release --cleared-by-professional`."
    ),
    "en": (
        "You reported a warning sign, so recommendations of intensive training are paused "
        "(SAFETY_HOLD). Please get an evaluation by a healthcare professional before training "
        "hard again. Once a professional clears you, lift the pause with "
        "`coach safety release --cleared-by-professional`."
    ),
}
_EMERGENCY: Final = {
    "es": (
        "Si el síntoma es intenso o empezó de golpe, contactá ahora a los servicios de "
        "emergencia de tu zona."
    ),
    "en": (
        "If the symptom is severe or started suddenly, contact your local emergency services now."
    ),
}
_DEFAULT_LANGUAGE: Final = "es"


def hold_notice(flags: Iterable[RedFlag], language: str = _DEFAULT_LANGUAGE) -> str:
    """The notice shown when red flags activate a SAFETY_HOLD."""
    primary = language.split("-", 1)[0].lower()
    if primary not in _HOLD:
        primary = _DEFAULT_LANGUAGE
    text = _HOLD[primary]
    if ACUTE & set(flags):
        text = f"{_EMERGENCY[primary]} {text}"
    return text
