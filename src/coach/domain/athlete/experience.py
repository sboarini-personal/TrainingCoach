"""Experience stored independently per sport (FR-PRF-003). ``None`` means UNKNOWN."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.provenance import ValueSource

EXERCISE_ID = re.compile(r"^ex\.[a-z0-9_]+$")


def _check(value: float | None, name: str, *, low: float = 0.0, high: float | None = None) -> None:
    if value is None:
        return
    if value < low or (high is not None and value > high):
        bounds = f"{low} and {high}" if high is not None else f"at least {low}"
        raise DomainValidationError(f"{name} must be {bounds}")


@dataclass(frozen=True, slots=True)
class RaceResult:
    time_seconds: float
    on: date
    source: ValueSource

    def __post_init__(self) -> None:
        if self.time_seconds <= 0:
            raise DomainValidationError("race time must be positive")
        if self.source is ValueSource.DERIVED:
            raise DomainValidationError("race results are declared or provider-reported")


@dataclass(frozen=True, slots=True)
class FtpValue:
    watts: int
    on: date
    measured: bool

    def __post_init__(self) -> None:
        _check(self.watts, "FTP", low=50, high=600)


@dataclass(frozen=True, slots=True, kw_only=True)
class RunningExperience:
    years_experience: float | None = None
    weekly_distance_km: float | None = None
    longest_distance_km: float | None = None
    recent_5k: RaceResult | None = None
    recent_10k: RaceResult | None = None
    recent_half_marathon: RaceResult | None = None
    recent_marathon: RaceResult | None = None

    def __post_init__(self) -> None:
        _check(self.years_experience, "years of experience", high=80)
        _check(self.weekly_distance_km, "weekly distance")
        _check(self.longest_distance_km, "longest distance")


@dataclass(frozen=True, slots=True, kw_only=True)
class CyclingExperience:
    years_experience: float | None = None
    weekly_distance_km: float | None = None
    usual_ride_distance_km: float | None = None
    longest_ride_km: float | None = None
    ftp: FtpValue | None = None
    power_meter_available: bool | None = None

    def __post_init__(self) -> None:
        _check(self.years_experience, "years of experience", high=80)
        _check(self.weekly_distance_km, "weekly distance")
        _check(self.usual_ride_distance_km, "usual ride distance")
        _check(self.longest_ride_km, "longest ride")


@dataclass(frozen=True, slots=True, kw_only=True)
class SwimmingExperience:
    years_experience: float | None = None
    weekly_distance_m: float | None = None
    typical_pace_100m_seconds: float | None = None
    longest_distance_m: float | None = None
    pool_length_m: float | None = None

    def __post_init__(self) -> None:
        _check(self.years_experience, "years of experience", high=80)
        _check(self.weekly_distance_m, "weekly distance")
        _check(self.longest_distance_m, "longest distance")
        if self.typical_pace_100m_seconds is not None and self.typical_pace_100m_seconds <= 0:
            raise DomainValidationError("pace must be positive")
        if self.pool_length_m is not None and not 0 < self.pool_length_m <= 100:
            raise DomainValidationError("pool length must be between 0 and 100 m")


@dataclass(frozen=True, slots=True)
class LoadReference:
    exercise_id: str
    load_kg: float
    reps: int
    on: date

    def __post_init__(self) -> None:
        if not EXERCISE_ID.match(self.exercise_id):
            raise DomainValidationError(f"invalid exercise id: {self.exercise_id!r}")
        _check(self.load_kg, "load", high=500)
        _check(self.reps, "reps", low=1, high=100)


@dataclass(frozen=True, slots=True, kw_only=True)
class StrengthExperience:
    years_experience: float | None = None
    sessions_per_week: float | None = None
    familiar_exercise_ids: tuple[str, ...] = ()
    approximate_loads: tuple[LoadReference, ...] = ()
    rpe_familiar: bool | None = None
    rir_familiar: bool | None = None

    def __post_init__(self) -> None:
        _check(self.years_experience, "years of experience", high=80)
        _check(self.sessions_per_week, "sessions per week", high=14)
        for exercise_id in self.familiar_exercise_ids:
            if not EXERCISE_ID.match(exercise_id):
                raise DomainValidationError(f"invalid exercise id: {exercise_id!r}")


class TennisProfile(StrEnum):
    MATCH = "MATCH"
    PRACTICE = "PRACTICE"
    MIXED = "MIXED"


@dataclass(frozen=True, slots=True, kw_only=True)
class TennisExperience:
    """``sessions_per_week=0`` is an explicit "no tennis" declaration."""

    sessions_per_week: float | None = None
    typical_duration_minutes: int | None = None
    typical_rpe: float | None = None
    profile: TennisProfile | None = None

    def __post_init__(self) -> None:
        _check(self.sessions_per_week, "sessions per week", high=14)
        _check(self.typical_duration_minutes, "typical duration", low=1, high=600)
        _check(self.typical_rpe, "typical RPE", high=10)


type SportExperience = (
    RunningExperience
    | CyclingExperience
    | SwimmingExperience
    | StrengthExperience
    | TennisExperience
)
