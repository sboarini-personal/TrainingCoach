"""Time in heart-rate zones, method 1.0.0 (metrics spec section 9.2).

Each interval between consecutive samples counts toward the zone of the heart rate at its
start, when that heart rate is known and the interval lasts more than 0 and at most
``MAX_SAMPLE_GAP_SECONDS`` (a longer gap is a pause or a recording gap). Zone ``k`` spans
``[boundaries[k], boundaries[k + 1])``; a heart rate at or above the last boundary counts in
the top zone, one below the first boundary counts in no zone. An interval without a heart rate
is unknown time, which lowers the coverage; a pause does not. Sums use ``math.fsum``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Final

from coach.domain.common.errors import DomainValidationError

MAX_SAMPLE_GAP_SECONDS: Final = 20.0
MINIMUM_COVERAGE: Final = 0.9


@dataclass(frozen=True, slots=True)
class ZoneTimes:
    seconds: tuple[float, ...]
    below_seconds: float
    unknown_seconds: float
    gap_seconds: float

    @property
    def coverage(self) -> float | None:
        """Share of the recorded (non-pause) time with a known heart rate."""
        known = math.fsum((*self.seconds, self.below_seconds))
        total = known + self.unknown_seconds
        return None if total == 0 else known / total


def _zone(heart_rate: float, boundaries: Sequence[int]) -> int | None:
    """Zero-based zone index; ``None`` below the first boundary."""
    if heart_rate < boundaries[0]:
        return None
    for index, upper in enumerate(boundaries[1:]):
        if heart_rate < upper:
            return index
    return len(boundaries) - 2  # at or above the last boundary: the top zone


def time_in_zones(
    offsets: Sequence[float],
    heart_rates: Sequence[float | None],
    boundaries: Sequence[int],
    *,
    max_gap_seconds: float = MAX_SAMPLE_GAP_SECONDS,
) -> ZoneTimes:
    if len(offsets) != len(heart_rates):
        raise DomainValidationError("offsets and heart rates differ in length")
    if len(boundaries) < 2:
        raise DomainValidationError("a zone model has at least two boundaries")
    per_zone: list[list[float]] = [[] for _ in range(len(boundaries) - 1)]
    below: list[float] = []
    unknown: list[float] = []
    gaps: list[float] = []
    for (start, end), heart_rate in zip(pairwise(offsets), heart_rates, strict=False):
        duration = end - start
        if not 0 < duration <= max_gap_seconds:
            gaps.append(max(duration, 0.0))
            continue
        if heart_rate is None:
            unknown.append(duration)
            continue
        zone = _zone(heart_rate, boundaries)
        (below if zone is None else per_zone[zone]).append(duration)
    return ZoneTimes(
        tuple(math.fsum(durations) for durations in per_zone),
        math.fsum(below),
        math.fsum(unknown),
        math.fsum(gaps),
    )
