"""Safety notices (safety spec section 3; SAFE-MED-004).

Fixed text produced in code: the athlete hears the safety reason (the warning signs
registered, named as the athlete would name them), a recommendation of appropriate
healthcare evaluation and, since every red flag may be acute and severe, to contact local
emergency services if the symptom is severe or started suddenly. The text never names a
cause, never says a symptom is normal and never gives medical advice (SAFE-MED-001).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Final

from coach.domain.recovery.checkin import RedFlag

# Red flags that may be acute and severe: contact emergency services (safety spec 3). All of
# them, palpitations with dizziness included (conservative, pending review).
ACUTE: Final = frozenset(RedFlag)
LABELS: Final[Mapping[str, Mapping[RedFlag, str]]] = {
    "es": {
        RedFlag.RF_CHEST_PAIN: "dolor o presión en el pecho",
        RedFlag.RF_FAINTING: "desmayo o casi desmayo",
        RedFlag.RF_BREATHING: "dificultad inusual para respirar",
        RedFlag.RF_NEURO: "síntomas neurológicos repentinos",
        RedFlag.RF_STRENGTH_LOSS: "pérdida repentina de fuerza",
        RedFlag.RF_SEVERE_ACUTE_PAIN: "dolor intenso y agudo",
        RedFlag.RF_PALPITATIONS: "palpitaciones o latidos irregulares",
    },
    "en": {
        RedFlag.RF_CHEST_PAIN: "chest pain or pressure",
        RedFlag.RF_FAINTING: "fainting or near-fainting",
        RedFlag.RF_BREATHING: "unusual breathing difficulty",
        RedFlag.RF_NEURO: "sudden neurological symptoms",
        RedFlag.RF_STRENGTH_LOSS: "sudden loss of strength",
        RedFlag.RF_SEVERE_ACUTE_PAIN: "severe acute pain",
        RedFlag.RF_PALPITATIONS: "palpitations or an irregular heartbeat",
    },
}
_HOLD: Final = {
    "es": (
        "Registraste una señal de alerta ({signs}), así que pausé las recomendaciones de "
        "entrenamiento intenso (SAFETY_HOLD). Te recomiendo una evaluación con un profesional "
        "de la salud antes de volver a entrenar fuerte. Cuando un profesional te habilite, "
        "levantá la pausa con `coach safety release --cleared-by-professional`."
    ),
    "en": (
        "You reported a warning sign ({signs}), so recommendations of intensive training are "
        "paused (SAFETY_HOLD). Please get an evaluation by a healthcare professional before "
        "training hard again. Once a professional clears you, lift the pause with "
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
_UNNAMED: Final = {"es": "sin detalle", "en": "unspecified"}
_DEFAULT_LANGUAGE: Final = "es"


def hold_notice(flags: Iterable[RedFlag], language: str = _DEFAULT_LANGUAGE) -> str:
    """The notice shown when red flags activate a SAFETY_HOLD."""
    primary = language.split("-", 1)[0].lower()
    if primary not in _HOLD:
        primary = _DEFAULT_LANGUAGE
    raised = list(dict.fromkeys(flags))
    signs = "; ".join(LABELS[primary][flag] for flag in raised) or _UNNAMED[primary]
    text = _HOLD[primary].format(signs=signs)
    if ACUTE & set(raised):
        text = f"{_EMERGENCY[primary]} {text}"
    return text
