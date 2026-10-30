"""Comparable groups and trends, version 1.0.0 (spec/functional/metrics.spec.md section 11).

Pure functions: which comparable group a session belongs to, and the least-squares trend of
one group. Groups are never combined; interpretation belongs to insights and the AI coach.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final
from uuid import UUID

from coach.domain.activity.activity import Activity
from coach.domain.activity.fields import StrokeType
from coach.domain.common.quality import Confidence
from coach.domain.common.sport import Sport

MIN_TREND_SESSIONS: Final = 5
HIGH_CONFIDENCE_SESSIONS: Final = 10
HIGH_CONFIDENCE_T: Final = 2.5
MEDIUM_CONFIDENCE_T: Final = 2.0
FLAT_MAX_GAIN_PER_KM: Final = 10.0
ROLLING_MAX_GAIN_PER_KM: Final = 25.0


@dataclass(frozen=True, slots=True)
class Point:
    day: date
    value: float
    activity_id: UUID


@dataclass(frozen=True, slots=True)
class Trend:
    sessions: int
    slope_per_day: float
    change: float  # over the window
    relative_change_percent: float | None
    t_value: float
    confidence: Confidence


def terrain(view: Activity) -> str:
    """FLAT, ROLLING or HILLY from elevation gain per kilometre; UNKNOWN without both."""
    if view.values.get("classification.subtype") == "TREADMILL":
        return "FLAT"
    gain, meters = view.number("elevation.gainMeters"), view.number("distance.meters")
    if gain is None or meters is None or meters <= 0:
        return "UNKNOWN"
    per_km = gain / (meters / 1000.0)
    if per_km <= FLAT_MAX_GAIN_PER_KM:
        return "FLAT"
    return "ROLLING" if per_km <= ROLLING_MAX_GAIN_PER_KM else "HILLY"


def group_of(view: Activity, stroke: StrokeType | None = None) -> str:
    """The comparable group of a session (metrics spec 11), led by its sport."""
    subtype = view.values.get("classification.subtype")
    label = f"{view.sport.value} {'UNKNOWN' if subtype is None else subtype}"
    if view.sport is Sport.RUNNING:
        return label if subtype == "TREADMILL" else f"{label} {terrain(view)}"
    if view.sport is Sport.SWIMMING and subtype == "POOL":
        pool = view.number("environment.poolLengthMeters")
        label += " pool length unknown" if pool is None else f" {pool:g} m"
    if stroke is not None:
        label += f" {stroke.value}"
    return label


def trend(points: Sequence[Point], window_days: int) -> Trend | None:
    """Least-squares trend of a group; ``None`` with fewer than 5 sessions."""
    if len(points) < MIN_TREND_SESSIONS:
        return None
    first = min(point.day for point in points)
    xs = [float((point.day - first).days) for point in points]
    ys = [point.value for point in points]
    mean_x, mean_y = statistics.fmean(xs), statistics.fmean(ys)
    sxx = math.fsum((x - mean_x) ** 2 for x in xs)
    if sxx == 0:  # every session on one day: no slope to speak of
        return Trend(len(points), 0.0, 0.0, 0.0, 0.0, Confidence.LOW)
    slope = math.fsum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / sxx
    residuals = [y - (mean_y + slope * (x - mean_x)) for x, y in zip(xs, ys, strict=True)]
    variance = math.fsum(value**2 for value in residuals) / (len(points) - 2)
    error = math.sqrt(variance / sxx)
    if error == 0:
        t_value = 0.0 if slope == 0 else math.copysign(math.inf, slope)
    else:
        t_value = slope / error
    change = slope * window_days
    relative = None if mean_y == 0 else change / abs(mean_y) * 100.0
    return Trend(len(points), slope, change, relative, t_value, _confidence(len(points), t_value))


def _confidence(sessions: int, t_value: float) -> Confidence:
    if sessions >= HIGH_CONFIDENCE_SESSIONS and abs(t_value) >= HIGH_CONFIDENCE_T:
        return Confidence.HIGH
    if abs(t_value) >= MEDIUM_CONFIDENCE_T:
        return Confidence.MEDIUM
    return Confidence.LOW
