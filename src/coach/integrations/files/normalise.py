"""Semantic validation and normalisation rules 1.1.0 (stages 10 and 11; files spec 8).

Maps parser output in each format's own vocabulary to canonical field values, converting
units in code. Nothing is inferred that the file does not state; text stays untrusted.
1.1.0 (P9) adds laps, pool lengths and the power sensor (files spec 8.6).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from coach.application.files import DecodedSession, FileRejectedError
from coach.domain.activity.fields import (
    Cadence,
    CadenceUnit,
    PowerSensor,
    PowerSource,
    PowerSummary,
    ProviderMetric,
    StrokeType,
    check_subtype,
    check_values,
)
from coach.domain.activity.segments import (
    Lap,
    LapIntensity,
    Laps,
    LapTrigger,
    Lengths,
    LengthType,
    SwimLength,
)
from coach.domain.activity.streams import TimeSeries
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.provenance import MetricOrigin
from coach.domain.common.sport import Sport
from coach.domain.common.time import format_utc_offset
from coach.domain.ingestion.record import RejectionReason
from coach.domain.raw.source import DetectedType
from coach.integrations.files.output import ParsedFile, ParsedSegment, ParsedSession
from coach.metrics.quality import detect_gps_jumps

RULES_VERSION: Final = "1.1.0"
NORMALISERS: Final = {
    DetectedType.FIT: "fit",
    DetectedType.TCX: "tcx",
    DetectedType.GPX: "gpx",
    DetectedType.CSV: "csv_activity",
}
EARLIEST_START: Final = datetime(1990, 1, 1, tzinfo=UTC)

_FIT_SUBTYPES: Final[Mapping[str, Mapping[str, str | None]]] = {
    "running": {
        "road": "ROAD",
        "street": "ROAD",
        "trail": "TRAIL",
        "track": "TRACK",
        "treadmill": "TREADMILL",
        "indoor_running": "TREADMILL",
        "generic": None,
    },
    "cycling": {
        "road": "ROAD",
        "mountain": "MOUNTAIN",
        "downhill": "MOUNTAIN",
        "gravel_cycling": "GRAVEL",
        "cyclocross": "GRAVEL",
        "indoor_cycling": "INDOOR_TRAINER",
        "spin": "INDOOR_TRAINER",
        "virtual_activity": "INDOOR_TRAINER",
        "generic": None,
    },
    "swimming": {"lap_swimming": "POOL", "open_water": "OPEN_WATER"},
    "tennis": {"match": "MATCH"},
}
_FIT_OTHER_SUBTYPE: Final = {"running": "OTHER", "cycling": "OTHER"}
_FIT_SPORTS: Final = {
    "running": Sport.RUNNING,
    "cycling": Sport.CYCLING,
    "swimming": Sport.SWIMMING,
    "tennis": Sport.TENNIS,
}
_TCX_SPORTS: Final = {"running": Sport.RUNNING, "biking": Sport.CYCLING}
_GPX_SPORTS: Final = {
    "running": Sport.RUNNING,
    "run": Sport.RUNNING,
    "cycling": Sport.CYCLING,
    "biking": Sport.CYCLING,
    "ride": Sport.CYCLING,
    "swimming": Sport.SWIMMING,
    "swim": Sport.SWIMMING,
    "tennis": Sport.TENNIS,
    "strength_training": Sport.STRENGTH,
}
_STROKES: Final = {
    "freestyle": StrokeType.FREESTYLE,
    "backstroke": StrokeType.BACKSTROKE,
    "breaststroke": StrokeType.BREASTSTROKE,
    "butterfly": StrokeType.BUTTERFLY,
    "drill": StrokeType.DRILL,
    "mixed": StrokeType.MIXED,
    "im": StrokeType.MIXED,
    "im_by_round": StrokeType.MIXED,
}
_SUMMARY_FIELDS: Final = {
    "elapsedSeconds": "timing.elapsedSeconds",
    "movingSeconds": "timing.movingSeconds",
    "distanceMeters": "distance.meters",
    "ascentMeters": "elevation.gainMeters",
    "descentMeters": "elevation.lossMeters",
    "avgSpeedMps": "performance.averageSpeedMps",
    "maxSpeedMps": "performance.maxSpeedMps",
}
_HEART_RATE_FIELDS: Final = {
    "avgHeartRate": "cardiovascular.averageHr",
    "maxHeartRate": "cardiovascular.maxHr",
}
# Laps and lengths (files spec 8.6), per format vocabulary.
_INTENSITIES: Final[Mapping[DetectedType, Mapping[str, LapIntensity]]] = {
    DetectedType.FIT: {
        "active": LapIntensity.ACTIVE,
        "rest": LapIntensity.REST,
        "warmup": LapIntensity.WARMUP,
        "cooldown": LapIntensity.COOLDOWN,
        "recovery": LapIntensity.RECOVERY,
        "interval": LapIntensity.INTERVAL,
        "other": LapIntensity.OTHER,
    },
    DetectedType.TCX: {"Active": LapIntensity.ACTIVE, "Resting": LapIntensity.REST},
}
_TRIGGERS: Final[Mapping[DetectedType, Mapping[str, LapTrigger]]] = {
    DetectedType.FIT: {
        "manual": LapTrigger.MANUAL,
        "time": LapTrigger.TIME,
        "distance": LapTrigger.DISTANCE,
        "position_start": LapTrigger.POSITION,
        "position_lap": LapTrigger.POSITION,
        "position_waypoint": LapTrigger.POSITION,
        "position_marked": LapTrigger.POSITION,
        "session_end": LapTrigger.SESSION_END,
        "fitness_equipment": LapTrigger.OTHER,
    },
    DetectedType.TCX: {
        "Manual": LapTrigger.MANUAL,
        "Distance": LapTrigger.DISTANCE,
        "Location": LapTrigger.POSITION,
        "Time": LapTrigger.TIME,
        "HeartRate": LapTrigger.OTHER,
    },
}
_LENGTH_TYPES: Final = {"active": LengthType.ACTIVE, "idle": LengthType.IDLE}
# FIT device types that measure power (files spec 8.6); a dedicated meter wins.
_POWER_METERS: Final = frozenset({"bike_power"})
_SMART_TRAINERS: Final = frozenset({"fitness_equipment", "bike_trainer"})


def _semantic(detail: str) -> FileRejectedError:
    return FileRejectedError(RejectionReason.SEMANTIC_INVALID, detail)


def _normalisation(detail: str) -> FileRejectedError:
    return FileRejectedError(RejectionReason.NORMALIZATION_FAILED, detail)


def whole(value: float) -> int:
    """Round half up to whole units (heart rate, lengths)."""
    return math.floor(value + 0.5)


@dataclass(frozen=True, slots=True)
class _Classification:
    sport: Sport
    subtype: str | None
    provider_type: str | None


def _fit_classification(session: ParsedSession) -> _Classification:
    sport, sub = session.sport or "", session.sub_sport or ""
    label = f"fit:{sport or 'unknown'}/{sub or 'unknown'}"
    if sport == "training" and sub == "strength_training":
        return _Classification(Sport.STRENGTH, None, label)
    canonical = _FIT_SPORTS.get(sport, Sport.OTHER)
    subtypes = _FIT_SUBTYPES.get(sport, {})
    subtype = subtypes[sub] if sub in subtypes else (_FIT_OTHER_SUBTYPE.get(sport) if sub else None)
    return _Classification(canonical, subtype, label)


def _tcx_classification(session: ParsedSession) -> _Classification:
    sport = (session.sport or "").lower()
    label = None if session.sport is None else f"tcx:{session.sport}"
    return _Classification(_TCX_SPORTS.get(sport, Sport.OTHER), None, label)


def _gpx_classification(session: ParsedSession) -> _Classification:
    kind = (session.sport or "").strip().lower()
    label = None if session.sport is None else f"gpx:{session.sport}"
    return _Classification(_GPX_SPORTS.get(kind, Sport.OTHER), None, label)


def _csv_classification(session: ParsedSession) -> _Classification:
    try:
        sport = Sport(session.sport or "")
    except ValueError:
        raise _normalisation("UNKNOWN_SPORT") from None
    return _Classification(sport, session.labels.get("subtype"), None)


_CLASSIFIERS: Final[Mapping[DetectedType, Callable[[ParsedSession], _Classification]]] = {
    DetectedType.FIT: _fit_classification,
    DetectedType.TCX: _tcx_classification,
    DetectedType.GPX: _gpx_classification,
    DetectedType.CSV: _csv_classification,
}


def _check_session(session: ParsedSession, received_at: datetime) -> None:
    latest = datetime(received_at.year + 2, 1, 1, tzinfo=UTC)
    if not EARLIEST_START <= session.start < latest:
        raise _semantic("START_OUT_OF_RANGE")
    if any(value < 0 for value in session.summary.values()):
        raise _semantic("NEGATIVE_VALUE")
    previous = 0.0
    for offset in session.offsets:
        if offset < previous:
            raise _semantic("TIME_ORDER")
        previous = offset
    distance = 0.0
    for value in session.samples.get("distanceMeters", ()):
        if value is None:
            continue
        if value < distance:
            raise _semantic("DISTANCE_ORDER")
        distance = value


def _cadence_unit(fmt: DetectedType, sport: Sport) -> tuple[CadenceUnit, float] | None:
    """Canonical cadence unit and the factor from the file's unit (files spec 8.1 to 8.3)."""
    if sport is Sport.RUNNING and fmt is DetectedType.FIT:
        return CadenceUnit.STEPS_PER_MIN, 2.0  # FIT stores strides per minute
    if sport is Sport.CYCLING and fmt is not DetectedType.CSV:
        return CadenceUnit.RPM, 1.0
    return None


class _Builder:
    def __init__(self, session: ParsedSession) -> None:
        self.session = session
        self.values: dict[str, object] = {}
        self.paths: dict[str, str] = {}

    def put(self, path: str, value: object, source_key: str | None = None) -> None:
        if value is None:
            return
        self.values[path] = value
        location = None if source_key is None else self.session.source_paths.get(source_key)
        if location is not None:
            self.paths[path] = location


def _summary(builder: _Builder) -> None:
    summary = builder.session.summary
    for key, path in _SUMMARY_FIELDS.items():
        builder.put(path, summary.get(key), key)
    for key, path in _HEART_RATE_FIELDS.items():
        value = summary.get(key)
        builder.put(path, None if value is None else whole(value), key)


def _cadence(builder: _Builder, fmt: DetectedType, sport: Sport) -> None:
    unit = _cadence_unit(fmt, sport)
    average = builder.session.summary.get("avgCadence")
    maximum = builder.session.summary.get("maxCadence")
    if unit is None or (average is None and maximum is None):
        return
    canonical, factor = unit
    builder.put(
        "performance.cadence",
        Cadence(
            None if average is None else average * factor,
            None if maximum is None else maximum * factor,
            canonical,
        ),
        "avgCadence",
    )


def _fit_extras(builder: _Builder, sport: Sport, subtype: str | None) -> None:
    summary = builder.session.summary
    average, maximum = summary.get("avgPowerWatts"), summary.get("maxPowerWatts")
    if average is not None or maximum is not None:
        builder.put(
            "performance.power",
            PowerSummary(PowerSource.UNKNOWN, average, maximum),
            "avgPowerWatts",
        )
    work = summary.get("totalWorkJoules")
    builder.put(
        "training.load.external.workKilojoules",
        None if work is None else work / 1000,
        "totalWorkJoules",
    )
    manufacturer = builder.session.manufacturer
    if manufacturer is not None:
        for key, path, name in (
            ("trainingEffectAerobic", "training.aerobicEffect", "total training effect"),
            (
                "trainingEffectAnaerobic",
                "training.anaerobicEffect",
                "total anaerobic training effect",
            ),
        ):
            value = summary.get(key)
            if value is not None:
                metric = ProviderMetric(value, manufacturer, name, MetricOrigin.PROVIDER_DERIVED)
                builder.put(path, metric, key)
    if sport is Sport.SWIMMING:
        lengths = summary.get("lengthCount")
        builder.put(
            "swimming.lengthCount", None if lengths is None else whole(lengths), "lengthCount"
        )
        stroke = builder.session.labels.get("strokeType")
        builder.put("swimming.strokeType", _STROKES.get(stroke or ""), "strokeType")
        if subtype == "POOL":
            builder.put(
                "environment.poolLengthMeters", summary.get("poolLengthMeters"), "poolLengthMeters"
            )


def _power_sensor(builder: _Builder) -> None:
    session = builder.session
    has_power = (
        session.summary.get("avgPowerWatts") is not None
        or session.summary.get("maxPowerWatts") is not None
        or "powerWatts" in session.samples
    )
    if not has_power:
        return
    sensors = set(session.sensors)
    if sensors & _POWER_METERS:
        builder.put("performance.powerSensor", PowerSensor.POWER_METER, None)
    elif sensors & _SMART_TRAINERS:
        builder.put("performance.powerSensor", PowerSensor.SMART_TRAINER, None)


def _texts(builder: _Builder, fmt: DetectedType) -> None:
    texts = builder.session.texts
    builder.put("metadata.name", texts.get("name"), "name")
    builder.put("metadata.description", texts.get("description"), "description")
    if fmt is DetectedType.CSV:
        builder.put("subjective.rpe", builder.session.summary.get("rpe"), "rpe")
        builder.put("subjective.comments", texts.get("comments"), "comments")


def _time_series(session: ParsedSession, fmt: DetectedType, sport: Sport) -> TimeSeries | None:
    if not session.offsets:
        return None
    unit = _cadence_unit(fmt, sport)
    raw_cadence = session.samples.get("cadence")
    cadence = None
    if unit is not None and raw_cadence is not None:
        cadence = tuple(None if value is None else value * unit[1] for value in raw_cadence)
    try:
        return TimeSeries(
            start_time_utc=session.start,
            offset_seconds=session.offsets,
            distance_meters=session.samples.get("distanceMeters"),
            heart_rate_bpm=session.samples.get("heartRate"),
            speed_mps=session.samples.get("speedMps"),
            cadence=cadence,
            cadence_unit=None if cadence is None or unit is None else unit[0],
            power_watts=session.samples.get("powerWatts"),
            altitude_meters=session.samples.get("altitudeMeters"),
        )
    except DomainValidationError:
        raise _semantic("SAMPLE_RANGE") from None


def _kept(segments: tuple[ParsedSegment, ...]) -> list[ParsedSegment]:
    """Leave out laps and lengths without any time (a double press of the lap button)."""
    return [
        segment
        for segment in segments
        if (segment.numbers.get("elapsedSeconds") or 0) > 0
        or (segment.numbers.get("timerSeconds") or 0) > 0
    ]


def _inside(offsets: list[float], session: ParsedSession) -> bool:
    elapsed = session.summary.get("elapsedSeconds")
    return elapsed is None or all(offset <= elapsed for offset in offsets)


def _positive(value: float | None) -> float | None:
    """A duration of 0 is unknown here: the other time of the segment is the known one."""
    return value if value is not None and value > 0 else None


def _laps(session: ParsedSession, fmt: DetectedType, sport: Sport) -> Laps | None:
    kept = _kept(session.laps)
    if not kept or not _inside([lap.numbers["startOffsetSeconds"] for lap in kept], session):
        return None
    unit = _cadence_unit(fmt, sport)
    intensities, triggers = _INTENSITIES.get(fmt, {}), _TRIGGERS.get(fmt, {})
    laps = []
    try:
        for segment in kept:
            numbers, labels = segment.numbers, segment.labels
            cadence = numbers.get("avgCadence")
            laps.append(
                Lap(
                    start_offset_seconds=numbers["startOffsetSeconds"],
                    elapsed_seconds=_positive(numbers.get("elapsedSeconds")),
                    timer_seconds=_positive(numbers.get("timerSeconds")),
                    distance_meters=numbers.get("distanceMeters"),
                    average_hr=numbers.get("avgHeartRate"),
                    max_hr=numbers.get("maxHeartRate"),
                    average_cadence=None if unit is None or cadence is None else cadence * unit[1],
                    average_power_watts=numbers.get("avgPowerWatts"),
                    intensity=intensities.get(labels.get("intensity", "")),
                    trigger=triggers.get(labels.get("trigger", "")),
                )
            )
        with_cadence = any(lap.average_cadence is not None for lap in laps)
        return Laps(
            start_time_utc=session.start,
            laps=tuple(laps),
            cadence_unit=unit[0] if unit is not None and with_cadence else None,
        )
    except DomainValidationError:
        return None  # not stored; the session is still imported (files spec 8.6)


def _lengths(session: ParsedSession, sport: Sport) -> Lengths | None:
    kept = _kept(session.lengths)
    if sport is not Sport.SWIMMING or not kept:
        return None
    if not _inside([length.numbers["startOffsetSeconds"] for length in kept], session):
        return None
    try:
        return Lengths(
            start_time_utc=session.start,
            lengths=tuple(
                SwimLength(
                    start_offset_seconds=segment.numbers["startOffsetSeconds"],
                    elapsed_seconds=_positive(segment.numbers.get("elapsedSeconds")),
                    timer_seconds=_positive(segment.numbers.get("timerSeconds")),
                    type=_LENGTH_TYPES.get(segment.labels.get("type", "")),
                    strokes=_strokes(segment.numbers.get("strokes")),
                    stroke_type=_STROKES.get(segment.labels.get("strokeType", "")),
                )
                for segment in kept
            ),
        )
    except DomainValidationError:
        return None


def _strokes(value: float | None) -> int | None:
    return None if value is None else whole(value)


def normalise(parsed: ParsedFile, *, received_at: datetime) -> list[DecodedSession]:
    """Canonical sessions of a parsed file; rejects the whole file on any problem."""
    # The file type is checked first: wellness files (monitoring, sleep, metrics) have no
    # session either, and their type is the precise reason (files spec 8).
    if parsed.format is DetectedType.FIT and parsed.file_type != "activity":
        raise _semantic("NOT_AN_ACTIVITY")
    if not parsed.sessions:
        raise _semantic("NO_SESSION")
    decoded = []
    for part, session in enumerate(parsed.sessions):
        _check_session(session, received_at)
        classification = _CLASSIFIERS[parsed.format](session)
        builder = _Builder(session)
        builder.put("classification.sport", classification.sport, None)
        builder.put("classification.subtype", classification.subtype, "subtype")
        builder.put("classification.providerType", classification.provider_type, None)
        builder.put("timing.startTimeUtc", session.start, None)
        if session.utc_offset_minutes is not None:
            offset = format_utc_offset(timedelta(minutes=session.utc_offset_minutes))
            builder.put("timing.utcOffset", offset, None)
        _summary(builder)
        _cadence(builder, parsed.format, classification.sport)
        if parsed.format is DetectedType.FIT:
            _fit_extras(builder, classification.sport, classification.subtype)
            _power_sensor(builder)
        _texts(builder, parsed.format)
        try:
            values = check_values(builder.values)
            check_subtype(classification.sport, classification.subtype)
        except DomainValidationError:
            raise _normalisation("FIELD_VALUE") from None
        series = _time_series(session, parsed.format, classification.sport)
        anomalies = (
            ()
            if series is None
            else detect_gps_jumps(series.distance_samples(), classification.sport)
        )
        decoded.append(
            DecodedSession(
                part=part,
                values=values,
                normaliser=NORMALISERS[parsed.format],
                normaliser_version=RULES_VERSION,
                source_paths={
                    path: builder.paths[path] for path in values if path in builder.paths
                },
                anomalies=anomalies,
                time_series=series,
                laps=_laps(session, parsed.format, classification.sport),
                lengths=_lengths(session, classification.sport),
            )
        )
    return decoded
