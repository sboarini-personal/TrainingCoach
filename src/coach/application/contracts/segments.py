"""Laps <-> laps contract; Lengths <-> lengths contract (files spec 8.6, P9)."""

from __future__ import annotations

from typing import Any

from coach.application.contracts._json import (
    SCHEMA_VERSION,
    Document,
    Json,
    instant,
    parse_instant,
)
from coach.domain.activity.fields import CadenceUnit, StrokeType
from coach.domain.activity.segments import (
    Lap,
    LapIntensity,
    Laps,
    LapTrigger,
    Lengths,
    LengthType,
    SwimLength,
)


def _code(value: Any) -> str | None:
    return None if value is None else str(value.value)


def _float(value: Any) -> float | None:
    return None if value is None else float(value)


def laps_to_contract(laps: Laps) -> Json:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "startTimeUtc": instant(laps.start_time_utc),
        "cadenceUnit": _code(laps.cadence_unit),
        "laps": [
            {
                "startOffsetSeconds": lap.start_offset_seconds,
                "elapsedSeconds": lap.elapsed_seconds,
                "timerSeconds": lap.timer_seconds,
                "distanceMeters": lap.distance_meters,
                "averageHr": lap.average_hr,
                "maxHr": lap.max_hr,
                "averageCadence": lap.average_cadence,
                "averagePowerWatts": lap.average_power_watts,
                "intensity": _code(lap.intensity),
                "trigger": _code(lap.trigger),
            }
            for lap in laps.laps
        ],
    }


def laps_from_contract(document: Document) -> Laps:
    unit = document["cadenceUnit"]
    return Laps(
        start_time_utc=parse_instant(document["startTimeUtc"]),
        cadence_unit=None if unit is None else CadenceUnit(unit),
        laps=tuple(
            Lap(
                start_offset_seconds=float(item["startOffsetSeconds"]),
                elapsed_seconds=_float(item["elapsedSeconds"]),
                timer_seconds=_float(item["timerSeconds"]),
                distance_meters=_float(item["distanceMeters"]),
                average_hr=_float(item["averageHr"]),
                max_hr=_float(item["maxHr"]),
                average_cadence=_float(item["averageCadence"]),
                average_power_watts=_float(item["averagePowerWatts"]),
                intensity=None if item["intensity"] is None else LapIntensity(item["intensity"]),
                trigger=None if item["trigger"] is None else LapTrigger(item["trigger"]),
            )
            for item in document["laps"]
        ),
    )


def lengths_to_contract(lengths: Lengths) -> Json:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "startTimeUtc": instant(lengths.start_time_utc),
        "lengths": [
            {
                "startOffsetSeconds": length.start_offset_seconds,
                "elapsedSeconds": length.elapsed_seconds,
                "timerSeconds": length.timer_seconds,
                "type": _code(length.type),
                "strokes": length.strokes,
                "strokeType": _code(length.stroke_type),
            }
            for length in lengths.lengths
        ],
    }


def lengths_from_contract(document: Document) -> Lengths:
    return Lengths(
        start_time_utc=parse_instant(document["startTimeUtc"]),
        lengths=tuple(
            SwimLength(
                start_offset_seconds=float(item["startOffsetSeconds"]),
                elapsed_seconds=_float(item["elapsedSeconds"]),
                timer_seconds=_float(item["timerSeconds"]),
                type=None if item["type"] is None else LengthType(item["type"]),
                strokes=item["strokes"],
                stroke_type=None if item["strokeType"] is None else StrokeType(item["strokeType"]),
            )
            for item in document["lengths"]
        ),
    )
