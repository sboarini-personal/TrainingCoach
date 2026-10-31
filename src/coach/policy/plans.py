"""Plan policies of policy set 1.2.0 (policy spec sections 2 and 5; FR-POL-001, FR-PRF-005,
FR-PRF-006, SAFE-MED-003).

A plan proposal or activation is checked workout by workout against what the application
assembled from stored state (``PlanningContext``): the coaching conditions, the effective
availability of each day, the athlete's resources and equipment, the active health
constraints and the end of a recovery block. Only workouts from ``today`` on are checked, and
not those already done (the ACTIVE version's workouts with activities today): the past of a
plan is history, not a prescription. Unknown never allows: an undefined day, a duration or an effort
that is not stated, missing resources or a missing health declaration deny what they would
have to allow. Reasons carry codes, dates and identifiers, never athlete text.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Final
from uuid import UUID

from coach.domain.athlete.health import HealthConstraint, HealthDeclaration, RestrictionKind
from coach.domain.athlete.resources import Resources
from coach.domain.availability.availability import (
    Availability,
    AvailabilityBasis,
    EffectiveAvailability,
)
from coach.domain.coaching.state import Condition
from coach.domain.common.sport import Sport
from coach.domain.common.time import Weekday
from coach.domain.plans.plan import PlanContent, TrainingWeek
from coach.domain.plans.workout import PlannedWorkout, WorkoutType
from coach.domain.recovery.pain import PainRestriction
from coach.domain.strength.catalogue import CATALOGUE
from coach.domain.strength.equipment import equipment_available
from coach.domain.strength.exercise import Exercise, ExerciseCatalogue
from coach.domain.strength.prescription import EffortCue, PrescribedExercise
from coach.domain.strength.regions import region_named, regions_loaded
from coach.domain.strength.vocabulary import (
    LOWER_BODY_PATTERNS,
    BodyRegion,
    ContraindicationTag,
)
from coach.metrics.strength import HARD_SET_MAX_RIR, HARD_SET_MIN_RPE
from coach.policy.reasons import PolicyReason

IMPACT_SPORTS: Final = frozenset({Sport.RUNNING, Sport.TENNIS})
OVERHEAD_SPORTS: Final = frozenset({Sport.SWIMMING, Sport.TENNIS})


@dataclass(frozen=True, slots=True, kw_only=True)
class PlanningContext:
    """What plan policies read, assembled by the application from stored state."""

    today: date
    availability: Availability | None = None
    health: HealthDeclaration | None = None
    resources: Resources | None = None
    recovery_block_until: date | None = None  # re-evaluation of the blocking prescriptions
    pain: PainRestriction = field(default_factory=PainRestriction)  # recent pain (P12)
    done: frozenset[UUID] = frozenset()  # workouts already done today: history
    catalogue: ExerciseCatalogue = CATALOGUE


def _reason(
    policy: str, code: str, workout: PlannedWorkout | None = None, **parameters: str
) -> PolicyReason:
    fields: dict[str, str] = {}
    if workout is not None:
        fields["day"] = workout.training_day.isoformat()
        fields["workoutId"] = str(workout.workout_id)
    fields.update(parameters)
    return PolicyReason(policy, code, tuple(fields.items()))


def _upcoming(content: PlanContent, planning: PlanningContext) -> list[PlannedWorkout]:
    return [
        item
        for item in content.workouts
        if item.training_day >= planning.today
        and item.type is not WorkoutType.REST
        and item.workout_id not in planning.done
    ]


def own_tennis(content: PlanContent, resources: Resources | None) -> frozenset[UUID]:
    """The tennis sessions of the athlete's declared schedule: their own commitment, which
    the plan counts as load but does not place (policy spec 5). A declared slot covers one
    session on its weekday, no longer than the slot (the shortest slot that fits)."""
    if resources is None or not resources.tennis_schedule:
        return frozenset()
    found: set[UUID] = set()
    for week in content.weeks:
        for workouts in week.days:
            sessions = [(item, item.tennis) for item in workouts if item.tennis is not None]
            if not sessions:
                continue
            weekday = Weekday.of(sessions[0][0].training_day)
            slots = sorted(
                slot.duration_minutes * 60
                for slot in resources.tennis_schedule
                if slot.weekday is weekday
            )
            for item, tennis in sessions:
                fitting = next((s for s in slots if tennis.duration_seconds <= s), None)
                if fitting is not None:
                    slots.remove(fitting)
                    found.add(item.workout_id)
    return frozenset(found)


# --------------------------------------------------------------------------- conditions


def safety_hold(
    content: PlanContent, conditions: frozenset[Condition], planning: PlanningContext
) -> list[PolicyReason]:
    if Condition.SAFETY_HOLD not in conditions:
        return []
    return [
        _reason("POL-SAFETY-HOLD", "SAFETY_HOLD_ACTIVE", item)
        for item in _upcoming(content, planning)
        if item.intensive
    ]


def recovery_block(
    content: PlanContent, conditions: frozenset[Condition], planning: PlanningContext
) -> list[PolicyReason]:
    """Intensity before the block's re-evaluation; any intensity when its end is unknown."""
    if Condition.RECOVERY_BLOCK not in conditions:
        return []
    until = planning.recovery_block_until
    extra = {} if until is None else {"reevaluateAfter": until.isoformat()}
    return [
        _reason("POL-RECOVERY-BLOCK", "RECOVERY_BLOCK_ACTIVE", item, **extra)
        for item in _upcoming(content, planning)
        if item.intensive and (until is None or item.training_day < until)
    ]


# --------------------------------------------------------------------------- availability


def _effective(planning: PlanningContext, day: date) -> EffectiveAvailability | None:
    return None if planning.availability is None else planning.availability.resolve(day)


def _day_availability(
    day: date,
    workouts: Sequence[PlannedWorkout],
    planning: PlanningContext,
    own: frozenset[UUID],
) -> Iterator[PolicyReason]:
    found = _effective(planning, day)
    explicit = found is not None and found.basis is AvailabilityBasis.EXCEPTION
    for item in workouts:
        if item.workout_id in own and not explicit:
            continue
        if found is None or not found.plannable:
            yield _reason("POL-AVAILABILITY", "DAY_NOT_PLANNABLE", item)
        elif not found.available:
            yield _reason("POL-AVAILABILITY", "DAY_UNAVAILABLE", item)
        elif (
            found.allowed_sports is not None
            and item.sport is not None
            and item.sport not in found.allowed_sports
        ):
            yield _reason("POL-AVAILABILITY", "SPORT_NOT_ALLOWED", item, sport=item.sport.value)
    placed = [item for item in workouts if item.workout_id not in own]
    recurring = None if planning.availability is None else planning.availability.recurring
    doubles = recurring is not None and recurring.double_sessions_allowed
    if len(placed) > 1 and not doubles:
        yield _reason(
            "POL-AVAILABILITY",
            "DOUBLE_SESSION_NOT_ALLOWED",
            day=day.isoformat(),
            sessions=str(len(placed)),
        )


def _day_duration(
    day: date,
    workouts: Sequence[PlannedWorkout],
    planning: PlanningContext,
    own: frozenset[UUID],
) -> Iterator[PolicyReason]:
    found = _effective(planning, day)
    if found is None or not found.plannable or not found.available:
        return  # already denied by POL-AVAILABILITY
    placed = [item for item in workouts if item.workout_id not in own]
    for item in placed:
        if item.planned_seconds is None:
            yield _reason("POL-MAX-DURATION", "DURATION_UNKNOWN", item)
    planned = sum(item.planned_seconds or 0.0 for item in placed)
    if planned > found.maximum_duration_minutes * 60:
        yield _reason(
            "POL-MAX-DURATION",
            "DAY_DURATION_EXCEEDED",
            day=day.isoformat(),
            plannedMinutes=str(round(planned / 60)),
            maximumMinutes=str(found.maximum_duration_minutes),
        )


def _week_limits(
    week: TrainingWeek, planning: PlanningContext, own: frozenset[UUID]
) -> Iterator[PolicyReason]:
    recurring = None if planning.availability is None else planning.availability.recurring
    if recurring is None or week.end < planning.today:
        return
    placed = [
        item
        for day in week.days
        for item in day
        if item.type is not WorkoutType.REST and item.workout_id not in own
    ]
    days = len({item.training_day for item in placed})
    if days > recurring.days_per_week:
        yield _reason(
            "POL-AVAILABILITY",
            "TOO_MANY_TRAINING_DAYS",
            weekStart=week.start.isoformat(),
            trainingDays=str(days),
            maximumDays=str(recurring.days_per_week),
        )
    hours = sum(item.planned_seconds or 0.0 for item in placed) / 3600
    if hours > recurring.maximum_hours_per_week:
        yield _reason(
            "POL-MAX-DURATION",
            "WEEK_DURATION_EXCEEDED",
            weekStart=week.start.isoformat(),
            plannedHours=f"{hours:.1f}",
            maximumHours=f"{recurring.maximum_hours_per_week:g}",
        )


def availability(content: PlanContent, planning: PlanningContext) -> list[PolicyReason]:
    """POL-AVAILABILITY and POL-MAX-DURATION. Without a recurring weekly pattern the weekly
    limits are unknown, so a plan placing workouts is denied (WEEKLY_PATTERN_MISSING)."""
    own = own_tennis(content, planning.resources)
    by_day: dict[date, list[PlannedWorkout]] = defaultdict(list)
    for item in _upcoming(content, planning):
        by_day[item.training_day].append(item)
    reasons: list[PolicyReason] = []
    for day in sorted(by_day):
        reasons.extend(_day_availability(day, by_day[day], planning, own))
    recurring = None if planning.availability is None else planning.availability.recurring
    placed = any(item.workout_id not in own for items in by_day.values() for item in items)
    if recurring is None and placed:
        reasons.append(_reason("POL-AVAILABILITY", "WEEKLY_PATTERN_MISSING"))
    for day in sorted(by_day):
        reasons.extend(_day_duration(day, by_day[day], planning, own))
    for week in content.weeks:
        reasons.extend(_week_limits(week, planning, own))
    return reasons


# --------------------------------------------------------------------------- equipment


def _exercises(
    workout: PlannedWorkout, catalogue: ExerciseCatalogue
) -> Iterator[tuple[PrescribedExercise, Exercise | None]]:
    if workout.strength is None:
        return
    for item in workout.strength.exercises:
        yield item, catalogue.get(item.exercise_id) if item.exercise_id in catalogue else None


def equipment(content: PlanContent, planning: PlanningContext) -> list[PolicyReason]:
    resources = planning.resources
    owned = frozenset() if resources is None else resources.strength_equipment
    pool = resources is not None and resources.pool is not None and resources.pool.available
    bicycle = resources is not None and (
        resources.bicycle is True or resources.smart_trainer is True
    )
    reasons: list[PolicyReason] = []
    for item in _upcoming(content, planning):
        for prescribed, exercise in _exercises(item, planning.catalogue):
            if exercise is None:
                reasons.append(
                    _reason(
                        "POL-EQUIPMENT", "EXERCISE_UNKNOWN", item, exerciseId=prescribed.exercise_id
                    )
                )
            elif not equipment_available(exercise, owned):
                reasons.append(
                    _reason(
                        "POL-EQUIPMENT", "EQUIPMENT_MISSING", item, exerciseId=exercise.exercise_id
                    )
                )
        if item.sport is Sport.SWIMMING and not pool:
            reasons.append(_reason("POL-EQUIPMENT", "POOL_REQUIRED", item))
        if item.sport is Sport.CYCLING and not bicycle:
            reasons.append(_reason("POL-EQUIPMENT", "BICYCLE_REQUIRED", item))
    return reasons


# --------------------------------------------------------------------------- health


def _hard_target(item: PrescribedExercise) -> bool:
    """A planned exercise read at its target (strength spec 8), hard when either its RIR or
    its RPE target is; an unknown effort may be hard."""
    readings = []
    if item.target_rir is not None:
        readings.append(item.target_rir <= HARD_SET_MAX_RIR)
    if item.target_rpe is not None:
        readings.append(item.target_rpe >= HARD_SET_MIN_RPE)
    if readings:
        return any(readings)
    return item.effort_cue is None or item.effort_cue is EffortCue.HARD


def _regions(constraint: HealthConstraint) -> frozenset[BodyRegion]:
    found: set[BodyRegion] = set()
    for name in constraint.affected_regions:
        region = BodyRegion(name) if name in BodyRegion.__members__ else region_named(name)
        if region is not None:
            found.add(region)
    return frozenset(found)


def _violations(
    workout: PlannedWorkout, constraint: HealthConstraint, catalogue: ExerciseCatalogue
) -> Iterator[PolicyReason]:
    def deny(code: str, **parameters: str) -> PolicyReason:
        return _reason(
            "POL-HEALTH-CONSTRAINT",
            code,
            workout,
            constraintId=str(constraint.constraint_id),
            **parameters,
        )

    sport = workout.sport
    in_scope = not constraint.affected_sports or sport in constraint.affected_sports
    exercises = [
        (prescribed, exercise)
        for prescribed, exercise in _exercises(workout, catalogue)
        if exercise is not None
    ]
    for restriction in constraint.restrictions:
        if restriction.kind is RestrictionKind.NO_SPORT:
            if sport is not None and sport is restriction.sport:
                yield deny("SPORT_RESTRICTED", sport=sport.value)
            continue
        if not in_scope:
            continue
        match restriction.kind:
            case RestrictionKind.MAX_RPE:
                rpe = workout.max_rpe
                limit = restriction.max_rpe if restriction.max_rpe is not None else 0
                if rpe is None or rpe > limit:
                    yield deny("RPE_ABOVE_LIMIT", maxRpe=str(limit))
            case RestrictionKind.NO_IMPACT:
                if sport in IMPACT_SPORTS:
                    yield deny("IMPACT_RESTRICTED")
                for _, exercise in exercises:
                    if ContraindicationTag.HIGH_IMPACT in exercise.contraindication_tags:
                        yield deny("IMPACT_RESTRICTED", exerciseId=exercise.exercise_id)
            case RestrictionKind.NO_OVERHEAD:
                if sport in OVERHEAD_SPORTS:
                    yield deny("OVERHEAD_RESTRICTED")
                for _, exercise in exercises:
                    if ContraindicationTag.OVERHEAD in exercise.contraindication_tags:
                        yield deny("OVERHEAD_RESTRICTED", exerciseId=exercise.exercise_id)
            case RestrictionKind.NO_HEAVY_LOWER_BODY:
                for prescribed, exercise in exercises:
                    if exercise.movement_pattern in LOWER_BODY_PATTERNS and _hard_target(
                        prescribed
                    ):
                        yield deny("HEAVY_LOWER_BODY_RESTRICTED", exerciseId=exercise.exercise_id)
            case _:  # OTHER: free text, not checked by code (policy spec 5)
                pass
    if not in_scope:
        return
    regions = _regions(constraint)
    for prescribed, exercise in exercises:
        loaded = sorted(regions & regions_loaded(exercise))
        if loaded and _hard_target(prescribed):
            yield deny("REGION_RESTRICTED", exerciseId=exercise.exercise_id, region=loaded[0].value)


def health(content: PlanContent, planning: PlanningContext) -> list[PolicyReason]:
    if planning.health is None:
        return [_reason("POL-HEALTH-CONSTRAINT", "HEALTH_NOT_DECLARED")]
    reasons: list[PolicyReason] = []
    for item in _upcoming(content, planning):
        for constraint in planning.health.active_constraints(on=item.training_day):
            reasons.extend(_violations(item, constraint, planning.catalogue))
        hurting = planning.pain.applies(item.training_day)
        for prescribed, exercise in _exercises(item, planning.catalogue):
            loaded = sorted(hurting & regions_loaded(exercise)) if exercise else []
            if loaded and exercise is not None and _hard_target(prescribed):
                reasons.append(
                    _reason(
                        "POL-HEALTH-CONSTRAINT",
                        "PAIN_REGION_RESTRICTED",
                        item,
                        exerciseId=exercise.exercise_id,
                        region=loaded[0].value,
                    )
                )
    return reasons


def plan_reasons(
    content: PlanContent, conditions: frozenset[Condition], planning: PlanningContext
) -> list[PolicyReason]:
    """Every plan policy, in a fixed order (deterministic reasons)."""
    return [
        *safety_hold(content, conditions, planning),
        *recovery_block(content, conditions, planning),
        *availability(content, planning),
        *equipment(content, planning),
        *health(content, planning),
    ]
