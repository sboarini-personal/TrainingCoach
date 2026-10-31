"""Adaptation rules 1.0.0 (planning spec 7; FR-ADP-001 to FR-ADP-004, AT-ADP-001,
AT-EXP-001, AT-UNP-001, AT-TEN-001). Synthetic data only.

The sample week (``plan_samples.week``) from Monday 2026-10-26: rest on Monday, goblet squats
at RPE 7 on Tuesday (45 min), an easy 40-minute run on Wednesday, 60 minutes of intervals up to
RPE 8 on Thursday and an easy 60-minute run on Saturday.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID

import pytest

from coach.domain.availability.availability import Availability, DayDefaults
from coach.domain.common.evidence import EvidenceKind, EvidenceRef
from coach.domain.common.quality import Confidence
from coach.domain.common.sport import Sport
from coach.domain.plans.adaptation import AdaptationReason, Decision
from coach.domain.plans.execution import (
    Compliance,
    DoneActivity,
    ExecutionStatus,
    WorkoutExecution,
)
from coach.domain.plans.plan import PlanContent
from coach.domain.plans.workout import WorkoutType
from coach.domain.recovery.assessment import (
    PrescriptionType,
    Reason,
    RecoveryAssessment,
    RecoveryPrescription,
    RecoveryStatus,
)
from coach.domain.recovery.checkin import CheckInPain, DailyCheckIn
from coach.planning.adaptation import AdaptationInputs, Outcome, decide
from coach.policy.plans import plan_reasons
from plan_samples import MONDAY, content, easy_run, intervals, rest, strength, week
from plan_world import PATTERN, open_planning
from samples import ATHLETE

TUESDAY, WEDNESDAY, THURSDAY = (MONDAY + timedelta(days=offset) for offset in (1, 2, 3))
FRIDAY, SATURDAY = MONDAY + timedelta(days=4), MONDAY + timedelta(days=5)
AT = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)
AVAILABILITY = Availability.new(ATHLETE, recurring=PATTERN, defaults=DayDefaults(240))


def assessment(status: RecoveryStatus, day: date = MONDAY) -> RecoveryAssessment:
    return RecoveryAssessment(
        assessment_id=UUID(int=0xA00 + day.day),
        athlete_id=ATHLETE,
        day=day,
        status=status,
        reasons=(Reason.NO_DEVIATION if status is RecoveryStatus.NORMAL else Reason.TWO_GROUPS,),
        signals=(),
        pain=None,
        confidence=Confidence.MEDIUM,
        uncertainty_reasons=frozenset(),
        assessed_at=AT,
    )


def prescription(kind: PrescriptionType, days: int, day: date = MONDAY) -> RecoveryPrescription:
    found = assessment(
        RecoveryStatus.REST if kind is PrescriptionType.REST else RecoveryStatus.REDUCE, day
    )
    return RecoveryPrescription(
        prescription_id=UUID(int=0xB00 + day.day),
        athlete_id=ATHLETE,
        assessment_id=found.assessment_id,
        day=day,
        type=kind,
        reason=Reason.THREE_GROUPS,
        evidence=(EvidenceRef(EvidenceKind.RECOVERY_ASSESSMENT, found.assessment_id),),
        duration_days=days,
        reevaluate_after=day + timedelta(days=days),
        created_at=AT,
    )


def done(
    day: date, sport: Sport, minutes: float, *, rpe: float | None = None, number: int = 0
) -> DoneActivity:
    return DoneActivity(
        activity_id=UUID(int=0xC00 + day.day * 10 + number),
        training_day=day,
        start=datetime.combine(day, time(18), tzinfo=UTC),
        sport=sport,
        elapsed_seconds=minutes * 60,
        rpe=rpe,
    )


def executed(
    plan: PlanContent,
    day: date,
    status: ExecutionStatus,
    activity: DoneActivity | None = None,
    *,
    ratio: float | None = None,
) -> WorkoutExecution:
    planned = None if status is ExecutionStatus.UNPLANNED else plan.on(day)[0].workout_id
    return WorkoutExecution(
        training_day=day,
        planned_workout_id=planned,
        completed_activity_ids=() if activity is None else (activity.activity_id,),
        status=status,
        compliance=Compliance(duration_ratio=ratio),
        session_rpe=None if activity is None else activity.rpe,
    )


def inputs(plan: PlanContent, **changes: Any) -> AdaptationInputs:
    made = AdaptationInputs(today=MONDAY, content=plan, availability=AVAILABILITY)
    fields = {"assessment": assessment(RecoveryStatus.NORMAL), **changes}
    return AdaptationInputs(
        **{**{key: getattr(made, key) for key in made.__dataclass_fields__}, **fields}
    )


def passes_policies(plan: PlanContent, today: date) -> None:
    reasons = plan_reasons(plan, frozenset(), open_planning(today=today))
    assert reasons == [], [(item.code, item.parameters) for item in reasons]


# --------------------------------------------------------------------------- MAINTAIN


@pytest.mark.req("FR-ADP-002", "FR-ADP-003")
@pytest.mark.acceptance("AT-ADP-001")
def test_one_poor_workout_does_not_rebuild_the_plan() -> None:
    plan = content()
    run = done(WEDNESDAY, Sport.RUNNING, 20, rpe=4)
    found = decide(
        inputs(
            plan,
            today=THURSDAY,
            executions=(executed(plan, WEDNESDAY, ExecutionStatus.PARTIAL, run, ratio=0.5),),
            done={run.activity_id: run},
        )
    )
    assert (found.decision, found.reasons, found.content) == (
        Decision.MAINTAIN,
        (AdaptationReason.ISOLATED_DEVIATION,),
        None,
    )
    assert "50% of the planned duration" in found.interpretation
    assert found.evidence == (EvidenceRef(EvidenceKind.ACTIVITY, run.activity_id),)
    assert decide(inputs(plan)).reasons == (AdaptationReason.NO_SIGNAL,)
    held = decide(inputs(plan, safety_hold=True))
    assert (held.decision, held.reasons) == (
        Decision.MAINTAIN,
        (AdaptationReason.SAFETY_HOLD_ACTIVE,),
    )


@pytest.mark.req("FR-ADP-001", "FR-ACT-011")
@pytest.mark.acceptance("AT-UNP-001")
def test_unplanned_tennis_re_evaluates_the_next_intervals_with_its_load_as_evidence() -> None:
    plan = content(week(MONDAY, {2: [rest(WEDNESDAY)], 3: [intervals(THURSDAY)]}))
    tennis = done(WEDNESDAY, Sport.TENNIS, 120, rpe=7)
    found = decide(
        inputs(
            plan,
            today=THURSDAY,
            executions=(executed(plan, WEDNESDAY, ExecutionStatus.UNPLANNED, tennis),),
            done={tennis.activity_id: tennis},
        )
    )
    assert (found.decision, found.reasons) == (
        Decision.MAINTAIN,
        (AdaptationReason.INTERFERENCE_ASSESSED,),
    )
    assert found.evidence == (EvidenceRef(EvidenceKind.ACTIVITY, tennis.activity_id),)
    assert "tennis on Wed 2026-10-28, 120 min, RPE 7" in found.interpretation


# --------------------------------------------------------------------------- changes


@pytest.mark.req("FR-ADP-001", "FR-STR-007", "FR-TEN-001", "FR-ADP-004")
@pytest.mark.acceptance("AT-TEN-001", "AT-EXP-001")
def test_demanding_unplanned_tennis_moves_the_intervals_and_lightens_heavy_legs() -> None:
    hard_legs = strength(THURSDAY, rpe=8, order=1)
    plan = content(
        week(
            MONDAY,
            {2: [rest(WEDNESDAY)], 3: [intervals(THURSDAY), hard_legs], 5: [easy_run(SATURDAY)]},
        )
    )
    tennis = done(WEDNESDAY, Sport.TENNIS, 120, rpe=8)
    found = decide(
        inputs(
            plan,
            today=THURSDAY,
            executions=(executed(plan, WEDNESDAY, ExecutionStatus.UNPLANNED, tennis),),
            done={tennis.activity_id: tennis},
        )
    )
    assert (found.decision, found.reasons) == (
        Decision.RESCHEDULE,
        (AdaptationReason.UNPLANNED_LOAD_INTERFERENCE,),
    )
    assert "TENNIS_INTERVALS_HEAVY_LEGS (VERY_HIGH)" in found.interpretation
    assert found.content is not None
    moved = found.content.workout(plan.weeks[0].days[3][0].workout_id)
    assert moved is not None
    assert moved.training_day == FRIDAY  # free and two days after the tennis
    lighter = found.content.workout(hard_legs.workout_id)
    assert lighter is not None
    assert lighter.max_rpe == 5.0
    assert set(found.affected) == {moved.workout_id, hard_legs.workout_id}
    passes_policies(found.content, THURSDAY)


@pytest.mark.req("FR-ADP-002", "FR-ADP-004", "NFR-EXP-001")
@pytest.mark.acceptance("AT-EXP-001")
def test_a_recovery_prescription_turns_the_blocked_days_into_rest_or_easy_days() -> None:
    plan = content()
    rest_until_thursday = prescription(PrescriptionType.REST, 3)
    found = decide(
        inputs(
            plan, prescriptions=(rest_until_thursday,), assessment=assessment(RecoveryStatus.REST)
        )
    )
    assert (found.decision, found.reasons) == (
        Decision.REST,
        (AdaptationReason.RECOVERY_PRESCRIPTION,),
    )
    assert found.content is not None
    assert [item.type for item in found.content.on(TUESDAY)] == [WorkoutType.REST]
    assert [item.type for item in found.content.on(WEDNESDAY)] == [WorkoutType.REST]
    assert found.content.on(THURSDAY) == plan.on(THURSDAY)  # re-evaluated on Thursday
    assert found.evidence[0].kind is EvidenceKind.RECOVERY_ASSESSMENT
    assert "until 2026-10-29: 2 planned workouts" in found.interpretation
    assert found.confidence is Confidence.MEDIUM
    easy = decide(inputs(plan, prescriptions=(prescription(PrescriptionType.ACTIVE_RECOVERY, 4),)))
    assert easy.decision is Decision.REDUCE
    assert easy.content is not None
    assert not any(item.intensive for item in easy.content.workouts if item.training_day < FRIDAY)
    passes_policies(easy.content, MONDAY)


@pytest.mark.req("FR-ADP-002", "SAFE-MED-001")
def test_pain_eases_the_next_days_where_it_hurts() -> None:
    plan = content()
    knee = DailyCheckIn(
        checkin_id=UUID(int=0xD01),
        athlete_id=ATHLETE,
        recorded_at=AT,
        training_day=MONDAY,
        pain=CheckInPain(intensity=5, location="rodilla"),
        pain_flag=True,
    )
    found = decide(inputs(plan, checkins=(knee,)))
    assert (found.decision, found.reasons) == (Decision.REDUCE, (AdaptationReason.PAIN_REPORTED,))
    assert found.evidence == (
        EvidenceRef(EvidenceKind.DAILY_CHECKIN, knee.checkin_id, note="pain"),
    )
    assert "pain of 5 of 10 on 2026-10-26 (knees)" in found.interpretation
    assert "professional evaluation" in found.interpretation
    tuesday = plan.on(TUESDAY)[0]
    assert found.affected == (tuesday.workout_id,)  # the goblet squats load the knees
    assert found.content is not None
    assert found.content.workout(tuesday.workout_id).max_rpe == 5.0  # type: ignore[union-attr]
    mild = replace_pain(knee, 3)
    assert decide(inputs(plan, checkins=(mild,))).decision is Decision.MAINTAIN


def replace_pain(item: DailyCheckIn, intensity: int) -> DailyCheckIn:
    return replace(item, pain=CheckInPain(intensity=intensity, location="rodilla"))


@pytest.mark.req("FR-ADP-002", "FR-ADP-004")
def test_deviations_reduce_only_with_a_recovery_signal() -> None:
    plan = content()
    short = done(TUESDAY, Sport.STRENGTH, 20)
    hard = done(WEDNESDAY, Sport.RUNNING, 40, rpe=7)  # the easy run felt like RPE 7
    executions = (
        executed(plan, TUESDAY, ExecutionStatus.PARTIAL, short, ratio=0.44),
        executed(plan, WEDNESDAY, ExecutionStatus.COMPLETE, hard, ratio=1.0),
    )
    activities = {short.activity_id: short, hard.activity_id: hard}
    calm = decide(inputs(plan, today=THURSDAY, executions=executions, done=activities))
    assert (calm.decision, calm.reasons) == (
        Decision.MAINTAIN,
        (AdaptationReason.DEVIATIONS_WITHOUT_RECOVERY_SIGNAL,),
    )
    tired = decide(
        inputs(
            plan,
            today=THURSDAY,
            executions=executions,
            done=activities,
            assessment=assessment(RecoveryStatus.REDUCE, THURSDAY),
        )
    )
    assert (tired.decision, tired.reasons) == (
        Decision.REDUCE,
        (AdaptationReason.DEVIATIONS_WITH_RECOVERY_SIGNAL,),
    )
    assert tired.affected == (plan.on(THURSDAY)[0].workout_id,)
    assert "RPE 7 for a target up to 4" in tired.interpretation
    assert "44% of the planned duration" in tired.interpretation
    assert {ref.kind for ref in tired.evidence} == {
        EvidenceKind.RECOVERY_ASSESSMENT,
        EvidenceKind.ACTIVITY,
    }


@pytest.mark.req("FR-ADP-002", "FR-PLN-006")
def test_consistent_completion_with_normal_recovery_progresses() -> None:
    plan = content(week(MONDAY), week(MONDAY + timedelta(weeks=1), index=2))
    today = MONDAY + timedelta(weeks=1)
    days = [TUESDAY, WEDNESDAY, THURSDAY, SATURDAY]
    sports = [Sport.STRENGTH, Sport.RUNNING, Sport.RUNNING, Sport.RUNNING]
    activities = [done(day, sport, 45, rpe=4) for day, sport in zip(days, sports, strict=True)]
    executions = tuple(
        executed(plan, day, ExecutionStatus.COMPLETE, item, ratio=1.0)
        for day, item in zip(days, activities, strict=True)
    )
    found = decide(
        inputs(
            plan,
            today=today,
            executions=executions,
            done={item.activity_id: item for item in activities},
            assessment=assessment(RecoveryStatus.NORMAL, today),
        )
    )
    assert (found.decision, found.reasons, found.content) == (
        Decision.PROGRESS,
        (AdaptationReason.CONSISTENT_COMPLETION,),
        None,  # the application regenerates from the updated history
    )
    assert found.interpretation.startswith("4 planned workouts of the last 14 days")
    unknown = decide(inputs(plan, today=today, executions=executions, done={}, assessment=None))
    assert unknown.decision is Decision.MAINTAIN  # unknown recovery never progresses


# --------------------------------------------------------------------------- review of P12.7

SUNDAY = MONDAY + timedelta(days=6)


def race_week(*, done_thursday: bool) -> tuple[PlanContent, AdaptationInputs]:
    plan = content(
        week(
            MONDAY,
            {
                3: [intervals(THURSDAY)],
                4: [easy_run(FRIDAY)],
                5: [rest(SATURDAY)],
            },
        )
    )
    lift = done(WEDNESDAY, Sport.STRENGTH, 45)  # unplanned, its sets unknown: possibly heavy
    executions = [executed(plan, WEDNESDAY, ExecutionStatus.UNPLANNED, lift)]
    activities = {lift.activity_id: lift}
    if done_thursday:
        run = done(THURSDAY, Sport.RUNNING, 60, rpe=8)
        executions.append(executed(plan, THURSDAY, ExecutionStatus.COMPLETE, run, ratio=1.0))
        activities[run.activity_id] = run
    return plan, inputs(
        plan,
        today=THURSDAY,
        executions=tuple(executions),
        done=activities,
        goal_date=SUNDAY,
    )


@pytest.mark.req("FR-ADP-001", "FR-STR-007")
def test_a_reschedule_never_lands_on_a_rest_day_or_the_goal_and_never_moves_done_work() -> None:
    plan, given = race_week(done_thursday=False)
    found = decide(given)
    assert found.decision is Decision.REPLACE  # Friday busy, Saturday rest, Sunday the goal
    assert found.content is not None
    eased_run = found.content.workout(plan.on(THURSDAY)[0].workout_id)
    assert eased_run is not None
    assert (eased_run.training_day, eased_run.intensive) == (THURSDAY, False)
    _plan, already = race_week(done_thursday=True)
    assert decide(already).decision is Decision.MAINTAIN  # the intervals are done: history


@pytest.mark.req("FR-ADP-002", "FR-ADP-004")
def test_an_assessed_load_never_hides_a_needed_reduction() -> None:
    plan = content()
    short = done(TUESDAY, Sport.STRENGTH, 20)
    slow = done(WEDNESDAY, Sport.RUNNING, 10)
    swim = done(WEDNESDAY, Sport.SWIMMING, 30, rpe=3, number=1)  # unplanned and easy
    found = decide(
        inputs(
            plan,
            today=THURSDAY,
            executions=(
                executed(plan, TUESDAY, ExecutionStatus.PARTIAL, short, ratio=0.44),
                executed(plan, WEDNESDAY, ExecutionStatus.PARTIAL, slow, ratio=0.25),
                executed(plan, WEDNESDAY, ExecutionStatus.UNPLANNED, swim),
            ),
            done={item.activity_id: item for item in (short, slow, swim)},
            assessment=assessment(RecoveryStatus.REDUCE, THURSDAY),
        )
    )
    assert found.decision is Decision.REDUCE
    assert found.reasons == (
        AdaptationReason.INTERFERENCE_ASSESSED,
        AdaptationReason.DEVIATIONS_WITH_RECOVERY_SIGNAL,
    )
    assert found.interpretation.startswith("Unplanned load (swimming on Wed 2026-10-28")


@pytest.mark.req("FR-ADP-002", "FR-PLN-006")
def test_progress_needs_known_effort_and_room_before_the_goal() -> None:
    plan = content(week(MONDAY), week(MONDAY + timedelta(weeks=1), index=2))
    today = MONDAY + timedelta(weeks=1)
    days = [TUESDAY, WEDNESDAY, THURSDAY, SATURDAY]
    sports = [Sport.STRENGTH, Sport.RUNNING, Sport.RUNNING, Sport.RUNNING]

    def run(rpe: float | None, goal: date | None) -> Outcome:
        activities = [
            done(day, sport, 45, rpe=rpe) for day, sport in zip(days, sports, strict=True)
        ]
        return decide(
            inputs(
                plan,
                today=today,
                executions=tuple(
                    executed(plan, day, ExecutionStatus.COMPLETE, item, ratio=1.0)
                    for day, item in zip(days, activities, strict=True)
                ),
                done={item.activity_id: item for item in activities},
                assessment=assessment(RecoveryStatus.NORMAL, today),
                goal_date=goal,
            )
        )

    assert run(4, date(2027, 3, 14)).decision is Decision.PROGRESS
    unknown = run(None, date(2027, 3, 14))
    assert unknown.reasons == (AdaptationReason.NO_SIGNAL,)  # unknown effort never progresses
    close = run(4, today + timedelta(weeks=3))
    assert (close.decision, close.reasons) == (
        Decision.MAINTAIN,
        (AdaptationReason.CONSISTENT_COMPLETION,),
    )
    assert "within 6 weeks of the goal" in close.interpretation


@pytest.mark.req("FR-ADP-004", "NFR-EXP-001")
@pytest.mark.acceptance("AT-EXP-001")
def test_maintain_explanations_say_what_recovery_shows() -> None:
    plan = content(week(MONDAY, {1: [easy_run(TUESDAY)], 2: [easy_run(WEDNESDAY)]}))
    first = done(TUESDAY, Sport.RUNNING, 10)
    second = done(WEDNESDAY, Sport.RUNNING, 10)
    common: dict[str, Any] = {
        "today": THURSDAY,
        "executions": (
            executed(plan, TUESDAY, ExecutionStatus.PARTIAL, first, ratio=0.25),
            executed(plan, WEDNESDAY, ExecutionStatus.PARTIAL, second, ratio=0.25),
        ),
        "done": {first.activity_id: first, second.activity_id: second},
    }
    unknown = decide(inputs(plan, assessment=None, **common))
    assert "no recovery assessment says whether recovery deviates" in unknown.interpretation
    tired = decide(inputs(plan, assessment=assessment(RecoveryStatus.REDUCE, THURSDAY), **common))
    assert tired.reasons == (
        AdaptationReason.DEVIATIONS_WITH_RECOVERY_SIGNAL,
        AdaptationReason.NOTHING_TO_CHANGE,
    )
    assert "recovery is below your baseline" in tired.interpretation
