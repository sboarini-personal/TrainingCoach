"""Cycling session analyses, version 1.0.0 (spec/functional/cycling.spec.md section 6).

Pure functions over the time series and laps of one ride with MEASURED power; the engine
checks that the power is measured before calling them. Sums use ``math.fsum``.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Final

from coach.domain.activity.activity import Activity
from coach.domain.activity.fields import PowerSource, PowerSummary
from coach.domain.activity.segments import LapIntensity, Laps
from coach.domain.activity.streams import TimeSeries
from coach.metrics.analyses import Analysis
from coach.metrics.zones import ZoneTimes, time_in_zones

MAX_SAMPLE_GAP_SECONDS: Final = 20.0
MIN_POWER_SECONDS: Final = 1200.0
JOULES_PER_KILOJOULE: Final = 1000.0
# Allen and Coggan (2010) zones in percent of FTP; the top zone is open. Whole percents
# keep the bounds exact (0.55 x 200 is 110.00000000000001 in floating point).
POWER_ZONE_PERCENTS: Final = (0, 55, 75, 90, 105, 120, 150)
POWER_ZONES: Final = len(POWER_ZONE_PERCENTS)
MIN_REPETITIONS: Final = 3
REPETITION_TIME_TOLERANCE: Final = 0.15
_WORK: Final = frozenset({LapIntensity.ACTIVE, LapIntensity.INTERVAL})
_RECOVERY: Final = frozenset({LapIntensity.REST, LapIntensity.RECOVERY})


def measured_power(view: Activity) -> bool:
    """Whether the EFFECTIVE view's power is MEASURED (cycling spec 2; AT-CYC-001)."""
    power = view.values.get("performance.power")
    return isinstance(power, PowerSummary) and power.source is PowerSource.MEASURED


@dataclass(frozen=True, slots=True)
class _Span:
    seconds: float
    watts: float


def _power_spans(series: TimeSeries) -> list[_Span]:
    """Intervals of at most 20 s with a known power at their start (coasting at 0 W counts)."""
    power = series.power_watts
    if power is None:
        return []
    offsets = series.offset_seconds
    spans = []
    for index in range(len(offsets) - 1):
        seconds = offsets[index + 1] - offsets[index]
        watts = power[index]
        if 0 < seconds <= MAX_SAMPLE_GAP_SECONDS and watts is not None:
            spans.append(_Span(seconds, watts))
    return spans


def work_kilojoules(series: TimeSeries) -> float | None:
    """Mechanical work: the sum of power x duration, in kJ; ``None`` without power."""
    if series.power_watts is None:
        return None
    return math.fsum(span.watts * span.seconds for span in _power_spans(series)) / (
        JOULES_PER_KILOJOULE
    )


def power_halves(series: TimeSeries) -> tuple[float, float] | None:
    """Mean power of the two halves of the time with a known power; ``None`` when shorter
    than 1,200 s."""
    spans = _power_spans(series)
    total = math.fsum(span.seconds for span in spans)
    if total < MIN_POWER_SECONDS:
        return None
    half = total / 2
    sums: list[list[float]] = [[], []]
    times: list[list[float]] = [[], []]
    filled = 0.0
    for span in spans:
        first_part = min(span.seconds, max(half - filled, 0.0))
        second_part = span.seconds - first_part
        for index, seconds in ((0, first_part), (1, second_part)):
            if seconds > 0:
                times[index].append(seconds)
                sums[index].append(span.watts * seconds)
        filled += span.seconds
    first = math.fsum(sums[0]) / math.fsum(times[0])
    second = math.fsum(sums[1]) / math.fsum(times[1])
    return first, second


def power_zone_times(series: TimeSeries, ftp_watts: float) -> ZoneTimes | None:
    """Time in the 7 power zones (cycling spec 6.1); ``None`` without a power column."""
    if series.power_watts is None:
        return None
    boundaries = [ftp_watts * percent / 100 for percent in POWER_ZONE_PERCENTS]
    return time_in_zones(
        series.offset_seconds,
        series.power_watts,
        [*boundaries, math.inf],
        max_gap_seconds=MAX_SAMPLE_GAP_SECONDS,
    )


def repetition_powers(laps: Laps) -> list[float] | None:
    """Average power of the comparable work laps of a structured session, else ``None``."""
    if not any(lap.intensity in _RECOVERY for lap in laps.laps):
        return None
    work = [lap for lap in laps.laps if lap.intensity in _WORK]
    if len(work) < MIN_REPETITIONS:
        return None
    timers = [lap.timer_seconds for lap in work]
    powers = [lap.average_power_watts for lap in work]
    known_timers = [value for value in timers if value is not None]
    known_powers = [value for value in powers if value is not None]
    if len(known_timers) != len(work) or len(known_powers) != len(work):
        return None
    median = statistics.median(known_timers)
    if any(abs(value - median) > REPETITION_TIME_TOLERANCE * median for value in known_timers):
        return None
    return known_powers


# --------------------------------------------------------------------------- analyses


def work(series: TimeSeries) -> Analysis:
    """Inputs of ``cyc.work_kj``; a series without power is UNKNOWN."""
    return Analysis({"work_kilojoules": work_kilojoules(series)})


def fade(series: TimeSeries) -> Analysis | None:
    """Inputs of ``cyc.power_fade``; ``None`` when the ride is too short."""
    if series.power_watts is None:
        return Analysis({"first_half_watts": None, "second_half_watts": None})
    halves = power_halves(series)
    if halves is None:
        return None
    return Analysis({"first_half_watts": halves[0], "second_half_watts": halves[1]})


def interval_consistency(laps: Laps) -> Analysis | None:
    """Inputs of ``cyc.interval_power_consistency``; ``None`` without comparable reps."""
    powers = repetition_powers(laps)
    if powers is None:
        return None
    return Analysis(
        {
            "repetition_mean_watts": statistics.fmean(powers),
            "repetition_stdev_watts": statistics.pstdev(powers),
        }
    )
