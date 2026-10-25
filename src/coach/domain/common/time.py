"""Temporal model: UTC instants, original local context, training day.

Canonical timestamps are UTC. The training day is the local date at activity start in the
activity's own timezone, or in the athlete's timezone as an estimate when the activity's
timezone is unknown (spec/functional/activities.spec.md section 2).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import IntEnum, StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from coach.domain.common.errors import DomainValidationError


def ensure_utc(value: datetime) -> datetime:
    """Return ``value`` in UTC; reject naive datetimes."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError("naive datetime: timestamps must be timezone-aware")
    return value.astimezone(UTC)


def parse_timezone(name: str) -> ZoneInfo:
    """Resolve an IANA timezone name, rejecting unknown or malformed names."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise DomainValidationError(f"unknown IANA timezone: {name!r}") from exc


class Weekday(IntEnum):
    MONDAY = 1
    TUESDAY = 2
    WEDNESDAY = 3
    THURSDAY = 4
    FRIDAY = 5
    SATURDAY = 6
    SUNDAY = 7

    @classmethod
    def of(cls, day: date) -> Weekday:
        return cls(day.isoweekday())


class TrainingDayBasis(StrEnum):
    ACTIVITY_TIMEZONE = "ACTIVITY_TIMEZONE"
    ATHLETE_TIMEZONE_ESTIMATED = "ATHLETE_TIMEZONE_ESTIMATED"
    CONFIGURED = "CONFIGURED"


@dataclass(frozen=True, slots=True)
class TrainingDay:
    day: date
    basis: TrainingDayBasis


@dataclass(frozen=True, slots=True)
class LocalContext:
    """Original wall-clock time of an event, with its timezone and UTC offset."""

    local_time: datetime
    timezone: str
    utc_offset: str


def training_day(
    start: datetime, *, activity_timezone: ZoneInfo | None, athlete_timezone: ZoneInfo
) -> TrainingDay:
    start_utc = ensure_utc(start)
    if activity_timezone is not None:
        return TrainingDay(
            start_utc.astimezone(activity_timezone).date(), TrainingDayBasis.ACTIVITY_TIMEZONE
        )
    return TrainingDay(
        start_utc.astimezone(athlete_timezone).date(), TrainingDayBasis.ATHLETE_TIMEZONE_ESTIMATED
    )


def local_context(instant: datetime, zone: ZoneInfo) -> LocalContext:
    local = ensure_utc(instant).astimezone(zone)
    offset = local.utcoffset() or timedelta(0)
    return LocalContext(
        local_time=local.replace(tzinfo=None),
        timezone=zone.key,
        utc_offset=_format_offset(offset),
    )


def _format_offset(offset: timedelta) -> str:
    total_minutes = int(offset.total_seconds()) // 60
    sign = "-" if total_minutes < 0 else "+"
    hours, minutes = divmod(abs(total_minutes), 60)
    return f"{sign}{hours:02d}:{minutes:02d}"
