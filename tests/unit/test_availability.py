from datetime import date, time, timedelta
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from coach.domain.availability.availability import (
    Availability,
    AvailabilityBasis,
    AvailabilityException,
    DayDefaults,
    TimeWindow,
    WeeklyAvailability,
)
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.sport import Sport
from coach.domain.common.time import Weekday

ATHLETE = UUID("0193b2a0-0000-7000-8000-000000000001")
MONDAY = date(2026, 11, 2)
TUESDAY = date(2026, 11, 3)
THURSDAY = date(2026, 11, 5)
NEXT_THURSDAY = date(2026, 11, 12)
SATURDAY = date(2026, 11, 7)


def weekly() -> WeeklyAvailability:
    return WeeklyAvailability(
        days_per_week=5,
        maximum_hours_per_week=7.5,
        preferred_rest_days=frozenset({Weekday.MONDAY}),
        daily_windows={
            Weekday.THURSDAY: (TimeWindow(time(7, 0), time(8, 30)),),
            Weekday.SATURDAY: (
                TimeWindow(time(8, 0), time(9, 0)),
                TimeWindow(time(17, 0), time(18, 30)),
            ),
        },
        double_sessions_allowed=False,
    )


def swim_only_exception(day: date = THURSDAY) -> AvailabilityException:
    return AvailabilityException(
        day=day,
        available=True,
        maximum_duration_minutes=30,
        allowed_sports=frozenset({Sport.SWIMMING}),
        reason="Work trip, hotel pool only",
    )


@pytest.mark.req("FR-PRF-005")
def test_exception_overrides_recurring_availability() -> None:
    availability = Availability.new(ATHLETE, recurring=weekly()).with_exception(
        swim_only_exception()
    )

    first = availability.resolve(THURSDAY)
    assert first.basis is AvailabilityBasis.EXCEPTION
    assert first.maximum_duration_minutes == 30
    assert first.allowed_sports == frozenset({Sport.SWIMMING})

    second = availability.resolve(NEXT_THURSDAY)
    assert second.basis is AvailabilityBasis.RECURRING
    assert second.maximum_duration_minutes == 90
    assert second.allowed_sports is None


@pytest.mark.req("FR-PRF-005")
def test_recurring_rest_day_is_unavailable() -> None:
    monday = Availability.new(ATHLETE, recurring=weekly()).resolve(MONDAY)
    assert monday.basis is AvailabilityBasis.RECURRING
    assert not monday.available


@pytest.mark.req("FR-PRF-005")
def test_defaults_apply_when_recurring_does_not_cover_the_day() -> None:
    availability = Availability.new(
        ATHLETE, recurring=weekly(), defaults=DayDefaults(maximum_duration_minutes=45)
    )
    tuesday = availability.resolve(TUESDAY)
    assert tuesday.basis is AvailabilityBasis.DEFAULT
    assert tuesday.maximum_duration_minutes == 45


@pytest.mark.req("FR-PRF-005")
def test_day_without_any_availability_is_not_plannable() -> None:
    tuesday = Availability.new(ATHLETE, recurring=weekly()).resolve(TUESDAY)
    assert tuesday.basis is AvailabilityBasis.UNDEFINED
    assert not tuesday.plannable
    assert not tuesday.available


@pytest.mark.req("FR-PRF-004")
def test_recurring_duration_is_the_sum_of_the_day_windows() -> None:
    assert (
        Availability.new(ATHLETE, recurring=weekly()).resolve(SATURDAY).maximum_duration_minutes
        == 150
    )


@pytest.mark.req("FR-PRF-005")
def test_at_most_one_exception_per_date() -> None:
    blocked = AvailabilityException(day=THURSDAY, available=False)
    availability = (
        Availability.new(ATHLETE, recurring=weekly())
        .with_exception(swim_only_exception())
        .with_exception(blocked)
    )
    assert len(availability.exceptions) == 1
    assert not availability.resolve(THURSDAY).available
    assert (
        availability.without_exception(THURSDAY).resolve(THURSDAY).basis
        is AvailabilityBasis.RECURRING
    )


@pytest.mark.req("FR-PRF-005")
def test_available_exception_needs_a_maximum_duration() -> None:
    with pytest.raises(DomainValidationError):
        AvailabilityException(day=THURSDAY, available=True)


@pytest.mark.req("FR-PRF-004")
def test_time_windows_are_ordered_and_do_not_overlap() -> None:
    with pytest.raises(DomainValidationError):
        TimeWindow(time(9, 0), time(8, 0))
    with pytest.raises(DomainValidationError):
        WeeklyAvailability(
            days_per_week=3,
            maximum_hours_per_week=5,
            daily_windows={
                Weekday.TUESDAY: (
                    TimeWindow(time(7, 0), time(8, 0)),
                    TimeWindow(time(7, 30), time(9, 0)),
                )
            },
        )


@pytest.mark.req("FR-PRF-004")
def test_rest_day_cannot_also_have_windows() -> None:
    with pytest.raises(DomainValidationError):
        WeeklyAvailability(
            days_per_week=3,
            maximum_hours_per_week=5,
            preferred_rest_days=frozenset({Weekday.TUESDAY}),
            daily_windows={Weekday.TUESDAY: (TimeWindow(time(7, 0), time(8, 0)),)},
        )


@pytest.mark.req("FR-PRF-005")
@given(
    offset=st.integers(min_value=0, max_value=3650), minutes=st.integers(min_value=1, max_value=600)
)
def test_an_exception_always_takes_precedence(offset: int, minutes: int) -> None:
    day = date(2026, 1, 1) + timedelta(days=offset)
    availability = Availability.new(
        ATHLETE, recurring=weekly(), defaults=DayDefaults(maximum_duration_minutes=60)
    ).with_exception(
        AvailabilityException(day=day, available=True, maximum_duration_minutes=minutes)
    )
    resolved = availability.resolve(day)
    assert resolved.basis is AvailabilityBasis.EXCEPTION
    assert resolved.maximum_duration_minutes == minutes
