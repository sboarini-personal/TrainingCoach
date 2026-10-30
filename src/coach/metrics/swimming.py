"""Pool swimming session analyses, version 1.0.0 (spec/functional/swimming.spec.md section 5).

Pure functions over the pool lengths of one session; the engine supplies the pool length.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from coach.domain.activity.fields import StrokeType
from coach.domain.activity.segments import Lengths, LengthType, SwimLength
from coach.metrics.subjects import STROKES

MIN_STROKE_LENGTHS: Final = 4
MIN_SET_LENGTHS: Final = 6
EQUAL_PACE_PERCENT: Final = 3.0


@dataclass(frozen=True, slots=True)
class ValidLength:
    seconds: float
    strokes: int
    stroke: StrokeType


@dataclass(frozen=True, slots=True)
class StrokeStats:
    """A stroke type's valid lengths of the session."""

    lengths: int
    strokes: int
    mean_seconds: float
    stdev_seconds: float
    mean_strokes: float


@dataclass(frozen=True, slots=True)
class Thirds:
    """First and last k lengths of the qualifying sets, pooled (seconds or strokes)."""

    first: float
    last: float

    @property
    def change_percent(self) -> float:
        return (self.last - self.first) / self.first * 100.0


def _valid(length: SwimLength) -> ValidLength | None:
    seconds = length.timer_seconds or length.elapsed_seconds
    if length.type is not LengthType.ACTIVE or seconds is None or not length.strokes:
        return None  # a length without strokes (a kick length) says nothing of a stroke
    stroke = length.stroke_type
    if stroke is None or stroke not in STROKES:
        return None
    return ValidLength(seconds, length.strokes, stroke)


def by_stroke(lengths: Lengths) -> dict[StrokeType, StrokeStats]:
    """Statistics of each stroke type with at least 4 valid lengths."""
    grouped: dict[StrokeType, list[ValidLength]] = {}
    for length in lengths.lengths:
        valid = _valid(length)
        if valid is not None:
            grouped.setdefault(valid.stroke, []).append(valid)
    stats = {}
    for stroke, items in grouped.items():
        if len(items) < MIN_STROKE_LENGTHS:
            continue
        seconds = [item.seconds for item in items]
        strokes = [item.strokes for item in items]
        stats[stroke] = StrokeStats(
            lengths=len(items),
            strokes=sum(strokes),
            mean_seconds=statistics.fmean(seconds),
            stdev_seconds=statistics.pstdev(seconds),
            mean_strokes=statistics.fmean(strokes),
        )
    return stats


def sets(lengths: Lengths) -> list[list[ValidLength]]:
    """Runs of valid lengths of one stroke, ended by a rest, a change of stroke or any
    length that is not valid."""
    found: list[list[ValidLength]] = []
    current: list[ValidLength] = []
    for length in lengths.lengths:
        valid = _valid(length)
        if valid is None or (current and valid.stroke is not current[-1].stroke):
            if current:
                found.append(current)
            current = [] if valid is None else [valid]
            continue
        current.append(valid)
    if current:
        found.append(current)
    return [item for item in found if len(item) >= MIN_SET_LENGTHS]


def _pooled(groups: Sequence[list[ValidLength]], value: str) -> Thirds | None:
    first: list[float] = []
    last: list[float] = []
    for group in groups:
        k = len(group) // 3
        first.extend(float(getattr(item, value)) for item in group[:k])
        last.extend(float(getattr(item, value)) for item in group[-k:])
    if not first or statistics.fmean(first) <= 0:
        return None
    return Thirds(statistics.fmean(first), statistics.fmean(last))


def pace_degradation(lengths: Lengths) -> Thirds | None:
    """Length times of the first and last thirds of the qualifying sets, pooled."""
    return _pooled(sets(lengths), "seconds")


def technical_fatigue(lengths: Lengths) -> Thirds | None:
    """Strokes of the first and last thirds of the qualifying sets swum at an equal pace."""
    steady = [
        group
        for group in sets(lengths)
        if (pace := _pooled([group], "seconds")) is not None
        and abs(pace.change_percent) <= EQUAL_PACE_PERCENT
    ]
    return _pooled(steady, "strokes")
