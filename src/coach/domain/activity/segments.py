"""Laps and pool lengths of a session (spec/integration/files.spec.md section 8.6).

Stored like the time series, as LAPS and LENGTHS stream blobs. Offsets are seconds from the
session start and never decrease; ``None`` is UNKNOWN. No positions or device identifiers.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from itertools import pairwise
from typing import Final

from coach.domain.activity.fields import CadenceUnit, StrokeType
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.numbers import require_finite
from coach.domain.common.time import ensure_utc

MAX_SEGMENT_SECONDS: Final = 86_400.0


class LapIntensity(StrEnum):
    ACTIVE = "ACTIVE"
    REST = "REST"
    WARMUP = "WARMUP"
    COOLDOWN = "COOLDOWN"
    RECOVERY = "RECOVERY"
    INTERVAL = "INTERVAL"
    OTHER = "OTHER"


class LapTrigger(StrEnum):
    MANUAL = "MANUAL"
    TIME = "TIME"
    DISTANCE = "DISTANCE"
    POSITION = "POSITION"
    SESSION_END = "SESSION_END"
    OTHER = "OTHER"


class LengthType(StrEnum):
    ACTIVE = "ACTIVE"
    IDLE = "IDLE"  # a rest at the wall


def _offset(value: float) -> float:
    if require_finite(value, "start offset") < 0:
        raise DomainValidationError("start offsets must not be negative")
    return value


def _duration(value: float | None, name: str) -> None:
    if value is not None and not 0 < require_finite(value, name) <= MAX_SEGMENT_SECONDS:
        raise DomainValidationError(f"{name} must be above 0 and at most {MAX_SEGMENT_SECONDS:g} s")


def _non_negative(value: float | None, name: str) -> None:
    if value is not None and require_finite(value, name) < 0:
        raise DomainValidationError(f"{name} must not be negative")


def _ascending(offsets: Sequence[float], what: str) -> None:
    if any(later < earlier for earlier, later in pairwise(offsets)):
        raise DomainValidationError(f"{what} start offsets never decrease")


@dataclass(frozen=True, slots=True, kw_only=True)
class Lap:
    start_offset_seconds: float
    elapsed_seconds: float | None = None
    timer_seconds: float | None = None
    distance_meters: float | None = None
    average_hr: float | None = None
    max_hr: float | None = None
    average_cadence: float | None = None
    average_power_watts: float | None = None
    intensity: LapIntensity | None = None
    trigger: LapTrigger | None = None

    def __post_init__(self) -> None:
        _offset(self.start_offset_seconds)
        _duration(self.elapsed_seconds, "lap elapsed time")
        _duration(self.timer_seconds, "lap timer time")
        for name in (
            "distance_meters",
            "average_hr",
            "max_hr",
            "average_cadence",
            "average_power_watts",
        ):
            _non_negative(getattr(self, name), name)


@dataclass(frozen=True, slots=True, kw_only=True)
class Laps:
    start_time_utc: datetime
    laps: tuple[Lap, ...]
    cadence_unit: CadenceUnit | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "start_time_utc", ensure_utc(self.start_time_utc))
        if not self.laps:
            raise DomainValidationError("a lap list has at least one lap")
        _ascending([lap.start_offset_seconds for lap in self.laps], "lap")
        with_cadence = any(lap.average_cadence is not None for lap in self.laps)
        if with_cadence and self.cadence_unit is None:
            raise DomainValidationError("lap cadences name their unit")

    def __len__(self) -> int:
        return len(self.laps)


@dataclass(frozen=True, slots=True, kw_only=True)
class SwimLength:
    start_offset_seconds: float
    elapsed_seconds: float | None = None
    timer_seconds: float | None = None
    type: LengthType | None = None
    strokes: int | None = None
    stroke_type: StrokeType | None = None

    def __post_init__(self) -> None:
        _offset(self.start_offset_seconds)
        _duration(self.elapsed_seconds, "length elapsed time")
        _duration(self.timer_seconds, "length timer time")
        if self.strokes is not None and (isinstance(self.strokes, bool) or self.strokes < 0):
            raise DomainValidationError("stroke counts are whole numbers from 0")


@dataclass(frozen=True, slots=True, kw_only=True)
class Lengths:
    start_time_utc: datetime
    lengths: tuple[SwimLength, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "start_time_utc", ensure_utc(self.start_time_utc))
        if not self.lengths:
            raise DomainValidationError("a length list has at least one length")
        _ascending([length.start_offset_seconds for length in self.lengths], "length")

    def __len__(self) -> int:
        return len(self.lengths)
