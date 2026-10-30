"""Synthetic Garmin Personal API payloads, shaped by the Swagger schemas (garmin spec 1, 10).

``ActivitySummaryDTO`` and ``SummarizedActivityDTO`` field names and units come from the
Swagger document (2026-10-26). Values are invented; no real athlete data.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final

from file_samples import fit_file, fit_session, zip_file

ACTIVITY_ID: Final = 21004587311
START_GMT: Final = "2026-10-20T21:00:00.000"
START_LOCAL: Final = "2026-10-20T18:00:00.000"
SYNTHETIC_KEY: Final = "synthetic-garmin-key-7f3a91c2d4e5"  # pragma: allowlist secret


def activity_details(
    activity_id: int = ACTIVITY_ID,
    *,
    type_key: str = "running",
    summary: dict[str, Any] | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """An ``ActivitySummaryDTO`` of a 10-minute run (matches ``file_samples.fit_file()``)."""
    document: dict[str, Any] = {
        "activityId": activity_id,
        "activityName": "Evening run",
        "description": None,
        "activityTypeDTO": {"typeId": 1, "typeKey": type_key, "parentTypeId": 17},
        "eventTypeDTO": {"typeId": 9, "typeKey": "uncategorized"},
        "timeZoneUnitDTO": {
            "unitId": 1,
            "unitKey": "America/Argentina/Buenos_Aires",
            "timeZone": "America/Argentina/Buenos_Aires",
        },
        "userProfileId": 99999,
        "summaryDTO": {
            "startTimeGMT": START_GMT,
            "startTimeLocal": START_LOCAL,
            "elapsedDuration": 600.0,
            "movingDuration": 590.0,
            "duration": 600.0,
            "distance": 1800.0,
            "elevationGain": 40.0,
            "elevationLoss": 38.0,
            "averageHR": 152.4,
            "maxHR": 154.0,
            "averageSpeed": 3.0,
            "maxSpeed": 3.4,
            "averageRunCadence": 170.0,
            "maxRunCadence": 180.0,
            "strideLength": 105.0,
            "verticalOscillation": 8.2,
            "groundContactTime": 250.0,
            "trainingEffect": 3.1,
            "anaerobicTrainingEffect": 0.4,
            "activityTrainingLoad": 61.5,
            "calories": 140.0,
            "avgStress": 30.0,
            "differenceBodyBattery": -5,
            "directWorkoutRpe": 30,
        },
    }
    if summary is not None:
        document["summaryDTO"].update(summary)
    document.update(fields)
    return document


def details_bytes(activity_id: int = ACTIVITY_ID, **options: Any) -> bytes:
    return json.dumps(activity_details(activity_id, **options)).encode()


def list_item(activity_id: int, *, start_gmt: str = "2026-10-20 21:00:00") -> dict[str, Any]:
    """A ``SummarizedActivityDTO`` with the fields a list page carries."""
    return {
        "activityId": activity_id,
        "activityName": f"Activity {activity_id}",
        "activityType": {"typeId": 1, "typeKey": "running", "parentTypeId": 17},
        "startTimeGMT": start_gmt,
        "startTimeLocal": start_gmt,
        "beginTimestamp": 1792530000000,
        "distance": 1800.0,
        "ownerFullName": "Synthetic Athlete",
        "comments": [],
    }


def list_page(*activity_ids: int) -> bytes:
    return json.dumps([list_item(activity_id) for activity_id in activity_ids]).encode()


def download(activity_id: int = ACTIVITY_ID) -> bytes:
    """The original-file ZIP of the synthetic run."""
    return zip_file({f"{activity_id}_ACTIVITY.fit": fit_file()})


def jwt(*, expires: datetime, subject: str = "synthetic") -> str:
    """An unsigned three-part token whose payload has ``exp`` (the broker reads only that)."""

    def part(value: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    claims = {"sub": subject, "exp": int(expires.timestamp())}
    return f"{part({'alg': 'RS256', 'typ': 'JWT'})}.{part(claims)}.c2lnbmF0dXJl"


def token_for(now: datetime, *, hours: float = 24) -> str:
    return jwt(expires=now.astimezone(UTC) + timedelta(hours=hours))


def activity_on(day: date, activity_id: int, *, hour: int = 7) -> tuple[bytes, bytes]:
    """Details and original-file ZIP of one synthetic run starting on ``day`` at ``hour`` UTC."""
    start = datetime(day.year, day.month, day.day, hour, tzinfo=UTC)
    local = start - timedelta(hours=3)
    details = details_bytes(
        activity_id,
        summary={
            "startTimeGMT": start.replace(tzinfo=None).isoformat(timespec="milliseconds"),
            "startTimeLocal": local.replace(tzinfo=None).isoformat(timespec="milliseconds"),
        },
    )
    fit = fit_file([fit_session(start=start)])
    return details, zip_file({f"{activity_id}_ACTIVITY.fit": fit})


# --------------------------------------------------------------------------- daily recovery (P11)
# Shapes from the Swagger schemas DailyHeartRateDTO, HrvDailySummaryDTO, DailySleepDTO and
# BodyBatteryDailyReportDTO (garmin spec 12); synthetic values only.


def resting_hr_payload(day: date, resting: int | None) -> bytes:
    document: dict[str, Any] = {
        "calendarDate": {"date": f"{day.isoformat()}T00:00:00.0", "localDate": day.isoformat()},
        "restingHeartRate": resting,
        "lastSevenDaysAvgRestingHeartRate": resting,
        "maxHeartRate": 150,
        "minHeartRate": 45,
        "heartRateValueDescriptors": [
            {"index": 0, "key": "timestamp"},
            {"index": 1, "key": "heartrate"},
        ],
        "heartRateValues": [[1, 60], [2, 62]],
    }
    return json.dumps(document).encode()


def hrv_payload(values: Mapping[date, int | None]) -> bytes:
    summaries = [
        {
            "calendarDate": day.isoformat(),
            "lastNightAvg": value,
            "lastNight5MinHigh": None if value is None else value + 20,
            "weeklyAvg": value,
            "status": "BALANCED",
            "feedbackPhrase": "HRV_BALANCED_1",
        }
        for day, value in sorted(values.items())
    ]
    return json.dumps({"hrvSummaries": summaries, "userProfilePk": 1}).encode()


def sleep_payload(values: Mapping[date, int | None]) -> bytes:
    items = [
        {
            "calendarDate": day.isoformat(),
            "sleepTimeSeconds": value,
            "deepSleepSeconds": None if value is None else value // 5,
            "userNote": "free text that is never read",
        }
        for day, value in sorted(values.items())
    ]
    return json.dumps(items).encode()


def body_battery_payload(values: Mapping[date, int | None]) -> bytes:
    items: list[dict[str, Any]] = [
        {"date": day.isoformat(), "charged": value, "drained": 40, "bodyBatteryValuesArray": []}
        for day, value in sorted(values.items())
    ]
    return json.dumps(items).encode()
