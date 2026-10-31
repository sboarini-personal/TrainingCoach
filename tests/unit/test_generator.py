"""Plan generator rules 1.0.0 (planning spec 5.1; FR-PLN-001, FR-PLN-005, FR-PLN-006,
FR-PLN-008, FR-STR-001, FR-STR-007, FR-ONB-004, FR-ONB-005; AT-STR-001, AT-STR-002,
AT-ONB-003, AT-ONB-004, AT-CYC-001). Synthetic data only.

Default inputs: today Sunday 2026-10-25, so the plan starts on Monday 2026-10-26; a 10 km goal
on Sunday 2027-03-14 (20 weeks); five days and 8 hours a week, Monday a rest day, 2 hours a
day; dumbbells, kettlebells and a bench, a pool and a bicycle; no health constraint; strength
experience with RPE, two sessions a week; three years of running and 8 weeks of history.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, time, timedelta
from typing import Any
from uuid import UUID

import pytest

from coach.application.contracts.plans import content_hash
from coach.domain.athlete.experience import StrengthExperience
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
from coach.domain.coaching.state import Condition
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.sport import Sport
from coach.domain.common.time import Weekday
from coach.domain.onboarding.readiness import ReadinessInput
from coach.domain.plans.plan import Phase, PlanContent
from coach.domain.plans.workout import Repeat, Step, WorkoutType
from coach.domain.strength.catalogue import CATALOGUE
from coach.domain.strength.vocabulary import InterferenceLevel
from coach.planning.generator import (
    GenerationInputs,
    Progression,
    generate,
    phases,
    progression,
    session_load,
    volumes,
)
from coach.planning.interference import assess
from coach.policy.plans import PlanningContext, plan_reasons
from plan_world import DECLARED
from samples import ATHLETE

TODAY = date(2026, 10, 25)
START = date(2026, 10, 26)
TARGET = date(2027, 3, 14)
GOAL = UUID(int=0x601)
PATTERN = WeeklyAvailability(
    days_per_week=5,
    maximum_hours_per_week=8,
    preferred_rest_days=frozenset({Weekday.MONDAY}),
)
RESOURCES = Resources(
    pool=PoolAccess(available=True, length_m=25),
    bicycle=True,
    gym=True,
    strength_equipment=frozenset({Equipment.DUMBBELLS, Equipment.KETTLEBELLS, Equipment.BENCH}),
)
HISTORY = (120.0, 130.0, 150.0, 140.0, 160.0, 150.0, 170.0, 160.0)


def inputs(**changes: Any) -> GenerationInputs:
    made = GenerationInputs(
        today=TODAY,
        goal_id=GOAL,
        sport=Sport.RUNNING,
        target_date=TARGET,
        availability=Availability.new(ATHLETE, recurring=PATTERN, defaults=DayDefaults(120)),
        resources=RESOURCES,
        health=HealthDeclaration(declared_at=DECLARED),
        strength=StrengthExperience(years_experience=2, sessions_per_week=2, rpe_familiar=True),
        sport_years=3,
        weekly_minutes=HISTORY,
    )
    return replace(made, **changes)


def constraint(*restrictions: Restriction, **fields: Any) -> HealthDeclaration:
    made = HealthConstraint(
        constraint_id=UUID(int=0x700),
        description="Synthetic note",
        restrictions=restrictions,
        declared_at=DECLARED,
        **fields,
    )
    return HealthDeclaration(declared_at=DECLARED, constraints=(made,))


def planning_of(given: GenerationInputs) -> PlanningContext:
    return PlanningContext(
        today=given.today,
        availability=given.availability,
        health=given.health,
        resources=given.resources,
        recovery_block_until=given.recovery_block_until,
    )


def conditions_of(given: GenerationInputs) -> frozenset[Condition]:
    found = set()
    if given.safety_hold:
        found.add(Condition.SAFETY_HOLD)
    if given.recovery_block:
        found.add(Condition.RECOVERY_BLOCK)
    return frozenset(found)


def allowed(given: GenerationInputs) -> PlanContent:
    """Generate and check the plan against every plan policy (it must pass them all)."""
    content = generate(given)
    reasons = plan_reasons(content, conditions_of(given), planning_of(given))
    assert reasons == [], [(item.code, item.parameters) for item in reasons]
    return content


# --------------------------------------------------------------------------- rules


@pytest.mark.req("FR-PLN-001")
@pytest.mark.parametrize(
    ("weeks", "adaptation", "expected"),
    [
        (1, False, "S"),
        (3, False, "BBS"),
        (5, False, "BBUST"),
        (10, False, "BBBBUUSSTT"),
        (12, True, "AABBBBUUSSTT"),
        (20, True, "AABBBBBBBBUUUUUSSSTT"),
    ],
)
def test_phases_run_from_base_to_taper(weeks: int, adaptation: bool, expected: str) -> None:
    letters = {
        Phase.ADAPTATION: "A",
        Phase.BASE: "B",
        Phase.BUILD: "U",
        Phase.SPECIFIC: "S",
        Phase.TAPER: "T",
    }
    assert "".join(letters[item] for item in phases(weeks, adaptation=adaptation)) == expected


@pytest.mark.req("FR-PLN-006")
def test_volume_starts_from_history_and_grows_by_the_athletes_own_steps() -> None:
    plan = progression(inputs(), provisional=False)
    assert (plan.start, plan.step, plan.peak) == (160, 20, 230)  # mean of the last 4
    # weeks; the largest increase; the highest week (170) plus three steps
    fresh = progression(inputs(weekly_minutes=()), provisional=True)
    assert (fresh.start, fresh.step, fresh.peak) == (90, 5, 105)  # conservative, halved
    assert "halved: provisional plan" in fresh.step_reason
    novice = progression(inputs(sport_years=0.5), provisional=False)
    assert novice.step == 10
    big = progression(inputs(weekly_minutes=(60, 200, 210, 220)), provisional=False)
    assert big.step == 30  # never more than 30 minutes
    weekly = volumes(
        [Phase.BASE] * 7 + [Phase.TAPER] * 2,
        Progression(100, 10, "start", "step", 135),
        held_weeks=0,
    )
    assert [minutes for minutes, _ in weekly] == [100, 110, 120, 100, 130, 135, 135, 90, 60]
    assert "held at the peak" in weekly[6][1]
    assert "unloading" in weekly[3][1]
    assert "Bosquet 2007" in weekly[7][1]  # 65% of the last loading week (135)
    held = volumes([Phase.BASE] * 3, Progression(100, 10, "start", "step", 200), held_weeks=2)
    assert [minutes for minutes, _ in held] == [100, 100, 110]


# --------------------------------------------------------------------------- plans


@pytest.mark.req("FR-PLN-001", "FR-PLN-004", "FR-PLN-008", "FR-STR-001", "FR-POL-001")
@pytest.mark.acceptance("AT-STR-002")
def test_a_goal_plan_runs_to_the_target_and_has_strength_every_week() -> None:
    content = allowed(inputs())
    assert (content.start, len(content.weeks), content.provisional) == (START, 20, False)
    assert [block.phase for block in content.phases] == [
        Phase.BASE,
        Phase.BUILD,
        Phase.SPECIFIC,
        Phase.TAPER,
    ]
    assert all(week.strength_sessions for week in content.weeks)
    # Five days: the runs come before a second strength session; with every day free the
    # second one (two declared sessions a week) is planned, except while tapering.
    every = Availability.new(
        ATHLETE,
        recurring=replace(PATTERN, days_per_week=7, preferred_rest_days=frozenset()),
        defaults=DayDefaults(120),
    )
    roomy = allowed(inputs(availability=every))
    assert all(len(week.strength_sessions) == 2 for week in roomy.weeks[:-2])
    assert all(len(week.strength_sessions) == 1 for week in roomy.weeks[-2:])
    assert not content.on(TARGET)  # the goal itself is not a workout
    assert [item.type for item in content.on(TARGET - timedelta(days=1))] == [WorkoutType.REST]
    assert all(
        item.training_day.isoweekday() != 1
        for item in content.workouts
        if item.type is not WorkoutType.REST
    )
    assert generate(inputs()) == content  # deterministic, identifiers included
    assert content_hash(generate(inputs())) == content_hash(content)
    first = content.weeks[0]
    assert "Running 160 min: starting from your recent average" in first.primary_goal


@pytest.mark.req("FR-STR-007", "FR-STR-001")
@pytest.mark.acceptance("AT-STR-001", "AT-TEN-001")
def test_heavy_legs_never_land_before_hard_intervals_or_a_long_run() -> None:
    slot = TennisSlot(Weekday.WEDNESDAY, 19 * 60, 120, TennisSessionType.MATCH)
    for given in (
        inputs(),
        inputs(resources=replace(RESOURCES, tennis_schedule=(slot,))),
        inputs(
            availability=Availability.new(
                ATHLETE, recurring=replace(PATTERN, days_per_week=4), defaults=DayDefaults(150)
            )
        ),
    ):
        content = allowed(given)
        loads = [
            load for item in content.workouts if (load := session_load(item, CATALOGUE)) is not None
        ]
        levels = {item.level for item in assess(loads)}
        assert not levels & {InterferenceLevel.HIGH, InterferenceLevel.VERY_HIGH}
        expected = {
            item.strength.expected_interference.level
            for item in content.workouts
            if item.strength is not None
        }
        assert expected <= {InterferenceLevel.LOW, InterferenceLevel.MODERATE}


@pytest.mark.req("FR-PRF-005", "FR-PRF-006", "FR-POL-001", "SAFE-MED-003")
@pytest.mark.parametrize(
    "case",
    [
        "windows",
        "tennis",
        "max rpe",
        "no heavy legs",
        "knees",
        "recovery block",
        "unknown block end",
        "safety hold",
        "swimming",
        "cycling",
        "no equipment",
        "cue only",
        "short goal",
    ],
)
def test_generated_plans_pass_every_plan_policy(case: str) -> None:
    windows = replace(
        PATTERN,
        daily_windows={
            Weekday.TUESDAY: (TimeWindow(time(18), time(19)),),
            Weekday.THURSDAY: (TimeWindow(time(6), time(7)), TimeWindow(time(18), time(19))),
        },
    )
    match case:
        case "windows":
            given = inputs(
                availability=Availability.new(ATHLETE, recurring=windows, defaults=DayDefaults(90))
            )
        case "tennis":
            slots = (
                TennisSlot(Weekday.TUESDAY, 19 * 60, 90, TennisSessionType.PRACTICE),
                TennisSlot(Weekday.SATURDAY, 10 * 60, 120, TennisSessionType.MATCH),
            )
            given = inputs(resources=replace(RESOURCES, tennis_schedule=slots))
        case "max rpe":
            given = inputs(health=constraint(Restriction(RestrictionKind.MAX_RPE, max_rpe=6)))
        case "no heavy legs":
            given = inputs(health=constraint(Restriction(RestrictionKind.NO_HEAVY_LOWER_BODY)))
        case "knees":
            given = inputs(health=constraint(affected_regions=("KNEES",)))
        case "recovery block":
            given = inputs(recovery_block=True, recovery_block_until=START + timedelta(days=3))
        case "unknown block end":
            given = inputs(recovery_block=True)
        case "safety hold":
            given = inputs(safety_hold=True)
        case "swimming":
            given = inputs(sport=Sport.SWIMMING)
        case "cycling":
            given = inputs(
                sport=Sport.CYCLING, resources=replace(RESOURCES, bicycle=None, smart_trainer=True)
            )
        case "no equipment":
            given = inputs(resources=Resources(strength_equipment=frozenset()))
        case "cue only":
            given = inputs(strength=StrengthExperience(years_experience=0.5, sessions_per_week=1))
        case _:
            given = inputs(target_date=START + timedelta(days=17))
    content = allowed(given)
    assert content.workouts


@pytest.mark.req("SAFE-MED-003", "FR-STR-001")
@pytest.mark.acceptance("AT-STR-002")
def test_what_is_left_out_is_stated() -> None:
    held = allowed(inputs(safety_hold=True))
    assert not any(item.intensive for item in held.workouts)
    assert not any(week.strength_sessions for week in held.weeks)
    assert "Strength is not planned while the safety hold is active." in held.assumptions
    restricted = allowed(
        inputs(health=constraint(Restriction(RestrictionKind.NO_SPORT, sport=Sport.STRENGTH)))
    )
    assert not any(week.strength_sessions for week in restricted.weeks)
    assert (
        "Strength is not planned while an active health constraint restricts strength."
        in restricted.assumptions
    )
    match = TennisSlot(Weekday.THURSDAY, 19 * 60, 90, TennisSessionType.MATCH)
    paused = allowed(
        inputs(safety_hold=True, resources=replace(RESOURCES, tennis_schedule=(match,)))
    )
    assert not any(item.tennis is not None for item in paused.workouts)
    assert any(note.startswith("Tennis on Thursday is left out") for note in paused.assumptions)
    other = allowed(inputs(health=constraint(Restriction(RestrictionKind.OTHER))))
    assert any("free-text restriction" in note for note in other.assumptions)


@pytest.mark.req("FR-ONB-004")
@pytest.mark.acceptance("AT-ONB-003")
def test_missing_preferred_inputs_give_a_provisional_plan_with_its_assumptions() -> None:
    content = allowed(
        inputs(
            weekly_minutes=(),
            missing_preferred=frozenset(
                {ReadinessInput.ACTIVITY_HISTORY, ReadinessInput.RECOVERY_HISTORY}
            ),
        )
    )
    assert content.provisional
    assert content.assumptions[0].startswith("No activity in the last 8 weeks")
    assert content.assumptions[1].startswith("No check-in or recovery data")
    assert content.weeks[0].phase is Phase.ADAPTATION  # no history: an adaptation block
    assert "Running 90 min: starting from a conservative start" in content.weeks[0].primary_goal


@pytest.mark.req("FR-ONB-005", "FR-PLN-005", "FR-PRF-002")
@pytest.mark.acceptance("AT-ONB-004")
def test_baseline_collection_proposes_submaximal_assessments_and_no_thresholds() -> None:
    content = allowed(inputs(baseline=True, weekly_minutes=(0, 0, 30, 0)))
    assert len(content.weeks) == 2
    assert {block.phase for block in content.phases} == {Phase.ADAPTATION}
    assert content.provisional
    assert content.assumptions[0].startswith("Baseline collection")
    evaluations = [item.evaluation for item in content.workouts if item.evaluation is not None]
    assert len(evaluations) == 2
    assert all(item.submaximal and not item.requires_opt_in for item in evaluations)
    assert not any(item.intensive for item in content.workouts)
    steps = [
        step
        for item in content.workouts
        if item.endurance is not None
        for step, _ in item.endurance.steps()
    ]
    assert steps
    assert all(
        (step.target.heart_rate, step.target.pace, step.target.power) == (None, None, None)
        for step in steps
    )


@pytest.mark.req("FR-CYC-002", "FR-PLN-005")
@pytest.mark.acceptance("AT-CYC-001")
def test_a_cycling_plan_never_sets_a_power_target() -> None:
    content = allowed(inputs(sport=Sport.CYCLING))
    rides = [item.endurance for item in content.workouts if item.endurance is not None]
    assert rides
    assert all(ride.sport is Sport.CYCLING for ride in rides)
    for ride in rides:
        for part in (*ride.warmup, *ride.main_set, *ride.cooldown):
            steps = part.steps if isinstance(part, Repeat) else (part,)
            assert all(isinstance(step, Step) and step.target.power is None for step in steps)
    assert any("RPE (0 to 10) only" in note for note in content.assumptions)


@pytest.mark.req("FR-PLN-001")
def test_goals_the_rules_cannot_plan_are_refused() -> None:
    with pytest.raises(DomainValidationError):
        generate(inputs(sport=Sport.TENNIS))
    with pytest.raises(DomainValidationError):
        generate(inputs(sport=Sport.SWIMMING, resources=replace(RESOURCES, pool=None)))
    with pytest.raises(DomainValidationError):
        generate(inputs(sport=Sport.CYCLING, resources=replace(RESOURCES, bicycle=False)))
    with pytest.raises(DomainValidationError):
        generate(inputs(health=constraint(Restriction(RestrictionKind.NO_IMPACT))))
    with pytest.raises(DomainValidationError):
        generate(inputs(target_date=TODAY))
    far = allowed(inputs(target_date=START + timedelta(weeks=40)))
    assert len(far.weeks) == 26
    assert any("more than 26 weeks away" in note for note in far.assumptions)


# --------------------------------------------------------------------------- review of P12.5


@pytest.mark.req("FR-ONB-005", "FR-PRF-006", "FR-POL-001")
@pytest.mark.acceptance("AT-ONB-004")
def test_a_baseline_block_states_its_effort_so_a_max_rpe_allows_it() -> None:
    capped = constraint(
        Restriction(RestrictionKind.MAX_RPE, max_rpe=9), affected_sports=frozenset({Sport.RUNNING})
    )
    content = allowed(inputs(baseline=True, health=capped))
    evaluations = [item for item in content.workouts if item.evaluation is not None]
    assert evaluations
    assert all(item.max_rpe == 5 and not item.intensive for item in evaluations)
    low = allowed(
        inputs(baseline=True, health=constraint(Restriction(RestrictionKind.MAX_RPE, max_rpe=4)))
    )
    assert all(item.max_rpe == 4 for item in low.workouts if item.evaluation is not None)


@pytest.mark.req("FR-TEN-001", "FR-PRF-005")
def test_two_tennis_slots_on_one_day_are_two_sessions() -> None:
    slots = (
        TennisSlot(Weekday.SATURDAY, 9 * 60, 60, TennisSessionType.PRACTICE),
        TennisSlot(Weekday.SATURDAY, 17 * 60, 90, TennisSessionType.MATCH),
    )
    content = allowed(inputs(resources=replace(RESOURCES, tennis_schedule=slots)))
    first = content.weeks[0]
    assert len([item for item in first.workouts if item.tennis is not None]) == 2


@pytest.mark.req("FR-ONB-005", "FR-PRF-005")
def test_the_assessment_keeps_its_minutes_within_the_weekly_hours() -> None:
    few = replace(PATTERN, days_per_week=3, maximum_hours_per_week=2)
    allowed(
        inputs(
            baseline=True,
            availability=Availability.new(ATHLETE, recurring=few, defaults=DayDefaults(120)),
        )
    )


@pytest.mark.req("FR-PRF-005", "FR-TEN-001")
def test_an_exception_that_excludes_tennis_leaves_the_athletes_tennis_out() -> None:
    slot = TennisSlot(Weekday.WEDNESDAY, 19 * 60, 90, TennisSessionType.PRACTICE)
    running_only = AvailabilityException(
        day=START + timedelta(days=2),
        available=True,
        maximum_duration_minutes=90,
        allowed_sports=frozenset({Sport.RUNNING}),
    )
    availability = Availability.new(
        ATHLETE, recurring=PATTERN, defaults=DayDefaults(120)
    ).with_exception(running_only)
    content = allowed(
        inputs(availability=availability, resources=replace(RESOURCES, tennis_schedule=(slot,)))
    )
    assert not [item for item in content.on(START + timedelta(days=2)) if item.tennis is not None]
    assert [item for item in content.on(START + timedelta(days=9)) if item.tennis is not None]


@pytest.mark.req("FR-STR-001", "FR-PLN-008")
@pytest.mark.acceptance("AT-STR-002")
def test_few_hours_keep_the_goal_sport_first_and_say_when_strength_goes() -> None:
    one_hour = replace(PATTERN, maximum_hours_per_week=1)
    content = allowed(
        inputs(
            availability=Availability.new(ATHLETE, recurring=one_hour, defaults=DayDefaults(120))
        )
    )
    assert all(any(item.sport is Sport.RUNNING for item in week.workouts) for week in content.weeks)
    assert any("weekly hours leave no room" in note for note in content.assumptions)


@pytest.mark.req("FR-PLN-001")
def test_the_goal_week_states_what_is_planned_before_the_goal_and_a_close_goal_is_refused() -> None:
    monday_goal = date(2027, 3, 8)  # a Monday: the goal's week has nothing before the goal
    content = allowed(inputs(target_date=monday_goal))
    assert "The goal's week: 0 min planned before the goal." in content.weeks[-1].primary_goal
    with pytest.raises(DomainValidationError):
        generate(inputs(target_date=START + timedelta(days=1)))
