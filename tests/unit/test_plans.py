"""Planned workouts and immutable plan versions (planning spec 1 to 4; FR-PLN-001 to
FR-PLN-004, AT-PLN-001, AT-PLN-002). Synthetic data only."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest

from coach.application.contracts import PLAN_HEADER_CODEC, PLAN_VERSION_CODEC
from coach.application.contracts.plans import (
    content_hash,
    plan_version_from_contract,
    plan_version_to_contract,
    workout_from_contract,
    workout_to_contract,
)
from coach.application.errors import ConcurrencyConflictError, NotFoundError
from coach.application.events import EventName
from coach.application.ownership import OwnerScope
from coach.application.plans import PlanNotReadyError
from coach.application.policy_gate import PolicyDeniedError
from coach.domain.coaching.state import Actor, Condition, LifecycleState
from coach.domain.common.errors import DomainValidationError, InvalidTransitionError
from coach.domain.common.sport import Sport
from coach.domain.goals.goal import GoalStatus
from coach.domain.onboarding.readiness import Readiness, ReadinessInput, ReadinessStatus
from coach.domain.plans.plan import PlanStatus, SupersededReason
from coach.domain.plans.workout import (
    SAFETY_STOP,
    EnduranceWorkout,
    EvaluationSession,
    Step,
    StepKind,
    WorkoutType,
)
from coach.infrastructure.sqlite.aggregates import SqliteAggregateRepository
from coach.infrastructure.sqlite.database import SqliteDatabase
from plan_samples import MONDAY, content, easy_run, intervals, rest, strength, target, tennis, week
from plan_world import ENGINE, GOAL, SCOPE, PlanWorld, goal
from samples import OTHER_ATHLETE
from schemas import CONTRACTS_DIR, load_json

PLANTED = "Zx81Qp0LmN4vB7cT2wYe9KdR"  # pragma: allowlist secret


# --------------------------------------------------------------------------- model


@pytest.mark.req("FR-PLN-005", "FR-PLN-008", "SAFE-MED-002")
def test_a_workout_has_its_own_detail_block_and_the_safety_stop() -> None:
    run = easy_run(MONDAY)
    with pytest.raises(DomainValidationError):
        replace(run, type=WorkoutType.STRENGTH)  # the block does not match the type
    with pytest.raises(DomainValidationError):
        EnduranceWorkout(
            sport=Sport.RUNNING,
            main_set=(Step(kind=StepKind.STEADY, duration_seconds=600, target=target(3, 4)),),
            stop_conditions=("Stop when tired",),  # no safety rule
        )
    with pytest.raises(DomainValidationError):
        EnduranceWorkout(
            sport=Sport.TENNIS,
            main_set=(Step(kind=StepKind.STEADY, duration_seconds=600, target=target(3, 4)),),
            stop_conditions=(SAFETY_STOP,),
        )
    with pytest.raises(DomainValidationError):
        EvaluationSession(
            sport=Sport.RUNNING,
            protocol_id="eval.time_trial",
            description="All out",
            submaximal=False,
            requires_opt_in=False,  # a maximal test needs the opt-in
            safety_notes=("Stop on red flags",),
        )
    hard = intervals(MONDAY)
    assert hard.planned_seconds == 900 + 5 * (300 + 120) + 600
    assert (hard.max_rpe, hard.intensive) == (8, True)
    assert (easy_run(MONDAY).intensive, rest(MONDAY).intensive, tennis(MONDAY).intensive) == (
        False,
        False,
        False,
    )
    assert strength(MONDAY, rpe=8).intensive
    assert rest(MONDAY).sport is None


@pytest.mark.req("FR-PLN-004", "FR-ONB-004")
def test_weeks_and_contents_keep_their_shape() -> None:
    with pytest.raises(DomainValidationError):
        week(MONDAY + timedelta(days=1))  # not a Monday
    with pytest.raises(DomainValidationError):
        week(MONDAY, {0: [easy_run(MONDAY + timedelta(days=1))]})  # listed on another day
    with pytest.raises(DomainValidationError):
        content(week(MONDAY), week(MONDAY + timedelta(days=14), index=2))  # not consecutive
    with pytest.raises(DomainValidationError):
        replace(content(), provisional=True)  # a provisional plan states its assumptions
    found = week()
    assert found.recovery_days == (MONDAY,)  # an empty day is free, not a recovery day
    assert len(found.strength_sessions) == 1
    assert found.key_workouts == (found.days[3][0].workout_id,)


@pytest.mark.req("FR-PLN-002", "NFR-REL-004")
def test_published_workouts_and_plan_round_trip_with_their_hash() -> None:
    for name in ("workout.endurance.example.json", "workout.strength.example.json"):
        document = load_json(CONTRACTS_DIR / "examples" / name)
        assert workout_to_contract(workout_from_contract(document)) == document
    example = load_json(CONTRACTS_DIR / "examples" / "training-plan.example.json")
    version = plan_version_from_contract(example)
    assert version.content_hash == content_hash(version.content)
    sample = content()  # integer and float targets alike, after a JSON round trip
    stored = json.loads(
        json.dumps(
            plan_version_to_contract(
                replace(version, content=sample, content_hash=content_hash(sample))
            )
        )
    )
    assert plan_version_from_contract(stored).content_hash == content_hash(sample)
    tampered = json.loads(json.dumps(example))
    tampered["weeks"][0]["primaryGoal"] = "Something else"
    with pytest.raises(DomainValidationError):  # the content no longer matches its hash
        plan_version_from_contract(tampered)


# --------------------------------------------------------------------------- service


@pytest.mark.req("FR-PLN-002", "FR-PLN-003", "FR-ONB-006")
@pytest.mark.acceptance("AT-PLN-001")
def test_an_adaptation_is_a_new_version_and_activation_supersedes_the_old() -> None:
    world = PlanWorld()
    world.to_goal_defined()
    service = world.service
    first = service.propose(SCOPE, goal_id=GOAL, content=content(), generator=ENGINE)
    assert (first.number, first.status, first.parent_number) == (1, PlanStatus.PROPOSED, None)
    assert world.lifecycle.current(SCOPE).lifecycle is LifecycleState.PLAN_READY
    world.activate(first.plan_id, 1)
    assert world.lifecycle.current(SCOPE).lifecycle is LifecycleState.TRAINING
    before = service.version(SCOPE, first.plan_id, 1)
    adapted = content(week(MONDAY, {0: [rest(MONDAY)], 2: [easy_run(MONDAY + timedelta(days=2))]}))
    second = service.propose(
        SCOPE, goal_id=GOAL, content=adapted, generator=ENGINE, plan_id=first.plan_id, parent=1
    )
    assert (second.number, second.status, second.parent_number) == (2, PlanStatus.PROPOSED, 1)
    unchanged = service.version(SCOPE, first.plan_id, 1)
    assert (unchanged.content, unchanged.content_hash, unchanged.status) == (
        before.content,
        before.content_hash,
        PlanStatus.ACTIVE,
    )
    reason = f"Adaptation accepted, api_key={PLANTED}"
    activated = world.activate(first.plan_id, 2, reason=reason)
    assert activated.activation_reason is not None
    assert PLANTED not in activated.activation_reason  # screened before it is stored
    statuses = [
        (item.number, item.status, item.superseded_reason)
        for item in service.versions(SCOPE, first.plan_id)
    ]
    assert statuses == [
        (1, PlanStatus.SUPERSEDED, SupersededReason.ACTIVATED_NEWER_VERSION),
        (2, PlanStatus.ACTIVE, None),
    ]
    assert service.active(SCOPE) == service.version(SCOPE, first.plan_id, 2)
    assert (EventName.PLAN_SUPERSEDED, {"version": "1"}) in world.events.events
    actions = [record.document["action"] for record in world.audit.all_records()]
    assert actions.count("plan.activated") == 2


@pytest.mark.req("FR-PLN-003")
@pytest.mark.acceptance("AT-PLN-002")
def test_two_activations_from_the_same_reading_cannot_both_succeed() -> None:
    world = PlanWorld()
    service = world.service
    first = service.propose(SCOPE, goal_id=GOAL, content=content(), generator=ENGINE)
    world.activate(first.plan_id, 1)
    for _ in range(2):
        service.propose(
            SCOPE,
            goal_id=GOAL,
            content=content(),
            generator=ENGINE,
            plan_id=first.plan_id,
            parent=1,
        )
    seen = service.plan(SCOPE, first.plan_id).version  # both athletes read the plan now
    service.activate(SCOPE, first.plan_id, 2, expected_revision=seen)
    with pytest.raises(ConcurrencyConflictError):
        service.activate(SCOPE, first.plan_id, 3, expected_revision=seen)
    active = [
        item for item in service.versions(SCOPE, first.plan_id) if item.status is PlanStatus.ACTIVE
    ]
    assert [item.number for item in active] == [2]


@pytest.mark.req("FR-PLN-003", "NFR-REL-001")
@pytest.mark.acceptance("AT-PLN-002")
def test_a_failed_activation_writes_nothing_in_sqlite(tmp_path: Path) -> None:
    database = SqliteDatabase.open(tmp_path / "coach.sqlite3")
    headers = SqliteAggregateRepository(database, PLAN_HEADER_CODEC)
    versions = SqliteAggregateRepository(database, PLAN_VERSION_CODEC)
    world = PlanWorld(headers, versions, batch=database.batch)
    service = world.service
    first = service.propose(SCOPE, goal_id=GOAL, content=content(), generator=ENGINE)
    world.activate(first.plan_id, 1)
    for _ in range(2):
        service.propose(
            SCOPE,
            goal_id=GOAL,
            content=content(),
            generator=ENGINE,
            plan_id=first.plan_id,
            parent=1,
        )
    stale = service.plan(SCOPE, first.plan_id)
    service.activate(SCOPE, first.plan_id, 2, expected_revision=stale.version)
    with pytest.raises(ConcurrencyConflictError):
        service.activate(SCOPE, first.plan_id, 3, expected_revision=stale.version)
    statuses = {item.number: item.status for item in service.versions(SCOPE, first.plan_id)}
    assert statuses == {1: PlanStatus.SUPERSEDED, 2: PlanStatus.ACTIVE, 3: PlanStatus.PROPOSED}
    assert service.plan(SCOPE, first.plan_id).active_number == 2


@pytest.mark.req("SAFE-MED-003", "FR-PLN-002")
def test_activation_is_blocked_during_a_safety_hold_and_proposals_can_be_rejected() -> None:
    world = PlanWorld()
    first = world.service.propose(SCOPE, goal_id=GOAL, content=content(), generator=ENGINE)
    world.conditions.activate(SCOPE, Condition.SAFETY_HOLD, reason="RF_CHEST_PAIN")
    with pytest.raises(PolicyDeniedError) as denied:
        world.activate(first.plan_id, 1)
    reasons = denied.value.decision.reasons
    assert {item.code for item in reasons} == {"SAFETY_HOLD_ACTIVE"}
    assert reasons[0].parameters == ()  # the activation itself, then each intensive workout
    assert [item.parameter("day") for item in reasons[1:]] == ["2026-10-27", "2026-10-29"]
    rejected = world.service.reject(SCOPE, first.plan_id, 1)  # rejecting is always allowed
    world.conditions.clear(
        SCOPE, Condition.SAFETY_HOLD, reason="CLEARED_BY_PROFESSIONAL", actor=Actor.ATHLETE
    )
    assert (rejected.status, rejected.superseded_reason) == (
        PlanStatus.SUPERSEDED,
        SupersededReason.REJECTED,
    )
    with pytest.raises(InvalidTransitionError):
        rejected.moved(PlanStatus.ACTIVE)
    second = world.service.propose(
        SCOPE, goal_id=GOAL, content=content(), generator=ENGINE, plan_id=first.plan_id
    )
    third = world.service.propose(
        SCOPE,
        goal_id=GOAL,
        content=content(),
        generator=ENGINE,
        plan_id=first.plan_id,
        replaces=second.number,
    )
    statuses = [
        (item.number, item.status, item.superseded_reason)
        for item in world.service.versions(SCOPE, first.plan_id)
    ]
    assert statuses == [
        (1, PlanStatus.SUPERSEDED, SupersededReason.REJECTED),
        (2, PlanStatus.SUPERSEDED, SupersededReason.REPLACED_BY_NEWER_PROPOSAL),
        (3, PlanStatus.PROPOSED, None),
    ]
    with pytest.raises(InvalidTransitionError):  # only a proposal can be replaced
        world.service.propose(
            SCOPE,
            goal_id=GOAL,
            content=content(),
            generator=ENGINE,
            plan_id=first.plan_id,
            replaces=1,
        )
    assert len(world.service.versions(SCOPE, first.plan_id)) == 3  # nothing was written
    world.goals.add(SCOPE, goal(UUID(int=0x602)))
    with pytest.raises(DomainValidationError):  # a plan keeps its goal
        world.service.propose(
            SCOPE,
            goal_id=UUID(int=0x602),
            content=content(),
            generator=ENGINE,
            plan_id=third.plan_id,
        )
    assert plan_version_from_contract(plan_version_to_contract(rejected)) == rejected


@pytest.mark.req("FR-PLN-002", "FR-PLN-003")
def test_only_a_proposal_can_be_rejected_or_replaced() -> None:
    world = PlanWorld()
    first = world.service.propose(SCOPE, goal_id=GOAL, content=content(), generator=ENGINE)
    world.activate(first.plan_id, 1)
    with pytest.raises(InvalidTransitionError):  # the ACTIVE version stays ACTIVE
        world.service.reject(SCOPE, first.plan_id, 1)
    with pytest.raises(InvalidTransitionError):
        world.service.propose(
            SCOPE,
            goal_id=GOAL,
            content=content(),
            generator=ENGINE,
            plan_id=first.plan_id,
            replaces=1,
        )
    assert world.service.version(SCOPE, first.plan_id, 1).status is PlanStatus.ACTIVE
    second = world.service.propose(
        SCOPE, goal_id=GOAL, content=content(), generator=ENGINE, plan_id=first.plan_id, parent=1
    )
    assert world.activate(first.plan_id, second.number).status is PlanStatus.ACTIVE
    assert world.service.active(SCOPE) == world.service.version(SCOPE, first.plan_id, 2)


@pytest.mark.req("FR-ONB-003", "FR-ONB-004", "SEC-AUTHZ-001")
@pytest.mark.acceptance("AT-ONB-002")
def test_a_proposal_needs_an_owned_open_goal_and_the_definition_of_ready() -> None:
    world = PlanWorld()
    with pytest.raises(NotFoundError):  # unknown
        world.service.propose(SCOPE, goal_id=UUID(int=0x699), content=content(), generator=ENGINE)
    world.goals.add(
        OwnerScope(OTHER_ATHLETE), replace(goal(UUID(int=0x698)), athlete_id=OTHER_ATHLETE)
    )
    with pytest.raises(NotFoundError):  # another athlete's goal
        world.service.propose(SCOPE, goal_id=UUID(int=0x698), content=content(), generator=ENGINE)
    world.goals.add(SCOPE, goal(UUID(int=0x697), status=GoalStatus.ACHIEVED))
    with pytest.raises(DomainValidationError):  # a closed goal
        world.service.propose(SCOPE, goal_id=UUID(int=0x697), content=content(), generator=ENGINE)
    world.ready = Readiness(ReadinessStatus.NOT_READY, (ReadinessInput.WEEKLY_AVAILABILITY,), ())
    with pytest.raises(PlanNotReadyError) as missing:
        world.service.propose(SCOPE, goal_id=GOAL, content=content(), generator=ENGINE)
    assert missing.value.missing == (ReadinessInput.WEEKLY_AVAILABILITY,)
    world.ready = Readiness(ReadinessStatus.PROVISIONAL, (), (ReadinessInput.ACTIVITY_HISTORY,))
    with pytest.raises(DomainValidationError):  # only a provisional plan
        world.service.propose(SCOPE, goal_id=GOAL, content=content(), generator=ENGINE)
    assert world.versions.list_all(SCOPE) == []
    made = world.service.propose(
        SCOPE, goal_id=GOAL, content=content(provisional=True), generator=ENGINE
    )
    assert made.content.provisional


@pytest.mark.req("FR-PLN-003")
def test_an_athlete_has_one_active_version_across_plans() -> None:
    world = PlanWorld()
    first = world.service.propose(SCOPE, goal_id=GOAL, content=content(), generator=ENGINE)
    world.activate(first.plan_id, 1)
    world.goals.add(SCOPE, goal(UUID(int=0x602)))
    other = world.service.propose(
        SCOPE, goal_id=UUID(int=0x602), content=content(), generator=ENGINE
    )
    world.activate(other.plan_id, 1)
    old = world.service.version(SCOPE, first.plan_id, 1)
    assert (old.status, old.superseded_reason) == (
        PlanStatus.SUPERSEDED,
        SupersededReason.ACTIVATED_NEWER_VERSION,
    )
    assert world.service.plan(SCOPE, first.plan_id).active_number is None
    assert world.service.active(SCOPE) == world.service.version(SCOPE, other.plan_id, 1)


@pytest.mark.req("SAFE-MED-003", "FR-PLN-003")
def test_a_hold_arriving_during_an_activation_is_not_bypassed() -> None:
    @contextmanager
    def hold_arrives() -> Iterator[None]:
        world.conditions.activate(SCOPE, Condition.SAFETY_HOLD, reason="RF_CHEST_PAIN")
        yield

    world = PlanWorld()
    first = world.service.propose(SCOPE, goal_id=GOAL, content=content(), generator=ENGINE)
    world.service._batch = hold_arrives  # the hold lands after the first decision
    with pytest.raises(ConcurrencyConflictError):
        world.activate(first.plan_id, 1)
    assert world.service.version(SCOPE, first.plan_id, 1).status is PlanStatus.PROPOSED
    assert world.service.plan(SCOPE, first.plan_id).active_number is None


@pytest.mark.req("SAFE-MED-003", "FR-STR-004")
def test_the_harder_of_rpe_and_rir_is_the_effort_of_a_strength_exercise() -> None:
    workout = strength(MONDAY, rpe=5)
    exercise = workout.strength.exercises[0]  # type: ignore[union-attr]
    to_failure = replace(exercise, target_rir=0)  # RPE 5 and no repetition in reserve
    contradictory = replace(
        workout,
        strength=replace(workout.strength, exercises=(to_failure,)),  # type: ignore[type-var]
    )
    assert (contradictory.max_rpe, contradictory.intensive) == (10.0, True)
