"""Garmin activity record normalisation rules 1.0.0 (garmin spec section 10).

Maps one ``ActivitySummaryDTO`` (activity details endpoint) to canonical field values, with
the units the Swagger document states, converting in code. Only documented activity type
keys are mapped (G-6); anything the record does not state stays absent. A record that does
not match the documented shape is rejected, never repaired.

The JSON is parsed in process: the standard parser executes nothing, ``NaN`` and
``Infinity`` are refused, and nesting too deep for the parser is a rejection.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Final, NoReturn

from coach.application.files import DecodedSession, FileRejectedError
from coach.domain.activity.fields import (
    Cadence,
    CadenceUnit,
    PowerSource,
    PowerSummary,
    ProviderMetric,
    check_values,
)
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.provenance import MetricOrigin
from coach.domain.common.sport import Sport
from coach.domain.common.time import MAX_UTC_OFFSET, format_utc_offset, parse_timezone
from coach.domain.ingestion.record import RejectionReason
from coach.integrations.files.normalise import EARLIEST_START, whole

NORMALISER: Final = "garmin_activity"
RULES_VERSION: Final = "1.0.0"
PROVIDER: Final = "garmin"
LATEST_AHEAD: Final = timedelta(days=365)
_DATE_TIME: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}")

# Documented type keys only (Swagger, 2026-10-26): running, cycling and swimming as parent
# types; trail_running and road_cycling as subtype examples.
TYPES: Final[Mapping[str, tuple[Sport, str | None]]] = {
    "running": (Sport.RUNNING, None),
    "trail_running": (Sport.RUNNING, "TRAIL"),
    "cycling": (Sport.CYCLING, None),
    "road_cycling": (Sport.CYCLING, "ROAD"),
    "swimming": (Sport.SWIMMING, None),
}
_TOTALS: Final = {
    "elapsedDuration": "timing.elapsedSeconds",
    "movingDuration": "timing.movingSeconds",
    "distance": "distance.meters",
    "elevationGain": "elevation.gainMeters",
    "elevationLoss": "elevation.lossMeters",
    "averageSpeed": "performance.averageSpeedMps",
    "maxSpeed": "performance.maxSpeedMps",
}
_HEART_RATE: Final = {"averageHR": "cardiovascular.averageHr", "maxHR": "cardiovascular.maxHr"}
_CADENCE: Final = {
    Sport.RUNNING: ("averageRunCadence", "maxRunCadence", CadenceUnit.STEPS_PER_MIN),
    Sport.CYCLING: ("averageBikeCadence", "maxBikeCadence", CadenceUnit.RPM),
}
# Garmin unit -> canonical unit factor (cm to m, cm to mm, ms to ms).
_DYNAMICS: Final = {
    "strideLength": ("performance.runningDynamics.strideLengthMeters", 0.01),
    "verticalOscillation": ("performance.runningDynamics.verticalOscillationMm", 10.0),
    "groundContactTime": ("performance.runningDynamics.groundContactTimeMs", 1.0),
}
_METRICS: Final = {
    "anaerobicTrainingEffect": ("training.anaerobicEffect", "anaerobic training effect"),
    "activityTrainingLoad": ("training.load.internal.providerLoad", "activity training load"),
}


def _reject(reason: RejectionReason, detail: str) -> NoReturn:
    raise FileRejectedError(reason, detail)


def _schema(detail: str) -> NoReturn:
    _reject(RejectionReason.SCHEMA_INVALID, detail)


def _no_constants(name: str) -> NoReturn:
    raise ValueError(f"JSON constant {name} is not allowed")


def _load(data: bytes) -> dict[str, Any]:
    try:
        document = json.loads(data.decode("utf-8"), parse_constant=_no_constants)
    except (UnicodeDecodeError, ValueError, RecursionError):
        _schema("JSON")
    if not isinstance(document, dict):
        _schema("JSON")
    return document


def _object(container: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = container.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        _schema("FIELD_TYPE")
    return value


def _text(container: Mapping[str, Any], key: str) -> str | None:
    value = container.get(key)
    if value is not None and not isinstance(value, str):
        _schema("FIELD_TYPE")
    return value


def _number(container: Mapping[str, Any], key: str) -> float | None:
    value = container.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        _schema("FIELD_TYPE")
    try:
        number = float(value)
    except OverflowError:  # an integer too large for any measurement
        _reject(RejectionReason.NORMALIZATION_FAILED, "FIELD_VALUE")
    if not math.isfinite(number) or number < 0:
        _reject(RejectionReason.SEMANTIC_INVALID, "NEGATIVE_VALUE")
    return number


def _timestamp(container: Mapping[str, Any], key: str) -> datetime | None:
    """A Garmin timestamp: ISO 8601 without an offset (Swagger ``SummaryDTO``)."""
    text = _text(container, key)
    if text is None:
        return None
    if not _DATE_TIME.match(text):  # a date alone would invent midnight
        _schema("FIELD_TYPE")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        _schema("FIELD_TYPE")
    if moment.tzinfo is not None:
        _schema("FIELD_TYPE")
    return moment


class _Builder:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}
        self.paths: dict[str, str] = {}

    def put(self, path: str, value: object, location: str) -> None:
        if value is not None:
            self.values[path] = value
            self.paths[path] = location


def _check_id(document: Mapping[str, Any], provider_object_id: str) -> None:
    activity_id = document.get("activityId")
    if isinstance(activity_id, bool) or not isinstance(activity_id, int):
        _schema("ACTIVITY_ID_MISMATCH")
    if str(activity_id) != provider_object_id:
        _schema("ACTIVITY_ID_MISMATCH")


def _timing(
    builder: _Builder, document: Mapping[str, Any], summary: Mapping[str, Any], received: datetime
) -> None:
    naive_start = _timestamp(summary, "startTimeGMT")
    if naive_start is None:
        _reject(RejectionReason.SEMANTIC_INVALID, "NO_START")
    start = naive_start.replace(tzinfo=UTC)
    if not EARLIEST_START <= start <= received + LATEST_AHEAD:
        _reject(RejectionReason.SEMANTIC_INVALID, "START_OUT_OF_RANGE")
    builder.put("timing.startTimeUtc", start, "summaryDTO.startTimeGMT")
    local = _timestamp(summary, "startTimeLocal")
    if local is not None:
        offset = local - naive_start
        if offset % timedelta(minutes=1) == timedelta(0) and abs(offset) <= MAX_UTC_OFFSET:
            builder.put("timing.utcOffset", format_utc_offset(offset), "summaryDTO.startTimeLocal")
    zone = _text(_object(document, "timeZoneUnitDTO"), "timeZone")
    if zone is not None:
        try:
            parse_timezone(zone)
        except DomainValidationError:
            zone = None
        builder.put("timing.originalTimezone", zone, "timeZoneUnitDTO.timeZone")


def _summary(builder: _Builder, summary: Mapping[str, Any], sport: Sport) -> None:
    for key, path in _TOTALS.items():
        builder.put(path, _number(summary, key), f"summaryDTO.{key}")
    for key, path in _HEART_RATE.items():
        value = _number(summary, key)
        builder.put(path, None if value is None else whole(value), f"summaryDTO.{key}")
    cadence = _CADENCE.get(sport)
    if cadence is not None:
        average_key, max_key, unit = cadence
        average, maximum = _number(summary, average_key), _number(summary, max_key)
        if average is not None or maximum is not None:
            builder.put(
                "performance.cadence",
                Cadence(average, maximum, unit),
                f"summaryDTO.{average_key}",
            )
    average_power, max_power = _number(summary, "averagePower"), _number(summary, "maxPower")
    if average_power is not None or max_power is not None:
        builder.put(
            "performance.power",
            PowerSummary(PowerSource.UNKNOWN, average_power, max_power),
            "summaryDTO.averagePower",
        )
    if sport is Sport.RUNNING:
        for key, (path, factor) in _DYNAMICS.items():
            value = _number(summary, key)
            builder.put(path, None if value is None else value * factor, f"summaryDTO.{key}")
    if sport is Sport.SWIMMING:
        lengths = _number(summary, "numberOfActiveLengths")
        builder.put(
            "swimming.lengthCount",
            None if lengths is None else whole(lengths),
            "summaryDTO.numberOfActiveLengths",
        )
    for key, (path, name) in _METRICS.items():
        value = _number(summary, key)
        if value is not None:
            metric = ProviderMetric(value, PROVIDER, name, MetricOrigin.PROVIDER_DERIVED)
            builder.put(path, metric, f"summaryDTO.{key}")


class GarminActivityRecords:
    """Implements ``ProviderRecords`` for Garmin activity details."""

    def decode(
        self, data: bytes, *, provider_object_id: str, received_at: datetime
    ) -> Sequence[DecodedSession]:
        try:
            return self._decode(data, provider_object_id, received_at)
        except (ValueError, TypeError, OverflowError, RecursionError):
            # Any shape the rules did not foresee is a rejection, never a crash (spec 10).
            raise FileRejectedError(RejectionReason.NORMALIZATION_FAILED, "FIELD_VALUE") from None

    def _decode(
        self, data: bytes, provider_object_id: str, received_at: datetime
    ) -> Sequence[DecodedSession]:
        document = _load(data)
        _check_id(document, provider_object_id)
        summary = _object(document, "summaryDTO")
        type_key = _text(_object(document, "activityTypeDTO"), "typeKey")
        sport, subtype = TYPES.get(type_key or "", (Sport.OTHER, None))
        builder = _Builder()
        builder.put("classification.sport", sport, "activityTypeDTO.typeKey")
        builder.put("classification.subtype", subtype, "activityTypeDTO.typeKey")
        provider_type = None if type_key is None else f"{PROVIDER}:{type_key}"
        builder.put("classification.providerType", provider_type, "activityTypeDTO.typeKey")
        builder.put("metadata.name", _text(document, "activityName"), "activityName")
        builder.put("metadata.description", _text(document, "description"), "description")
        _timing(builder, document, summary, received_at)
        _summary(builder, summary, sport)
        try:
            values = check_values(builder.values)
        except DomainValidationError:
            _reject(RejectionReason.NORMALIZATION_FAILED, "FIELD_VALUE")
        return [
            DecodedSession(
                part=0,
                values=values,
                normaliser=NORMALISER,
                normaliser_version=RULES_VERSION,
                source_paths={path: builder.paths[path] for path in values},
            )
        ]
