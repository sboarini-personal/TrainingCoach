"""Strength volume (strength spec section 8; metrics spec 9.6): volume load, version 1.0.0,
and hard sets per muscle group, version 1.0.0.

Both read the executed sets (``StrengthSetResult``, reconciled from the watch and the
athlete). Volume load is repetitions x external load; a bodyweight set adds 0 kg, and a set
whose repetitions or load is unknown is left out and counted, never estimated. A hard set is
a set taken close to failure, judged from the athlete's own effort (RIR, else RPE); a set
without either has an unknown effort. Its muscles are those of its catalogue exercise:
primary muscles count 1, secondary 0.5.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final

from coach.domain.strength.exercise import ExerciseCatalogue
from coach.domain.strength.session import StrengthSetResult
from coach.domain.strength.vocabulary import MuscleGroup

# Hard set (rules 1.0.0): at most 4 repetitions in reserve, which is RPE 6 or more on the
# RIR-based RPE scale (RPE 10 = 0 RIR; Zourdos et al. 2016).
HARD_SET_MAX_RIR: Final = 4
HARD_SET_MIN_RPE: Final = 6.0
PRIMARY_SHARE: Final = 1.0
SECONDARY_SHARE: Final = 0.5


@dataclass(frozen=True, slots=True)
class VolumeLoad:
    kilograms: float  # repetitions x load over the counted sets
    counted: int
    left_out: int  # sets whose repetitions or load is unknown


def volume_load(sets: Iterable[StrengthSetResult]) -> VolumeLoad:
    known: list[float] = []
    left_out = 0
    for item in sets:
        if item.completed_reps is None or item.actual_load_kg is None:
            left_out += 1
            continue
        known.append(item.completed_reps * item.actual_load_kg)
    return VolumeLoad(math.fsum(known), len(known), left_out)


def is_hard(item: StrengthSetResult) -> bool | None:
    """Whether the set was taken close to failure; ``None`` when its effort is unknown."""
    if item.actual_rir is not None:
        return item.actual_rir <= HARD_SET_MAX_RIR
    if item.actual_rpe is not None:
        return item.actual_rpe >= HARD_SET_MIN_RPE
    return None


def muscle_shares(
    item: StrengthSetResult, catalogue: ExerciseCatalogue
) -> Mapping[MuscleGroup, float]:
    """What one set counts for each muscle group of its exercise (empty when unknown)."""
    if item.exercise_id is None or item.exercise_id not in catalogue:
        return {}
    exercise = catalogue.get(item.exercise_id)
    shares = dict.fromkeys(exercise.primary_muscles, PRIMARY_SHARE)
    shares.update(dict.fromkeys(exercise.secondary_muscles, SECONDARY_SHARE))
    return shares
