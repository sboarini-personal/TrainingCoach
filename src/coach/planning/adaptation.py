"""Adaptation rules 1.0.0 (planning spec 7; FR-ADP-001 to FR-ADP-004, AT-ADP-001, AT-EXP-001,
AT-UNP-001, AT-TEN-001).

PLANNED -> ACTUAL -> DIFFERENCE -> RECOVERY -> INTERPRETATION -> DECISION, as a pure function
of the ACTIVE content, the recent executions and their activities, recovery (the assessment and
prescriptions in force today, recent check-ins) and availability. The first rule that applies
decides:

1. A safety hold: MAINTAIN (the hold's own rules apply; nothing is proposed until release).
2. A blocking recovery prescription with planned intensity before its re-evaluation: REST for a
   REST prescription (those days become rest days), REDUCE for an active-recovery one (the
   intensity is taken out).
3. Pain of 4 or more reported in the last 3 days: REDUCE (intensity out of the next 3 days,
   strength light where it loads the named region, or everywhere without one).
4. Unplanned load of the last 2 days in HIGH or VERY_HIGH interference (strength spec 7) with a
   planned session of the next 2 days: RESCHEDULE an endurance session to a free available day
   of its week at least 2 days after that load, else REPLACE it with an easy one; heavy legs
   become light. Assessed but not severe: MAINTAIN, with the load as evidence (AT-UNP-001).
5. Two or more deviated sessions in 14 days (below 80% of the planned duration, or an RPE 2 or
   more above the target) with a recovery assessment of REDUCE or REST: REDUCE the next 7 days.
6. Four or more planned workouts in 14 days, all COMPLETE at or below their target effort,
   normal recovery and no pain in 7 days: PROGRESS (the application regenerates the plan from
   the updated history).
7. Otherwise MAINTAIN: one poor workout never rebuilds the plan (FR-ADP-003, AT-ADP-001).

Every decision states its evidence, an interpretation whose numbers come from that evidence,
its uncertainty and confidence (FR-ADP-004). Changes keep the identifiers of the workouts they
change, so links and executions follow them.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Final
from uuid import UUID, uuid5

from coach.domain.availability.availability import Availability
from coach.domain.common.evidence import EvidenceKind, EvidenceRef
from coach.domain.common.quality import Confidence, UncertaintyReason
from coach.domain.common.sport import Sport
from coach.domain.plans.adaptation import AdaptationReason, Decision
from coach.domain.plans.execution import DoneActivity, ExecutionStatus, WorkoutExecution
from coach.domain.plans.plan import Phase, PlanContent
from coach.domain.plans.workout import (
    SAFETY_STOP,
    EnduranceWorkout,
    IntensityTarget,
    PlannedWorkout,
    RpeRange,
    Step,
    StepKind,
    WorkoutType,
)
from coach.domain.recovery.assessment import (
    PrescriptionType,
    RecoveryAssessment,
    RecoveryPrescription,
    RecoveryStatus,
)
from coach.domain.recovery.checkin import DailyCheckIn
from coach.domain.recovery.pain import PAIN_RESTRICTION_DAYS, PAIN_RESTRICTION_MIN, recent_pain
from coach.domain.strength.catalogue import CATALOGUE
from coach.domain.strength.exercise import ExerciseCatalogue
from coach.domain.strength.prescription import EffortCue, PrescribedExercise
from coach.domain.strength.regions import region_named, regions_loaded
from coach.domain.strength.vocabulary import BodyRegion, InterferenceLevel
from coach.metrics.strength import HARD_SET_MAX_RIR, HARD_SET_MIN_RPE
from coach.metrics.tennis import LoadLevel, load_level
from coach.planning.generator import session_load
from coach.planning.interference import (
    LOWER_REGIONS,
    SessionLoad,
    assess,
    run_demands,
    strength_demands,
    swim_demands,
    tennis_demands,
)

WINDOW_DAYS: Final = 7
LOOKBACK_DAYS: Final = 14
PAIN_DAYS: Final = PAIN_RESTRICTION_DAYS
PAIN_FREE_DAYS: Final = 7
PAIN_THRESHOLD: Final = PAIN_RESTRICTION_MIN
DEVIATION_RATIO: Final = 0.8
DEVIATION_RPE: Final = 2.0
DEVIATED_SESSIONS: Final = 2
PROGRESS_SESSIONS: Final = 4
INTERFERENCE_DAYS: Final = 2
LIGHT_RPE: Final = 5.0
PROGRESS_MIN_WEEKS: Final = 6
PROGRESS_PHASES: Final = frozenset({Phase.ADAPTATION, Phase.BASE, Phase.BUILD})
_IDS: Final = UUID("0193b2a0-7a51-7c3e-9d10-5e6f7a8b9c0e")
_SEVERE: Final = frozenset({InterferenceLevel.HIGH, InterferenceLevel.VERY_HIGH})


@dataclass(frozen=True, slots=True, kw_only=True)
class AdaptationInputs:
    """What the rules read, assembled by the application from stored state."""

    today: date
    content: PlanContent  # the ACTIVE version's
    executions: tuple[WorkoutExecution, ...] = ()  # the last 14 days through today
    done: Mapping[UUID, DoneActivity] = field(default_factory=dict)  # their activities
    assessment: RecoveryAssessment | None = None  # in force today
    prescriptions: tuple[RecoveryPrescription, ...] = ()  # in force today
    checkins: tuple[DailyCheckIn, ...] = ()  # the last 7 days
    availability: Availability | None = None
    safety_hold: bool = False
    goal_date: date | None = None  # nothing moves onto it or the day before it
    catalogue: ExerciseCatalogue = CATALOGUE


@dataclass(frozen=True, slots=True)
class Outcome:
    decision: Decision
    reasons: tuple[AdaptationReason, ...]
    evidence: tuple[EvidenceRef, ...]
    interpretation: str
    uncertainty: frozenset[UncertaintyReason] = frozenset()
    confidence: Confidence = Confidence.MEDIUM
    affected: tuple[UUID, ...] = ()
    content: PlanContent | None = None  # the adapted content; PROGRESS regenerates instead


# --------------------------------------------------------------------------- helpers


def _upcoming(inputs: AdaptationInputs, days: int) -> list[PlannedWorkout]:
    """The planned workouts of the next days not done yet (a workout done today is
    history)."""
    end = inputs.today + timedelta(days=days)
    done = {
        item.planned_workout_id
        for item in inputs.executions
        if item.planned_workout_id is not None and item.completed_activity_ids
    }
    return [
        item
        for item in inputs.content.workouts
        if inputs.today <= item.training_day < end
        and item.type is not WorkoutType.REST
        and item.workout_id not in done
    ]


def _hard(item: PrescribedExercise) -> bool:
    readings = []
    if item.target_rir is not None:
        readings.append(item.target_rir <= HARD_SET_MAX_RIR)
    if item.target_rpe is not None:
        readings.append(item.target_rpe >= HARD_SET_MIN_RPE)
    return any(readings) if readings else item.effort_cue in (None, EffortCue.HARD)


def _light(item: PrescribedExercise) -> PrescribedExercise:
    return replace(
        item,
        sets=min(item.sets, 2),
        target_rpe=None if item.target_rpe is None else min(item.target_rpe, LIGHT_RPE),
        target_rir=None if item.target_rir is None else max(item.target_rir, 5),
        effort_cue=EffortCue.MODERATE if item.effort_cue is EffortCue.HARD else item.effort_cue,
    )


def eased(
    workout: PlannedWorkout,
    *,
    regions: frozenset[BodyRegion] | None = None,
    catalogue: ExerciseCatalogue = CATALOGUE,
) -> PlannedWorkout | None:
    """The workout without its intensity; ``None`` when it is taken out (the athlete's hard
    tennis, a maximal test). ``regions`` limits the strength change to exercises loading
    them (every hard exercise when ``None``)."""
    if workout.tennis is not None or workout.evaluation is not None:
        return None if workout.intensive else workout
    if workout.endurance is not None and workout.intensive:
        seconds = workout.planned_seconds or 1800.0
        sport = workout.endurance.sport
        return replace(
            workout,
            is_key=False,
            objective=f"Easy session ({sport.value.lower()}, adapted)",
            what=f"{round(seconds / 60)} minutes easy (RPE 3 to 4)",
            why="Adapted: the intensity is taken out for now",
            endurance=EnduranceWorkout(
                sport=sport,
                main_set=(
                    Step(
                        kind=StepKind.STEADY,
                        duration_seconds=seconds,
                        target=IntensityTarget(rpe=RpeRange(3, 4)),
                    ),
                ),
                stop_conditions=(SAFETY_STOP,),
            ),
        )
    if workout.strength is not None:
        changed = tuple(
            _light(item)
            if _hard(item)
            and (
                regions is None
                or (
                    item.exercise_id in catalogue
                    and regions_loaded(catalogue.get(item.exercise_id)) & regions
                )
            )
            else item
            for item in workout.strength.exercises
        )
        if changed != workout.strength.exercises:
            return replace(
                workout,
                why="Adapted: lighter strength for now",
                strength=replace(workout.strength, exercises=changed),
            )
    return workout


def _rest(day: date) -> PlannedWorkout:
    return PlannedWorkout(
        workout_id=uuid5(_IDS, f"rest/{day.isoformat()}"),
        type=WorkoutType.REST,
        training_day=day,
        order=0,
        is_key=False,
        objective="Rest (adapted)",
        what="Full rest",
        why="Adapted: recovery comes first",
    )


def adapted(
    content: PlanContent,
    changes: Mapping[UUID, PlannedWorkout | None],
    *,
    rest_days: Iterable[date] = (),
) -> PlanContent:
    """The content with workouts replaced, moved (a new training day) or taken out, and the
    given days made rest days."""
    rests = set(rest_days)
    by_day: dict[date, list[PlannedWorkout]] = defaultdict(list)
    for item in content.workouts:
        if item.training_day in rests:
            continue
        changed = changes.get(item.workout_id, item) if item.workout_id in changes else item
        if changed is not None:
            by_day[changed.training_day].append(changed)
    for day in rests:
        by_day[day] = [_rest(day)]
    weeks = []
    for week in content.weeks:
        days = tuple(
            tuple(
                replace(item, order=order)
                for order, item in enumerate(by_day.get(week.start + timedelta(days=offset), []))
            )
            for offset in range(7)
        )
        weeks.append(replace(week, days=days))
    return replace(content, weeks=tuple(weeks))


def _completed_load(activity: DoneActivity) -> SessionLoad | None:
    """A completed activity as the interference rules see it (strength without its sets is
    taken as possibly heavy: interference is never dismissed for lack of data)."""
    start = activity.start
    minutes = None if activity.elapsed_seconds is None else activity.elapsed_seconds / 60
    match activity.sport:
        case Sport.TENNIS:
            high = (
                None
                if minutes is None or activity.rpe is None
                else load_level(minutes, activity.rpe, match=False) is LoadLevel.HIGH
            )
            demands, possible = tennis_demands(high_load=high)
            return SessionLoad(activity.activity_id, start, Sport.TENNIS, False, demands, possible)
        case Sport.RUNNING:
            demands, possible = run_demands(
                rpe=activity.rpe,
                elapsed_seconds=activity.elapsed_seconds,
                structured_intervals=None,
            )
            return SessionLoad(activity.activity_id, start, Sport.RUNNING, False, demands, possible)
        case Sport.SWIMMING:
            demands, possible = swim_demands(rpe=activity.rpe)
            return SessionLoad(
                activity.activity_id, start, Sport.SWIMMING, False, demands, possible
            )
        case Sport.STRENGTH:
            demands, possible, regions = strength_demands((), CATALOGUE, incomplete=True)
            return SessionLoad(
                activity.activity_id, start, Sport.STRENGTH, False, demands, possible, regions
            )
        case _:
            return None


def _activity(reference: UUID, done: Mapping[UUID, DoneActivity]) -> str:
    item = done.get(reference)
    if item is None:
        return "an activity"
    minutes = "" if item.elapsed_seconds is None else f", {round(item.elapsed_seconds / 60)} min"
    rpe = "" if item.rpe is None else f", RPE {item.rpe:g}"
    return f"{item.sport.value.lower()} on {item.training_day:%a %Y-%m-%d}{minutes}{rpe}"


def _ref(kind: EvidenceKind, ref_id: UUID, note: str | None = None) -> EvidenceRef:
    return EvidenceRef(kind, ref_id, note=note)


# --------------------------------------------------------------------------- rules


@dataclass(frozen=True, slots=True)
class _Note:
    """What a rule found without changing anything: it joins the decision's explanation."""

    reason: AdaptationReason
    evidence: tuple[EvidenceRef, ...]
    text: str
    uncertainty: frozenset[UncertaintyReason] = frozenset()


def _blocked(inputs: AdaptationInputs) -> Outcome | None:
    blocking = [item for item in inputs.prescriptions if item.blocks_intensity]
    if not blocking:
        return None
    until = max(item.reevaluate_after for item in blocking)
    rest = any(item.type is PrescriptionType.REST for item in blocking)
    within = [
        item for item in _upcoming(inputs, (until - inputs.today).days) if item.training_day < until
    ]
    affected = [item for item in within if rest or item.intensive]
    if not affected:
        return None
    evidence = tuple(
        _ref(
            EvidenceKind.RECOVERY_ASSESSMENT,
            item.assessment_id,
            f"prescription {item.type.value} until {item.reevaluate_after}",
        )
        for item in blocking
    )
    kind = "rest" if rest else "active recovery"
    if rest:
        days = sorted({item.training_day for item in affected})
        content = adapted(inputs.content, {}, rest_days=days)
        effect = "those days are rest days"
    else:
        content = adapted(inputs.content, {item.workout_id: eased(item) for item in affected})
        effect = "their intensity is taken out"
    assessment = inputs.assessment
    return Outcome(
        Decision.REST if rest else Decision.REDUCE,
        (AdaptationReason.RECOVERY_PRESCRIPTION,),
        evidence,
        f"Your recovery prescription ({kind}) lasts until {until}: {len(affected)} planned "
        f"workouts before then change, {effect}. It is load management, not a diagnosis.",
        frozenset() if assessment is None else assessment.uncertainty_reasons,
        Confidence.MEDIUM if assessment is None else assessment.confidence,
        tuple(item.workout_id for item in affected),
        content,
    )


def _pain(inputs: AdaptationInputs) -> Outcome | _Note | None:
    reports = recent_pain(inputs.checkins, inputs.today)
    if not reports:
        return None
    named = {region_named(item.pain.location) for item in reports if item.pain is not None}
    regions = None if None in named else frozenset(region for region in named if region is not None)
    changes = {
        item.workout_id: eased(item, regions=regions, catalogue=inputs.catalogue)
        for item in _upcoming(inputs, PAIN_DAYS)
    }
    changes = {
        key: value
        for key, value in changes.items()
        if value is None or value != inputs.content.workout(key)
    }
    evidence = tuple(_ref(EvidenceKind.DAILY_CHECKIN, item.checkin_id, "pain") for item in reports)
    worst = max(
        reports, key=lambda item: (item.pain.intensity if item.pain else 0, item.training_day)
    )
    where = (
        ""
        if regions is None
        else " (" + ", ".join(sorted(region.value.lower() for region in regions)) + ")"
    )
    intensity = 0 if worst.pain is None else worst.pain.intensity
    told = (
        f"You reported pain of {intensity} of 10 on {worst.training_day}{where}. If the pain "
        "lasts more than 7 days or gets worse, consider a professional evaluation."
    )
    uncertainty = frozenset({UncertaintyReason.MISSING_DATA}) if regions is None else frozenset()
    if not changes:
        return _Note(
            AdaptationReason.PAIN_REPORTED,
            evidence,
            told + " The next days have no intensity to take out.",
            uncertainty,
        )
    return Outcome(
        Decision.REDUCE,
        (AdaptationReason.PAIN_REPORTED,),
        evidence,
        told + f" {len(changes)} planned workouts of the next {PAIN_DAYS} days are eased.",
        uncertainty,
        Confidence.MEDIUM,
        tuple(changes),
        adapted(inputs.content, changes),
    )


def _free_day(inputs: AdaptationInputs, workout: PlannedWorkout, after: date) -> date | None:
    """A later day of the workout's week with nothing planned (a rest day is not free), open
    for it in the availability, at least 2 days after the load it interferes with, and
    before the day preceding the goal."""
    monday = workout.training_day - timedelta(days=workout.training_day.isoweekday() - 1)
    busy = {item.training_day for item in inputs.content.workouts}
    minutes = (workout.planned_seconds or 0) / 60
    last = monday + timedelta(days=6)
    if inputs.goal_date is not None:
        last = min(last, inputs.goal_date - timedelta(days=2))
    day = max(workout.training_day + timedelta(days=1), after + timedelta(days=INTERFERENCE_DAYS))
    while day <= last:
        if day not in busy and inputs.availability is not None:
            found = inputs.availability.resolve(day)
            sport_ok = found.allowed_sports is None or workout.sport in found.allowed_sports
            if (
                found.plannable
                and found.available
                and found.maximum_duration_minutes >= minutes
                and sport_ok
            ):
                return day
        day += timedelta(days=1)
    return None


def _interference(inputs: AdaptationInputs) -> Outcome | _Note | None:
    since = inputs.today - timedelta(days=INTERFERENCE_DAYS)
    unplanned = [
        reference
        for item in inputs.executions
        if item.status is ExecutionStatus.UNPLANNED and since <= item.training_day <= inputs.today
        for reference in item.completed_activity_ids
    ]
    completed = [
        load
        for reference in unplanned
        if reference in inputs.done
        and (load := _completed_load(inputs.done[reference])) is not None
    ]
    if not completed:
        return None
    upcoming = {item.workout_id: item for item in _upcoming(inputs, INTERFERENCE_DAYS + 1)}
    planned = [
        load
        for item in upcoming.values()
        if (load := session_load(item, inputs.catalogue)) is not None
    ]
    found = [
        item
        for item in assess(completed + planned)
        if any(session in upcoming for session in item.sessions)
        and any(session.session_id in item.sessions for session in completed)
    ]
    evidence = tuple(_ref(EvidenceKind.ACTIVITY, load.session_id) for load in completed)
    loads = "; ".join(_activity(load.session_id, inputs.done) for load in completed)
    severe = [item for item in found if item.level in _SEVERE]
    uncertainty = frozenset(reason for item in found for reason in item.reasons)
    if not severe:
        level = max(
            (item.level for item in found),
            key=list(InterferenceLevel).index,
            default=InterferenceLevel.NONE,
        )
        return _Note(
            AdaptationReason.INTERFERENCE_ASSESSED,
            evidence,
            f"Unplanned load ({loads}) was assessed against the next {INTERFERENCE_DAYS + 1} "
            f"days of the plan: interference {level.value} at most.",
            uncertainty,
        )
    changes: dict[UUID, PlannedWorkout | None] = {}
    moved = False
    for assessment in severe:
        last = max(
            (
                inputs.done[load.session_id].training_day
                for load in completed
                if load.session_id in assessment.sessions
            ),
            default=inputs.today,
        )
        for session in assessment.sessions:
            workout = upcoming.get(session)
            if workout is None or session in changes or workout.tennis is not None:
                continue  # the athlete's own tennis stays theirs
            if workout.endurance is not None and workout.intensive:
                day = _free_day(inputs, workout, last)
                if day is not None:
                    changes[session] = replace(workout, training_day=day)
                    moved = True
                    continue
            changes[session] = eased(workout, regions=LOWER_REGIONS, catalogue=inputs.catalogue)
    changes = {key: value for key, value in changes.items() if value != upcoming[key]}
    if not changes:
        return None
    rules = ", ".join(sorted({f"{item.rule.value} ({item.level.value})" for item in severe}))
    return Outcome(
        Decision.RESCHEDULE if moved else Decision.REPLACE,
        (AdaptationReason.UNPLANNED_LOAD_INTERFERENCE,),
        evidence,
        f"Unplanned load ({loads}) interferes with the next days of the plan: {rules}. "
        f"{len(changes)} planned workouts are {'moved or eased' if moved else 'eased'}.",
        uncertainty,
        Confidence.LOW if uncertainty else Confidence.MEDIUM,
        tuple(changes),
        adapted(inputs.content, changes),
    )


def _deviated(inputs: AdaptationInputs) -> list[WorkoutExecution]:
    since = inputs.today - timedelta(days=LOOKBACK_DAYS - 1)
    found = []
    for item in inputs.executions:
        if item.planned_workout_id is None or not since <= item.training_day <= inputs.today:
            continue
        planned = inputs.content.workout(item.planned_workout_id)
        short = (
            item.status is ExecutionStatus.PARTIAL
            and item.compliance.duration_ratio is not None
            and item.compliance.duration_ratio < DEVIATION_RATIO
        )
        target = None if planned is None else planned.max_rpe
        harder = (
            item.session_rpe is not None
            and target is not None
            and item.session_rpe - target >= DEVIATION_RPE
        )
        if short or harder:
            found.append(item)
    return found


def _deviation_note(item: WorkoutExecution, inputs: AdaptationInputs) -> str:
    ratio = item.compliance.duration_ratio
    planned = (
        None if item.planned_workout_id is None else inputs.content.workout(item.planned_workout_id)
    )
    parts = [f"{item.training_day:%a %Y-%m-%d}"]
    if ratio is not None:
        parts.append(f"{round(ratio * 100)}% of the planned duration")
    if item.session_rpe is not None and planned is not None and planned.max_rpe is not None:
        parts.append(f"RPE {item.session_rpe:g} for a target up to {planned.max_rpe:g}")
    return ", ".join(parts)


def _recovering(inputs: AdaptationInputs) -> bool:
    status = None if inputs.assessment is None else inputs.assessment.status
    return status in (RecoveryStatus.REDUCE, RecoveryStatus.REST)


def _deviations(inputs: AdaptationInputs) -> Outcome | None:
    deviated = _deviated(inputs)
    if len(deviated) < DEVIATED_SESSIONS or not _recovering(inputs) or inputs.assessment is None:
        return None
    changes = {
        item.workout_id: eased(item, catalogue=inputs.catalogue)
        for item in _upcoming(inputs, WINDOW_DAYS)
        if item.intensive
    }
    if not changes:
        return None
    evidence = (
        _ref(
            EvidenceKind.RECOVERY_ASSESSMENT,
            inputs.assessment.assessment_id,
            inputs.assessment.status.value,
        ),
        *(
            _ref(EvidenceKind.ACTIVITY, reference)
            for item in deviated
            for reference in item.completed_activity_ids
        ),
    )
    notes = "; ".join(_deviation_note(item, inputs) for item in deviated)
    return Outcome(
        Decision.REDUCE,
        (AdaptationReason.DEVIATIONS_WITH_RECOVERY_SIGNAL,),
        evidence,
        f"Recovery is below your baseline ({inputs.assessment.status.value} on "
        f"{inputs.assessment.day}) and {len(deviated)} sessions fell short of the plan ({notes}): "
        f"the intensity of {len(changes)} planned workouts of the next {WINDOW_DAYS} days is "
        "taken out.",
        frozenset(inputs.assessment.uncertainty_reasons),
        inputs.assessment.confidence,
        tuple(changes),
        adapted(inputs.content, changes),
    )


def _phase_today(inputs: AdaptationInputs) -> Phase | None:
    return next(
        (week.phase for week in inputs.content.weeks if week.start <= inputs.today <= week.end),
        None,
    )


def _progress(inputs: AdaptationInputs) -> Outcome | _Note | None:
    since = inputs.today - timedelta(days=LOOKBACK_DAYS - 1)
    recent = [
        item
        for item in inputs.executions
        if item.planned_workout_id is not None
        and since <= item.training_day < inputs.today
        and item.status is not ExecutionStatus.PLANNED
    ]
    if len(recent) < PROGRESS_SESSIONS or any(
        item.status is not ExecutionStatus.COMPLETE for item in recent
    ):
        return None
    if _deviated(inputs) or inputs.assessment is None:
        return None
    if inputs.assessment.status is not RecoveryStatus.NORMAL:
        return None
    pain_since = inputs.today - timedelta(days=PAIN_FREE_DAYS - 1)
    if any(
        item.pain is not None and item.pain.intensity > 0 and item.training_day >= pain_since
        for item in inputs.checkins
    ):
        return None
    for item in recent:  # measured and at or below the target effort: never assumed
        planned = inputs.content.workout(item.planned_workout_id)  # type: ignore[arg-type]
        target = None if planned is None else planned.max_rpe
        if (
            item.compliance.duration_ratio is None
            or item.session_rpe is None
            or target is None
            or item.session_rpe > target
        ):
            return None
    evidence = (
        _ref(EvidenceKind.RECOVERY_ASSESSMENT, inputs.assessment.assessment_id, "NORMAL"),
        *(
            _ref(EvidenceKind.ACTIVITY, reference)
            for item in recent
            for reference in item.completed_activity_ids
        ),
    )
    done = (
        f"{len(recent)} planned workouts of the last {LOOKBACK_DAYS} days were completed "
        "within 80% to 120% of the planned duration at or below their target RPE, and recovery "
        f"is normal ({inputs.assessment.day})"
    )
    phase = _phase_today(inputs)
    far = inputs.goal_date is None or inputs.goal_date >= inputs.today + timedelta(
        weeks=PROGRESS_MIN_WEEKS
    )
    if phase not in PROGRESS_PHASES or not far:
        return _Note(
            AdaptationReason.CONSISTENT_COMPLETION,
            evidence,
            done + ": the plan's own progression continues (no regeneration in the specific "
            f"and taper phases or within {PROGRESS_MIN_WEEKS} weeks of the goal).",
        )
    return Outcome(
        Decision.PROGRESS,
        (AdaptationReason.CONSISTENT_COMPLETION,),
        evidence,
        done + ": the plan progresses from your updated history.",
        frozenset(inputs.assessment.uncertainty_reasons),
        inputs.assessment.confidence,
    )


def _maintain(inputs: AdaptationInputs) -> Outcome:
    deviated = _deviated(inputs)
    evidence = tuple(
        _ref(EvidenceKind.ACTIVITY, reference)
        for item in deviated
        for reference in item.completed_activity_ids
    )
    if len(deviated) == 1:
        return Outcome(
            Decision.MAINTAIN,
            (AdaptationReason.ISOLATED_DEVIATION,),
            evidence,
            f"One session fell short of the plan ({_deviation_note(deviated[0], inputs)}) with "
            "no other signal: the plan stays; one session never rebuilds the plan.",
        )
    if deviated:
        notes = "; ".join(_deviation_note(item, inputs) for item in deviated)
        if inputs.assessment is None:
            return Outcome(
                Decision.MAINTAIN,
                (AdaptationReason.DEVIATIONS_WITHOUT_RECOVERY_SIGNAL,),
                evidence,
                f"{len(deviated)} sessions fell short of the plan ({notes}) and no recovery "
                "assessment says whether recovery deviates: the plan stays and the next "
                "sessions are watched.",
                frozenset({UncertaintyReason.MISSING_DATA}),
            )
        if _recovering(inputs):
            return Outcome(
                Decision.MAINTAIN,
                (
                    AdaptationReason.DEVIATIONS_WITH_RECOVERY_SIGNAL,
                    AdaptationReason.NOTHING_TO_CHANGE,
                ),
                (
                    _ref(
                        EvidenceKind.RECOVERY_ASSESSMENT,
                        inputs.assessment.assessment_id,
                        inputs.assessment.status.value,
                    ),
                    *evidence,
                ),
                f"{len(deviated)} sessions fell short of the plan ({notes}) and recovery is below "
                f"your baseline ({inputs.assessment.status.value} on {inputs.assessment.day}), "
                f"but the next {WINDOW_DAYS} days have no intensity to take out: the plan stays.",
                frozenset(inputs.assessment.uncertainty_reasons),
                inputs.assessment.confidence,
            )
        return Outcome(
            Decision.MAINTAIN,
            (AdaptationReason.DEVIATIONS_WITHOUT_RECOVERY_SIGNAL,),
            evidence,
            f"{len(deviated)} sessions fell short of the plan ({notes}) but recovery shows no "
            "deviation: the plan stays and the next sessions are watched.",
        )
    return Outcome(
        Decision.MAINTAIN,
        (AdaptationReason.NO_SIGNAL,),
        (),
        "Nothing in the recent sessions or recovery calls for a change: the plan stays.",
    )


def _with_notes(outcome: Outcome, notes: Sequence[_Note]) -> Outcome:
    """The decision explained with what the earlier rules found without deciding."""
    if not notes:
        return outcome
    reasons = tuple(note.reason for note in notes)
    if outcome.reasons != (AdaptationReason.NO_SIGNAL,):
        reasons += tuple(item for item in outcome.reasons if item not in reasons)
    text = " ".join(note.text for note in notes)
    tail = (
        "The plan stays."
        if outcome.reasons == (AdaptationReason.NO_SIGNAL,)
        else outcome.interpretation
    )
    return replace(
        outcome,
        reasons=reasons,
        evidence=tuple(
            dict.fromkeys((*(ref for note in notes for ref in note.evidence), *outcome.evidence))
        ),
        interpretation=f"{text} {tail}",
        uncertainty=outcome.uncertainty.union(*(note.uncertainty for note in notes)),
    )


def _respecting_pain(outcome: Outcome, inputs: AdaptationInputs) -> Outcome:
    """Recent pain eases the adapted content too, whichever rule changed it: the pain
    restriction holds for every version (pain rules 1.0.0), so the plan policies would refuse
    an adapted version that keeps hard work where it hurts."""
    if outcome.content is None:
        return outcome
    found = _pain(replace(inputs, content=outcome.content))
    if not isinstance(found, Outcome):
        return outcome
    return replace(
        outcome,
        reasons=(
            *outcome.reasons,
            *(item for item in found.reasons if item not in outcome.reasons),
        ),
        evidence=tuple(dict.fromkeys((*outcome.evidence, *found.evidence))),
        interpretation=f"{outcome.interpretation} {found.interpretation}",
        uncertainty=outcome.uncertainty | found.uncertainty,
        affected=tuple(dict.fromkeys((*outcome.affected, *found.affected))),
        content=found.content,
    )


def decide(inputs: AdaptationInputs) -> Outcome:
    """The decision of adaptation rules 1.0.0."""
    if inputs.safety_hold:
        return Outcome(
            Decision.MAINTAIN,
            (AdaptationReason.SAFETY_HOLD_ACTIVE,),
            (),
            "A safety hold is active: nothing is adapted until you release it, after a "
            "healthcare professional has cleared you to train.",
            confidence=Confidence.HIGH,
        )
    notes: list[_Note] = []
    for rule in (_blocked, _pain, _interference, _deviations, _progress):
        found = rule(inputs)
        if isinstance(found, Outcome):
            return _with_notes(_respecting_pain(found, inputs), notes)
        if isinstance(found, _Note):
            notes.append(found)
    return _with_notes(_maintain(inputs), notes)
