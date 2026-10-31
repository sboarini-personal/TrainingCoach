"""Synthetic planned workouts and plan contents for the planning tests (P12)."""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Final
from uuid import UUID

from coach.domain.athlete.resources import TennisSessionType
from coach.domain.common.sport import Sport
from coach.domain.plans.plan import EstimatedLoad, Phase, PhaseBlock, PlanContent, TrainingWeek
from coach.domain.plans.workout import (
    SAFETY_STOP,
    EnduranceWorkout,
    IntensityTarget,
    PlannedWorkout,
    RecoverySession,
    RecoveryType,
    Repeat,
    RpeRange,
    Step,
    StepKind,
    TennisSession,
    WorkoutType,
)
from coach.domain.strength.catalogue import CATALOGUE
from coach.domain.strength.prescription import (
    ExpectedInterference,
    Load,
    LoadKind,
    PrescribedExercise,
    StrengthPrescription,
)
from coach.domain.strength.vocabulary import InterferenceLevel

MONDAY: Final = date(2026, 10, 26)
_IDS = itertools.count(1)


def next_id() -> UUID:
    return UUID(int=0x40000 + next(_IDS))


def target(low: float, high: float) -> IntensityTarget:
    return IntensityTarget(rpe=RpeRange(low, high))


def easy_run(
    day: date, *, minutes: int = 40, sport: Sport = Sport.RUNNING, order: int = 0
) -> PlannedWorkout:
    return PlannedWorkout(
        workout_id=next_id(),
        type=WorkoutType.ENDURANCE,
        training_day=day,
        order=order,
        is_key=False,
        objective="Easy aerobic session",
        what=f"{minutes} minutes easy",
        why="Aerobic base",
        endurance=EnduranceWorkout(
            sport=sport,
            main_set=(
                Step(kind=StepKind.STEADY, duration_seconds=minutes * 60.0, target=target(3, 4)),
            ),
            stop_conditions=(SAFETY_STOP,),
        ),
    )


def intervals(
    day: date, *, repetitions: int = 5, high: float = 8, sport: Sport = Sport.RUNNING
) -> PlannedWorkout:
    return PlannedWorkout(
        workout_id=next_id(),
        type=WorkoutType.ENDURANCE,
        training_day=day,
        order=0,
        is_key=True,
        objective="Threshold intervals",
        what=f"{repetitions} x 5 minutes hard",
        why="Sustainable pace for the goal",
        endurance=EnduranceWorkout(
            sport=sport,
            warmup=(Step(kind=StepKind.WARMUP, duration_seconds=900, target=target(2, 3)),),
            main_set=(
                Repeat(
                    repetitions,
                    (
                        Step(
                            kind=StepKind.WORK, duration_seconds=300, target=target(high - 1, high)
                        ),
                        Step(kind=StepKind.RECOVERY, duration_seconds=120, target=target(2, 3)),
                    ),
                ),
            ),
            cooldown=(Step(kind=StepKind.COOLDOWN, duration_seconds=600, target=target(1, 2)),),
            stop_conditions=(SAFETY_STOP,),
        ),
    )


def strength(
    day: date, *, rpe: float = 7, exercise: str = "ex.goblet_squat", order: int = 0
) -> PlannedWorkout:
    found = CATALOGUE.get(exercise)
    return PlannedWorkout(
        workout_id=next_id(),
        type=WorkoutType.STRENGTH,
        training_day=day,
        order=order,
        is_key=False,
        objective="Strength",
        what="3 x 8",
        why="Strength is planned every week",
        strength=StrengthPrescription(
            purpose="Maintain strength",
            estimated_duration_seconds=2700,
            warmup=("Mobility",),
            exercises=(
                PrescribedExercise.of(
                    found,
                    sets=3,
                    reps_min=8,
                    reps_max=10,
                    load=Load(LoadKind.RPE_BASED),
                    target_rpe=rpe,
                    rest_seconds=90,
                ),
            ),
            progression_rule="Add repetitions before load",
            regression_rule="Reduce load when RPE overshoots",
            expected_interference=ExpectedInterference(level=InterferenceLevel.LOW),
        ),
    )


def tennis(day: date, *, minutes: int = 90, rpe: float = 6) -> PlannedWorkout:
    return PlannedWorkout(
        workout_id=next_id(),
        type=WorkoutType.TENNIS,
        training_day=day,
        order=0,
        is_key=False,
        objective="Recreational tennis",
        what=f"{minutes} minutes",
        why="Tennis counts as load",
        tennis=TennisSession(TennisSessionType.PRACTICE, minutes * 60, rpe),
    )


def rest(day: date) -> PlannedWorkout:
    return PlannedWorkout(
        workout_id=next_id(),
        type=WorkoutType.REST,
        training_day=day,
        order=0,
        is_key=False,
        objective="Rest",
        what="Full rest",
        why="Rest day",
    )


def recovery(day: date, *, minutes: int = 30) -> PlannedWorkout:
    return PlannedWorkout(
        workout_id=next_id(),
        type=WorkoutType.RECOVERY,
        training_day=day,
        order=0,
        is_key=False,
        objective="Active recovery",
        what=f"{minutes} minutes very easy",
        why="Recovery",
        recovery=RecoverySession(RecoveryType.ACTIVE_RECOVERY, minutes * 60, "Very easy"),
    )


def week(
    start: date = MONDAY,
    plan: Mapping[int, Sequence[PlannedWorkout]] | None = None,
    *,
    index: int = 1,
    phase: Phase = Phase.BASE,
) -> TrainingWeek:
    """A week from day offsets (0 is Monday) to workouts; a default balanced week."""
    if plan is None:
        plan = {
            0: [rest(start)],
            1: [strength(start + timedelta(days=1))],
            2: [easy_run(start + timedelta(days=2))],
            3: [intervals(start + timedelta(days=3))],
            5: [easy_run(start + timedelta(days=5), minutes=60)],
        }
    return TrainingWeek(
        week_index=index,
        start=start,
        phase=phase,
        primary_goal="Aerobic base",
        estimated_load=EstimatedLoad(None),
        days=tuple(tuple(plan.get(offset, ())) for offset in range(7)),
    )


def content(*weeks: TrainingWeek, provisional: bool = False) -> PlanContent:
    found = weeks or (week(),)
    start, end = found[0].start, found[-1].end
    return PlanContent(
        start=start,
        end=end,
        phases=(PhaseBlock(Phase.BASE, start, end, "Aerobic base"),),
        weeks=found,
        provisional=provisional,
        assumptions=("No recent history",) if provisional else (),
    )
