"""NaN and infinity are never valid domain values; UNKNOWN is ``None`` (FR-PRF-002).

Comparisons with NaN are always false, so range checks written as ``value < low`` silently
accept it. Every numeric domain input must reject non-finite numbers explicitly.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import pytest

from coach.domain.athlete.experience import (
    CyclingExperience,
    LoadReference,
    RaceResult,
    RunningExperience,
    StrengthExperience,
    SwimmingExperience,
    TennisExperience,
)
from coach.domain.athlete.profile import AthleteProfile, ProfileUpdate
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.provenance import FieldValue, ValueSource
from coach.domain.common.sport import Sport
from coach.domain.common.units import km_to_m, m_to_km, pace_seconds_per_100m, pace_seconds_per_km
from coach.domain.goals.goal import Goal, GoalPriority, SportEvent

NOW = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)
ATHLETE = UUID("0193b2a0-0000-7000-8000-000000000001")
NON_FINITE = [float("nan"), float("inf"), float("-inf")]


def _goal(**values: Any) -> Goal:
    return Goal.new(
        goal_id=UUID("0193b2a0-0000-7000-8000-000000000601"),
        athlete_id=ATHLETE,
        sport=Sport.RUNNING,
        target_date=date(2026, 12, 13),
        priority=GoalPriority.A,
        created_at=NOW,
        **values,
    )


def _event(distance: float) -> SportEvent:
    return SportEvent(
        event_id=UUID("0193b2a0-0000-7000-8000-000000000611"),
        athlete_id=ATHLETE,
        name="Synthetic 10K",
        sport=Sport.RUNNING,
        event_date=date(2026, 12, 13),
        priority=GoalPriority.A,
        distance_meters=distance,
    )


CONSTRUCTORS: dict[str, Callable[[float], object]] = {
    "running weekly distance": lambda x: RunningExperience(weekly_distance_km=x),
    "cycling longest ride": lambda x: CyclingExperience(longest_ride_km=x),
    "swimming pace": lambda x: SwimmingExperience(typical_pace_100m_seconds=x),
    "strength sessions": lambda x: StrengthExperience(sessions_per_week=x),
    "tennis RPE": lambda x: TennisExperience(typical_rpe=x),
    "race time": lambda x: RaceResult(x, date(2026, 9, 14), ValueSource.ATHLETE_DECLARED),
    "reference load": lambda x: LoadReference("ex.goblet_squat", x, 10, date(2026, 10, 1)),
    "goal distance": lambda x: _goal(distance_meters=x),
    "goal target time": lambda x: _goal(target_time_seconds=x),
    "event distance": _event,
    "profile height": lambda x: AthleteProfile.new(ATHLETE).apply(
        ProfileUpdate(height_cm=FieldValue.declared(x, at=NOW))
    ),
    "km to m": km_to_m,
    "m to km": m_to_km,
    "pace per km": pace_seconds_per_km,
    "pace per 100 m": pace_seconds_per_100m,
}


@pytest.mark.req("FR-PRF-002", "FR-MET-001")
@pytest.mark.parametrize("name", sorted(CONSTRUCTORS))
@pytest.mark.parametrize("value", NON_FINITE, ids=["nan", "inf", "-inf"])
def test_non_finite_numbers_are_rejected(name: str, value: float) -> None:
    with pytest.raises(DomainValidationError):
        CONSTRUCTORS[name](value)
