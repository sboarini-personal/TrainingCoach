"""Strict decoding of the sandbox response (stage 9; spec/integration/files.spec.md section 7).

The response is untrusted: anything outside the parser-output contract is SCHEMA_INVALID.
The decoder mirrors contracts/parser-output.schema.json and adds the checks a JSON Schema
cannot express (equal sample columns, the session limit, finite numbers).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from coach.application.files import FileRejectedError
from coach.domain.common.time import ensure_utc
from coach.domain.ingestion.record import RejectionReason, check_code
from coach.domain.raw.source import DetectedType

SUPPORTED_MAJOR: Final = "1."
SUMMARY_KEYS: Final = frozenset(
    {
        "elapsedSeconds",
        "timerSeconds",
        "movingSeconds",
        "distanceMeters",
        "ascentMeters",
        "descentMeters",
        "avgHeartRate",
        "maxHeartRate",
        "avgCadence",
        "maxCadence",
        "avgSpeedMps",
        "maxSpeedMps",
        "avgPowerWatts",
        "maxPowerWatts",
        "totalWorkJoules",
        "trainingEffectAerobic",
        "trainingEffectAnaerobic",
        "poolLengthMeters",
        "lengthCount",
        "rpe",
    }
)
LABEL_KEYS: Final = frozenset({"strokeType", "subtype"})
TEXT_KEYS: Final = frozenset({"name", "description", "comments"})
SAMPLE_KEYS: Final = frozenset(
    {"distanceMeters", "heartRate", "speedMps", "cadence", "powerWatts", "altitudeMeters"}
)
SESSION_KEYS: Final = frozenset(
    {
        "index",
        "sport",
        "subSport",
        "manufacturer",
        "startTime",
        "utcOffsetMinutes",
        "summary",
        "labels",
        "texts",
        "sourcePaths",
        "samples",
        "laps",
        "lengths",
        "sets",
        "sensors",
    }
)
LAP_KEYS: Final = frozenset(
    {
        "startOffsetSeconds",
        "elapsedSeconds",
        "timerSeconds",
        "distanceMeters",
        "avgHeartRate",
        "maxHeartRate",
        "avgCadence",
        "avgPowerWatts",
        "intensity",
        "trigger",
    }
)
LENGTH_KEYS: Final = frozenset(
    {"startOffsetSeconds", "elapsedSeconds", "timerSeconds", "type", "strokes", "strokeType"}
)
SET_KEYS: Final = frozenset(
    {"startOffsetSeconds", "durationSeconds", "repetitions", "weightKg", "type", "exercises"}
)
MAX_SET_EXERCISES: Final = 4
SEGMENT_LABELS: Final = frozenset({"intensity", "trigger", "type", "strokeType"})
MAX_SENSORS: Final = 32
OUTPUT_KEYS: Final = frozenset(
    {"schemaVersion", "format", "parser", "recordCount", "fileType", "sessions"}
)
WORKER_REASONS: Final = {
    "PARSE_FAILED": RejectionReason.PARSE_FAILED,
    "LIMIT_EXCEEDED": RejectionReason.LIMIT_EXCEEDED,
    "SEMANTIC_INVALID": RejectionReason.SEMANTIC_INVALID,
}
_CODE: Final = re.compile(r"^[a-z0-9_]{1,40}$")
_EXERCISE_CODE: Final = re.compile(r"^[a-z0-9_]{1,40}(/[a-z0-9_]{1,80})?$")
_SEMVER: Final = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_UTC_INSTANT: Final = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z$")


class _Invalid(Exception):  # noqa: N818 - internal control flow
    pass


@dataclass(frozen=True, slots=True)
class ParsedSegment:
    """A lap or a pool length in the file's vocabulary: numbers and labels by key."""

    numbers: Mapping[str, float]
    labels: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ParsedSet:
    """A strength set in the file's vocabulary; ``exercises`` are the file's codes."""

    numbers: Mapping[str, float]
    type: str | None
    exercises: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ParsedSession:
    index: int
    sport: str | None
    sub_sport: str | None
    manufacturer: str | None
    start: datetime
    utc_offset_minutes: int | None
    summary: Mapping[str, float]
    labels: Mapping[str, str]
    texts: Mapping[str, str]
    source_paths: Mapping[str, str]
    offsets: tuple[float, ...]
    samples: Mapping[str, tuple[float | None, ...]]
    laps: tuple[ParsedSegment, ...] = ()
    lengths: tuple[ParsedSegment, ...] = ()
    sets: tuple[ParsedSet, ...] = ()
    sensors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ParsedFile:
    format: DetectedType
    parser: str
    parser_version: str
    record_count: int
    file_type: str | None
    sessions: tuple[ParsedSession, ...]


def _check(condition: bool) -> None:
    if not condition:
        raise _Invalid


def _object(
    value: Any, allowed: frozenset[str], required: frozenset[str] = frozenset()
) -> dict[str, Any]:
    _check(isinstance(value, dict) and set(value) <= allowed and required <= set(value))
    return dict(value)


def _int(value: Any, minimum: int, maximum: int | None = None) -> int:
    _check(isinstance(value, int) and not isinstance(value, bool) and value >= minimum)
    _check(maximum is None or value <= maximum)
    return int(value)


def _number(value: Any) -> float:
    _check(isinstance(value, int | float) and not isinstance(value, bool))
    _check(math.isfinite(value))
    return float(value)


def _text(value: Any, maximum: int) -> str | None:
    if value is None:
        return None
    _check(isinstance(value, str) and 0 < len(value) <= maximum)
    return str(value)


def _code(value: Any) -> str | None:
    if value is None:
        return None
    _check(isinstance(value, str) and bool(_CODE.match(value)))
    return str(value)


def _start(value: Any) -> datetime:
    _check(isinstance(value, str) and bool(_UTC_INSTANT.match(value)))
    try:
        return ensure_utc(datetime.fromisoformat(value))
    except ValueError:
        raise _Invalid from None


def _strings(value: Any, allowed: frozenset[str] | None, maximum: int) -> dict[str, str]:
    _check(isinstance(value, dict))
    result: dict[str, str] = {}
    for key, item in value.items():
        _check(isinstance(key, str) and (allowed is None or key in allowed))
        text = _text(item, maximum)
        if text is not None:
            result[key] = text
    return result


def _segment(value: Any, keys: frozenset[str]) -> ParsedSegment:
    document = _object(value, keys, frozenset({"startOffsetSeconds"}))
    numbers: dict[str, float] = {}
    labels: dict[str, str] = {}
    for key, item in document.items():
        if key in SEGMENT_LABELS:
            text = _text(item, 64)
            if text is not None:
                labels[key] = text
        elif item is not None:
            numbers[key] = _number(item)
    _check("startOffsetSeconds" in numbers)
    return ParsedSegment(numbers, labels)


def _list(value: Any) -> list[Any]:
    _check(isinstance(value, list))
    return list(value)


def _segments(value: Any, keys: frozenset[str]) -> tuple[ParsedSegment, ...]:
    return tuple(_segment(item, keys) for item in _list(value))


def _set(value: Any) -> ParsedSet:
    document = _object(value, SET_KEYS, SET_KEYS)
    exercises = document.pop("exercises")
    _check(isinstance(exercises, list) and len(exercises) <= MAX_SET_EXERCISES)
    _check(all(isinstance(item, str) and _EXERCISE_CODE.match(item) for item in exercises))
    label = _text(document.pop("type"), 64)
    numbers = {key: _number(item) for key, item in document.items() if item is not None}
    _check("startOffsetSeconds" in numbers)
    return ParsedSet(numbers, label, tuple(exercises))


def _sensors(value: Any) -> tuple[str, ...]:
    _check(isinstance(value, list) and len(value) <= MAX_SENSORS)
    codes = tuple(_code(item) for item in value)
    _check(all(code is not None for code in codes))
    return tuple(code for code in codes if code is not None)


def _session(value: Any) -> ParsedSession:
    document = _object(value, SESSION_KEYS, SESSION_KEYS)
    summary = _object(document["summary"], SUMMARY_KEYS)
    samples = _object(
        document["samples"], SAMPLE_KEYS | {"offsetSeconds"}, frozenset({"offsetSeconds"})
    )
    offsets_raw = samples.pop("offsetSeconds")
    _check(isinstance(offsets_raw, list))
    offsets = tuple(_number(item) for item in offsets_raw)
    columns: dict[str, tuple[float | None, ...]] = {}
    for name, column in samples.items():
        _check(isinstance(column, list) and len(column) == len(offsets))
        columns[name] = tuple(None if item is None else _number(item) for item in column)
    offset_minutes = document["utcOffsetMinutes"]
    return ParsedSession(
        index=_int(document["index"], 0),
        sport=_text(document["sport"], 64),
        sub_sport=_text(document["subSport"], 64),
        manufacturer=_code(document["manufacturer"]),
        start=_start(document["startTime"]),
        utc_offset_minutes=None if offset_minutes is None else _int(offset_minutes, -840, 840),
        summary={key: _number(item) for key, item in summary.items() if item is not None},
        labels=_strings(document["labels"], LABEL_KEYS, 64),
        texts=_strings(document["texts"], TEXT_KEYS, 4000),
        source_paths=_strings(document["sourcePaths"], None, 300),
        offsets=offsets,
        samples=columns,
        laps=_segments(document["laps"], LAP_KEYS),
        lengths=_segments(document["lengths"], LENGTH_KEYS),
        sets=tuple(_set(item) for item in _list(document["sets"])),
        sensors=_sensors(document["sensors"]),
    )


def _rejection(response: Mapping[str, Any]) -> FileRejectedError:
    reason = WORKER_REASONS.get(str(response.get("reason")))
    detail = response.get("detail")
    if reason is None or not isinstance(detail, str):
        return FileRejectedError(RejectionReason.SCHEMA_INVALID, "PARSER_RESPONSE")
    try:
        return FileRejectedError(reason, check_code(detail, "detail"))
    except ValueError:
        return FileRejectedError(RejectionReason.SCHEMA_INVALID, "PARSER_RESPONSE")


def decode_response(
    response: Mapping[str, Any], expected: DetectedType, *, max_sessions: int
) -> ParsedFile:
    """The parser output of a successful response; the worker's rejection otherwise."""
    if response.get("ok") is not True:
        raise _rejection(response)
    try:
        document = _object(response.get("output"), OUTPUT_KEYS, OUTPUT_KEYS)
        version = document["schemaVersion"]
        _check(isinstance(version, str) and version.startswith(SUPPORTED_MAJOR))
        _check(document["format"] == expected.value)
        parser = _object(
            document["parser"], frozenset({"name", "version"}), frozenset({"name", "version"})
        )
        name, parser_version = _code(parser["name"]), parser["version"]
        _check(name is not None and isinstance(parser_version, str))
        _check(bool(_SEMVER.match(parser_version)))
        sessions_raw = document["sessions"]
        _check(isinstance(sessions_raw, list) and len(sessions_raw) <= max_sessions)
        sessions = tuple(_session(item) for item in sessions_raw)
        return ParsedFile(
            format=expected,
            parser=str(name),
            parser_version=parser_version,
            record_count=_int(document["recordCount"], 0),
            file_type=_code(document["fileType"]),
            sessions=sessions,
        )
    except (_Invalid, KeyError, TypeError):
        raise FileRejectedError(RejectionReason.SCHEMA_INVALID, "PARSER_OUTPUT") from None
