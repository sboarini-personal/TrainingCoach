"""Time series of a session (spec/integration/files.spec.md section 8.5).

Columnar: every column has the length of the offsets; ``None`` is UNKNOWN at a sample and a
column that is ``None`` was not recorded. Positions are never part of it (data minimisation).
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
from typing import Final

from coach.domain.activity.fields import CadenceUnit
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.numbers import require_finite
from coach.domain.common.time import ensure_utc

type Column = tuple[float | None, ...]

_NON_NEGATIVE: Final = ("distance_meters", "heart_rate_bpm", "speed_mps", "cadence", "power_watts")
ALTITUDE_RANGE: Final = (-500.0, 9000.0)


def _check_column(name: str, column: Column, *, minimum: float, maximum: float | None) -> None:
    for value in column:
        if value is None:
            continue
        require_finite(value, name)
        if value < minimum or (maximum is not None and value > maximum):
            raise DomainValidationError(f"{name} sample out of range: {value}")


@dataclass(frozen=True, slots=True, kw_only=True)
class TimeSeries:
    start_time_utc: datetime
    offset_seconds: tuple[float, ...]
    distance_meters: Column | None = None
    heart_rate_bpm: Column | None = None
    speed_mps: Column | None = None
    cadence: Column | None = None
    cadence_unit: CadenceUnit | None = None
    power_watts: Column | None = None
    altitude_meters: Column | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "start_time_utc", ensure_utc(self.start_time_utc))
        previous = 0.0
        for offset in self.offset_seconds:
            if require_finite(offset, "offset") < previous:
                raise DomainValidationError("time series offsets never decrease")
            previous = offset
        length = len(self.offset_seconds)
        for item in fields(self):
            value = getattr(self, item.name)
            if isinstance(value, tuple) and len(value) != length:
                raise DomainValidationError(f"column {item.name} differs in length")
        for name in _NON_NEGATIVE:
            column = getattr(self, name)
            if column is not None:
                _check_column(name, column, minimum=0.0, maximum=None)
        if self.altitude_meters is not None:
            low, high = ALTITUDE_RANGE
            _check_column("altitude", self.altitude_meters, minimum=low, maximum=high)
        if (self.cadence is None) != (self.cadence_unit is None):
            raise DomainValidationError("a cadence column names its unit")

    def __len__(self) -> int:
        return len(self.offset_seconds)

    def distance_samples(self) -> list[tuple[float, float]]:
        """``(offset, cumulative distance)`` pairs where the distance is known."""
        if self.distance_meters is None:
            return []
        return [
            (offset, distance)
            for offset, distance in zip(self.offset_seconds, self.distance_meters, strict=True)
            if distance is not None
        ]
