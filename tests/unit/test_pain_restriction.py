"""Pain below the red-flag threshold restricts load on its region (pain rules 1.0.0; safety
spec 5). Synthetic data only."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from test_generator import allowed, inputs

from coach.application.ownership import OwnerScope
from coach.application.planning_context import PlanningContextSource
from coach.domain.athlete.profile import AthleteProfile
from coach.domain.recovery.checkin import CheckInPain, DailyCheckIn
from coach.domain.recovery.pain import PainRestriction, pain_restriction
from coach.domain.strength.catalogue import CATALOGUE
from coach.domain.strength.regions import regions_loaded
from coach.domain.strength.vocabulary import BodyRegion
from coach.policy.plans import health, plan_reasons
from plan_samples import MONDAY, content, strength, week
from plan_world import open_planning
from samples import ATHLETE

TUESDAY, THURSDAY = MONDAY + timedelta(days=1), MONDAY + timedelta(days=3)
AT = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)


def report(day: date, intensity: int, location: str | None) -> DailyCheckIn:
    return DailyCheckIn(
        checkin_id=UUID(int=0xD00 + day.day),
        athlete_id=ATHLETE,
        recorded_at=AT,
        training_day=day,
        pain=CheckInPain(intensity=intensity, location=location),
        pain_flag=True,
    )


@pytest.mark.req("SAFE-MED-001", "FR-STR-006")
def test_recent_pain_naming_a_region_restricts_it_for_three_days() -> None:
    knee = pain_restriction([report(MONDAY, 5, "rodilla")], MONDAY)
    assert (knee.regions, knee.until) == (frozenset({BodyRegion.KNEES}), THURSDAY)
    assert knee.applies(date(2026, 10, 28)) == {BodyRegion.KNEES}
    assert knee.applies(THURSDAY) == frozenset()
    assert pain_restriction([report(MONDAY, 3, "rodilla")], MONDAY) == PainRestriction()  # mild
    assert pain_restriction([report(MONDAY, 6, "me molesta algo")], MONDAY) == PainRestriction()
    old = MONDAY - timedelta(days=3)
    assert pain_restriction([report(old, 6, "rodilla")], MONDAY) == PainRestriction()


@pytest.mark.req("SAFE-MED-001", "FR-POL-001", "FR-PRF-006")
def test_the_policy_denies_hard_work_on_a_hurting_region_and_the_generator_avoids_it() -> None:
    plan = content(week(MONDAY, {1: [strength(TUESDAY, rpe=7)], 3: [strength(THURSDAY, rpe=7)]}))
    hurting = open_planning(pain=pain_restriction([report(MONDAY, 5, "rodilla")], MONDAY))
    found = health(plan, hurting)
    assert [(item.code, item.parameter("day"), item.parameter("region")) for item in found] == [
        ("PAIN_REGION_RESTRICTED", "2026-10-27", "KNEES")  # Thursday is past the window
    ]
    assert plan_reasons(plan, frozenset(), open_planning()) == []
    # Pain on Monday restricts the knees to Wednesday, the plan's first strength day.
    pained = inputs(pain=pain_restriction([report(MONDAY, 6, "knees")], MONDAY))
    generated = allowed(pained)  # passes every policy, the pain restriction included
    early = [
        item
        for item in generated.workouts
        if item.strength is not None and item.training_day < pained.pain.until  # type: ignore[operator]
    ]
    knees = [
        exercise
        for item in early
        for exercise in item.strength.exercises  # type: ignore[union-attr]
        if BodyRegion.KNEES in regions_loaded(CATALOGUE.get(exercise.exercise_id))
    ]
    assert knees  # the squat and the split squat load the knees
    assert all(exercise.target_rpe == 5 for exercise in knees)  # light, the rest as planned


@pytest.mark.req("SAFE-MED-001")
def test_the_planning_context_reads_recent_check_ins() -> None:
    class Inputs:
        def profile(self, scope: Any) -> AthleteProfile:
            return AthleteProfile.new(ATHLETE)

        def availability(self, scope: Any) -> None:
            return None

    asked: list[tuple[date, int]] = []

    def checkins(_scope: Any, until: date, days: int) -> list[DailyCheckIn]:
        asked.append((until, days))
        return [report(MONDAY, 5, "hombro")]

    source = PlanningContextSource(
        Inputs(),  # type: ignore[arg-type]
        lambda _scope, _day: (),
        today=lambda _scope: MONDAY,
        checkins=checkins,
    )
    context = source(OwnerScope(ATHLETE))
    assert context.pain.regions == frozenset({BodyRegion.SHOULDERS})
    assert asked == [(MONDAY, 3)]
    assert replace(context, pain=PainRestriction()).pain.regions == frozenset()
