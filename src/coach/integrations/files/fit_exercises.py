"""FIT exercise names -> catalogue exercises, mapping 1.0.0 (files spec 8.7).

Keys are ``category/name`` codes of the FIT SDK profile (``exercise_category`` and the
``<category>_exercise_name`` types). Only exact equivalents are listed: a loaded variant of
an exercise ("weighted_...") is the same exercise, its load is the set's weight. A category
alone, a subtype without a name, or a name not listed maps to nothing: the set keeps the
file's code and its exercise is UNKNOWN until the athlete names it. Never guessed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final

MAPPING_VERSION: Final = "1.0.0"
PROVIDER_PREFIX: Final = "fit:"

_BY_EXERCISE: Final[Mapping[str, tuple[str, ...]]] = {
    # squat
    "ex.back_squat": ("squat/barbell_back_squat",),
    "ex.front_squat": ("squat/barbell_front_squat",),
    "ex.goblet_squat": ("squat/goblet_squat",),
    "ex.leg_press": ("squat/leg_press",),
    "ex.bodyweight_squat": ("squat/air_squat",),
    "ex.jump_squat": (
        "plyo/jump_squat",
        "plyo/body_weight_jump_squat",
        "plyo/weighted_jump_squat",
        "plyo/barbell_jump_squat",
        "plyo/dumbbell_jump_squat",
    ),
    "ex.box_jump": ("plyo/box_jump",),
    # hip hinge
    "ex.deadlift": ("deadlift/barbell_deadlift",),
    "ex.romanian_deadlift": ("deadlift/romanian_deadlift",),
    "ex.single_leg_romanian_deadlift": ("deadlift/single_leg_romanian_deadlift_with_dumbbell",),
    "ex.hip_thrust": (
        "hip_raise/barbell_hip_thrust_on_floor",
        "hip_raise/barbell_hip_thrust_with_bench",
    ),
    "ex.kettlebell_swing": ("hip_raise/kettlebell_swing",),
    "ex.back_extension": ("core/ghd_back_extensions",),
    # lunge
    "ex.reverse_lunge": ("lunge/barbell_reverse_lunge", "lunge/dumbbell_reverse_lunge"),
    "ex.walking_lunge": ("lunge/walking_lunge", "lunge/weighted_walking_lunge"),
    "ex.split_squat": ("lunge/barbell_split_squat", "squat/dumbbell_split_squat"),
    "ex.bulgarian_split_squat": (
        "lunge/barbell_bulgarian_split_squat",
        "lunge/dumbbell_bulgarian_split_squat",
        "lunge/back_foot_elevated_dumbbell_split_squat",
    ),
    "ex.step_up": (
        "squat/step_up",
        "squat/weighted_step_up",
        "squat/dumbbell_step_up",
        "squat/barbell_step_up",
    ),
    "ex.lateral_lunge": (
        "lunge/side_lunge",
        "lunge/weighted_side_lunge",
        "lunge/dumbbell_side_lunge",
        "lunge/barbell_side_lunge",
    ),
    # calf raise
    "ex.standing_calf_raise": (
        "calf_raise/standing_calf_raise",
        "calf_raise/weighted_standing_calf_raise",
        "calf_raise/standing_barbell_calf_raise",
        "calf_raise/standing_dumbbell_calf_raise",
    ),
    "ex.seated_calf_raise": (
        "calf_raise/seated_calf_raise",
        "calf_raise/weighted_seated_calf_raise",
    ),
    "ex.single_leg_calf_raise": (
        "calf_raise/single_leg_standing_calf_raise",
        "calf_raise/single_leg_standing_dumbbell_calf_raise",
    ),
    # push
    "ex.bench_press": ("bench_press/barbell_bench_press",),
    "ex.dumbbell_bench_press": ("bench_press/dumbbell_bench_press",),
    "ex.incline_dumbbell_press": ("bench_press/incline_dumbbell_bench_press",),
    "ex.push_up": ("push_up/push_up", "push_up/weighted_push_up"),
    "ex.overhead_press": (
        "shoulder_press/overhead_barbell_press",
        "shoulder_press/barbell_shoulder_press",
        "shoulder_press/military_press",
    ),
    "ex.dumbbell_shoulder_press": (
        "shoulder_press/dumbbell_shoulder_press",
        "shoulder_press/seated_dumbbell_shoulder_press",
        "shoulder_press/overhead_dumbbell_press",
    ),
    "ex.pike_push_up": ("push_up/pike_push_up", "push_up/weighted_pike_push_up"),
    # pull
    "ex.barbell_row": ("row/barbell_row", "row/bent_over_row_with_barbell"),
    "ex.dumbbell_row": ("row/dumbbell_row",),
    "ex.inverted_row": ("row/inverted_row", "row/weighted_inverted_row"),
    "ex.seated_cable_row": ("row/seated_cable_row",),
    "ex.pull_up": ("pull_up/pull_up", "pull_up/weighted_pull_up"),
    "ex.chin_up": ("pull_up/chin_up", "pull_up/weighted_chin_up"),
    "ex.lat_pulldown": ("pull_up/lat_pulldown",),
    # core
    "ex.plank": ("plank/plank", "plank/weighted_plank"),
    "ex.side_plank": ("plank/side_plank", "plank/weighted_side_plank"),
    "ex.dead_bug": ("hip_stability/dead_bug", "hip_stability/weighted_dead_bug"),
    "ex.ab_wheel_rollout": ("core/kneeling_ab_wheel", "core/weighted_kneeling_ab_wheel"),
    "ex.hanging_knee_raise": (
        "leg_raise/hanging_knee_raise",
        "leg_raise/weighted_hanging_knee_raise",
    ),
    # carry and scapular stability
    "ex.farmers_carry": ("carry/farmers_walk", "carry/farmers_carry"),
    "ex.band_pull_apart": ("banded_exercises/pull_apart",),
    "ex.face_pull": ("row/face_pull",),
    "ex.external_rotation": (
        "shoulder_stability/band_external_rotation",
        "shoulder_stability/cable_external_rotation",
    ),
}

FIT_EXERCISES: Final[Mapping[str, str]] = {
    code: exercise_id for exercise_id, codes in _BY_EXERCISE.items() for code in codes
}


def map_exercises(codes: Sequence[str]) -> tuple[str | None, str | None]:
    """The catalogue exercise of a set's FIT codes and the provenance code.

    The exercise is known only when every code maps to the same catalogue exercise; the
    provenance code is the first one (``fit:`` prefixed), ``None`` when the file names none.
    """
    if not codes:
        return None, None
    found = {FIT_EXERCISES.get(code) for code in codes}
    exercise = next(iter(found)) if len(found) == 1 else None
    return exercise, f"{PROVIDER_PREFIX}{codes[0]}"
