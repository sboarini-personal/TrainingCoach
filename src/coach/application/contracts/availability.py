"""Availability <-> availability contract (urn:coach:schema:availability:1)."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from coach.application.contracts._json import (
    SCHEMA_VERSION,
    Document,
    Json,
    clock,
    parse_clock,
    sorted_values,
)
from coach.domain.availability.availability import (
    Availability,
    AvailabilityException,
    DayDefaults,
    TimeWindow,
    WeeklyAvailability,
)
from coach.domain.common.sport import Sport
from coach.domain.common.time import Weekday


def _sports(sports: frozenset[Sport] | None) -> list[str] | None:
    return None if sports is None else sorted_values(sports)


def _parse_sports(values: list[str] | None) -> frozenset[Sport] | None:
    return None if values is None else frozenset(Sport(value) for value in values)


def _windows(windows: tuple[TimeWindow, ...]) -> list[Json]:
    return [{"start": clock(window.start), "end": clock(window.end)} for window in windows]


def _exception(exception: AvailabilityException) -> Json:
    return {
        "date": exception.day.isoformat(),
        "available": exception.available,
        "maximumDurationMinutes": exception.maximum_duration_minutes,
        "allowedSports": _sports(exception.allowed_sports),
        "reason": exception.reason,
    }


def _parse_exception(document: Document) -> AvailabilityException:
    return AvailabilityException(
        day=date.fromisoformat(document["date"]),
        available=document["available"],
        maximum_duration_minutes=document["maximumDurationMinutes"],
        allowed_sports=_parse_sports(document["allowedSports"]),
        reason=document["reason"],
    )


def availability_to_contract(availability: Availability) -> Json:
    recurring = availability.recurring
    defaults = availability.defaults
    return {
        "schemaVersion": SCHEMA_VERSION,
        "athleteId": str(availability.athlete_id),
        "version": availability.version,
        "recurring": None
        if recurring is None
        else {
            "daysPerWeek": recurring.days_per_week,
            "maximumHoursPerWeek": recurring.maximum_hours_per_week,
            "preferredRestDays": [day.name for day in sorted(recurring.preferred_rest_days)],
            "dailyWindows": {
                day.name: _windows(windows)
                for day, windows in sorted(recurring.daily_windows.items())
            },
            "doubleSessionsAllowed": recurring.double_sessions_allowed,
            "restrictions": recurring.restrictions,
        },
        "defaults": None
        if defaults is None
        else {
            "maximumDurationMinutes": defaults.maximum_duration_minutes,
            "allowedSports": _sports(defaults.allowed_sports),
        },
        "exceptions": [
            _exception(availability.exceptions[day]) for day in sorted(availability.exceptions)
        ],
    }


def availability_from_contract(document: Document) -> Availability:
    recurring = document["recurring"]
    defaults = document["defaults"]
    exceptions = (_parse_exception(item) for item in document["exceptions"])
    return Availability(
        athlete_id=UUID(document["athleteId"]),
        version=document["version"],
        recurring=None
        if recurring is None
        else WeeklyAvailability(
            days_per_week=recurring["daysPerWeek"],
            maximum_hours_per_week=recurring["maximumHoursPerWeek"],
            preferred_rest_days=frozenset(Weekday[day] for day in recurring["preferredRestDays"]),
            daily_windows={
                Weekday[day]: tuple(
                    TimeWindow(parse_clock(window["start"]), parse_clock(window["end"]))
                    for window in windows
                )
                for day, windows in recurring["dailyWindows"].items()
            },
            double_sessions_allowed=recurring["doubleSessionsAllowed"],
            restrictions=recurring["restrictions"],
        ),
        defaults=None
        if defaults is None
        else DayDefaults(
            maximum_duration_minutes=defaults["maximumDurationMinutes"],
            allowed_sports=_parse_sports(defaults["allowedSports"]),
        ),
        exceptions={exception.day: exception for exception in exceptions},
    )
