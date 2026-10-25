"""Activity local time and training day (FR-ACT-003, FR-ACT-004).

The training day is the local date at start in the activity's own time (its UTC offset, or
its timezone) and otherwise an estimate in the athlete's timezone
(spec/functional/activities.spec.md section 2).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from coach.domain.common.time import (
    TrainingDayBasis,
    ensure_utc,
    format_utc_offset,
    parse_timezone,
    parse_utc_offset,
)


@dataclass(frozen=True, slots=True)
class ActivityTime:
    start_time_utc: datetime
    original_local_time: datetime | None
    original_timezone: str | None
    utc_offset: str | None
    offset_from_timezone: bool
    training_day: date
    basis: TrainingDayBasis


def activity_time(
    start: datetime,
    *,
    utc_offset: str | None,
    timezone: str | None,
    athlete_timezone: ZoneInfo,
) -> ActivityTime:
    start_utc = ensure_utc(start)
    offset_text = utc_offset
    from_timezone = False
    if offset_text is None and timezone is not None:
        offset = start_utc.astimezone(parse_timezone(timezone)).utcoffset() or timedelta(0)
        offset_text = format_utc_offset(offset)
        from_timezone = True
    if offset_text is not None:
        local = (start_utc + parse_utc_offset(offset_text)).replace(tzinfo=None)
        return ActivityTime(
            start_time_utc=start_utc,
            original_local_time=local,
            original_timezone=timezone,
            utc_offset=format_utc_offset(parse_utc_offset(offset_text)),
            offset_from_timezone=from_timezone,
            training_day=local.date(),
            basis=TrainingDayBasis.ACTIVITY_TIMEZONE,
        )
    return ActivityTime(
        start_time_utc=start_utc,
        original_local_time=None,
        original_timezone=None,
        utc_offset=None,
        offset_from_timezone=False,
        training_day=start_utc.astimezone(athlete_timezone).date(),
        basis=TrainingDayBasis.ATHLETE_TIMEZONE_ESTIMATED,
    )
