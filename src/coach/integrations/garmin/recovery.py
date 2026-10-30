"""Garmin daily recovery payloads: endpoints and normaliser ``garmin_daily`` 1.0.0 (P11).

Each recovery metric has one documented endpoint (garmin spec section 12) whose payload holds
that consent category only, so a payload is never stored without its consent:

- RESTING_HR (RECOVERY): ``/wellness-service/wellness/dailyHeartRate?date=``,
  ``restingHeartRate``;
- HRV_NIGHTLY (HRV): ``/hrv-service/hrv/daily/{from}/{to}``, ``hrvSummaries[].lastNightAvg``;
- SLEEP_DURATION (SLEEP): ``/sleep-service/sleep/dailySleepSummariesByDate``,
  ``[].sleepTimeSeconds``;
- BODY_BATTERY_CHARGED (PROVIDER_RECOVERY_METRICS):
  ``/wellness-service/wellness/bodyBattery/reports/daily``, ``[].charged``.

A payload that is not what the document describes is a protocol error (``DAILY_FORMAT``),
a resting heart rate payload whose ``calendarDate`` is not a ``ConnectDate`` naming the
requested day included; a value that is absent, not a number or outside the metric's range is
left out; a day stated twice with different values is left out. Nothing else of the payload is
read, and the payload itself is never stored: the sync keeps a minimised document of the
values read (garmin spec 12).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import date
from typing import Any, Final, NoReturn

from coach.application.providers import DailyValue, ProviderError, RawPayload
from coach.domain.integration.status import IntegrationReason
from coach.domain.recovery.daily import METRICS, RecoveryMetric

NORMALISER_VERSION: Final = "1.0.0"
RESTING_HR_PATH: Final = "/wellness-service/wellness/dailyHeartRate"
HRV_PATH: Final = "/hrv-service/hrv/daily/{start}/{end}"
SLEEP_PATH: Final = "/sleep-service/sleep/dailySleepSummariesByDate"
BODY_BATTERY_PATH: Final = "/wellness-service/wellness/bodyBattery/reports/daily"
CATEGORIES: Final = {
    RecoveryMetric.RESTING_HR: "GARMIN_DAILY_HEART_RATE",
    RecoveryMetric.HRV_NIGHTLY: "GARMIN_HRV",
    RecoveryMetric.SLEEP_DURATION: "GARMIN_SLEEP",
    RecoveryMetric.BODY_BATTERY_CHARGED: "GARMIN_BODY_BATTERY",
}
# Days per request: documented for sleep and Body Battery (31); one date for the heart rate;
# HRV states none, so a conservative 28 (garmin spec 12).
WINDOW_DAYS: Final = {
    RecoveryMetric.RESTING_HR: 1,
    RecoveryMetric.HRV_NIGHTLY: 28,
    RecoveryMetric.SLEEP_DURATION: 31,
    RecoveryMetric.BODY_BATTERY_CHARGED: 31,
}


def _format() -> NoReturn:
    raise ProviderError(IntegrationReason.PROVIDER_PROTOCOL_ERROR, "DAILY_FORMAT")


def _no_constants(name: str) -> NoReturn:
    raise ValueError(name)


def _json(data: bytes) -> Any:
    try:
        return json.loads(data.decode("utf-8"), parse_constant=_no_constants)
    except (UnicodeDecodeError, ValueError, RecursionError):
        _format()


def _day(value: Any) -> date:
    if not isinstance(value, str):
        _format()
    try:
        return date.fromisoformat(value)
    except ValueError:
        _format()


def _objects(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        _format()
    return value


def _number(metric: RecoveryMetric, value: Any) -> float | None:
    """A value in the metric's range; ``None`` (left out) for anything else."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    spec = METRICS[metric]
    try:
        number = float(value)
    except OverflowError:  # an integer too large for any measurement
        return None
    if metric is RecoveryMetric.SLEEP_DURATION and number == 0:
        return None  # a night without measured sleep (the watch not worn): no reading
    return number if spec.low <= number <= spec.high else None


def _resting(payload: RawPayload) -> list[DailyValue]:
    document = _json(payload.data)
    if not isinstance(document, dict) or payload.window is None:
        _format()
    requested = payload.window.start
    stated = document.get("calendarDate")
    # calendarDate, when present, is a ConnectDate object naming the requested day.
    if stated is not None and (
        not isinstance(stated, dict) or _day(stated.get("localDate")) != requested
    ):
        _format()
    value = _number(RecoveryMetric.RESTING_HR, document.get("restingHeartRate"))
    if value is None:
        return []
    return [DailyValue(requested, RecoveryMetric.RESTING_HR, value, "restingHeartRate")]


def _rows(
    metric: RecoveryMetric, items: list[dict[str, Any]], day_key: str, value_key: str, prefix: str
) -> list[DailyValue]:
    by_day: dict[date, DailyValue | None] = {}
    for index, item in enumerate(items):
        day = _day(item.get(day_key))
        value = _number(metric, item.get(value_key))
        if value is None:
            continue
        found = DailyValue(day, metric, value, f"{prefix}[{index}].{value_key}")
        if day in by_day:
            earlier = by_day[day]
            if earlier is None or earlier.value != value:
                by_day[day] = None  # stated twice, differently: left out
            continue
        by_day[day] = found
    return [value for value in by_day.values() if value is not None]


def _hrv(payload: RawPayload) -> list[DailyValue]:
    document = _json(payload.data)
    if not isinstance(document, dict):
        _format()
    items = _objects(document.get("hrvSummaries", []))
    return _rows(RecoveryMetric.HRV_NIGHTLY, items, "calendarDate", "lastNightAvg", "hrvSummaries")


def _sleep(payload: RawPayload) -> list[DailyValue]:
    items = _objects(_json(payload.data))
    return _rows(RecoveryMetric.SLEEP_DURATION, items, "calendarDate", "sleepTimeSeconds", "")


def _body_battery(payload: RawPayload) -> list[DailyValue]:
    items = _objects(_json(payload.data))
    return _rows(RecoveryMetric.BODY_BATTERY_CHARGED, items, "date", "charged", "")


_DECODERS: Final[dict[RecoveryMetric, Callable[[RawPayload], list[DailyValue]]]] = {
    RecoveryMetric.RESTING_HR: _resting,
    RecoveryMetric.HRV_NIGHTLY: _hrv,
    RecoveryMetric.SLEEP_DURATION: _sleep,
    RecoveryMetric.BODY_BATTERY_CHARGED: _body_battery,
}


class GarminDailyRecords:
    """``DailyRecords`` for Garmin payloads (normaliser ``garmin_daily`` 1.0.0)."""

    def decode(self, metric: RecoveryMetric, payload: RawPayload) -> Sequence[DailyValue]:
        if payload.category != CATEGORIES[metric]:
            _format()
        found = _DECODERS[metric](payload)
        window = payload.window
        return [
            value for value in found if window is None or window.start <= value.day <= window.end
        ]
