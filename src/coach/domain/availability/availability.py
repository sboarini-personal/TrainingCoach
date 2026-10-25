"""Availability aggregate and resolution (FR-PRF-004, FR-PRF-005).

Effective availability for a date: the date's exception, else the recurring weekly pattern
for its weekday (a preferred rest day is unavailable), else the athlete's defaults, else
UNDEFINED (not plannable; raises the DATA_INCOMPLETE condition upstream).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time
from enum import StrEnum
from itertools import pairwise
from types import MappingProxyType
from uuid import UUID

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.sport import Sport
from coach.domain.common.text import clean_optional_text
from coach.domain.common.time import Weekday

MINUTES_PER_DAY = 24 * 60


def _minutes(moment: time) -> int:
    return moment.hour * 60 + moment.minute


@dataclass(frozen=True, slots=True)
class TimeWindow:
    start: time
    end: time

    def __post_init__(self) -> None:
        if self.start.tzinfo is not None or self.end.tzinfo is not None:
            raise DomainValidationError("time windows use local wall-clock times")
        if self.start >= self.end:
            raise DomainValidationError("a time window must end after it starts")

    @property
    def minutes(self) -> int:
        return _minutes(self.end) - _minutes(self.start)


def _check_sports(sports: frozenset[Sport] | None) -> None:
    if sports is not None and not sports:
        raise DomainValidationError("allowed sports must not be empty; use None for any sport")


@dataclass(frozen=True, slots=True, kw_only=True)
class WeeklyAvailability:
    days_per_week: int
    maximum_hours_per_week: float
    preferred_rest_days: frozenset[Weekday] = field(default_factory=frozenset)
    daily_windows: Mapping[Weekday, tuple[TimeWindow, ...]] = field(default_factory=dict)
    double_sessions_allowed: bool = False
    restrictions: str | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.days_per_week <= 7:
            raise DomainValidationError("days per week must be between 0 and 7")
        if not 0 <= self.maximum_hours_per_week <= 80:
            raise DomainValidationError("maximum hours per week must be between 0 and 80")
        if self.preferred_rest_days & self.daily_windows.keys():
            raise DomainValidationError("a preferred rest day cannot also have time windows")
        windows: dict[Weekday, tuple[TimeWindow, ...]] = {}
        for weekday, day_windows in self.daily_windows.items():
            ordered = tuple(sorted(day_windows, key=lambda window: window.start))
            if not ordered:
                raise DomainValidationError("a day listed in daily windows needs a window")
            for earlier, later in pairwise(ordered):
                if later.start < earlier.end:
                    raise DomainValidationError(f"overlapping time windows on {weekday.name}")
            windows[weekday] = ordered
        object.__setattr__(self, "daily_windows", MappingProxyType(windows))
        object.__setattr__(
            self,
            "restrictions",
            clean_optional_text(self.restrictions, max_length=500, field="restrictions"),
        )


@dataclass(frozen=True, slots=True)
class DayDefaults:
    maximum_duration_minutes: int
    allowed_sports: frozenset[Sport] | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.maximum_duration_minutes <= MINUTES_PER_DAY:
            raise DomainValidationError("maximum duration must be 1 to 1440 minutes")
        _check_sports(self.allowed_sports)


@dataclass(frozen=True, slots=True, kw_only=True)
class AvailabilityException:
    day: date
    available: bool
    maximum_duration_minutes: int | None = None
    allowed_sports: frozenset[Sport] | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.day, datetime):
            raise DomainValidationError("an exception applies to a calendar date")
        if self.available:
            duration = self.maximum_duration_minutes
            if duration is None or not 1 <= duration <= MINUTES_PER_DAY:
                raise DomainValidationError(
                    "an available day needs a maximum duration of 1-1440 min"
                )
        elif self.maximum_duration_minutes is not None:
            raise DomainValidationError("an unavailable day has no maximum duration")
        _check_sports(self.allowed_sports)
        object.__setattr__(
            self, "reason", clean_optional_text(self.reason, max_length=200, field="reason")
        )


class AvailabilityBasis(StrEnum):
    EXCEPTION = "EXCEPTION"
    RECURRING = "RECURRING"
    DEFAULT = "DEFAULT"
    UNDEFINED = "UNDEFINED"


@dataclass(frozen=True, slots=True)
class EffectiveAvailability:
    day: date
    available: bool
    maximum_duration_minutes: int
    allowed_sports: frozenset[Sport] | None
    windows: tuple[TimeWindow, ...]
    basis: AvailabilityBasis

    @property
    def plannable(self) -> bool:
        return self.basis is not AvailabilityBasis.UNDEFINED


@dataclass(frozen=True, slots=True, kw_only=True)
class Availability:
    athlete_id: UUID
    version: int = 1
    recurring: WeeklyAvailability | None = None
    defaults: DayDefaults | None = None
    exceptions: Mapping[date, AvailabilityException] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if any(day != exception.day for day, exception in self.exceptions.items()):
            raise DomainValidationError("exceptions must be keyed by their own date")
        object.__setattr__(self, "exceptions", MappingProxyType(dict(self.exceptions)))

    @classmethod
    def new(
        cls,
        athlete_id: UUID,
        *,
        recurring: WeeklyAvailability | None = None,
        defaults: DayDefaults | None = None,
    ) -> Availability:
        return cls(athlete_id=athlete_id, recurring=recurring, defaults=defaults)

    @property
    def entity_id(self) -> UUID:
        return self.athlete_id

    def with_version(self, version: int) -> Availability:
        return replace(self, version=version)

    def with_exception(self, exception: AvailabilityException) -> Availability:
        return replace(self, exceptions={**self.exceptions, exception.day: exception})

    def without_exception(self, day: date) -> Availability:
        return replace(
            self, exceptions={key: value for key, value in self.exceptions.items() if key != day}
        )

    def resolve(self, day: date) -> EffectiveAvailability:
        exception = self.exceptions.get(day)
        if exception is not None:
            return EffectiveAvailability(
                day=day,
                available=exception.available,
                maximum_duration_minutes=exception.maximum_duration_minutes or 0,
                allowed_sports=exception.allowed_sports,
                windows=(),
                basis=AvailabilityBasis.EXCEPTION,
            )
        weekday = Weekday.of(day)
        if self.recurring is not None:
            if weekday in self.recurring.preferred_rest_days:
                return EffectiveAvailability(day, False, 0, None, (), AvailabilityBasis.RECURRING)
            windows = self.recurring.daily_windows.get(weekday)
            if windows:
                minutes = sum(window.minutes for window in windows)
                return EffectiveAvailability(
                    day, True, minutes, None, windows, AvailabilityBasis.RECURRING
                )
        if self.defaults is not None:
            return EffectiveAvailability(
                day=day,
                available=True,
                maximum_duration_minutes=self.defaults.maximum_duration_minutes,
                allowed_sports=self.defaults.allowed_sports,
                windows=(),
                basis=AvailabilityBasis.DEFAULT,
            )
        return EffectiveAvailability(day, False, 0, None, (), AvailabilityBasis.UNDEFINED)
