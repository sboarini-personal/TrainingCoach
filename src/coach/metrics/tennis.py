"""Tennis regional neuromuscular load, version 1.0.0 (spec/functional/tennis.spec.md 4).

Initial, versioned rules (assumption A-18): a level from duration, RPE and session type, and
the fixed regions a tennis session loads for the following 48 hours. The interference engine
(P10) and recovery (P11) read them.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import IntEnum
from typing import Final

SECONDS_PER_MINUTE: Final = 60.0
MODERATE_FROM_MINUTES: Final = 45.0
HIGH_ABOVE_MINUTES: Final = 90.0
HARD_RPE: Final = 7.0
EASY_RPE: Final = 3.0
REGIONAL_LOAD_HOURS: Final = 48
TENNIS_REGIONS: Final = (
    "calves",
    "soleus",
    "achilles_region",
    "knees",
    "adductors",
    "shoulders",
    "elbows",
)


class LoadLevel(IntEnum):
    LOW = 1
    MODERATE = 2
    HIGH = 3


def load_level(minutes: float, rpe: float, *, match: bool) -> LoadLevel:
    if minutes < MODERATE_FROM_MINUTES:
        level = LoadLevel.LOW
    elif minutes <= HIGH_ABOVE_MINUTES:
        level = LoadLevel.MODERATE
    else:
        level = LoadLevel.HIGH
    step = 0
    if rpe >= HARD_RPE:
        step += 1
    elif rpe <= EASY_RPE:
        step -= 1
    if match:
        step += 1
    return LoadLevel(min(LoadLevel.HIGH, max(LoadLevel.LOW, level + step)))


def neuromuscular_load(inputs: Mapping[str, float]) -> float:
    """The level as a number (1 LOW, 2 MODERATE, 3 HIGH) for a derived value."""
    minutes = inputs["session_duration_seconds"] / SECONDS_PER_MINUTE
    return float(load_level(minutes, inputs["rpe"], match=inputs["match"] >= 1))
