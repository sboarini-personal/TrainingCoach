"""Candidate scoring for deduplication (FR-ACT-007; spec/integration/deduplication.spec.md).

Scores compare a new source with a live Activity: start-time difference, duration and
distance, with missing components excluded and the weights renormalised. Parameters are the
versioned rules 1.0.0 (assumption A-16).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from coach.domain.activity.fields import instant_of, known_number, sport_of
from coach.domain.common.sport import Sport

DEDUP_RULES_VERSION: Final = "1.0.0"
START_FULL_SECONDS: Final = 60.0
START_ZERO_SECONDS: Final = 600.0
RATIO_FULL: Final = 0.02
RATIO_ZERO: Final = 0.15
START_WEIGHT: Final = 0.4
DURATION_WEIGHT: Final = 0.3
DISTANCE_WEIGHT: Final = 0.3
LINK_THRESHOLD: Final = 0.85
AMBIGUOUS_THRESHOLD: Final = 0.60


class MatchDecision(StrEnum):
    LINK = "LINK"
    AMBIGUOUS = "AMBIGUOUS"
    DISTINCT = "DISTINCT"


@dataclass(frozen=True, slots=True)
class MatchFacts:
    sport: Sport
    start_time_utc: datetime
    elapsed_seconds: float | None = None
    moving_seconds: float | None = None
    distance_meters: float | None = None

    @classmethod
    def of(cls, values: Mapping[str, object]) -> MatchFacts:
        """Facts from observation or activity field values."""
        return cls(
            sport=sport_of(values),
            start_time_utc=instant_of(values, "timing.startTimeUtc"),
            elapsed_seconds=known_number(values, "timing.elapsedSeconds"),
            moving_seconds=known_number(values, "timing.movingSeconds"),
            distance_meters=known_number(values, "distance.meters"),
        )


@dataclass(frozen=True, slots=True)
class Match:
    activity_id: UUID
    score: float
    start_difference_seconds: float

    @property
    def decision(self) -> MatchDecision:
        return decide(self.score)


def sports_compatible(first: Sport, second: Sport) -> bool:
    return first is second or Sport.OTHER in {first, second}


def _linear(difference: float, full: float, zero: float) -> float:
    if difference <= full:
        return 1.0
    if difference >= zero:
        return 0.0
    return (zero - difference) / (zero - full)


def relative_difference(first: float, second: float) -> float:
    """``|a - b| / max(a, b)``; two zero values are identical."""
    largest = max(first, second)
    return 0.0 if largest == 0 else abs(first - second) / largest


def _ratio_score(first: float | None, second: float | None) -> float | None:
    if first is None or second is None:
        return None
    return _linear(relative_difference(first, second), RATIO_FULL, RATIO_ZERO)


def _duration_score(new: MatchFacts, existing: MatchFacts) -> float | None:
    if new.elapsed_seconds is not None and existing.elapsed_seconds is not None:
        return _ratio_score(new.elapsed_seconds, existing.elapsed_seconds)
    return _ratio_score(new.moving_seconds, existing.moving_seconds)


def score(new: MatchFacts, existing: MatchFacts) -> tuple[float, float] | None:
    """``(score, start difference in seconds)``; ``None`` when not a candidate."""
    if not sports_compatible(new.sport, existing.sport):
        return None
    start_difference = abs((new.start_time_utc - existing.start_time_utc).total_seconds())
    if start_difference >= START_ZERO_SECONDS:
        return None
    components = [
        (START_WEIGHT, _linear(start_difference, START_FULL_SECONDS, START_ZERO_SECONDS)),
        (DURATION_WEIGHT, _duration_score(new, existing)),
        (DISTANCE_WEIGHT, _ratio_score(new.distance_meters, existing.distance_meters)),
    ]
    present = [(weight, value) for weight, value in components if value is not None]
    total = sum(weight for weight, _ in present)
    combined = sum(weight * value for weight, value in present) / total
    return min(1.0, max(0.0, combined)), start_difference


def decide(value: float) -> MatchDecision:
    if value >= LINK_THRESHOLD:
        return MatchDecision.LINK
    if value >= AMBIGUOUS_THRESHOLD:
        return MatchDecision.AMBIGUOUS
    return MatchDecision.DISTINCT


def best_match(new: MatchFacts, candidates: Iterable[tuple[UUID, MatchFacts]]) -> Match | None:
    """Highest score, then smallest start difference, then lowest activity ID."""
    matches = []
    for activity_id, facts in candidates:
        result = score(new, facts)
        if result is not None:
            matches.append(Match(activity_id, result[0], result[1]))
    if not matches:
        return None
    return min(
        matches,
        key=lambda match: (-match.score, match.start_difference_seconds, str(match.activity_id)),
    )
