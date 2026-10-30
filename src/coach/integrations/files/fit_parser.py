"""FIT parser (runs in the sandbox): official Garmin FIT SDK with the CRC check enabled.

Reads file_id, session, activity and record messages into the parser-output document, from
P9 lap, length and device_info messages (device types only, never identifiers) and from P10
set messages (strength sets, the exercise as the file's own code); the mapping to canonical
fields happens in the parent (spec/integration/files.spec.md 8.1, 8.6, 8.7).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, cast

from garmin_fit_sdk import Decoder, Profile, Stream

from coach.integrations.files.parsing import (
    ParserLimits,
    RecordCounter,
    SessionData,
    limit_exceeded,
    number,
    output,
    parse_failed,
)

PARSER: Final = "fit"
VERSION: Final = "1.2.0"
FIT_EPOCH: Final = datetime(1989, 12, 31, tzinfo=UTC)
MAX_OFFSET_SECONDS: Final = 14 * 3600
_CODE: Final = re.compile(r"^[a-z0-9_]{1,40}$")

# summary key -> FIT session fields, first present wins
_SUMMARY: Final = (
    ("elapsedSeconds", ("total_elapsed_time",)),
    ("timerSeconds", ("total_timer_time",)),
    ("movingSeconds", ("total_moving_time",)),
    ("distanceMeters", ("total_distance",)),
    ("ascentMeters", ("total_ascent",)),
    ("descentMeters", ("total_descent",)),
    ("avgHeartRate", ("avg_heart_rate",)),
    ("maxHeartRate", ("max_heart_rate",)),
    ("avgSpeedMps", ("enhanced_avg_speed", "avg_speed")),
    ("maxSpeedMps", ("enhanced_max_speed", "max_speed")),
    ("avgPowerWatts", ("avg_power",)),
    ("maxPowerWatts", ("max_power",)),
    ("totalWorkJoules", ("total_work",)),
    ("trainingEffectAerobic", ("total_training_effect",)),
    ("trainingEffectAnaerobic", ("total_anaerobic_training_effect",)),
    ("poolLengthMeters", ("pool_length",)),
    ("lengthCount", ("num_active_lengths", "num_lengths")),
)


# A set names at most this many exercises (FIT category and category_subtype arrays).
MAX_SET_EXERCISES: Final = 4
_EXERCISE_NAMES: Final[dict[str, dict[int, str]]] = {
    name.removesuffix("_exercise_name"): table
    for name, table in Profile["types"].items()
    if name.endswith("_exercise_name")
}

# device_info subfields that name a device type (the SDK expands them by source_type)
_DEVICE_TYPES: Final = ("antplus_device_type", "ble_device_type")
MAX_SENSORS: Final = 32


class _RecordLimit(Exception):  # noqa: N818 - control flow inside the decoder callback
    pass


def _code(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if _CODE.match(text) else None


def _first(message: dict[str, Any], names: tuple[str, ...]) -> tuple[str, float] | None:
    for name in names:
        value = number(message.get(name))
        if value is not None:
            return name, value
    return None


def _value(message: dict[str, Any], names: tuple[str, ...]) -> float | None:
    found = _first(message, names)
    return None if found is None else found[1]


def _cadence(message: dict[str, Any], whole: str, fraction: str) -> float | None:
    cadence = number(message.get(whole))
    if cadence is None:
        return None
    return cadence + (number(message.get(fraction)) or 0.0)


def _utc_offset_minutes(activities: list[dict[str, Any]]) -> int | None:
    """``activity.local_timestamp - activity.timestamp`` (files spec 8.1)."""
    if len(activities) != 1:
        return None
    timestamp = activities[0].get("timestamp")
    local = activities[0].get("local_timestamp")
    if isinstance(local, datetime):
        local = (local.replace(tzinfo=UTC) - FIT_EPOCH) // timedelta(seconds=1)
    if not isinstance(timestamp, datetime) or isinstance(local, bool) or not isinstance(local, int):
        return None
    offset = local - (timestamp.astimezone(UTC) - FIT_EPOCH) // timedelta(seconds=1)
    if offset % 60 or abs(offset) > MAX_OFFSET_SECONDS:
        return None
    return offset // 60


def _window_end(session: dict[str, Any], start: datetime) -> datetime | None:
    elapsed = number(session.get("total_elapsed_time"))
    if elapsed is not None:
        return start + timedelta(seconds=elapsed)
    end = session.get("timestamp")
    return end if isinstance(end, datetime) else None


def _session(index: int, message: dict[str, Any], manufacturer: str | None) -> SessionData:
    start = message.get("start_time")
    if not isinstance(start, datetime):
        raise parse_failed("SESSION_WITHOUT_START")
    data = SessionData(
        index=index,
        start=start.astimezone(UTC),
        sport=_code(message.get("sport")),
        sub_sport=_code(message.get("sub_sport")),
        manufacturer=manufacturer,
    )
    for key, names in _SUMMARY:
        found = _first(message, names)
        if found is not None:
            data.put(key, found[1], f"session[{index}].{found[0]}")
    data.put(
        "avgCadence",
        _cadence(message, "avg_cadence", "avg_fractional_cadence"),
        f"session[{index}].avg_cadence",
    )
    data.put(
        "maxCadence",
        _cadence(message, "max_cadence", "max_fractional_cadence"),
        f"session[{index}].max_cadence",
    )
    stroke = _code(message.get("swim_stroke"))
    if stroke is not None:
        data.labels["strokeType"] = stroke
        data.source_paths["strokeType"] = f"session[{index}].swim_stroke"
    return data


@dataclass(frozen=True, slots=True)
class _Window:
    """A session's time window: its start, its end (closed) and the next session's start
    (open), so a boundary message of a multisport file belongs to one session only."""

    start: datetime
    end: datetime | None
    following: datetime | None

    def contains(self, moment: Any) -> datetime | None:
        """The moment when it falls inside the window, else ``None``."""
        if not isinstance(moment, datetime) or moment < self.start:
            return None
        if self.end is not None and moment > self.end:
            return None
        if self.following is not None and moment >= self.following:
            return None
        return moment


def _samples(data: SessionData, records: list[dict[str, Any]], window: _Window) -> None:
    for record in records:
        timestamp = window.contains(record.get("timestamp"))
        if timestamp is None:
            continue
        data.sample(
            (timestamp - data.start).total_seconds(),
            distanceMeters=number(record.get("distance")),
            heartRate=number(record.get("heart_rate")),
            speedMps=_value(record, ("enhanced_speed", "speed")),
            cadence=_cadence(record, "cadence", "fractional_cadence"),
            powerWatts=number(record.get("power")),
            altitudeMeters=_value(record, ("enhanced_altitude", "altitude")),
        )


def _inside(message: dict[str, Any], window: _Window) -> float | None:
    """Offset of a lap or length that starts inside the session window, else ``None``."""
    start = window.contains(message.get("start_time"))
    return None if start is None else (start - window.start).total_seconds()


def _laps(data: SessionData, laps: list[dict[str, Any]], window: _Window) -> None:
    for message in sorted(laps, key=_start_key):
        offset = _inside(message, window)
        if offset is None:
            continue
        data.laps.append(
            {
                "startOffsetSeconds": offset,
                "elapsedSeconds": number(message.get("total_elapsed_time")),
                "timerSeconds": number(message.get("total_timer_time")),
                "distanceMeters": number(message.get("total_distance")),
                "avgHeartRate": number(message.get("avg_heart_rate")),
                "maxHeartRate": number(message.get("max_heart_rate")),
                "avgCadence": _cadence(message, "avg_cadence", "avg_fractional_cadence"),
                "avgPowerWatts": number(message.get("avg_power")),
                "intensity": _code(message.get("intensity")),
                "trigger": _code(message.get("lap_trigger")),
            }
        )


def _lengths(data: SessionData, lengths: list[dict[str, Any]], window: _Window) -> None:
    for message in sorted(lengths, key=_start_key):
        offset = _inside(message, window)
        if offset is None:
            continue
        data.lengths.append(
            {
                "startOffsetSeconds": offset,
                "elapsedSeconds": number(message.get("total_elapsed_time")),
                "timerSeconds": number(message.get("total_timer_time")),
                "type": _code(message.get("length_type")),
                "strokes": number(message.get("total_strokes")),
                "strokeType": _code(message.get("swim_stroke")),
            }
        )


def _listed(value: Any) -> list[Any]:
    """The SDK decodes a one-element array as its element."""
    return list(value) if isinstance(value, list | tuple) else [value]


def _exercises(message: dict[str, Any]) -> list[str]:
    """The set's exercises as the file's codes: ``category/name`` (the SDK's exercise name of
    the subtype), ``category/<number>`` when the profile has no name, ``category`` alone when
    the subtype is unknown. Unknown categories are left out."""
    categories = _listed(message.get("category"))
    subtypes = _listed(message.get("category_subtype"))
    codes: list[str] = []
    for index, category in enumerate(categories[:MAX_SET_EXERCISES]):
        code = _code(category)
        if code is None or code == "unknown":
            continue
        subtype = subtypes[index] if index < len(subtypes) else None
        if isinstance(subtype, int) and not isinstance(subtype, bool):
            name = _code(_EXERCISE_NAMES.get(code, {}).get(subtype)) or str(subtype)
            code = f"{code}/{name}"
        if code not in codes:
            codes.append(code)
    return codes


def _sets(data: SessionData, sets: list[dict[str, Any]], window: _Window) -> None:
    for message in sorted(sets, key=_start_key):
        offset = _inside(message, window)
        if offset is None:
            continue
        data.sets.append(
            {
                "startOffsetSeconds": offset,
                "durationSeconds": number(message.get("duration")),
                "repetitions": number(message.get("repetitions")),
                "weightKg": number(message.get("weight")),
                "type": _code(message.get("set_type")),
                "exercises": _exercises(message),
            }
        )


def _start_key(message: dict[str, Any]) -> float:
    start = message.get("start_time")
    return start.timestamp() if isinstance(start, datetime) else 0.0


def _sensors(devices: list[dict[str, Any]]) -> list[str]:
    """Device types of the file's sensors; serial numbers and other identifiers are dropped."""
    found: list[str] = []
    for device in devices:
        for key in _DEVICE_TYPES:
            code = _code(device.get(key))
            if code is not None and code not in found and len(found) < MAX_SENSORS:
                found.append(code)
    return found


def parse_fit(path: Path, limits: ParserLimits) -> dict[str, Any]:
    counter = RecordCounter(limits.records)

    def count(_mesg_num: int, _message: dict[str, Any]) -> None:
        try:
            counter.add()
        except Exception as error:
            raise _RecordLimit from error

    stream = Stream.from_file(str(path))
    try:
        messages, errors = Decoder(stream).read(
            enable_crc_check=True, merge_heart_rates=False, mesg_listener=count
        )
    finally:
        stream.close()
    if any(isinstance(error, _RecordLimit) for error in errors):
        raise limit_exceeded("RECORDS")
    if errors:
        crc = any("CRC" in str(error) for error in errors)
        raise parse_failed("CRC_ERROR" if crc else "FIT_DECODE")
    decoded = cast(dict[str, list[dict[str, Any]]], messages)
    file_ids = decoded.get("file_id_mesgs", [])
    file_type = _code(file_ids[0].get("type")) if file_ids else None
    manufacturer = _code(file_ids[0].get("manufacturer")) if file_ids else None
    offset = _utc_offset_minutes(decoded.get("activity_mesgs", []))
    records = decoded.get("record_mesgs", [])
    sensors = _sensors(decoded.get("device_info_mesgs", []))
    messages_of_sessions = decoded.get("session_mesgs", [])
    starts = sorted(
        start
        for start in (message.get("start_time") for message in messages_of_sessions)
        if isinstance(start, datetime)
    )
    sessions = []
    for index, message in enumerate(messages_of_sessions):
        data = _session(index, message, manufacturer)
        data.utc_offset_minutes = offset
        following = next((start for start in starts if start > data.start), None)
        window = _Window(data.start, _window_end(message, data.start), following)
        _samples(data, records, window)
        _laps(data, decoded.get("lap_mesgs", []), window)
        _lengths(data, decoded.get("length_mesgs", []), window)
        _sets(data, decoded.get("set_mesgs", []), window)
        data.sensors = list(sensors)
        sessions.append(data)
    return output(
        "FIT",
        PARSER,
        VERSION,
        records=counter.count,
        sessions=sessions,
        limits=limits,
        file_type=file_type,
    )
