"""Execution rules 1.0.0 (planning spec 6; FR-PLN-007, FR-ACT-011, AT-UNP-001).

A pure function from the planned workouts of a period, the activities done in it and the
athlete's links to one execution per planned workout (REST excluded) and one per activity
left over (UNPLANNED):

1. A link to a planned workout of the plan attaches the activity to it (several may); a link
   to none makes it UNPLANNED; a link to a workout no longer in the plan is ignored.
2. Each planned workout, in its day's order, takes the earliest activity left that day of a
   compatible sport (the workout's sport; STRENGTH and TENNIS by type; a recovery session
   without a sport takes OTHER).
3. A planned workout still without one takes the earliest activity left that day of another
   sport: REPLACED by it.
4. With activities: COMPLETE when every measured ratio is within 80% to 120% of the plan,
   PARTIAL otherwise. Without: SKIPPED once the day after the training day has ended, else
   PLANNED. Activities on a REST day are UNPLANNED (AT-UNP-001).
5. Compliance 1.0.0 measures the duration (elapsed time) and, when the workout states one,
   the distance; intensity and structure are not measured (RPE targets have no time in
   range) and stay unknown.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from uuid import UUID

from coach.domain.common.sport import Sport
from coach.domain.plans.execution import (
    GRACE_DAYS,
    Compliance,
    DoneActivity,
    ExecutionLink,
    ExecutionStatus,
    WorkoutExecution,
)
from coach.domain.plans.workout import PlannedWorkout, WorkoutType


def compatible(workout: PlannedWorkout, sport: Sport) -> bool:
    match workout.type:
        case WorkoutType.STRENGTH:
            return sport is Sport.STRENGTH
        case WorkoutType.TENNIS:
            return sport is Sport.TENNIS
        case WorkoutType.RECOVERY:
            return (workout.sport or Sport.OTHER) is sport
        case WorkoutType.REST:
            return False
        case _:
            return workout.sport is sport


def _planned_meters(workout: PlannedWorkout) -> float | None:
    endurance = workout.endurance
    if endurance is None:
        return None
    if endurance.distance_meters is not None:
        return endurance.distance_meters
    steps = endurance.steps()
    if steps and all(step.distance_meters is not None for step, _ in steps):
        return sum((step.distance_meters or 0.0) * times for step, times in steps)
    return None


def _total(values: Sequence[float | None]) -> float | None:
    return (
        None
        if not values or any(value is None for value in values)
        else sum(value or 0.0 for value in values)
    )


def compliance(workout: PlannedWorkout, done: Sequence[DoneActivity]) -> Compliance:
    """Ratios of what was done to what was planned (1.0.0: duration and distance)."""
    seconds = workout.planned_seconds
    elapsed = _total([item.elapsed_seconds for item in done])
    meters = _planned_meters(workout)
    covered = _total([item.distance_meters for item in done])
    return Compliance(
        duration_ratio=None if not seconds or elapsed is None else round(elapsed / seconds, 3),
        distance_ratio=None if not meters or covered is None else round(covered / meters, 3),
    )


def _rpe(done: Sequence[DoneActivity]) -> float | None:
    known = [item.rpe for item in done if item.rpe is not None]
    return max(known) if known else None


def executions(
    planned: Sequence[PlannedWorkout],
    done: Sequence[DoneActivity],
    links: Mapping[UUID, ExecutionLink],
    *,
    today: date,
) -> list[WorkoutExecution]:
    """The executions of the planned workouts and the activities given (one period)."""
    workouts = [item for item in planned if item.type is not WorkoutType.REST]
    known = {item.workout_id for item in workouts}
    left = sorted(done, key=lambda item: (item.training_day, item.start, str(item.activity_id)))
    attached: dict[UUID, list[DoneActivity]] = {item.workout_id: [] for item in workouts}
    manual: set[UUID] = set()
    forced: list[DoneActivity] = []
    for activity in list(left):
        link = links.get(activity.activity_id)
        if link is None:
            continue
        if link.planned_workout_id is None:
            forced.append(activity)
            left.remove(activity)
        elif link.planned_workout_id in known:
            attached[link.planned_workout_id].append(activity)
            manual.add(link.planned_workout_id)
            left.remove(activity)
    ordered = sorted(workouts, key=lambda item: (item.training_day, item.order))
    for workout in ordered:
        if attached[workout.workout_id]:
            continue
        match = next(
            (
                item
                for item in left
                if item.training_day == workout.training_day and compatible(workout, item.sport)
            ),
            None,
        )
        if match is not None:
            attached[workout.workout_id].append(match)
            left.remove(match)
    replaced: dict[UUID, DoneActivity] = {}
    for workout in ordered:
        if attached[workout.workout_id]:
            continue
        other = next((item for item in left if item.training_day == workout.training_day), None)
        if other is not None:
            replaced[workout.workout_id] = other
            left.remove(other)
    found: list[WorkoutExecution] = []
    for workout in ordered:
        mine = attached[workout.workout_id]
        if mine:
            measured = compliance(workout, mine)
            status = ExecutionStatus.COMPLETE if measured.within_band else ExecutionStatus.PARTIAL
            found.append(
                WorkoutExecution(
                    training_day=workout.training_day,
                    planned_workout_id=workout.workout_id,
                    completed_activity_ids=tuple(item.activity_id for item in mine),
                    status=status,
                    compliance=measured,
                    session_rpe=_rpe(mine),
                    manual=workout.workout_id in manual,
                )
            )
        elif workout.workout_id in replaced:
            instead = replaced[workout.workout_id]
            found.append(
                WorkoutExecution(
                    training_day=workout.training_day,
                    planned_workout_id=workout.workout_id,
                    completed_activity_ids=(instead.activity_id,),
                    status=ExecutionStatus.REPLACED,
                    session_rpe=instead.rpe,
                )
            )
        else:
            due = workout.training_day + timedelta(days=GRACE_DAYS) < today
            found.append(
                WorkoutExecution(
                    training_day=workout.training_day,
                    planned_workout_id=workout.workout_id,
                    completed_activity_ids=(),
                    status=ExecutionStatus.SKIPPED if due else ExecutionStatus.PLANNED,
                )
            )
    for activity in sorted(left + forced, key=lambda item: (item.training_day, item.start)):
        found.append(
            WorkoutExecution(
                training_day=activity.training_day,
                planned_workout_id=None,
                completed_activity_ids=(activity.activity_id,),
                status=ExecutionStatus.UNPLANNED,
                session_rpe=activity.rpe,
                manual=activity in forced,
            )
        )
    return sorted(found, key=lambda item: (item.training_day, item.planned_workout_id is None))
