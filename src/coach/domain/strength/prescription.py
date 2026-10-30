"""Strength workout prescription, rules 1.0.0 (strength spec section 4; FR-STR-004).

The strength block of a planned workout. An exercise's pattern and muscles are the
catalogue's, never restated by hand; its alternatives are catalogue exercises of the same
pattern. Effort is prescribed as RPE or RIR only for an athlete familiar with that scale
(profile; unknown familiarity counts as not familiar); otherwise the load is conservative
and the effort is a plain-language cue (EASY or MODERATE, never HARD).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from coach.domain.athlete.experience import StrengthExperience
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.numbers import require_finite
from coach.domain.common.text import clean_text
from coach.domain.strength.exercise import Exercise, ExerciseCatalogue, check_exercise_id
from coach.domain.strength.sets import check_rir, check_rpe
from coach.domain.strength.vocabulary import (
    BodyRegion,
    InterferenceLevel,
    MovementPattern,
    MuscleGroup,
)

PRESCRIPTION_RULES_VERSION: Final = "1.0.0"
MAX_SETS: Final = 20
MAX_REPS: Final = 100
MAX_REST_SECONDS: Final = 900
MIN_DURATION_SECONDS: Final = 60
MAX_DURATION_SECONDS: Final = 10_800
MAX_TEXT: Final = 500
MAX_WARMUP_ITEM: Final = 300
_TEMPO: Final = re.compile(r"[0-9X]-[0-9X]-[0-9X]-[0-9X]")


class LoadKind(StrEnum):
    ABSOLUTE_KG = "ABSOLUTE_KG"
    PERCENT_1RM = "PERCENT_1RM"
    RPE_BASED = "RPE_BASED"  # the load that gives the target RPE or RIR
    BODYWEIGHT = "BODYWEIGHT"


class EffortCue(StrEnum):
    """Plain-language effort for an athlete not familiar with RPE or RIR."""

    EASY = "EASY"  # many more repetitions were possible
    MODERATE = "MODERATE"  # challenging, form stays perfect, several repetitions left
    HARD = "HARD"  # only a few repetitions left


@dataclass(frozen=True, slots=True)
class Load:
    kind: LoadKind
    value: float | None = None  # kg for ABSOLUTE_KG, % for PERCENT_1RM, else none

    def __post_init__(self) -> None:
        if self.kind in (LoadKind.RPE_BASED, LoadKind.BODYWEIGHT):
            if self.value is not None:
                raise DomainValidationError(f"a {self.kind} load states no value")
            return
        if self.value is None:
            raise DomainValidationError(f"a {self.kind} load states its value")
        high = 100.0 if self.kind is LoadKind.PERCENT_1RM else 1000.0
        if not 0 < require_finite(self.value, "load") <= high:
            raise DomainValidationError(f"a {self.kind} load is above 0 and at most {high:g}")


@dataclass(frozen=True, slots=True, kw_only=True)
class PrescribedExercise:
    exercise_id: str
    movement_pattern: MovementPattern
    primary_muscles: tuple[MuscleGroup, ...]
    secondary_muscles: tuple[MuscleGroup, ...]
    sets: int
    reps_min: int
    reps_max: int
    load: Load
    target_rpe: float | None = None
    target_rir: int | None = None
    effort_cue: EffortCue | None = None
    rest_seconds: int
    tempo: str | None = None
    alternatives: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        check_exercise_id(self.exercise_id)
        if not 1 <= self.sets <= MAX_SETS:
            raise DomainValidationError(f"sets are 1 to {MAX_SETS}")
        if not 1 <= self.reps_min <= self.reps_max <= MAX_REPS:
            raise DomainValidationError(f"repetitions are a range within 1 to {MAX_REPS}")
        check_rpe(self.target_rpe, "target RPE")
        check_rir(self.target_rir)
        if not 0 <= self.rest_seconds <= MAX_REST_SECONDS:
            raise DomainValidationError(f"rest is 0 to {MAX_REST_SECONDS} s")
        if self.tempo is not None and not _TEMPO.fullmatch(self.tempo):
            raise DomainValidationError("tempo is four digits or X, for example 3-1-1-0")
        for alternative in self.alternatives:
            check_exercise_id(alternative)
        if self.exercise_id in self.alternatives or len(set(self.alternatives)) != len(
            self.alternatives
        ):
            raise DomainValidationError("alternatives are other exercises, once each")
        scaled = self.target_rpe is not None or self.target_rir is not None
        if not scaled and self.effort_cue is None:
            raise DomainValidationError(f"{self.exercise_id}: state RPE, RIR or an effort cue")
        if self.load.kind is LoadKind.RPE_BASED and not scaled:
            raise DomainValidationError("an RPE-based load needs a target RPE or RIR")

    @classmethod
    def of(cls, exercise: Exercise, **fields: Any) -> PrescribedExercise:
        """A prescription of a catalogue exercise: its pattern and muscles are the catalogue's."""
        return cls(
            exercise_id=exercise.exercise_id,
            movement_pattern=exercise.movement_pattern,
            primary_muscles=exercise.primary_muscles,
            secondary_muscles=exercise.secondary_muscles,
            **fields,
        )

    @property
    def effort_scaled(self) -> bool:
        return self.target_rpe is not None or self.target_rir is not None


@dataclass(frozen=True, slots=True, kw_only=True)
class ExpectedInterference:
    level: InterferenceLevel
    affected_regions: tuple[BodyRegion, ...] = ()
    notes: str = ""

    def __post_init__(self) -> None:
        if len(set(self.affected_regions)) != len(self.affected_regions):
            raise DomainValidationError("a region is listed once")
        if self.notes:
            object.__setattr__(
                self, "notes", clean_text(self.notes, max_length=MAX_TEXT, field="notes")
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class StrengthPrescription:
    """The strength block of a planned workout (contract workout, ``strengthWorkout``)."""

    purpose: str
    estimated_duration_seconds: int
    warmup: tuple[str, ...]
    exercises: tuple[PrescribedExercise, ...]
    progression_rule: str
    regression_rule: str
    expected_interference: ExpectedInterference

    def __post_init__(self) -> None:
        for name in ("purpose", "progression_rule", "regression_rule"):
            object.__setattr__(
                self, name, clean_text(getattr(self, name), max_length=MAX_TEXT, field=name)
            )
        object.__setattr__(
            self,
            "warmup",
            tuple(
                clean_text(item, max_length=MAX_WARMUP_ITEM, field="warm-up")
                for item in self.warmup
            ),
        )
        duration = self.estimated_duration_seconds
        if not MIN_DURATION_SECONDS <= duration <= MAX_DURATION_SECONDS:
            raise DomainValidationError("a strength workout lasts 1 minute to 3 hours")
        if not self.exercises:
            raise DomainValidationError("a strength workout has at least one exercise")


def check_against(
    prescription: StrengthPrescription,
    catalogue: ExerciseCatalogue,
    experience: StrengthExperience | None,
) -> StrengthPrescription:
    """Rules that need the catalogue and the athlete (strength spec 4)."""
    rpe_familiar = experience is not None and experience.rpe_familiar is True
    rir_familiar = experience is not None and experience.rir_familiar is True
    for item in prescription.exercises:
        if item.exercise_id not in catalogue:
            raise DomainValidationError(f"unknown exercise: {item.exercise_id}")
        exercise = catalogue.get(item.exercise_id)
        if (item.movement_pattern, item.primary_muscles, item.secondary_muscles) != (
            exercise.movement_pattern,
            exercise.primary_muscles,
            exercise.secondary_muscles,
        ):
            raise DomainValidationError(
                f"{item.exercise_id}: pattern and muscles are the catalogue's"
            )
        for alternative in item.alternatives:
            if alternative not in catalogue:
                raise DomainValidationError(f"unknown alternative: {alternative}")
            if catalogue.get(alternative).movement_pattern is not exercise.movement_pattern:
                raise DomainValidationError(f"{alternative} is not a {exercise.movement_pattern}")
        if item.target_rpe is not None and not rpe_familiar:
            raise DomainValidationError("RPE targets only for an athlete familiar with RPE")
        if item.target_rir is not None and not rir_familiar:
            raise DomainValidationError("RIR targets only for an athlete familiar with RIR")
        if not item.effort_scaled and item.effort_cue is EffortCue.HARD:
            raise DomainValidationError("without RPE or RIR, effort stays EASY or MODERATE")
    return prescription
