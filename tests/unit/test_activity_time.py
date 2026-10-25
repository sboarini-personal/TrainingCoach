"""AT-TIM-001: UTC storage, original local time and training day of activities."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st

from coach.domain.activity.timing import activity_time
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.quality import UncertaintyReason
from coach.domain.common.time import TrainingDayBasis, format_utc_offset, parse_utc_offset
from samples import BUENOS_AIRES, sample_activity, sample_observation

MADRID = ZoneInfo("Europe/Madrid")
TOKYO = ZoneInfo("Asia/Tokyo")


@pytest.mark.req("FR-ACT-003", "FR-ACT-004")
@pytest.mark.acceptance("AT-TIM-001")
def test_activity_crossing_local_midnight_keeps_its_local_start_day() -> None:
    start = datetime(2026, 10, 21, 2, 30, tzinfo=UTC)  # 23:30 at UTC-3, ends after midnight
    time = activity_time(start, utc_offset="-03:00", timezone=None, athlete_timezone=TOKYO)
    assert time.start_time_utc == start
    assert time.original_local_time == datetime.fromisoformat("2026-10-20T23:30:00")
    assert time.utc_offset == "-03:00"
    assert time.original_timezone is None
    assert time.training_day == date(2026, 10, 20)
    assert time.basis is TrainingDayBasis.ACTIVITY_TIMEZONE


@pytest.mark.req("FR-ACT-004")
@pytest.mark.acceptance("AT-TIM-001")
def test_travel_uses_the_activity_timezone_not_the_athletes() -> None:
    start = datetime(2026, 10, 20, 23, 30, tzinfo=UTC)  # 01:30 next day in Madrid (CEST)
    time = activity_time(
        start, utc_offset=None, timezone="Europe/Madrid", athlete_timezone=BUENOS_AIRES
    )
    assert time.training_day == date(2026, 10, 21)
    assert time.original_timezone == "Europe/Madrid"
    assert time.utc_offset == "+02:00"
    assert time.offset_from_timezone
    assert time.original_local_time == datetime.fromisoformat("2026-10-21T01:30:00")
    assert time.basis is TrainingDayBasis.ACTIVITY_TIMEZONE


@pytest.mark.req("FR-ACT-003", "FR-ACT-004", "FR-ACT-010")
@pytest.mark.acceptance("AT-TIM-001")
def test_gpx_without_timezone_estimates_the_day_in_the_athletes_timezone() -> None:
    start = datetime(2026, 10, 21, 2, 30, tzinfo=UTC)
    time = activity_time(start, utc_offset=None, timezone=None, athlete_timezone=BUENOS_AIRES)
    assert time.training_day == date(2026, 10, 20)
    assert time.basis is TrainingDayBasis.ATHLETE_TIMEZONE_ESTIMATED
    assert (time.original_local_time, time.original_timezone, time.utc_offset) == (None,) * 3

    gpx = sample_observation(updates={"timing.startTimeUtc": start}, without=("timing.utcOffset",))
    activity = sample_activity(gpx)
    assert activity.values["timing.trainingDayBasis"] is (
        TrainingDayBasis.ATHLETE_TIMEZONE_ESTIMATED
    )
    assert activity.values["timing.originalLocalTime"] is None
    assert UncertaintyReason.ESTIMATED_VALUE in activity.quality.uncertainty_reasons


@pytest.mark.req("FR-ACT-003")
def test_source_offset_wins_over_the_timezone_and_both_are_kept() -> None:
    start = datetime(2026, 1, 15, 8, 0, tzinfo=UTC)
    time = activity_time(
        start, utc_offset="+01:00", timezone="Europe/Madrid", athlete_timezone=TOKYO
    )
    assert (time.utc_offset, time.original_timezone) == ("+01:00", "Europe/Madrid")
    assert not time.offset_from_timezone


@pytest.mark.req("FR-ACT-003")
def test_offset_from_timezone_follows_daylight_saving_at_the_start_instant() -> None:
    winter = activity_time(
        datetime(2026, 1, 15, 8, 0, tzinfo=UTC),
        utc_offset=None,
        timezone="Europe/Madrid",
        athlete_timezone=TOKYO,
    )
    summer = activity_time(
        datetime(2026, 7, 15, 8, 0, tzinfo=UTC),
        utc_offset=None,
        timezone="Europe/Madrid",
        athlete_timezone=TOKYO,
    )
    assert (winter.utc_offset, summer.utc_offset) == ("+01:00", "+02:00")


@pytest.mark.req("FR-ACT-003")
def test_naive_start_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        activity_time(
            datetime(2026, 10, 20, 21, 0),  # noqa: DTZ001 - the point of the test
            utc_offset=None,
            timezone=None,
            athlete_timezone=BUENOS_AIRES,
        )


@pytest.mark.req("FR-ACT-003")
@pytest.mark.parametrize("text", ["+14:01", "-15:00", "0300", "+3:00", "+03:60", "UTC", ""])
def test_invalid_offsets_are_rejected(text: str) -> None:
    with pytest.raises(DomainValidationError):
        parse_utc_offset(text)


@pytest.mark.req("FR-ACT-003")
def test_offsets_must_be_whole_minutes_within_fourteen_hours() -> None:
    with pytest.raises(DomainValidationError):
        format_utc_offset(timedelta(hours=14, minutes=1))
    with pytest.raises(DomainValidationError):
        format_utc_offset(timedelta(minutes=30, seconds=1))
    assert format_utc_offset(timedelta(hours=-9, minutes=-30)) == "-09:30"


_OFFSETS = st.integers(min_value=-14 * 60, max_value=14 * 60).map(lambda m: timedelta(minutes=m))
_STARTS = st.datetimes(
    min_value=datetime.fromisoformat("2000-01-01T00:00:00"),
    max_value=datetime.fromisoformat("2100-01-01T00:00:00"),
    timezones=st.just(UTC),
)


@pytest.mark.req("FR-ACT-003", "FR-ACT-004")
@pytest.mark.acceptance("AT-TIM-001")
@given(start=_STARTS, offset=_OFFSETS)
def test_training_day_is_the_local_date_at_start_for_any_offset(
    start: datetime, offset: timedelta
) -> None:
    text = format_utc_offset(offset)
    assert parse_utc_offset(text) == offset
    time = activity_time(start, utc_offset=text, timezone=None, athlete_timezone=TOKYO)
    local = (start + offset).replace(tzinfo=None)
    assert time.original_local_time == local
    assert time.training_day == local.date()
    assert time.start_time_utc == start


@pytest.mark.req("FR-ACT-004")
@given(start=_STARTS)
def test_training_day_in_a_timezone_matches_the_wall_clock_date(start: datetime) -> None:
    time = activity_time(start, utc_offset=None, timezone="Europe/Madrid", athlete_timezone=TOKYO)
    assert time.training_day == start.astimezone(MADRID).date()
