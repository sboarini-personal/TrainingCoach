"""GPX parser (runs in the sandbox): GPX 1.1 or 1.0 tracks (files spec 8.3).

Distance is the great-circle (haversine) sum within each segment; heart rate and cadence come
only from Garmin TrackPointExtension namespaces. Positions are used for distance only and are
not part of the output.
"""

from __future__ import annotations

import math
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

PARSER: Final = "gpx"
VERSION: Final = "1.1.0"
GPX_NAMESPACES: Final = frozenset(
    {"http://www.topografix.com/GPX/1/1", "http://www.topografix.com/GPX/1/0"}
)
EXTENSION_NAMESPACES: Final = frozenset(
    {
        "http://www.garmin.com/xmlschemas/TrackPointExtension/v1",
        "http://www.garmin.com/xmlschemas/TrackPointExtension/v2",
    }
)
EARTH_RADIUS_METERS: Final = 6_371_008.8


@dataclass(slots=True)
class _Point:
    time: datetime
    offset: int | None
    latitude: float
    longitude: float
    altitude: float | None
    heart_rate: float | None
    cadence: float | None
    segment: int


@dataclass(slots=True)
class _Track:
    name: str | None = None
    kind: str | None = None
    points: list[_Point] = field(default_factory=list)
    segments: int = 0


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_METERS * math.asin(min(1.0, math.sqrt(a)))


def _coordinate(text: str | None, limit: float) -> float:
    value = text_number(text)
    if value is None or abs(value) > limit:
        raise parse_failed("INVALID_COORDINATE")
    return value


def _extension_value(element: Element, name: str) -> float | None:
    for child in element.iter():
        if local(child.tag) == name and namespace(child.tag) in EXTENSION_NAMESPACES:
            return text_number(child.text)
    return None


def _point(element: Element, segment: int) -> _Point:
    time, offset = parse_time(child_text(element, "time"))
    return _Point(
        time=time,
        offset=offset,
        latitude=_coordinate(element.get("lat"), 90.0),
        longitude=_coordinate(element.get("lon"), 180.0),
        altitude=text_number(child_text(element, "ele")),
        heart_rate=_extension_value(element, "hr"),
        cadence=_extension_value(element, "cad"),
        segment=segment,
    )


def _weights(points: list[_Point]) -> list[float]:
    """Time to the next point of the same segment; 0 for the last point of a segment."""
    weights = []
    for current, following in zip(points, [*points[1:], None], strict=True):
        if following is None or following.segment != current.segment:
            weights.append(0.0)
        else:
            weights.append((following.time - current.time).total_seconds())
    return weights


def _weighted_mean(values: list[float | None], weights: list[float]) -> float | None:
    pairs = [
        (value, weight) for value, weight in zip(values, weights, strict=True) if value is not None
    ]
    if not pairs:
        return None
    total = sum(weight for _, weight in pairs)
    if total <= 0:
        return sum(value for value, _ in pairs) / len(pairs)
    return sum(value * weight for value, weight in pairs) / total


def _session(index: int, track: _Track) -> SessionData:
    if not track.points:
        raise semantic_invalid("EMPTY_TRACK")
    points = track.points
    first, last = points[0], points[-1]
    data = SessionData(
        index=index, start=first.time, sport=track.kind, utc_offset_minutes=first.offset
    )
    if track.name is not None:
        data.texts["name"] = track.name
    distance = 0.0
    previous: _Point | None = None
    for point in points:
        if previous is not None and previous.segment == point.segment:
            distance += haversine(
                previous.latitude, previous.longitude, point.latitude, point.longitude
            )
        data.sample(
            (point.time - first.time).total_seconds(),
            distanceMeters=distance,
            heartRate=point.heart_rate,
            cadence=point.cadence,
            altitudeMeters=point.altitude,
        )
        previous = point
    data.put("elapsedSeconds", (last.time - first.time).total_seconds(), "trk/trkseg/trkpt/time")
    data.put("distanceMeters", distance, "trk/trkseg/trkpt[@lat,@lon] (haversine)")
    weights = _weights(points)
    heart_rates = [point.heart_rate for point in points]
    cadences = [point.cadence for point in points]
    data.put("avgHeartRate", _weighted_mean(heart_rates, weights), "TrackPointExtension/hr")
    known_hr = [value for value in heart_rates if value is not None]
    data.put("maxHeartRate", max(known_hr) if known_hr else None, "TrackPointExtension/hr")
    data.put("avgCadence", _weighted_mean(cadences, weights), "TrackPointExtension/cad")
    known_cad = [value for value in cadences if value is not None]
    data.put("maxCadence", max(known_cad) if known_cad else None, "TrackPointExtension/cad")
    return data


def parse_gpx(path: Path, limits: ParserLimits) -> dict[str, Any]:
    counter = RecordCounter(limits.records)
    tracks: list[_Track] = []
    current: _Track | None = None
    stack: list[Element] = []
    for event, element, depth in events(path, limits):
        name = local(element.tag)
        if event == "start":
            if depth == 1 and (name != "gpx" or namespace(element.tag) not in GPX_NAMESPACES):
                raise parse_failed("UNSUPPORTED_NAMESPACE")
            stack.append(element)
            if name == "trk":
                current = _Track()
            elif name == "trkseg" and current is not None:
                current.segments += 1
            continue
        stack.pop()
        if current is None:
            continue
        parent = local(stack[-1].tag) if stack else ""
        if name == "trkpt":
            counter.add()
            current.points.append(_point(element, current.segments))
            stack[-1].remove(element)
        elif name == "name" and parent == "trk":
            current.name = element.text
        elif name == "type" and parent == "trk":
            current.kind = element.text
        elif name == "trk":
            tracks.append(current)
            current = None
            element.clear()
    sessions = [_session(index, track) for index, track in enumerate(tracks)]
    return output("GPX", PARSER, VERSION, records=counter.count, sessions=sessions, limits=limits)
