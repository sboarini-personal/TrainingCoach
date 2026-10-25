"""Training resources and equipment (FR-PRF-004)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.time import Weekday

MINUTES_PER_DAY = 24 * 60


class Equipment(StrEnum):
    BARBELL = "BARBELL"
    DUMBBELLS = "DUMBBELLS"
    KETTLEBELLS = "KETTLEBELLS"
    BANDS = "BANDS"
    MACHINES = "MACHINES"
    PULL_UP_BAR = "PULL_UP_BAR"
    BENCH = "BENCH"
    BODYWEIGHT_ONLY = "BODYWEIGHT_ONLY"
    OTHER = "OTHER"


@dataclass(frozen=True, slots=True)
class PoolAccess:
    available: bool
    length_m: float | None = None

    def __post_init__(self) -> None:
        if self.length_m is not None and not 0 < self.length_m <= 100:
            raise DomainValidationError("pool length must be between 0 and 100 m")


class TennisSessionType(StrEnum):
    MATCH = "MATCH"
    PRACTICE = "PRACTICE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class TennisSlot:
    weekday: Weekday
    start_minute: int
    duration_minutes: int
    session_type: TennisSessionType = TennisSessionType.UNKNOWN

    def __post_init__(self) -> None:
        if not 0 <= self.start_minute < MINUTES_PER_DAY:
            raise DomainValidationError("start must be a minute of the day")
        if not 1 <= self.duration_minutes <= 600:
            raise DomainValidationError("tennis duration must be 1 to 600 minutes")


@dataclass(frozen=True, slots=True, kw_only=True)
class Resources:
    pool: PoolAccess | None = None
    bicycle: bool | None = None
    smart_trainer: bool | None = None
    gym: bool | None = None
    strength_equipment: frozenset[Equipment] = field(default_factory=frozenset)
    tennis_schedule: tuple[TennisSlot, ...] = ()
