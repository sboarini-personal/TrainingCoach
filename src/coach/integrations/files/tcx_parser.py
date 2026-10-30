"""TCX parser (runs in the sandbox): Garmin TrainingCenterDatabase v2 (files spec 8.2).

Streams the document; finished track points are removed from the tree to bound memory.
Positions are read past, never extracted. From P9 the laps are also returned (files spec 8.6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from coach.integrations.files.parsing import (
    ParserLimits,
    RecordCounter,
    SessionData,
    output,
    parse_failed,
    parse_time,
    semantic_invalid,
    text_number,
)
from coach.integrations.files.xmlsafe import Element, child_text, events, local, namespace

PARSER: Final = "tcx"
VERSION: Final = "1.1.0"
TCX_NAMESPACE: Final = "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"


@dataclass(slots=True)
class _Lap:
    start: datetime
    offset: int | None
    seconds: float | None
    distance: float | None
    max_speed: float | None
    avg_hr: float | None
    max_hr: float | None
    cadence: float | None
    intensity: str | None
    trigger: str | None


@dataclass(slots=True)
class _Point:
    time: datetime
    distance: float | None
    heart_rate: float | None
    cadence: float | None
    altitude: float | None
    speed: float | None
    watts: float | None


@dataclass(slots=True)
class _Activity:
    sport: str | None
    laps: list[_Lap] = field(default_factory=list)
    points: list[_Point] = field(default_factory=list)
    notes: str | None = None


def _value_child(element: Element, name: str) -> float | None:
    """``<Name><Value>n</Value></Name>`` heart-rate values."""
    for child in element:
        if local(child.tag) == name:
            return text_number(child_text(child, "Value"))
    return None


def _extension(element: Element, name: str) -> float | None:
    for child in element.iter():
        if child is not element and local(child.tag) == name:
            return text_number(child.text)
    return None


def _lap(element: Element) -> _Lap:
    start, offset = parse_time(element.get("StartTime"))
    return _Lap(
        start=start,
        offset=offset,
        seconds=text_number(child_text(element, "TotalTimeSeconds")),
        distance=text_number(child_text(element, "DistanceMeters")),
        max_speed=text_number(child_text(element, "MaximumSpeed")),
        avg_hr=_value_child(element, "AverageHeartRateBpm"),
        max_hr=_value_child(element, "MaximumHeartRateBpm"),
        cadence=text_number(child_text(element, "Cadence")),
        intensity=child_text(element, "Intensity"),
        trigger=child_text(element, "TriggerMethod"),
    )


def _point(element: Element) -> _Point:
    time, _ = parse_time(child_text(element, "Time"))
    return _Point(
        time=time,
        distance=text_number(child_text(element, "DistanceMeters")),
        heart_rate=_value_child(element, "HeartRateBpm"),
        cadence=text_number(child_text(element, "Cadence")),
        altitude=text_number(child_text(element, "AltitudeMeters")),
        speed=_extension(element, "Speed"),
        watts=_extension(element, "Watts"),
    )


def _all(values: list[float | None]) -> list[float] | None:
    if not values or any(value is None for value in values):
        return None
    return [value for value in values if value is not None]


def _weighted(values: list[float | None], weights: list[float | None]) -> float | None:
    known, times = _all(values), _all(weights)
    if known is None or times is None or sum(times) <= 0:
        return None
    return sum(value * time for value, time in zip(known, times, strict=True)) / sum(times)


def _session(index: int, activity: _Activity) -> SessionData:
    if not activity.laps:
        raise semantic_invalid("NO_LAP")
    first, last = activity.laps[0], activity.laps[-1]
    data = SessionData(
        index=index, start=first.start, sport=activity.sport, utc_offset_minutes=first.offset
    )
    seconds = [lap.seconds for lap in activity.laps]
    timer = _all(seconds)
    distances = _all([lap.distance for lap in activity.laps])
    if last.seconds is not None:
        elapsed = (last.start - first.start).total_seconds() + last.seconds
        data.put("elapsedSeconds", elapsed, "Lap[last].StartTime+TotalTimeSeconds")
    if timer is not None:
        data.put("timerSeconds", sum(timer), "Lap[*].TotalTimeSeconds")
    if distances is not None:
        data.put("distanceMeters", sum(distances), "Lap[*].DistanceMeters")
        if timer is not None and sum(timer) > 0:
            data.put("avgSpeedMps", sum(distances) / sum(timer), "Lap[*].DistanceMeters")
    data.put(
        "avgHeartRate",
        _weighted([lap.avg_hr for lap in activity.laps], list(seconds)),
        "Lap[*].AverageHeartRateBpm",
    )
    max_hr = _all([lap.max_hr for lap in activity.laps])
    data.put("maxHeartRate", None if max_hr is None else max(max_hr), "Lap[*].MaximumHeartRateBpm")
    max_speed = _all([lap.max_speed for lap in activity.laps])
    data.put("maxSpeedMps", None if max_speed is None else max(max_speed), "Lap[*].MaximumSpeed")
    data.put(
        "avgCadence",
        _weighted([lap.cadence for lap in activity.laps], list(seconds)),
        "Lap[*].Cadence",
    )
    if activity.notes is not None:
        data.texts["description"] = activity.notes
    _laps(data, activity.laps)
    for point in activity.points:
        data.sample(
            (point.time - first.start).total_seconds(),
            distanceMeters=point.distance,
            heartRate=point.heart_rate,
            speedMps=point.speed,
            cadence=point.cadence,
            powerWatts=point.watts,
            altitudeMeters=point.altitude,
        )
    return data


def _label(value: str | None) -> str | None:
    text = (value or "").strip()
    return text[:64] if text else None


def _laps(data: SessionData, laps: list[_Lap]) -> None:
    """Each lap; its elapsed time runs to the next lap start (the last one's is unknown)."""
    first = laps[0].start
    for index, lap in enumerate(laps):
        following = laps[index + 1].start if index + 1 < len(laps) else None
        data.laps.append(
            {
                "startOffsetSeconds": (lap.start - first).total_seconds(),
                "elapsedSeconds": (
                    None if following is None else (following - lap.start).total_seconds()
                ),
                "timerSeconds": lap.seconds,
                "distanceMeters": lap.distance,
                "avgHeartRate": lap.avg_hr,
                "maxHeartRate": lap.max_hr,
                "avgCadence": lap.cadence,
                "avgPowerWatts": None,
                "intensity": _label(lap.intensity),
                "trigger": _label(lap.trigger),
            }
        )


def parse_tcx(path: Path, limits: ParserLimits) -> dict[str, Any]:
    counter = RecordCounter(limits.records)
    activities: list[_Activity] = []
    current: _Activity | None = None
    stack: list[Element] = []
    for event, element, depth in events(path, limits):
        name = local(element.tag)
        if event == "start":
            if depth == 1 and (
                name != "TrainingCenterDatabase" or namespace(element.tag) != TCX_NAMESPACE
            ):
                raise parse_failed("UNSUPPORTED_NAMESPACE")
            stack.append(element)
            if name == "Activity":
                current = _Activity(sport=element.get("Sport"))
            continue
        stack.pop()
        if current is None:
            continue
        if name == "Trackpoint":
            counter.add()
            current.points.append(_point(element))
            if stack:
                stack[-1].remove(element)
        elif name == "Lap":
            current.laps.append(_lap(element))
            element.clear()
        elif name == "Notes" and len(stack) >= 1 and local(stack[-1].tag) == "Activity":
            current.notes = element.text
        elif name == "Activity":
            activities.append(current)
            current = None
            element.clear()
    sessions = [_session(index, activity) for index, activity in enumerate(activities)]
    return output("TCX", PARSER, VERSION, records=counter.count, sessions=sessions, limits=limits)
