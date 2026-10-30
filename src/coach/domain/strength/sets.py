"""Strength sets recorded by a watch: the SETS stream (strength spec section 5; files spec 8.7).

Stored like laps, as a content-addressed blob of the source that recorded them. Offsets are
seconds from the session start and never decrease; ``None`` is UNKNOWN. A set names a
catalogue exercise only when the file's own exercise maps to one exactly; the file's code is
kept as provenance, never shown as an exercise name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from itertools import pairwise
from typing import Final

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.numbers import require_finite
from coach.domain.common.time import ensure_utc
from coach.domain.strength.exercise import check_exercise_id

MAX_SET_SECONDS: Final = 86_400.0
MAX_REPETITIONS: Final = 1000
MAX_LOAD_KG: Final = 1000.0
MAX_RIR: Final = 10
# the file format's own exercise code, for example fit:squat/barbell_back_squat
PROVIDER_EXERCISE: Final = re.compile(r"^[a-z]{2,10}:[a-z0-9_]{1,40}(/[a-z0-9_]{1,80})?$")


class SetType(StrEnum):
    ACTIVE = "ACTIVE"
    REST = "REST"


def check_repetitions(value: int | None, name: str = "repetitions") -> int | None:
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_REPETITIONS
    ):
        raise DomainValidationError(f"{name} are whole numbers from 0 to {MAX_REPETITIONS}")
    return value


def check_load(value: float | None, name: str = "load") -> float | None:
    """External load in kg; 0 is bodyweight only."""
    if value is not None and not 0 <= require_finite(value, name) <= MAX_LOAD_KG:
        raise DomainValidationError(f"{name} is 0 to {MAX_LOAD_KG:g} kg")
    return value


def check_rpe(value: float | None, name: str = "RPE") -> float | None:
    """RPE of a set: 1 to 10 in steps of 0.5 (strength spec section 5)."""
    if value is None:
        return None
    if not 1 <= require_finite(value, name) <= 10 or (value * 2) % 1:
        raise DomainValidationError(f"{name} is 1 to 10 in steps of 0.5")
    return value


def check_rir(value: int | None) -> int | None:
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_RIR
    ):
        raise DomainValidationError(f"repetitions in reserve are whole numbers from 0 to {MAX_RIR}")
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class WatchSet:
    start_offset_seconds: float
    duration_seconds: float | None = None
    type: SetType | None = None
    repetitions: int | None = None
    weight_kg: float | None = None
    exercise_id: str | None = None
    provider_exercise: str | None = None

    def __post_init__(self) -> None:
        if require_finite(self.start_offset_seconds, "set start offset") < 0:
            raise DomainValidationError("set start offsets must not be negative")
        duration = self.duration_seconds
        if duration is not None and not 0 < require_finite(duration, "set duration") <= (
            MAX_SET_SECONDS
        ):
            raise DomainValidationError("a set lasts more than 0 s and at most a day")
        check_repetitions(self.repetitions)
        check_load(self.weight_kg, "set weight")
        if self.exercise_id is not None:
            check_exercise_id(self.exercise_id)
        code = self.provider_exercise
        if code is not None and not PROVIDER_EXERCISE.fullmatch(code):
            raise DomainValidationError(f"invalid provider exercise code: {code!r}")

    @property
    def active(self) -> bool:
        """A set the file gives no type is taken as active; REST sets are the rests."""
        return self.type is not SetType.REST


@dataclass(frozen=True, slots=True, kw_only=True)
class WatchSets:
    start_time_utc: datetime
    sets: tuple[WatchSet, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "start_time_utc", ensure_utc(self.start_time_utc))
        if not self.sets:
            raise DomainValidationError("a set list has at least one set")
        offsets = [item.start_offset_seconds for item in self.sets]
        if any(later < earlier for earlier, later in pairwise(offsets)):
            raise DomainValidationError("set start offsets never decrease")

    def __len__(self) -> int:
        return len(self.sets)
