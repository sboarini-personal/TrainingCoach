from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.time import (
    TrainingDayBasis,
    Weekday,
    ensure_utc,
    local_context,
    parse_timezone,
    training_day,
)

BUENOS_AIRES = ZoneInfo("America/Argentina/Buenos_Aires")
MADRID = ZoneInfo("Europe/Madrid")


@pytest.mark.req("FR-ACT-003")
def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        ensure_utc(datetime(2026, 10, 20, 21, 0))  # noqa: DTZ001


@pytest.mark.req("FR-ACT-003")
def test_aware_datetime_is_normalised_to_utc() -> None:
    normalised = ensure_utc(datetime(2026, 10, 20, 18, 0, tzinfo=BUENOS_AIRES))
    assert normalised == datetime(2026, 10, 20, 21, 0, tzinfo=UTC)
    assert normalised.tzinfo is UTC


@pytest.mark.req("FR-ACT-003")
def test_local_context_preserves_original_time_zone_and_offset() -> None:
    context = local_context(datetime(2026, 10, 20, 21, 0, tzinfo=UTC), BUENOS_AIRES)
    assert context.local_time == datetime(2026, 10, 20, 18, 0)  # noqa: DTZ001
    assert context.timezone == "America/Argentina/Buenos_Aires"
    assert context.utc_offset == "-03:00"


@pytest.mark.req("FR-ACT-004")
def test_activity_crossing_local_midnight_uses_local_start_date() -> None:
    start = datetime(2026, 10, 21, 2, 30, tzinfo=UTC)  # 23:30 on the 20th at UTC-3
    day = training_day(start, activity_timezone=BUENOS_AIRES, athlete_timezone=BUENOS_AIRES)
    assert day.day == date(2026, 10, 20)
    assert day.basis is TrainingDayBasis.ACTIVITY_TIMEZONE


@pytest.mark.req("FR-ACT-004")
def test_activity_timezone_wins_when_travelling() -> None:
    start = datetime(2026, 10, 20, 23, 30, tzinfo=UTC)  # 01:30 on the 21st in Madrid
    day = training_day(start, activity_timezone=MADRID, athlete_timezone=BUENOS_AIRES)
    assert day.day == date(2026, 10, 21)
    assert day.basis is TrainingDayBasis.ACTIVITY_TIMEZONE


@pytest.mark.req("FR-ACT-004", "FR-ACT-010")
def test_unknown_activity_timezone_uses_athlete_timezone_as_estimate() -> None:
    start = datetime(2026, 10, 21, 2, 30, tzinfo=UTC)
    day = training_day(start, activity_timezone=None, athlete_timezone=BUENOS_AIRES)
    assert day.day == date(2026, 10, 20)
    assert day.basis is TrainingDayBasis.ATHLETE_TIMEZONE_ESTIMATED


@pytest.mark.req("FR-ACT-004")
@given(
    instant=st.datetimes(
        min_value=datetime(2000, 1, 1),  # noqa: DTZ001 - hypothesis bounds must be naive
        max_value=datetime(2100, 1, 1),  # noqa: DTZ001
        timezones=st.just(UTC),
    ),
    zone_name=st.sampled_from(
        ["America/Argentina/Buenos_Aires", "Europe/Madrid", "Asia/Tokyo", "Pacific/Auckland", "UTC"]
    ),
)
def test_training_day_is_the_local_calendar_date_of_the_start(
    instant: datetime, zone_name: str
) -> None:
    zone = ZoneInfo(zone_name)
    day = training_day(instant, activity_timezone=zone, athlete_timezone=BUENOS_AIRES)
    assert day.day == instant.astimezone(zone).date()


def test_unknown_timezone_name_is_rejected() -> None:
    assert parse_timezone("America/Argentina/Buenos_Aires") == BUENOS_AIRES
    for bad in ("Mars/Olympus_Mons", "../etc/passwd", ""):
        with pytest.raises(DomainValidationError):
            parse_timezone(bad)


def test_weekday_of_date() -> None:
    assert Weekday.of(date(2026, 10, 26)) is Weekday.MONDAY
    assert Weekday.of(date(2026, 11, 1)) is Weekday.SUNDAY
