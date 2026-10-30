"""Running session analyses, version 1.0.0 (spec/functional/running.spec.md section 4).

Pure functions over the time series and laps of one session; the metrics engine turns
their results into derived values. Sums use ``math.fsum``.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from coach.domain.activity.segments import LapIntensity, Laps
from coach.domain.activity.streams import TimeSeries
from coach.domain.common.quality import UncertaintyReason
from coach.metrics.analyses import Analysis

MAX_SAMPLE_GAP_SECONDS: Final = 20.0
MIN_MOVING_SPEED_MPS: Final = 1.0
WARMUP_SECONDS: Final = 600.0
MIN_WINDOW_SECONDS: Final = 1200.0
BLOCK_SECONDS: Final = 300.0
MAX_BLOCK_SPEED_CV: Final = 0.10
MIN_HR_COVERAGE: Final = 0.9
ALTITUDE_DIFFERENCE_METERS: Final = 20.0
SPLIT_METERS: Final = 1000.0
MIN_SPLITS: Final = 3
MIN_REPETITIONS: Final = 3
REPETITION_DISTANCE_TOLERANCE: Final = 0.15
_WORK: Final = frozenset({LapIntensity.ACTIVE, LapIntensity.INTERVAL})
_RECOVERY: Final = frozenset({LapIntensity.REST, LapIntensity.RECOVERY})


@dataclass(frozen=True, slots=True)
class Interval:
    """The span between two consecutive samples: moving time, distance, values at its start."""

    offset: float
    seconds: float
    meters: float
    heart_rate: float | None
    altitude: float | None


def moving_intervals(series: TimeSeries) -> list[Interval]:
    """Intervals at most 20 s long covered at 1 m/s or more (running spec 4.1).

    The distance comes from the cumulative distance column, else from the speed column; an
    interval whose distance is unknown is left out.
    """
    distances, speeds = series.distance_meters, series.speed_mps
    heart_rates, altitudes = series.heart_rate_bpm, series.altitude_meters
    intervals = []
    offsets = series.offset_seconds
    for index in range(len(offsets) - 1):
        seconds = offsets[index + 1] - offsets[index]
        if not 0 < seconds <= MAX_SAMPLE_GAP_SECONDS:
            continue
        meters = None
        if distances is not None:
            start, end = distances[index], distances[index + 1]
            if start is not None and end is not None:
                meters = end - start
        speed = None if speeds is None else speeds[index]
        if meters is None and speed is not None:
            meters = speed * seconds
        if meters is None or meters / seconds < MIN_MOVING_SPEED_MPS:
            continue
        intervals.append(
            Interval(
                offset=offsets[index],
                seconds=seconds,
                meters=meters,
                heart_rate=None if heart_rates is None else heart_rates[index],
                altitude=None if altitudes is None else altitudes[index],
            )
        )
    return intervals


@dataclass(slots=True)
class _Chunk:
    seconds: list[float]
    meters: list[float]
    heart_seconds: list[float]
    heart_beats: list[float]
    altitude_seconds: list[float]
    altitude_sum: list[float]

    @classmethod
    def empty(cls) -> _Chunk:
        return cls([], [], [], [], [], [])

    def add(self, interval: Interval, fraction: float) -> None:
        seconds = interval.seconds * fraction
        self.seconds.append(seconds)
        self.meters.append(interval.meters * fraction)
        if interval.heart_rate is not None:
            self.heart_seconds.append(seconds)
            self.heart_beats.append(interval.heart_rate * seconds)
        if interval.altitude is not None:
            self.altitude_seconds.append(seconds)
            self.altitude_sum.append(interval.altitude * seconds)

    @property
    def time(self) -> float:
        return math.fsum(self.seconds)

    @property
    def speed(self) -> float:
        return math.fsum(self.meters) / self.time

    @property
    def heart_rate(self) -> float | None:
        known = math.fsum(self.heart_seconds)
        return None if known == 0 else math.fsum(self.heart_beats) / known

    @property
    def heart_coverage(self) -> float:
        return math.fsum(self.heart_seconds) / self.time

    @property
    def altitude(self) -> float | None:
        known = math.fsum(self.altitude_seconds)
        return None if known < MIN_HR_COVERAGE * self.time else math.fsum(self.altitude_sum) / known


def _chunks(intervals: Sequence[Interval], size: float) -> list[_Chunk]:
    """Consecutive chunks of ``size`` seconds of moving time; a last shorter one is dropped."""
    chunks: list[_Chunk] = []
    current, filled = _Chunk.empty(), 0.0
    for interval in intervals:
        remaining = 1.0
        while remaining > 0:
            room = (size - filled) / interval.seconds
            used = min(remaining, room)
            current.add(interval, used)
            filled += interval.seconds * used
            remaining -= used
            if filled >= size - 1e-9:
                chunks.append(current)
                current, filled = _Chunk.empty(), 0.0
    return chunks


def _halves(intervals: Sequence[Interval], total: float) -> tuple[_Chunk, _Chunk]:
    """The first and second half of the moving time; an interval at the cut is split."""
    first, second = _Chunk.empty(), _Chunk.empty()
    filled = 0.0
    for interval in intervals:
        room = total / 2 - filled
        if room <= 0:
            second.add(interval, 1.0)
        elif interval.seconds <= room:
            first.add(interval, 1.0)
        else:
            fraction = room / interval.seconds
            first.add(interval, fraction)
            second.add(interval, 1.0 - fraction)
        filled += interval.seconds
    return first, second


@dataclass(frozen=True, slots=True)
class Halves:
    first_speed: float
    first_heart_rate: float | None
    first_coverage: float
    second_speed: float
    second_heart_rate: float | None
    second_coverage: float
    altitude_difference: float | None


def steady_halves(series: TimeSeries) -> Halves | None:
    """The two halves of a steady run's analysis window; ``None`` when it does not qualify."""
    window = [item for item in moving_intervals(series) if item.offset >= WARMUP_SECONDS]
    total = math.fsum(item.seconds for item in window)
    if total < MIN_WINDOW_SECONDS:
        return None
    blocks = _chunks(window, BLOCK_SECONDS)
    speeds = [block.speed for block in blocks]
    if len(speeds) < 2 or statistics.pstdev(speeds) / statistics.fmean(speeds) > MAX_BLOCK_SPEED_CV:
        return None
    first, second = _halves(window, total)
    altitudes = (first.altitude, second.altitude)
    difference = None
    if altitudes[0] is not None and altitudes[1] is not None:
        difference = altitudes[1] - altitudes[0]
    return Halves(
        first_speed=first.speed,
        first_heart_rate=first.heart_rate,
        first_coverage=first.heart_coverage,
        second_speed=second.speed,
        second_heart_rate=second.heart_rate,
        second_coverage=second.heart_coverage,
        altitude_difference=difference,
    )


def kilometre_splits(series: TimeSeries) -> list[float]:
    """Moving time of each whole kilometre (running spec 4.2), interpolated at the marks."""
    splits: list[float] = []
    covered = elapsed = previous = 0.0
    mark = SPLIT_METERS
    for interval in moving_intervals(series):
        start_meters, start_seconds = covered, elapsed
        covered += interval.meters
        elapsed += interval.seconds
        while covered >= mark:
            fraction = (mark - start_meters) / interval.meters
            at = start_seconds + fraction * interval.seconds
            splits.append(at - previous)
            previous = at
            mark += SPLIT_METERS
    return splits


def structured_intervals(laps: Laps) -> bool:
    """A structured interval session: a REST or RECOVERY lap and at least 3 work laps (4.3)."""
    work = sum(lap.intensity in _WORK for lap in laps.laps)
    return work >= MIN_REPETITIONS and any(lap.intensity in _RECOVERY for lap in laps.laps)


def repetition_speeds(laps: Laps) -> list[float] | None:
    """Speeds of the comparable work laps of a structured interval session, else ``None``."""
    if not structured_intervals(laps):
        return None
    work = [lap for lap in laps.laps if lap.intensity in _WORK]
    distances = [lap.distance_meters for lap in work]
    timers = [lap.timer_seconds for lap in work]
    if any(value is None or value <= 0 for value in distances) or any(
        value is None for value in timers
    ):
        return None
    known = [value for value in distances if value is not None]
    median = statistics.median(known)
    if any(abs(value - median) > REPETITION_DISTANCE_TOLERANCE * median for value in known):
        return None
    return [
        distance / timer for distance, timer in zip(known, timers, strict=True) if timer is not None
    ]


def first_and_last(speeds: Sequence[float]) -> tuple[float, float]:
    """Mean speed of the first k and of the last k repetitions, k = max(1, n // 3)."""
    k = max(1, len(speeds) // 3)
    return statistics.fmean(speeds[:k]), statistics.fmean(speeds[-k:])


# --------------------------------------------------------------------------- analyses

HR_DRIFT_INPUTS: Final = (
    "first_half_speed_mps",
    "first_half_hr_bpm",
    "second_half_speed_mps",
    "second_half_hr_bpm",
)


def _without_distance(series: TimeSeries) -> bool:
    return series.distance_meters is None and series.speed_mps is None


def hr_drift(series: TimeSeries) -> Analysis | None:
    """Inputs of ``run.hr_drift`` (running spec 4.1); ``None`` when the run does not qualify."""
    if _without_distance(series):
        return Analysis(dict.fromkeys(HR_DRIFT_INPUTS))
    halves = steady_halves(series)
    if halves is None:
        return None
    reasons = set()
    if min(halves.first_coverage, halves.second_coverage) < MIN_HR_COVERAGE:
        reasons.add(UncertaintyReason.MISSING_DATA)
    difference = halves.altitude_difference
    if difference is not None and abs(difference) > ALTITUDE_DIFFERENCE_METERS:
        reasons.add(UncertaintyReason.ENVIRONMENT)
    values = (
        halves.first_speed,
        halves.first_heart_rate,
        halves.second_speed,
        halves.second_heart_rate,
    )
    return Analysis(dict(zip(HR_DRIFT_INPUTS, values, strict=True)), frozenset(reasons))


def pace_variability(series: TimeSeries) -> Analysis | None:
    """Inputs of ``run.pace_variability`` (running spec 4.2)."""
    if _without_distance(series):
        return Analysis({"split_mean_seconds": None, "split_stdev_seconds": None})
    splits = kilometre_splits(series)
    if len(splits) < MIN_SPLITS:
        return None
    return Analysis(
        {
            "split_mean_seconds": statistics.fmean(splits),
            "split_stdev_seconds": statistics.pstdev(splits),
        }
    )


def interval_degradation(laps: Laps) -> Analysis | None:
    """Inputs of ``run.interval_degradation`` (running spec 4.3)."""
    speeds = repetition_speeds(laps)
    if speeds is None:
        return None
    first, last = first_and_last(speeds)
    return Analysis({"first_reps_speed_mps": first, "last_reps_speed_mps": last})
