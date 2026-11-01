"""The athlete's own changes to the ACTIVE plan: edit rules 1.0.0 and PlanService.edit
(planning spec 2, precision of the athlete's review of 2026-10-31; ADR-034). Synthetic data.

The sample week (``plan_samples.week``) from Monday 2026-10-26: rest on Monday, goblet squats
on Tuesday, an easy 40-minute run on Wednesday, intervals on Thursday and an easy 60-minute run
on Saturday.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from coach.application.errors import ConcurrencyConflictError
from coach.application.policy_gate import PolicyDeniedError
from coach.domain.coaching.state import Actor, Condition
from coach.domain.common.errors import DomainValidationError
from coach.domain.plans.plan import GeneratorKind, PlanStatus, PlanVersion
from coach.domain.plans.workout import WorkoutType
from coach.planning.edits import moved, removed, resized
from plan_samples import MONDAY, content, recovery, week
from plan_world import ENGINE, GOAL, SCOPE, PlanWorld

TUESDAY, WEDNESDAY, THURSDAY = (MONDAY + timedelta(days=offset) for offset in (1, 2, 3))
FRIDAY, SATURDAY, SUNDAY = (MONDAY + timedelta(days=offset) for offset in (4, 5, 6))


@pytest.mark.req("FR-PLN-002", "FR-PLN-004")
def test_a_workout_moves_to_another_day_and_keeps_its_identity() -> None:
    plan = content()
    squats = plan.on(TUESDAY)[0]
    found = moved(plan, squats.workout_id, FRIDAY, today=MONDAY)
    assert found.on(TUESDAY) == ()
    (friday,) = found.on(FRIDAY)
    assert (friday.workout_id, friday.strength) == (squats.workout_id, squats.strength)
    onto_rest = moved(plan, squats.workout_id, MONDAY, today=MONDAY)  # Monday was a rest day
    assert [item.type for item in onto_rest.on(MONDAY)] == [WorkoutType.STRENGTH]


@pytest.mark.req("FR-PLN-002")
@pytest.mark.parametrize(
    ("day", "today", "done"),
    [
        (FRIDAY, WEDNESDAY, False),  # Tuesday is history
        (FRIDAY, TUESDAY, True),  # done today: history too
        (MONDAY - timedelta(days=1), MONDAY, False),  # before the plan
        (MONDAY + timedelta(weeks=2), MONDAY, False),  # after the plan
        (TUESDAY, MONDAY, False),  # the same day
    ],
    ids=["past", "done", "before", "after", "same-day"],
)
def test_history_and_days_outside_the_plan_never_change(day: date, today: date, done: bool) -> None:
    plan = content()
    squats = plan.on(TUESDAY)[0]
    with pytest.raises(DomainValidationError):
        moved(plan, squats.workout_id, day, today=today, done={squats.workout_id} if done else ())


@pytest.mark.req("FR-PLN-002")
def test_a_workout_is_removed_and_a_rest_day_is_no_workout() -> None:
    plan = content()
    intervals = plan.on(THURSDAY)[0]
    assert removed(plan, intervals.workout_id, today=MONDAY).on(THURSDAY) == ()
    with pytest.raises(DomainValidationError):
        removed(plan, plan.on(MONDAY)[0].workout_id, today=MONDAY)  # the rest day itself


@pytest.mark.req("FR-PLN-002", "FR-PLN-005")
def test_only_steady_and_recovery_sessions_change_their_minutes() -> None:
    plan = content(week(MONDAY, {2: [content().on(WEDNESDAY)[0]], 4: [recovery(FRIDAY)]}))
    run, rest_day = plan.on(WEDNESDAY)[0], plan.on(FRIDAY)[0]
    longer = resized(plan, run.workout_id, 55, today=MONDAY).workout(run.workout_id)
    assert longer is not None
    assert (longer.planned_seconds, longer.max_rpe) == (3300.0, run.max_rpe)  # same effort
    assert longer.what == "55 minutes at the same effort (changed by you)"
    easier = resized(plan, rest_day.workout_id, 20, today=MONDAY).workout(rest_day.workout_id)
    assert easier is not None
    assert easier.planned_seconds == 1200.0
    sample = content()
    for workout in (sample.on(THURSDAY)[0], sample.on(TUESDAY)[0]):  # intervals, strength
        with pytest.raises(DomainValidationError, match="move or remove it instead"):
            resized(sample, workout.workout_id, 45, today=MONDAY)
    for minutes in (5, 400):
        with pytest.raises(DomainValidationError):
            resized(plan, run.workout_id, minutes, today=MONDAY)


def active_world() -> tuple[PlanWorld, PlanVersion]:
    world = PlanWorld()
    world.to_goal_defined()
    first = world.service.propose(SCOPE, goal_id=GOAL, content=content(), generator=ENGINE)
    world.activate(first.plan_id, 1)
    return world, first


@pytest.mark.req("FR-PLN-002", "FR-PLN-003", "FR-POL-001")
def test_an_edit_is_a_new_version_active_at_once() -> None:
    world, first = active_world()
    plan_id = first.plan_id
    squats = first.content.on(TUESDAY)[0]
    edited = moved(first.content, squats.workout_id, FRIDAY, today=MONDAY)
    revision = world.service.plan(SCOPE, plan_id).version
    stored = world.service.edit(
        SCOPE, plan_id, edited, expected_revision=revision, summary="moved by you"
    )
    assert (stored.number, stored.parent_number, stored.status) == (2, 1, PlanStatus.ACTIVE)
    assert stored.generator.kind is GeneratorKind.ATHLETE
    assert stored.activation_reason == "moved by you"
    assert world.service.version(SCOPE, plan_id, 1).status is PlanStatus.SUPERSEDED
    actions = [record.document["action"] for record in world.audit.all_records()]
    assert actions[-2:] == ["plan.proposed", "plan.activated"]
    with pytest.raises(ConcurrencyConflictError):  # read before the edit above
        world.service.edit(SCOPE, plan_id, edited, expected_revision=revision, summary="again")


@pytest.mark.req("FR-PLN-002", "FR-POL-001", "SAFE-MED-003")
def test_an_edit_is_refused_with_nothing_written() -> None:
    world, first = active_world()
    plan_id = first.plan_id
    squats = first.content.on(TUESDAY)[0]
    edited = moved(first.content, squats.workout_id, FRIDAY, today=MONDAY)

    def revision() -> int:
        return world.service.plan(SCOPE, plan_id).version

    world.conditions.activate(SCOPE, Condition.SAFETY_HOLD, reason="RF_CHEST_PAIN")
    with pytest.raises(PolicyDeniedError):  # any activation is denied under a hold
        world.service.edit(SCOPE, plan_id, edited, expected_revision=revision(), summary="x")
    assert [item.number for item in world.service.versions(SCOPE, plan_id)] == [1]
    world.conditions.clear(SCOPE, Condition.SAFETY_HOLD, reason="CLEARED", actor=Actor.ATHLETE)
    pending = world.service.propose(  # an adaptation waiting for the athlete
        SCOPE, goal_id=GOAL, content=content(), generator=ENGINE, plan_id=plan_id, parent=1
    )
    with pytest.raises(DomainValidationError, match="waiting for you"):
        world.service.edit(SCOPE, plan_id, edited, expected_revision=revision(), summary="x")
    assert world.service.version(SCOPE, plan_id, pending.number).status is PlanStatus.PROPOSED
