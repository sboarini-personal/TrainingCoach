"""Goal feasibility, rules 1.0.0 (goals spec 4 and 5; FR-GOL-003 to FR-GOL-005). Synthetic
data only."""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

import pytest

from coach.domain.athlete.experience import RaceResult, RunningExperience
from coach.domain.athlete.profile import AthleteProfile, ProfileUpdate
from coach.domain.availability.availability import Availability, WeeklyAvailability
from coach.domain.common.evidence import EvidenceKind
from coach.domain.common.provenance import FieldValue, ValueSource
from coach.domain.common.quality import Confidence
from coach.domain.common.sport import Sport
from coach.domain.goals.feasibility import (
    Classification,
    FeasibilityReason,
    assess_feasibility,
    current_state,
)
from coach.domain.goals.goal import Goal, GoalPriority
from samples import ATHLETE, NOW

TODAY = date(2026, 10, 25)


def runner(**races: RaceResult) -> AthleteProfile:
    experience = RunningExperience(**races)  # type: ignore[arg-type]
    return AthleteProfile.new(ATHLETE).apply(
        ProfileUpdate(running_experience=FieldValue.declared(experience, at=NOW))
    )


def race(seconds: float, days_ago: int) -> RaceResult:
    return RaceResult(seconds, TODAY - timedelta(days=days_ago), ValueSource.ATHLETE_DECLARED)


def goal(
    *,
    distance: float | None = 10_000,
    target: float | None,
    weeks: int = 8,
    sport: Sport = Sport.RUNNING,
) -> Goal:
    return Goal.new(
        goal_id=UUID(int=1),
        athlete_id=ATHLETE,
        sport=sport,
        target_date=TODAY + timedelta(weeks=weeks),
        priority=GoalPriority.A,
        created_at=NOW,
        distance_meters=distance,
        target_time_seconds=target,
    )


def hours(per_week: float) -> Availability:
    return Availability.new(
        ATHLETE, recurring=WeeklyAvailability(days_per_week=4, maximum_hours_per_week=per_week)
    )


@pytest.mark.req("FR-GOL-003", "FR-GOL-004", "FR-GOL-005")
@pytest.mark.acceptance("AT-GOL-001")
def test_a_target_six_minutes_faster_in_eight_weeks_is_unlikely_with_alternatives() -> None:
    profile = runner(recent_10k=race(3000, 60))  # 50:00 two months ago
    found = assess_feasibility(goal(target=2640), profile, hours(6), today=TODAY)  # 44:00
    current = found.current
    assert current is not None
    assert (current.seconds, current.confidence) == (3000.0, Confidence.HIGH)
    assert current.evidence.kind is EvidenceKind.ATHLETE_PROFILE
    assert current.evidence.field == "runningExperience.recent_10k"
    assert (found.gap_seconds, found.weeks_available, found.hours_per_week) == (360.0, 8.0, 6.0)
    assert found.required_seconds_per_week == pytest.approx(45.0)
    assert found.required_percent_per_week == pytest.approx(1.5)
    assert (found.classification, found.confidence) == (Classification.UNLIKELY, Confidence.HIGH)
    assert found.presents_alternatives
    assert found.desired_seconds == 2640.0
    assert found.realistic_seconds == pytest.approx(3000 * (1 - 0.0025 * 8))
    assert found.minimum_seconds == 3000.0


@pytest.mark.req("FR-GOL-003")
@pytest.mark.parametrize(
    ("target", "classification"),
    [
        (2970, Classification.LIKELY),  # 30 s in 8 weeks: 0.125 % a week
        (2900, Classification.POSSIBLE),  # 100 s: 0.42 % a week
        (3100, Classification.LIKELY),  # already faster than the target
    ],
)
def test_the_required_progression_classifies(target: float, classification: Classification) -> None:
    found = assess_feasibility(
        goal(target=target), runner(recent_10k=race(3000, 60)), hours(6), today=TODAY
    )
    assert found.classification is classification
    if classification is Classification.LIKELY:
        assert not found.presents_alternatives  # LIKELY with HIGH confidence


@pytest.mark.req("FR-GOL-003")
def test_other_distances_use_riegel_with_less_confidence_the_further_they_go() -> None:
    profile = runner(recent_10k=race(3000, 60))
    near = current_state(profile, 15_000, today=TODAY)
    assert near is not None
    assert near.seconds == pytest.approx(3000 * 1.5**1.06)
    assert near.confidence is Confidence.MEDIUM
    far = current_state(profile, 42_195, today=TODAY)
    assert far is not None
    assert far.confidence is Confidence.LOW
    found = assess_feasibility(
        goal(distance=42_195, target=14_000, weeks=20), profile, hours(8), today=TODAY
    )
    assert found.classification is Classification.INSUFFICIENT_DATA
    assert FeasibilityReason.LOW_CONFIDENCE in found.reasons
    both = runner(recent_10k=race(3000, 300), recent_half_marathon=race(6700, 30))
    chosen = current_state(both, 21_097.5, today=TODAY)
    assert chosen is not None
    assert chosen.source_seconds == 6700  # the nearest distance
    old = runner(recent_10k=race(3000, 400))
    assert current_state(old, 10_000, today=TODAY) is None  # older than 12 months


@pytest.mark.req("FR-GOL-003", "FR-GOL-005")
def test_what_rules_1_0_0_cannot_assess_is_insufficient_data() -> None:
    profile = runner(recent_10k=race(3000, 60))
    swim = assess_feasibility(
        goal(target=1800, sport=Sport.SWIMMING), profile, hours(6), today=TODAY
    )
    assert swim.reasons == (FeasibilityReason.SPORT_NOT_SUPPORTED,)
    assert swim.classification is Classification.INSUFFICIENT_DATA
    finish = assess_feasibility(goal(target=None), profile, hours(6), today=TODAY)
    assert finish.reasons == (FeasibilityReason.NO_TARGET_TIME,)
    nothing = assess_feasibility(
        goal(target=2900), AthleteProfile.new(ATHLETE), hours(6), today=TODAY
    )
    assert nothing.reasons == (FeasibilityReason.NO_COMPARABLE_RESULT,)
    few = assess_feasibility(goal(target=2970), profile, hours(1.5), today=TODAY)
    assert (few.classification, few.reasons) == (
        Classification.POSSIBLE,
        (FeasibilityReason.FEW_HOURS,),
    )
    met = assess_feasibility(goal(target=3100), profile, hours(1.0), today=TODAY)
    assert (met.gap_seconds, met.classification, met.reasons) == (
        -100.0,  # already faster than the target
        Classification.POSSIBLE,  # at most POSSIBLE with fewer than 2 hours (goals spec 4.5)
        (FeasibilityReason.FEW_HOURS,),
    )
