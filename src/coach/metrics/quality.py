"""Activity data quality rules 1.0.0 (FR-ACT-009, FR-ACT-010).

Completeness, anomalies, reliability, confidence and uncertainty reasons are computed from an
activity's field values (spec/functional/activities.spec.md section 5.1). The parameters are
initial values to review with real data (assumption A-18).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from itertools import pairwise
from typing import Final

from coach.domain.activity.fields import (
    HrSensor,
    PowerSource,
    PowerSummary,
    instant_of,
    known_number,
    sport_of,
)
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.quality import (
    Anomaly,
    DataQuality,
    Reliability,
    UncertaintyReason,
    derive_confidence,
)
from coach.domain.common.sport import Sport
from coach.domain.common.time import TrainingDayBasis, ensure_utc

QUALITY_RULES_VERSION: Final = "1.0.0"


class AnomalyCode(StrEnum):
    HR_OUT_OF_RANGE = "HR_OUT_OF_RANGE"
    SPEED_IMPLAUSIBLE = "SPEED_IMPLAUSIBLE"
    DURATION_INCONSISTENT = "DURATION_INCONSISTENT"
    DISTANCE_GPS_JUMP = "DISTANCE_GPS_JUMP"
    POWER_OUT_OF_RANGE = "POWER_OUT_OF_RANGE"
    TIME_IN_FUTURE = "TIME_IN_FUTURE"


HR_MIN_BPM: Final = 25
HR_MAX_BPM: Final = 230
POWER_MAX_WATTS: Final = 2500.0
SPEED_LIMITS_MPS: Final[Mapping[Sport, float]] = {
    Sport.RUNNING: 12.0,
    Sport.CYCLING: 30.0,
    Sport.SWIMMING: 3.0,
}

_TIMES: Final = ("timing.elapsedSeconds", "timing.movingSeconds")
EXPECTED_FIELDS: Final[Mapping[Sport, frozenset[str]]] = {
    Sport.RUNNING: frozenset(
        {
            *_TIMES,
            "distance.meters",
            "elevation.gainMeters",
            "cardiovascular.averageHr",
            "cardiovascular.maxHr",
            "performance.averageSpeedMps",
            "performance.cadence",
        }
    ),
    Sport.CYCLING: frozenset(
        {
            *_TIMES,
            "distance.meters",
            "elevation.gainMeters",
            "cardiovascular.averageHr",
            "cardiovascular.maxHr",
            "performance.averageSpeedMps",
            "performance.power",
            "performance.cadence",
        }
    ),
    Sport.SWIMMING: frozenset(
        {
            *_TIMES,
            "distance.meters",
            "cardiovascular.averageHr",
            "performance.averageSpeedMps",
            "swimming.strokeType",
        }
    ),
    Sport.STRENGTH: frozenset(
        {
            "timing.elapsedSeconds",
            "cardiovascular.averageHr",
            "cardiovascular.maxHr",
            "subjective.rpe",
        }
    ),
    Sport.TENNIS: frozenset(
        {
            "timing.elapsedSeconds",
            "cardiovascular.averageHr",
            "cardiovascular.maxHr",
            "subjective.rpe",
        }
    ),
    Sport.OTHER: frozenset({"timing.elapsedSeconds", "cardiovascular.averageHr", "subjective.rpe"}),
}

BLOCKING: Final = frozenset(
    {
        (AnomalyCode.SPEED_IMPLAUSIBLE, "performance.averageSpeedMps"),
        (AnomalyCode.SPEED_IMPLAUSIBLE, "distance.meters"),
        (AnomalyCode.DURATION_INCONSISTENT, "timing.movingSeconds"),
        (AnomalyCode.TIME_IN_FUTURE, "timing.startTimeUtc"),
    }
)
_DOWNGRADE: Final = {
    Reliability.HIGH: Reliability.MEDIUM,
    Reliability.MEDIUM: Reliability.LOW,
    Reliability.LOW: Reliability.LOW,
    Reliability.UNKNOWN: Reliability.LOW,
}
_RANK: Final = {Reliability.LOW: 0, Reliability.MEDIUM: 1, Reliability.HIGH: 2}
_HR_EVIDENCE: Final = {
    HrSensor.CHEST_STRAP: Reliability.HIGH,
    HrSensor.OPTICAL: Reliability.MEDIUM,
    HrSensor.OTHER: Reliability.MEDIUM,
}
_POWER_EVIDENCE: Final = {
    PowerSource.MEASURED: Reliability.HIGH,
    PowerSource.ESTIMATED: Reliability.MEDIUM,
}


def is_blocking(anomaly: Anomaly) -> bool:
    return (anomaly.code, anomaly.field) in BLOCKING


def _figure(value: float) -> str:
    return f"{value:g}"


def _power(values: Mapping[str, object]) -> PowerSummary | None:
    power = values.get("performance.power")
    return power if isinstance(power, PowerSummary) else None


def _heart_rate_anomalies(values: Mapping[str, object]) -> list[Anomaly]:
    found = []
    for path in ("cardiovascular.averageHr", "cardiovascular.maxHr"):
        bpm = known_number(values, path)
        if bpm is not None and bpm < HR_MIN_BPM:
            detail = f"{_figure(bpm)} below {HR_MIN_BPM}"
            found.append(Anomaly(AnomalyCode.HR_OUT_OF_RANGE, path, detail))
        elif bpm is not None and bpm > HR_MAX_BPM:
            detail = f"{_figure(bpm)} above {HR_MAX_BPM}"
            found.append(Anomaly(AnomalyCode.HR_OUT_OF_RANGE, path, detail))
    return found


def _speed_anomalies(values: Mapping[str, object], sport: Sport) -> list[Anomaly]:
    limit = SPEED_LIMITS_MPS.get(sport)
    if limit is None:
        return []
    found = []
    for path in ("performance.averageSpeedMps", "performance.maxSpeedMps"):
        speed = known_number(values, path)
        if speed is not None and speed > limit:
            detail = f"{_figure(speed)} m/s above {_figure(limit)}"
            found.append(Anomaly(AnomalyCode.SPEED_IMPLAUSIBLE, path, detail))
    distance = known_number(values, "distance.meters")
    duration = known_number(values, "timing.movingSeconds")
    if duration is None:
        duration = known_number(values, "timing.elapsedSeconds")
    if distance is not None and duration and distance / duration > limit:
        detail = f"{_figure(round(distance / duration, 3))} m/s above {_figure(limit)}"
        found.append(Anomaly(AnomalyCode.SPEED_IMPLAUSIBLE, "distance.meters", detail))
    return found


def detect_anomalies(values: Mapping[str, object], *, imported_at: datetime) -> tuple[Anomaly, ...]:
    """Plausibility anomalies of summary values (rules 1.0.0)."""
    found = _heart_rate_anomalies(values)
    found.extend(_speed_anomalies(values, sport_of(values)))
    elapsed = known_number(values, "timing.elapsedSeconds")
    moving = known_number(values, "timing.movingSeconds")
    if elapsed is not None and moving is not None and moving > elapsed:
        detail = f"moving {_figure(moving)} s above elapsed {_figure(elapsed)} s"
        found.append(Anomaly(AnomalyCode.DURATION_INCONSISTENT, "timing.movingSeconds", detail))
    power = _power(values)
    if power is not None:
        peak = max((watts for watts in (power.average_watts, power.max_watts) if watts), default=0)
        if peak > POWER_MAX_WATTS:
            detail = f"{_figure(peak)} W above {_figure(POWER_MAX_WATTS)}"
            found.append(Anomaly(AnomalyCode.POWER_OUT_OF_RANGE, "performance.power", detail))
    if instant_of(values, "timing.startTimeUtc") > ensure_utc(imported_at):
        found.append(Anomaly(AnomalyCode.TIME_IN_FUTURE, "timing.startTimeUtc", "after import"))
    return tuple(found)


def detect_gps_jumps(samples: Sequence[tuple[float, float]], sport: Sport) -> tuple[Anomaly, ...]:
    """DISTANCE_GPS_JUMP when a segment of ``(seconds, cumulative metres)`` samples is faster
    than the sport's limit; one anomaly per activity, with the number of segments."""
    limit = SPEED_LIMITS_MPS.get(sport)
    if limit is None:
        return ()
    ordered = sorted(samples)
    jumps = 0
    for (start, from_meters), (end, to_meters) in pairwise(ordered):
        gained = to_meters - from_meters
        elapsed = end - start
        if gained > 0 and (elapsed <= 0 or gained / elapsed > limit):
            jumps += 1
    if not jumps:
        return ()
    detail = f"{jumps} segments above {_figure(limit)} m/s"
    return (Anomaly(AnomalyCode.DISTANCE_GPS_JUMP, "distance.meters", detail),)


def completeness(values: Mapping[str, object]) -> float:
    expected = EXPECTED_FIELDS[sport_of(values)]
    known = sum(1 for path in expected if values.get(path) is not None)
    return round(known / len(expected), 4)


def reliability(values: Mapping[str, object], *, anomaly_count: int) -> Reliability:
    evidence = []
    sensor = values.get("cardiovascular.hrSensor")
    if isinstance(sensor, HrSensor):
        evidence.append(_HR_EVIDENCE[sensor])
    power = _power(values)
    if power is not None and power.source in _POWER_EVIDENCE:
        evidence.append(_POWER_EVIDENCE[power.source])
    base = min(evidence, key=_RANK.__getitem__) if evidence else Reliability.UNKNOWN
    if anomaly_count >= 2:
        return Reliability.LOW
    if anomaly_count == 1:
        return _DOWNGRADE[base]
    return base


def assess(
    values: Mapping[str, object],
    *,
    imported_at: datetime,
    source_anomalies: Iterable[Anomaly] = (),
) -> DataQuality:
    """Quality of an activity's field values under rules 1.0.0."""
    if "classification.sport" not in values:
        raise DomainValidationError("quality needs the activity's sport")
    anomalies = sorted(
        {*detect_anomalies(values, imported_at=imported_at), *source_anomalies},
        key=lambda anomaly: (anomaly.code, anomaly.field, anomaly.detail),
    )
    share = completeness(values)
    level = reliability(values, anomaly_count=len(anomalies))
    reasons = set()
    if share < 1:
        reasons.add(UncertaintyReason.MISSING_DATA)
    if anomalies or level is Reliability.LOW:
        reasons.add(UncertaintyReason.SENSOR_QUALITY)
    power = _power(values)
    estimated_power = power is not None and power.source is PowerSource.ESTIMATED
    estimated_day = values.get("timing.trainingDayBasis") is (
        TrainingDayBasis.ATHLETE_TIMEZONE_ESTIMATED
    )
    if estimated_day or estimated_power:
        reasons.add(UncertaintyReason.ESTIMATED_VALUE)
    return DataQuality(
        completeness=share,
        reliability=level,
        confidence=derive_confidence(
            completeness=share,
            reliability=level,
            anomalies=len(anomalies),
            blocking_anomaly=any(is_blocking(anomaly) for anomaly in anomalies),
        ),
        anomalies=tuple(anomalies),
        uncertainty_reasons=frozenset(reasons),
    )
