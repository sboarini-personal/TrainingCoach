"""Plan policies of policy set 1.2.0 (policy spec 2 and 5; FR-POL-001, FR-POL-002, FR-PRF-005,
FR-PRF-006, SAFE-MED-003; AT-POL-001, AT-SAFE-001 policy part). Synthetic data only.

The sample week (``plan_samples.week``) starting Monday 2026-10-26: rest on Monday, goblet
squats at RPE 7 on Tuesday (45 min), an easy 40 min run on Wednesday, 60 min of threshold
intervals up to RPE 8 on Thursday and an easy 60 min run on Saturday.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, time, timedelta
from typing import Any
from uuid import UUID

import pytest

from coach.application.audit import ActorKind
from coach.application.events import EventName
from coach.application.policy_gate import PolicyDeniedError
from coach.application.recovery import CheckInAnswers, RecoveryService
from coach.application.screening import SecretScreen
from coach.domain.athlete.health import (
    HealthConstraint,
    HealthDeclaration,
    Restriction,
    RestrictionKind,
)
from coach.domain.athlete.resources import (
    Equipment,
    PoolAccess,
    Resources,
    TennisSessionType,
    TennisSlot,
)
from coach.domain.availability.availability import (
    Availability,
    AvailabilityException,
    DayDefaults,
    TimeWindow,
    WeeklyAvailability,
)
from coach.domain.coaching.state import Actor, Condition, LifecycleState
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.sport import Sport
from coach.domain.common.time import Weekday
from coach.domain.consent.ledger import ConsentType
from coach.domain.plans.plan import PlanContent, PlanStatus
from coach.domain.plans.workout import EvaluationSession, PlannedWorkout, WorkoutType
from coach.domain.recovery.checkin import DailyCheckIn, RedFlag
from coach.domain.strength.catalogue import CATALOGUE
from coach.domain.strength.equipment import equipment_available
from coach.domain.strength.prescription import EffortCue
from coach.infrastructure.memory import InMemoryRepository
from coach.policy.engine import Action, ActionKind, Outcome, PolicyContext, evaluate
from coach.policy.plans import PlanningContext, equipment, health, plan_reasons
from coach.policy.reasons import PolicyReason
from plan_samples import MONDAY, content, easy_run, next_id, rest, strength, tennis, week
from plan_world import DECLARED, ENGINE, GOAL, PATTERN, SCOPE, PlanWorld, open_planning
from samples import ATHLETE, BUENOS_AIRES

TUESDAY, WEDNESDAY, THURSDAY = (MONDAY + timedelta(days=offset) for offset in (1, 2, 3))
FRIDAY, SATURDAY = MONDAY + timedelta(days=4), MONDAY + timedelta(days=5)
NOTE = "Synthetic knee note"


def found(reasons: list[PolicyReason], *names: str) -> list[tuple[str | None, ...]]:
    """Each reason as its code followed by the named parameters."""
    return [(item.code, *(item.parameter(name) for name in names)) for item in reasons]


def evaluation(day: date) -> PlannedWorkout:
    return PlannedWorkout(
        workout_id=next_id(),
        type=WorkoutType.EVALUATION,
        training_day=day,
        order=0,
        is_key=True,
        objective="Submaximal test",
        what="Submaximal heart rate test",
        why="A baseline",
        evaluation=EvaluationSession(
            sport=Sport.RUNNING,
            protocol_id="eval.submaximal_run",
            description="Steady 20 minutes",
            submaximal=True,
            requires_opt_in=False,
            safety_notes=("Stop on red flags",),
        ),
    )


def constraint(*restrictions: Restriction, **fields: Any) -> HealthDeclaration:
    made = HealthConstraint(
        constraint_id=UUID(int=0x700),
        description=NOTE,
        restrictions=restrictions,
        declared_at=DECLARED,
        **fields,
    )
    return HealthDeclaration(declared_at=DECLARED, constraints=(made,))


def one_week(plan: dict[int, list[PlannedWorkout]]) -> PlanContent:
    return content(week(MONDAY, {0: [rest(MONDAY)], **plan}))


# --------------------------------------------------------------------------- engine


@pytest.mark.req("FR-POL-001")
def test_an_open_context_allows_the_sample_plan_and_the_set_is_versioned() -> None:
    action = Action(kind=ActionKind.PROPOSE, name="plan.propose", plan=content())
    context = PolicyContext(ATHLETE, frozenset(ConsentType), frozenset(), planning=open_planning())
    decision = evaluate(action, context)
    assert (decision.outcome, decision.reasons, decision.policy_set_version) == (
        Outcome.ALLOW,
        (),
        "1.2.0",
    )
    assert evaluate(action, context) == decision  # deterministic
    with pytest.raises(DomainValidationError):  # the gate always assembles the context
        evaluate(action, replace(context, planning=None))


# --------------------------------------------------------------------------- conditions


@pytest.mark.req("SAFE-MED-003", "FR-POL-001")
def test_holds_and_blocks_deny_intensity_only_from_today_and_until_reevaluation() -> None:
    plan = content()
    held = plan_reasons(plan, frozenset({Condition.SAFETY_HOLD}), open_planning())
    assert found(held, "day") == [
        ("SAFETY_HOLD_ACTIVE", "2026-10-27"),
        ("SAFETY_HOLD_ACTIVE", "2026-10-29"),
    ]
    easy = one_week({2: [easy_run(WEDNESDAY)], 4: [tennis(FRIDAY, rpe=5)]})
    assert plan_reasons(easy, frozenset({Condition.SAFETY_HOLD}), open_planning()) == []
    later = open_planning(today=WEDNESDAY)  # Tuesday is history
    assert found(plan_reasons(plan, frozenset({Condition.SAFETY_HOLD}), later), "day") == [
        ("SAFETY_HOLD_ACTIVE", "2026-10-29")
    ]
    blocked = frozenset({Condition.RECOVERY_BLOCK})
    until_thursday = open_planning(recovery_block_until=THURSDAY)
    assert found(plan_reasons(plan, blocked, until_thursday), "day", "reevaluateAfter") == [
        ("RECOVERY_BLOCK_ACTIVE", "2026-10-27", "2026-10-29")
    ]
    assert found(plan_reasons(plan, blocked, open_planning()), "day") == [  # end unknown
        ("RECOVERY_BLOCK_ACTIVE", "2026-10-27"),
        ("RECOVERY_BLOCK_ACTIVE", "2026-10-29"),
    ]
    assert plan_reasons(plan, frozenset(), until_thursday) == []  # the condition decides


# --------------------------------------------------------------------------- availability


@pytest.mark.req("FR-PRF-005", "FR-PRF-004")
def test_days_windows_and_weekly_limits_bound_the_plan() -> None:
    recurring = WeeklyAvailability(
        days_per_week=3,
        maximum_hours_per_week=2,
        preferred_rest_days=frozenset({Weekday.THURSDAY}),
        daily_windows={Weekday.TUESDAY: (TimeWindow(time(18), time(18, 30)),)},
    )
    planning = open_planning(
        availability=Availability.new(ATHLETE, recurring=recurring, defaults=DayDefaults(120))
    )
    reasons = plan_reasons(content(), frozenset(), planning)
    assert found(reasons, "day", "plannedMinutes", "maximumMinutes") == [
        ("DAY_UNAVAILABLE", "2026-10-29", None, None),  # the preferred rest day
        ("DAY_DURATION_EXCEEDED", "2026-10-27", "45", "30"),  # the window is 30 minutes
        ("TOO_MANY_TRAINING_DAYS", None, None, None),
        ("WEEK_DURATION_EXCEEDED", None, None, None),
    ]
    assert found(reasons[2:], "weekStart", "trainingDays", "maximumDays") == [
        ("TOO_MANY_TRAINING_DAYS", "2026-10-26", "4", "3"),
        ("WEEK_DURATION_EXCEEDED", "2026-10-26", None, None),
    ]
    assert reasons[3].parameter("plannedHours") == "3.4"
    windows_only = Availability.new(
        ATHLETE,
        recurring=replace(recurring, days_per_week=7, maximum_hours_per_week=20),
    )
    undefined = plan_reasons(content(), frozenset(), open_planning(availability=windows_only))
    assert found(undefined, "day")[:2] == [
        ("DAY_NOT_PLANNABLE", "2026-10-28"),  # no window and no default: not plannable
        ("DAY_UNAVAILABLE", "2026-10-29"),
    ]
    unknown = plan_reasons(content(), frozenset(), open_planning(availability=None))
    assert [item.code for item in unknown] == ["DAY_NOT_PLANNABLE"] * 4 + [
        "WEEKLY_PATTERN_MISSING"  # no weekly limits known: unknown never allows
    ]
    defaults_only = Availability.new(ATHLETE, defaults=DayDefaults(240))
    assert [
        item.code
        for item in plan_reasons(content(), frozenset(), open_planning(availability=defaults_only))
    ] == ["WEEKLY_PATTERN_MISSING"]


@pytest.mark.req("FR-PRF-005")
def test_exceptions_double_sessions_and_unknown_durations_are_denied() -> None:
    running_only = AvailabilityException(
        day=TUESDAY,
        available=True,
        maximum_duration_minutes=60,
        allowed_sports=frozenset({Sport.RUNNING}),
    )
    defaults = Availability.new(
        ATHLETE, recurring=PATTERN, defaults=DayDefaults(240)
    ).with_exception(running_only)
    reasons = plan_reasons(content(), frozenset(), open_planning(availability=defaults))
    assert found(reasons, "day", "sport") == [("SPORT_NOT_ALLOWED", "2026-10-27", "STRENGTH")]
    double = one_week({2: [easy_run(WEDNESDAY), strength(WEDNESDAY, order=1)]})
    assert found(plan_reasons(double, frozenset(), open_planning()), "day", "sessions") == [
        ("DOUBLE_SESSION_NOT_ALLOWED", "2026-10-28", "2")
    ]
    doubles = Availability.new(
        ATHLETE,
        recurring=WeeklyAvailability(
            days_per_week=5, maximum_hours_per_week=10, double_sessions_allowed=True
        ),
        defaults=DayDefaults(240),
    )
    assert plan_reasons(double, frozenset(), open_planning(availability=doubles)) == []
    measured = one_week({2: [evaluation(WEDNESDAY)]})
    assert found(plan_reasons(measured, frozenset(), open_planning()), "day") == [
        ("DURATION_UNKNOWN", "2026-10-28")
    ]


@pytest.mark.req("FR-PRF-005", "FR-TEN-001")
def test_the_athletes_own_tennis_is_load_but_not_placed_by_the_plan() -> None:
    slot = TennisSlot(Weekday.THURSDAY, 19 * 60, 90, TennisSessionType.PRACTICE)
    recurring = WeeklyAvailability(
        days_per_week=1, maximum_hours_per_week=1, preferred_rest_days=frozenset({Weekday.THURSDAY})
    )
    planning = open_planning(
        availability=Availability.new(ATHLETE, recurring=recurring, defaults=DayDefaults(60)),
        resources=Resources(tennis_schedule=(slot,)),
    )
    own = one_week({2: [easy_run(WEDNESDAY)], 3: [tennis(THURSDAY, minutes=90)]})
    assert plan_reasons(own, frozenset(), planning) == []
    longer = one_week({2: [easy_run(WEDNESDAY)], 3: [tennis(THURSDAY, minutes=120)]})
    assert [item.code for item in plan_reasons(longer, frozenset(), planning)] == [
        "DAY_UNAVAILABLE",  # not the declared session: the plan placed it
        "TOO_MANY_TRAINING_DAYS",
        "WEEK_DURATION_EXCEEDED",
    ]
    away = AvailabilityException(day=THURSDAY, available=False, reason="Travel")
    travelling = replace(planning, availability=planning.availability.with_exception(away))  # type: ignore[union-attr]
    assert found(plan_reasons(own, frozenset(), travelling), "day") == [
        ("DAY_UNAVAILABLE", "2026-10-29")  # the athlete's own date-specific statement
    ]
    held = plan_reasons(
        one_week({3: [tennis(THURSDAY, minutes=90, rpe=8)]}),
        frozenset({Condition.SAFETY_HOLD}),
        planning,
    )
    assert [item.code for item in held] == ["SAFETY_HOLD_ACTIVE"]  # safety still applies


# --------------------------------------------------------------------------- equipment


@pytest.mark.req("FR-STR-002", "FR-PRF-004")
def test_equipment_rules_read_bench_as_needed_and_the_rest_as_alternatives() -> None:
    def usable(exercise: str, *owned: Equipment) -> bool:
        return equipment_available(CATALOGUE.get(exercise), frozenset(owned))

    assert usable("ex.goblet_squat", Equipment.KETTLEBELLS)
    assert not usable("ex.goblet_squat", Equipment.BARBELL)
    assert not usable("ex.bench_press", Equipment.BARBELL)
    assert usable("ex.bench_press", Equipment.BARBELL, Equipment.BENCH)
    assert not usable("ex.hip_thrust", Equipment.BENCH)
    assert usable("ex.bodyweight_squat")
    everything = frozenset(Equipment)
    assert all(equipment_available(item, everything) for item in CATALOGUE)


@pytest.mark.req("FR-PRF-004", "FR-POL-001")
def test_missing_equipment_pool_or_bicycle_is_denied() -> None:
    plan = one_week(
        {
            1: [strength(TUESDAY, exercise="ex.back_squat")],
            2: [easy_run(WEDNESDAY, sport=Sport.SWIMMING)],
            5: [easy_run(SATURDAY, sport=Sport.CYCLING)],
        }
    )
    planning = open_planning(
        resources=Resources(strength_equipment=frozenset({Equipment.DUMBBELLS}))
    )
    assert found(equipment(plan, planning), "day", "exerciseId") == [
        ("EQUIPMENT_MISSING", "2026-10-27", "ex.back_squat"),
        ("POOL_REQUIRED", "2026-10-28", None),
        ("BICYCLE_REQUIRED", "2026-10-31", None),
    ]
    trainer = open_planning(
        resources=Resources(
            pool=PoolAccess(available=True),
            smart_trainer=True,
            strength_equipment=frozenset({Equipment.BARBELL}),
        )
    )
    assert equipment(plan, trainer) == []
    unknown = one_week(
        {1: [strength(TUESDAY)], 2: [strength(WEDNESDAY, exercise="ex.bodyweight_squat")]}
    )
    assert found(equipment(unknown, open_planning(resources=None)), "exerciseId") == [
        ("EQUIPMENT_MISSING", "ex.goblet_squat")  # bodyweight work needs nothing
    ]


# --------------------------------------------------------------------------- health


@pytest.mark.req("FR-PRF-006", "SAFE-MED-001")
def test_structured_restrictions_deny_what_they_name() -> None:
    plan = content()
    assert found(health(plan, open_planning(health=None))) == [("HEALTH_NOT_DECLARED",)]
    capped = open_planning(health=constraint(Restriction(RestrictionKind.MAX_RPE, max_rpe=6)))
    assert found(health(plan, capped), "day", "maxRpe") == [
        ("RPE_ABOVE_LIMIT", "2026-10-27", "6"),
        ("RPE_ABOVE_LIMIT", "2026-10-29", "6"),
    ]
    impact_running = open_planning(
        health=constraint(
            Restriction(RestrictionKind.NO_IMPACT), affected_sports=frozenset({Sport.RUNNING})
        )
    )
    with_tennis = one_week({2: [easy_run(WEDNESDAY)], 4: [tennis(FRIDAY)]})
    assert found(health(with_tennis, impact_running), "day") == [
        ("IMPACT_RESTRICTED", "2026-10-28")  # tennis is outside the declared sports
    ]
    impact = open_planning(health=constraint(Restriction(RestrictionKind.NO_IMPACT)))
    jumps = one_week({1: [strength(TUESDAY, exercise="ex.jump_squat")], 4: [tennis(FRIDAY)]})
    assert found(health(jumps, impact), "day", "exerciseId") == [
        ("IMPACT_RESTRICTED", "2026-10-27", "ex.jump_squat"),
        ("IMPACT_RESTRICTED", "2026-10-30", None),
    ]
    no_strength = open_planning(
        health=constraint(Restriction(RestrictionKind.NO_SPORT, sport=Sport.STRENGTH))
    )
    assert found(health(plan, no_strength), "day", "sport") == [
        ("SPORT_RESTRICTED", "2026-10-27", "STRENGTH")
    ]
    overhead = open_planning(health=constraint(Restriction(RestrictionKind.NO_OVERHEAD)))
    pressing = one_week(
        {
            1: [strength(TUESDAY, exercise="ex.overhead_press")],
            2: [easy_run(WEDNESDAY, sport=Sport.SWIMMING)],
        }
    )
    assert found(health(pressing, overhead), "exerciseId") == [
        ("OVERHEAD_RESTRICTED", "ex.overhead_press"),
        ("OVERHEAD_RESTRICTED", None),  # swimming
    ]


@pytest.mark.req("FR-PRF-006", "FR-STR-001")
def test_heavy_lower_body_and_affected_regions_allow_only_easy_work() -> None:
    heavy = open_planning(health=constraint(Restriction(RestrictionKind.NO_HEAVY_LOWER_BODY)))
    knees = open_planning(health=constraint(affected_regions=("KNEES",)))
    for planning in (heavy, knees):
        hard = one_week({1: [strength(TUESDAY, rpe=7)]})
        assert len(health(hard, planning)) == 1
        easy = one_week({1: [strength(TUESDAY, cue=EffortCue.EASY)]})
        assert health(easy, planning) == []
    assert found(health(one_week({1: [strength(TUESDAY, rpe=6)]}), knees), "region") == [
        ("REGION_RESTRICTED", "KNEES")  # RPE 6 is a hard set (strength spec 8)
    ]
    upper = one_week({1: [strength(TUESDAY, exercise="ex.overhead_press", rpe=8)]})
    assert health(upper, heavy) == health(upper, knees) == []
    expired = open_planning(
        health=constraint(
            Restriction(RestrictionKind.MAX_RPE, max_rpe=3), valid_until=MONDAY - timedelta(days=1)
        )
    )
    assert health(content(), expired) == []
    other = open_planning(health=constraint(Restriction(RestrictionKind.OTHER)))
    assert health(content(), other) == []  # free text is not checked by code
    everything = plan_reasons(content(), frozenset({Condition.SAFETY_HOLD}), capped_all())
    assert everything
    assert all(NOTE not in value for item in everything for _, value in item.parameters)


def capped_all() -> PlanningContext:
    return open_planning(
        health=constraint(
            Restriction(RestrictionKind.MAX_RPE, max_rpe=2),
            Restriction(RestrictionKind.NO_IMPACT),
            affected_regions=("KNEES",),
        )
    )


# --------------------------------------------------------------------------- service


VIOLATIONS: dict[str, tuple[str, str]] = {
    "unavailable day": ("POL-AVAILABILITY", "DAY_UNAVAILABLE"),
    "health constraint": ("POL-HEALTH-CONSTRAINT", "RPE_ABOVE_LIMIT"),
    "safety hold": ("POL-SAFETY-HOLD", "SAFETY_HOLD_ACTIVE"),
}


@pytest.mark.req("FR-POL-001", "FR-POL-002", "FR-PRF-006", "NFR-OBS-001")
@pytest.mark.acceptance("AT-POL-001")
@pytest.mark.parametrize("violation", sorted(VIOLATIONS))
def test_a_violating_proposal_is_denied_however_often_the_agent_insists(violation: str) -> None:
    world = PlanWorld()
    world.to_goal_defined()
    match violation:
        case "unavailable day":
            recurring = WeeklyAvailability(
                days_per_week=5,
                maximum_hours_per_week=10,
                preferred_rest_days=frozenset({Weekday.THURSDAY}),
            )
            world.planning = open_planning(
                availability=Availability.new(
                    ATHLETE, recurring=recurring, defaults=DayDefaults(240)
                )
            )
        case "health constraint":
            world.planning = open_planning(
                health=constraint(Restriction(RestrictionKind.MAX_RPE, max_rpe=5))
            )
        case _:
            world.conditions.activate(SCOPE, Condition.SAFETY_HOLD, reason="RF_CHEST_PAIN")
    policy, code = VIOLATIONS[violation]
    for _attempt in range(3):
        with pytest.raises(PolicyDeniedError) as denied:
            world.service.propose(
                SCOPE, goal_id=GOAL, content=content(), generator=ENGINE, actor=ActorKind.AGENT
            )
        assert (policy, code) in {
            (item.policy, item.code) for item in denied.value.decision.reasons
        }
    assert world.versions.list_all(SCOPE) == []  # nothing persisted as PROPOSED
    assert world.headers.list_all(SCOPE) == []
    assert world.lifecycle.current(SCOPE).lifecycle is LifecycleState.GOAL_DEFINED
    denials = [
        record.document
        for record in world.audit.all_records()
        if record.document["action"] == "policy.evaluate"
    ]
    assert len(denials) == 3
    assert all(item["outcome"] == "DENIED" and item["actor"] == "AGENT" for item in denials)
    assert all(code in item["reasonCodes"] for item in denials)
    assert (
        EventName.AUTHZ_DENIED,
        {"action": "plan.propose", "policy": policy},
    ) in world.events.events


@pytest.mark.req("SAFE-MED-002", "SAFE-MED-003", "SAFE-MED-005")
@pytest.mark.acceptance("AT-SAFE-001")
def test_chest_pain_blocks_intensive_proposals_and_activation_until_the_athlete_releases() -> None:
    world = PlanWorld()
    world.to_goal_defined()
    plan = world.service.propose(SCOPE, goal_id=GOAL, content=content(), generator=ENGINE)
    recovery = RecoveryService(
        InMemoryRepository[DailyCheckIn](),
        conditions=world.conditions,
        screen=SecretScreen(world.events),
        timezone_of=lambda _scope: BUENOS_AIRES,
        events=world.events,
        clock=world.clock,
        ids=lambda: UUID(int=0x900),
    )
    result = recovery.record_checkin(
        SCOPE, CheckInAnswers(notes="Ayer senti dolor en el pecho corriendo")
    )
    assert RedFlag.RF_CHEST_PAIN in result.red_flags
    with pytest.raises(PolicyDeniedError):  # an intensive recommendation
        world.service.propose(SCOPE, goal_id=GOAL, content=content(), generator=ENGINE)
    with pytest.raises(PolicyDeniedError):  # any plan activation
        world.activate(plan.plan_id, 1)
    easy = one_week({2: [easy_run(WEDNESDAY)]})
    proposed = world.service.propose(
        SCOPE, goal_id=GOAL, content=easy, generator=ENGINE, plan_id=plan.plan_id
    )
    assert proposed.status is PlanStatus.PROPOSED  # easy work can wait for the release
    world.conditions.clear(
        SCOPE, Condition.SAFETY_HOLD, reason="CLEARED_BY_PROFESSIONAL", actor=Actor.ATHLETE
    )
    assert world.activate(plan.plan_id, 1).status is PlanStatus.ACTIVE


@pytest.mark.req("FR-PRF-005", "FR-TEN-001")
def test_a_declared_tennis_slot_covers_one_session_only() -> None:
    slot = TennisSlot(Weekday.THURSDAY, 19 * 60, 90, TennisSessionType.PRACTICE)
    recurring = WeeklyAvailability(
        days_per_week=1, maximum_hours_per_week=1, preferred_rest_days=frozenset({Weekday.THURSDAY})
    )
    planning = open_planning(
        availability=Availability.new(ATHLETE, recurring=recurring, defaults=DayDefaults(60)),
        resources=Resources(tennis_schedule=(slot,)),
    )
    twice = one_week({3: [tennis(THURSDAY, minutes=90), tennis(THURSDAY, minutes=90)]})
    reasons = plan_reasons(twice, frozenset(), planning)
    assert [item.code for item in reasons] == ["DAY_UNAVAILABLE", "WEEK_DURATION_EXCEEDED"]
    second = twice.weeks[0].days[3][1]
    assert reasons[0].parameter("workoutId") == str(second.workout_id)


@pytest.mark.req("FR-PRF-006", "SAFE-MED-003")
def test_a_strength_target_is_read_at_the_harder_of_rpe_and_rir() -> None:
    base = strength(TUESDAY, rpe=5)
    exercise = base.strength.exercises[0]  # type: ignore[union-attr]
    hard = replace(
        base,
        strength=replace(  # type: ignore[type-var]
            base.strength, exercises=(replace(exercise, target_rir=0),)
        ),
    )
    plan = one_week({1: [hard]})
    capped = open_planning(health=constraint(Restriction(RestrictionKind.MAX_RPE, max_rpe=6)))
    assert [item.code for item in health(plan, capped)] == ["RPE_ABOVE_LIMIT"]
    knees = open_planning(health=constraint(affected_regions=("KNEES",)))
    assert [item.code for item in health(plan, knees)] == ["REGION_RESTRICTED"]
    held = plan_reasons(plan, frozenset({Condition.SAFETY_HOLD}), open_planning())
    assert [item.code for item in held] == ["SAFETY_HOLD_ACTIVE"]
