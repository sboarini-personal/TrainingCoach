"""Deterministic red-flag phrase detection, lists 1.0.0 (safety spec section 2, source 2).

Athlete free text (check-in notes) is matched against versioned Spanish and English phrase
lists after normalisation: lower case, accents removed, every character other than a letter
or digit turned into a space. A phrase matches as whole words. Detection is fail-safe: there
is no negation handling ("no puedo respirar" must match), so a phrase that names a warning
sign activates the hold even in a sentence that denies it, and the athlete releases it
(safety spec 4). A pain whose location names the chest is chest pain. The lists are initial
values pending review; the text is never interpreted beyond these matches.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from typing import Final

from coach.domain.recovery.checkin import RedFlag

PHRASE_RULES_VERSION: Final = "1.0.0"
_NOT_WORD: Final = re.compile(r"[^a-z0-9]+")

PHRASES: Final[Mapping[RedFlag, tuple[str, ...]]] = {
    RedFlag.RF_CHEST_PAIN: (
        "dolor de pecho",
        "dolor en el pecho",
        "dolor toracico",
        "presion en el pecho",
        "opresion en el pecho",
        "puntada en el pecho",
        "duele el pecho",
        "aprieta el pecho",
        "chest pain",
        "chest pressure",
        "chest tightness",
        "chest hurts",
        "chest pains",
        "pain in my chest",
        "pressure in my chest",
        "tightness in my chest",
    ),
    RedFlag.RF_FAINTING: (
        "desmayo",
        "me desmaye",
        "casi me desmayo",
        "perdi el conocimiento",
        "perdida de conocimiento",
        "me desvaneci",
        "fainted",
        "fainting",
        "passed out",
        "blacked out",
        "lost consciousness",
    ),
    RedFlag.RF_BREATHING: (
        "falta de aire",
        "falta el aire",
        "cuesta respirar",
        "no puedo respirar",
        "no podia respirar",
        "dificultad para respirar",
        "me ahogaba",
        "shortness of breath",
        "short of breath",
        "can t breathe",
        "cannot breathe",
        "couldn t breathe",
        "difficulty breathing",
        "trouble breathing",
        "hard to breathe",
    ),
    RedFlag.RF_NEURO: (
        "desorientado",
        "desorientada",
        "confusion mental",
        "no podia hablar",
        "dificultad para hablar",
        "cara caida",
        "paralisis facial",
        "entumecimiento de un lado",
        "adormecimiento de un lado",
        "peor dolor de cabeza",
        "dolor de cabeza repentino",
        "perdi la vision",
        "perdida de vision",
        "disoriented",
        "mental confusion",
        "slurred speech",
        "trouble speaking",
        "facial droop",
        "face drooping",
        "numbness on one side",
        "worst headache",
        "sudden severe headache",
        "lost my vision",
        "vision loss",
    ),
    RedFlag.RF_STRENGTH_LOSS: (
        "perdida de fuerza",
        "perdi fuerza",
        "perdi la fuerza",
        "debilidad repentina",
        "loss of strength",
        "lost strength",
        "sudden weakness",
    ),
    RedFlag.RF_SEVERE_ACUTE_PAIN: (
        "dolor insoportable",
        "dolor subito",
        "dolor repentino e intenso",
        "unbearable pain",
        "excruciating pain",
        "sudden severe pain",
    ),
    RedFlag.RF_PALPITATIONS: (
        "palpitaciones",
        "taquicardia",
        "latidos irregulares",
        "arritmia",
        "palpitations",
        "irregular heartbeat",
        "heart racing",
        "racing heart",
        "arrhythmia",
    ),
}
CHEST_WORDS: Final = ("pecho", "torax", "toracico", "pectoral", "pectorales", "chest")


def normalised(text: str) -> str:
    """Lower case without accents, words separated by single spaces, padded with spaces."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return f" {_NOT_WORD.sub(' ', plain).strip()} "


def detect(text: str | None) -> tuple[RedFlag, ...]:
    """The red flags whose phrases appear in the text, in the order of ``RedFlag``."""
    if not text:
        return ()
    found = normalised(text)
    return tuple(
        flag
        for flag in RedFlag
        if any(normalised(phrase) in found for phrase in PHRASES.get(flag, ()))
    )


def names_chest(location: str | None) -> bool:
    """Whether a pain location names the chest (chest pain is a red flag)."""
    if not location:
        return False
    found = normalised(location)
    return any(f" {word} " in found for word in CHEST_WORDS)
