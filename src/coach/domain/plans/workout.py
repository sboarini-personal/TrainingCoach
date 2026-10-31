"""Planned workouts (contract workout 1.1.0; planning spec sections 1 and 4; FR-PLN-004,
FR-PLN-005, FR-PLN-008).

Exactly one detail block matches the workout's type (none for REST). Every endurance step
carries an RPE target; heart rate, pace and power are optional dimensions the planner adds only
when reliable athlete data supports them (FR-PLN-005). Stop conditions always include the
safety rule. A non-submaximal evaluation needs the athlete's opt-in (FR-ONB-005).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final
from uuid import UUID

from coach.domain.athlete.resources import TennisSessionType
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.numbers import require_finite
from coach.domain.common.sport import Sport
from coach.domain.common.text import clean_optional_text, clean_text
from coach.domain.strength.prescription import (
    EffortCue,
    PrescribedExercise,
    StrengthPrescription,
)

SAFETY_STOP: Final = (
    "Stop at once on red-flag symptoms (chest pain, fainting, unusual breathlessness) or "
    "unusual pain."
)
INTENSIVE_RPE: Final = 7.0  # a target from this RPE is intensive training (A-18)
MAX_PER_DAY: Final = 4
_PROTOCOL: Final = re.compile(r"^eval\.[a-z0-9_]+$")
_METRIC: Final = re.compile(r"^[a-z]+\.[a-z0-9_]+$")
ENDURANCE_SPORTS: Final = frozenset({Sport.RUNNING, Sport.CYCLING, Sport.SWIMMING})


class WorkoutType(StrEnum):
    ENDURANCE = "ENDURANCE"
    STRENGTH = "STRENGTH"
    TENNIS = "TENNIS"
    RECOVERY = "RECOVERY"
    REST = "REST"
    EVALUATION = "EVALUATION"


class StepKind(StrEnum):
    WARMUP = "WARMUP"
    WORK = "WORK"
    RECOVERY = "RECOVERY"
    COOLDOWN = "COOLDOWN"
    STEADY = "STEADY"


class PaceUnit(StrEnum):
    SECONDS_PER_KM = "SECONDS_PER_KM"
    SECONDS_PER_100M = "SECONDS_PER_100M"


class RecoveryType(StrEnum):
    ACTIVE_RECOVERY = "ACTIVE_RECOVERY"
    MOBILITY = "MOBILITY"
    EASY_SESSION = "EASY_SESSION"


def _ordered(low: float, high: float, name: str, *, lowest: float, highest: float) -> None:
    require_finite(low, name)
    require_finite(high, name)
    if not lowest <= low <= high <= highest:
        raise DomainValidationError(f"{name}: {lowest} <= min <= max <= {highest}")


@dataclass(frozen=True, slots=True)
class RpeRange:
    low: float
    high: float

    def __post_init__(self) -> None:
        _ordered(self.low, self.high, "RPE", lowest=0, highest=10)


@dataclass(frozen=True, slots=True)
class HeartRateTarget:
    zone_model_id: UUID
    min_bpm: int
    max_bpm: int

    def __post_init__(self) -> None:
        _ordered(self.min_bpm, self.max_bpm, "heart rate", lowest=30, highest=230)


@dataclass(frozen=True, slots=True)
class PaceTarget:
    unit: PaceUnit
    low: float  # seconds per unit; the faster bound
    high: float

    def __post_init__(self) -> None:
        _ordered(self.low, self.high, "pace", lowest=1, highest=3600)


@dataclass(frozen=True, slots=True)
class PowerTarget:
    min_watts: float
    max_watts: float

    def __post_init__(self) -> None:
        _ordered(self.min_watts, self.max_watts, "power", lowest=0, highest=2500)


@dataclass(frozen=True, slots=True, kw_only=True)
class IntensityTarget:
    rpe: RpeRange
    heart_rate: HeartRateTarget | None = None
    pace: PaceTarget | None = None
    power: PowerTarget | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Step:
    kind: StepKind
    target: IntensityTarget
    duration_seconds: float | None = None
    distance_meters: float | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        if self.duration_seconds is None and self.distance_meters is None:
            raise DomainValidationError("a step has a duration or a distance")
        for name in ("duration_seconds", "distance_meters"):
            value = getattr(self, name)
            if value is not None and require_finite(value, name) <= 0:
                raise DomainValidationError(f"a step's {name} is positive")
        object.__setattr__(
            self, "notes", clean_optional_text(self.notes, max_length=300, field="notes")
        )


@dataclass(frozen=True, slots=True)
class Repeat:
    count: int
    steps: tuple[Step, ...]

    def __post_init__(self) -> None:
        if isinstance(self.count, bool) or not 1 <= self.count <= 100:
            raise DomainValidationError("a repeat is done 1 to 100 times")
        if not self.steps:
            raise DomainValidationError("a repeat has steps")


def _steps(items: tuple[Step | Repeat, ...]) -> list[tuple[Step, int]]:
    found: list[tuple[Step, int]] = []
    for item in items:
        if isinstance(item, Repeat):
            found.extend((step, item.count) for step in item.steps)
        else:
            found.append((item, 1))
    return found


@dataclass(frozen=True, slots=True, kw_only=True)
class EnduranceWorkout:
    sport: Sport
    main_set: tuple[Step | Repeat, ...]
    stop_conditions: tuple[str, ...]
    warmup: tuple[Step, ...] = ()
    cooldown: tuple[Step, ...] = ()
    duration_seconds: float | None = None
    distance_meters: float | None = None
    technique_focus: str | None = None
    evaluation_metrics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.sport not in ENDURANCE_SPORTS:
            raise DomainValidationError("an endurance workout is running, cycling or swimming")
        if not self.main_set:
            raise DomainValidationError("an endurance workout has a main set")
        if SAFETY_STOP not in self.stop_conditions:
            raise DomainValidationError("stop conditions include the safety rule")
        object.__setattr__(
            self,
            "stop_conditions",
            tuple(
                clean_text(item, max_length=300, field="stop condition")
                for item in self.stop_conditions
            ),
        )
        object.__setattr__(
            self,
            "technique_focus",
            clean_optional_text(self.technique_focus, max_length=300, field="technique focus"),
        )
        if not all(_METRIC.match(item) for item in self.evaluation_metrics):
            raise DomainValidationError("evaluation metrics are metric IDs")

    def steps(self) -> list[tuple[Step, int]]:
        """Every step with how many times it is done, warm-up to cool-down."""
        return (
            [(step, 1) for step in self.warmup]
            + _steps(self.main_set)
            + [(step, 1) for step in self.cooldown]
        )

    @property
    def planned_seconds(self) -> float | None:
        if self.duration_seconds is not None:
            return self.duration_seconds
        found = self.steps()
        if any(step.duration_seconds is None for step, _ in found):
            return None
        return sum((step.duration_seconds or 0.0) * times for step, times in found)

    @property
    def max_rpe(self) -> float:
        return max(step.target.rpe.high for step, _ in self.steps())


@dataclass(frozen=True, slots=True)
class TennisSession:
    session_type: TennisSessionType
    duration_seconds: int
    expected_rpe: float

    def __post_init__(self) -> None:
        if not 0 < self.duration_seconds <= 36_000:
            raise DomainValidationError("a tennis session lasts up to 10 hours")
        _ordered(self.expected_rpe, self.expected_rpe, "expected RPE", lowest=0, highest=10)


@dataclass(frozen=True, slots=True)
class RecoverySession:
    recovery_type: RecoveryType
    duration_seconds: int
    description: str
    sport: Sport | None = None

    def __post_init__(self) -> None:
        if not 0 < self.duration_seconds <= 14_400:
            raise DomainValidationError("a recovery session lasts up to 4 hours")
        object.__setattr__(
            self, "description", clean_text(self.description, max_length=500, field="description")
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class EvaluationSession:
    sport: Sport
    protocol_id: str
    description: str
    submaximal: bool
    requires_opt_in: bool
    safety_notes: tuple[str, ...]
    duration_seconds: int | None = None  # contract 1.2.0

    def __post_init__(self) -> None:
        if not _PROTOCOL.match(self.protocol_id):
            raise DomainValidationError("an evaluation protocol is eval.<name>")
        if not self.submaximal and not self.requires_opt_in:
            raise DomainValidationError("a maximal test needs the athlete's opt-in (FR-ONB-005)")
        if self.duration_seconds is not None and not 60 <= self.duration_seconds <= 14_400:
            raise DomainValidationError("an evaluation lasts 1 minute to 4 hours")
        if not self.safety_notes:
            raise DomainValidationError("an evaluation states its safety notes")
        object.__setattr__(
            self, "description", clean_text(self.description, max_length=1000, field="description")
        )


_CUE_RPE: Final = {EffortCue.EASY: 3.0, EffortCue.MODERATE: 5.0, EffortCue.HARD: 8.0}


def _exercise_rpe(item: PrescribedExercise) -> float | None:
    """The effort targeted: the harder of the target RPE and 10 minus the target RIR, else
    the effort cue."""
    readings = [
        value
        for value in (
            item.target_rpe,
            None if item.target_rir is None else 10.0 - item.target_rir,
        )
        if value is not None
    ]
    if readings:
        return max(readings)
    return None if item.effort_cue is None else _CUE_RPE[item.effort_cue]


@dataclass(frozen=True, slots=True, kw_only=True)
class PlannedWorkout:
    workout_id: UUID
    type: WorkoutType
    training_day: date
    order: int
    is_key: bool
    objective: str
    what: str
    why: str
    endurance: EnduranceWorkout | None = None
    strength: StrengthPrescription | None = None
    tennis: TennisSession | None = None
    recovery: RecoverySession | None = None
    evaluation: EvaluationSession | None = None

    def __post_init__(self) -> None:
        if isinstance(self.order, bool) or not 0 <= self.order <= 9:
            raise DomainValidationError("a workout's order in its day is 0 to 9")
        object.__setattr__(
            self, "objective", clean_text(self.objective, max_length=500, field="objective")
        )
        object.__setattr__(self, "what", clean_text(self.what, max_length=1000, field="what"))
        object.__setattr__(self, "why", clean_text(self.why, max_length=1000, field="why"))
        details = {
            WorkoutType.ENDURANCE: self.endurance,
            WorkoutType.STRENGTH: self.strength,
            WorkoutType.TENNIS: self.tennis,
            WorkoutType.RECOVERY: self.recovery,
            WorkoutType.EVALUATION: self.evaluation,
        }
        present = {kind for kind, detail in details.items() if detail is not None}
        expected = set() if self.type is WorkoutType.REST else {self.type}
        if present != expected:
            raise DomainValidationError("a workout has exactly the detail block of its type")

    @property
    def sport(self) -> Sport | None:
        match self.type:
            case WorkoutType.ENDURANCE:
                return None if self.endurance is None else self.endurance.sport
            case WorkoutType.STRENGTH:
                return Sport.STRENGTH
            case WorkoutType.TENNIS:
                return Sport.TENNIS
            case WorkoutType.RECOVERY:
                return None if self.recovery is None else self.recovery.sport
            case WorkoutType.EVALUATION:
                return None if self.evaluation is None else self.evaluation.sport
            case _:
                return None

    @property
    def planned_seconds(self) -> float | None:
        """The planned duration; ``None`` when unknown (an evaluation, steps by distance)."""
        if self.endurance is not None:
            return self.endurance.planned_seconds
        if self.strength is not None:
            return float(self.strength.estimated_duration_seconds)
        if self.tennis is not None:
            return float(self.tennis.duration_seconds)
        if self.recovery is not None:
            return float(self.recovery.duration_seconds)
        if self.evaluation is not None and self.evaluation.duration_seconds is not None:
            return float(self.evaluation.duration_seconds)
        return 0.0 if self.type is WorkoutType.REST else None

    @property
    def max_rpe(self) -> float | None:
        """The highest RPE targeted; ``None`` when no RPE is stated (rest, evaluation)."""
        if self.endurance is not None:
            return self.endurance.max_rpe
        if self.strength is not None:
            rpes = [_exercise_rpe(item) for item in self.strength.exercises]
            known = [item for item in rpes if item is not None]
            return None if len(known) < len(rpes) else max(known)
        if self.tennis is not None:
            return self.tennis.expected_rpe
        if self.recovery is not None:
            return 3.0  # recovery and mobility are easy by definition
        return None

    @property
    def intensive(self) -> bool:
        """Intensive training: an RPE target of 7 or more, a maximal test, or an unknown
        effort that may be hard (planning spec 4; policy POL-SAFETY-HOLD)."""
        if self.evaluation is not None:
            return not self.evaluation.submaximal
        if self.type in (WorkoutType.REST, WorkoutType.RECOVERY):
            return False
        rpe = self.max_rpe
        return rpe is None or rpe >= INTENSIVE_RPE
