"""Controlled strength vocabularies (spec/functional/strength.spec.md section 2; FR-STR-002).

Movement patterns and muscle groups mirror the workout contract; body regions add the joint
and tendon regions that interference and regional load name; contraindication tags let a
health constraint exclude exercises by tag, never by name.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class MovementPattern(StrEnum):
    SQUAT = "SQUAT"
    HIP_HINGE = "HIP_HINGE"
    LUNGE = "LUNGE"
    CALF_RAISE = "CALF_RAISE"
    HORIZONTAL_PUSH = "HORIZONTAL_PUSH"
    VERTICAL_PUSH = "VERTICAL_PUSH"
    HORIZONTAL_PULL = "HORIZONTAL_PULL"
    VERTICAL_PULL = "VERTICAL_PULL"
    CORE_STABILIZATION = "CORE_STABILIZATION"
    ANTI_ROTATION = "ANTI_ROTATION"
    LOADED_CARRY = "LOADED_CARRY"
    SCAPULAR_STABILITY = "SCAPULAR_STABILITY"


class MuscleGroup(StrEnum):
    QUADRICEPS = "QUADRICEPS"
    HAMSTRINGS = "HAMSTRINGS"
    GLUTES = "GLUTES"
    CALVES = "CALVES"
    SOLEUS = "SOLEUS"
    ADDUCTORS = "ADDUCTORS"
    ABDUCTORS = "ABDUCTORS"
    FOOT_ANKLE = "FOOT_ANKLE"
    CORE = "CORE"
    ERECTOR_SPINAE = "ERECTOR_SPINAE"
    LATS = "LATS"
    RHOMBOIDS = "RHOMBOIDS"
    TRAPEZIUS = "TRAPEZIUS"
    PECTORALS = "PECTORALS"
    DELTOIDS = "DELTOIDS"
    ROTATOR_CUFF = "ROTATOR_CUFF"
    BICEPS = "BICEPS"
    TRICEPS = "TRICEPS"
    FOREARMS = "FOREARMS"


class BodyRegion(StrEnum):
    """Muscle groups plus the joint and tendon regions they do not cover (P10)."""

    QUADRICEPS = "QUADRICEPS"
    HAMSTRINGS = "HAMSTRINGS"
    GLUTES = "GLUTES"
    CALVES = "CALVES"
    SOLEUS = "SOLEUS"
    ADDUCTORS = "ADDUCTORS"
    ABDUCTORS = "ABDUCTORS"
    FOOT_ANKLE = "FOOT_ANKLE"
    CORE = "CORE"
    ERECTOR_SPINAE = "ERECTOR_SPINAE"
    LATS = "LATS"
    RHOMBOIDS = "RHOMBOIDS"
    TRAPEZIUS = "TRAPEZIUS"
    PECTORALS = "PECTORALS"
    DELTOIDS = "DELTOIDS"
    ROTATOR_CUFF = "ROTATOR_CUFF"
    BICEPS = "BICEPS"
    TRICEPS = "TRICEPS"
    FOREARMS = "FOREARMS"
    ACHILLES_REGION = "ACHILLES_REGION"
    KNEES = "KNEES"
    SHOULDERS = "SHOULDERS"
    ELBOWS = "ELBOWS"

    @classmethod
    def of(cls, muscle: MuscleGroup) -> BodyRegion:
        return cls(muscle.value)


class InterferenceLevel(StrEnum):
    """Expected or assessed interference between sessions (strength spec 7)."""

    NONE = "NONE"
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    VERY_HIGH = "VERY_HIGH"


class ContraindicationTag(StrEnum):
    AXIAL_LOAD = "AXIAL_LOAD"
    OVERHEAD = "OVERHEAD"
    HIGH_IMPACT = "HIGH_IMPACT"
    DEEP_KNEE_FLEXION = "DEEP_KNEE_FLEXION"
    SPINAL_FLEXION = "SPINAL_FLEXION"
    GRIP_INTENSIVE = "GRIP_INTENSIVE"


LOWER_BODY_PATTERNS: Final = frozenset(
    {
        MovementPattern.SQUAT,
        MovementPattern.HIP_HINGE,
        MovementPattern.LUNGE,
        MovementPattern.CALF_RAISE,
    }
)
UPPER_BODY_PATTERNS: Final = frozenset(
    {
        MovementPattern.HORIZONTAL_PUSH,
        MovementPattern.VERTICAL_PUSH,
        MovementPattern.HORIZONTAL_PULL,
        MovementPattern.VERTICAL_PULL,
    }
)  # strength spec 2: SCAPULAR_STABILITY and LOADED_CARRY are "additional", not upper body
