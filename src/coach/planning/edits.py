"""The athlete's own changes to the ACTIVE plan: edit rules 1.0.0 (planning spec 2, precision
of the athlete's review of 2026-10-31; ADR-034).

Pure functions from the ACTIVE content to the edited content. A workout is moved to another
day of the plan, removed, or given other minutes. Only workouts from today on change: the
past of a plan, and a workout already done today, is history. Only steady sessions (one steady
step, with or without a warm-up and a cool-down) and recovery sessions change their minutes;
intervals, strength, tennis and evaluations keep their structure and are moved or removed
instead. The plan policies check the result as they check every version.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import replace
from datetime import date
from typing import Final
from uuid import UUID

from coach.domain.common.errors import DomainValidationError
from coach.domain.plans.plan import PlanContent
from coach.domain.plans.workout import PlannedWorkout, Step, StepKind, WorkoutType
from coach.planning.adaptation import adapted

EDIT_RULES_VERSION: Final = "1.0.0"
MIN_MINUTES: Final = 10
MAX_MINUTES: Final = 300
MIN_STEADY_SECONDS: Final = 300.0  # what a steady step keeps after warm-up and cool-down


def _editable(
    content: PlanContent, workout_id: UUID, today: date, done: Collection[UUID]
) -> PlannedWorkout:
    workout = content.workout(workout_id)
    if workout is None or workout.type is WorkoutType.REST:
        raise DomainValidationError("not a workout of the ACTIVE version")
    if workout.training_day < today or workout_id in done:
        raise DomainValidationError("a workout of the past or already done is history")
    return workout


def moved(
    content: PlanContent,
    workout_id: UUID,
    day: date,
    *,
    today: date,
    done: Collection[UUID] = (),
) -> PlanContent:
    """The workout on another day of the plan, from today on; a rest day that receives it is
    no longer a rest day."""
    workout = _editable(content, workout_id, today, done)
    if day == workout.training_day:
        raise DomainValidationError("the workout is already on that day")
    if day < today or not content.start <= day <= content.end:
        raise DomainValidationError("move it to a day of the plan, from today on")
    changes: dict[UUID, PlannedWorkout | None] = {
        item.workout_id: None for item in content.on(day) if item.type is WorkoutType.REST
    }
    changes[workout_id] = replace(workout, training_day=day)
    return adapted(content, changes)


def removed(
    content: PlanContent, workout_id: UUID, *, today: date, done: Collection[UUID] = ()
) -> PlanContent:
    _editable(content, workout_id, today, done)
    return adapted(content, {workout_id: None})


def resized(
    content: PlanContent,
    workout_id: UUID,
    minutes: int,
    *,
    today: date,
    done: Collection[UUID] = (),
) -> PlanContent:
    """The workout with other minutes, at the same effort."""
    workout = _editable(content, workout_id, today, done)
    if not MIN_MINUTES <= minutes <= MAX_MINUTES:
        raise DomainValidationError(f"minutes are {MIN_MINUTES} to {MAX_MINUTES}")
    seconds = minutes * 60.0
    what = f"{minutes} minutes at the same effort (changed by you)"
    if workout.recovery is not None:
        changed = replace(
            workout, what=what, recovery=replace(workout.recovery, duration_seconds=int(seconds))
        )
        return adapted(content, {workout_id: changed})
    endurance = workout.endurance
    steady = None if endurance is None or len(endurance.main_set) != 1 else endurance.main_set[0]
    if (
        endurance is None
        or not isinstance(steady, Step)
        or steady.kind is not StepKind.STEADY
        or steady.duration_seconds is None
        or any(step.duration_seconds is None for step in (*endurance.warmup, *endurance.cooldown))
    ):
        raise DomainValidationError(
            "only steady and recovery sessions change their minutes: move or remove it instead"
        )
    around = sum(step.duration_seconds or 0.0 for step in (*endurance.warmup, *endurance.cooldown))
    if seconds - around < MIN_STEADY_SECONDS:
        raise DomainValidationError("too few minutes for its warm-up and cool-down")
    session = replace(
        endurance,
        main_set=(replace(steady, duration_seconds=seconds - around),),
        duration_seconds=None if endurance.duration_seconds is None else seconds,
    )
    return adapted(content, {workout_id: replace(workout, what=what, endurance=session)})
