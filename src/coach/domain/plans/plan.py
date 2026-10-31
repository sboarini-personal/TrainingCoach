"""Immutable plan versions (contract training-plan; planning spec sections 1 to 3; FR-PLN-001
to FR-PLN-004, ADR-010).

A version's content (phases, weeks, workouts, assumptions, dates, provisional) never changes
after it is created; its hash is the SHA-256 of the canonical content document and is checked
whenever the version is read. Any change creates a new version with its parent. Only the
status changes, through the transitions of the planning spec. The plan header holds the
active and the latest version numbers: activation updates it with optimistic concurrency in
the same transaction as the versions, so at most one version is ACTIVE (AT-PLN-002).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Final, Self
from uuid import UUID, uuid5

from coach.domain.common.errors import DomainValidationError, InvalidTransitionError
from coach.domain.common.sport import Sport
from coach.domain.common.text import clean_optional_text, clean_text
from coach.domain.common.time import ensure_utc
from coach.domain.plans.workout import MAX_PER_DAY, PlannedWorkout, WorkoutType

VERSION_NAMESPACE: Final = UUID("0193b2a0-9a17-7c0d-8e5f-1a2b3c4d5e6f")
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
_METRIC: Final = re.compile(r"^[a-z]+\.[a-z0-9_]+$")
DAYS: Final = 7


class Phase(StrEnum):
    ADAPTATION = "ADAPTATION"
    BASE = "BASE"
    BUILD = "BUILD"
    SPECIFIC = "SPECIFIC"
    TAPER = "TAPER"
    COMPETITION = "COMPETITION"
    RECOVERY = "RECOVERY"
    TRANSITION = "TRANSITION"


class PlanStatus(StrEnum):
    DRAFT = "DRAFT"
    PROPOSED = "PROPOSED"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    COMPLETED = "COMPLETED"


class SupersededReason(StrEnum):
    ACTIVATED_NEWER_VERSION = "ACTIVATED_NEWER_VERSION"
    REJECTED = "REJECTED"
    REPLACED_BY_NEWER_PROPOSAL = "REPLACED_BY_NEWER_PROPOSAL"


class GeneratorKind(StrEnum):
    ENGINE = "ENGINE"
    AI_ASSISTED = "AI_ASSISTED"


@dataclass(frozen=True, slots=True)
class PhaseBlock:
    phase: Phase
    start: date
    end: date
    focus: str

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise DomainValidationError("a phase ends on or after its start")
        object.__setattr__(self, "focus", clean_text(self.focus, max_length=500, field="focus"))


@dataclass(frozen=True, slots=True, kw_only=True)
class Generator:
    kind: GeneratorKind
    engine_version: str
    prompt_version: str | None = None
    model_id: str | None = None
    decision_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class SportLoad:
    duration_seconds: float | None
    distance_meters: float | None


@dataclass(frozen=True, slots=True)
class EstimatedLoad:
    session_rpe_load: float | None
    by_sport: Mapping[Sport, SportLoad] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class TrainingWeek:
    week_index: int
    start: date  # Monday, the athlete's local date
    phase: Phase
    primary_goal: str
    estimated_load: EstimatedLoad
    days: tuple[tuple[PlannedWorkout, ...], ...]  # Monday to Sunday
    monitoring_metrics: tuple[str, ...] = ()
    adaptation_rules: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.week_index < 1:
            raise DomainValidationError("weeks are numbered from 1")
        if self.start.isoweekday() != 1:
            raise DomainValidationError("a training week starts on a Monday")
        if len(self.days) != DAYS:
            raise DomainValidationError("a training week has seven days")
        object.__setattr__(
            self, "primary_goal", clean_text(self.primary_goal, max_length=500, field="goal")
        )
        ids = []
        for index, workouts in enumerate(self.days):
            if len(workouts) > MAX_PER_DAY:
                raise DomainValidationError(f"at most {MAX_PER_DAY} workouts a day")
            day = self.start + timedelta(days=index)
            for workout in workouts:
                if workout.training_day != day:
                    raise DomainValidationError("a workout is listed on its own training day")
                ids.append(workout.workout_id)
        if len(ids) != len(set(ids)):
            raise DomainValidationError("a workout appears once")
        if not all(_METRIC.match(item) for item in self.monitoring_metrics):
            raise DomainValidationError("monitoring metrics are metric IDs")
        object.__setattr__(
            self,
            "adaptation_rules",
            tuple(clean_text(item, max_length=500, field="rule") for item in self.adaptation_rules),
        )

    @property
    def end(self) -> date:
        return self.start + timedelta(days=DAYS - 1)

    @property
    def workouts(self) -> tuple[PlannedWorkout, ...]:
        return tuple(workout for day in self.days for workout in day)

    def _of_type(self, kind: WorkoutType) -> tuple[UUID, ...]:
        return tuple(item.workout_id for item in self.workouts if item.type is kind)

    @property
    def key_workouts(self) -> tuple[UUID, ...]:
        return tuple(item.workout_id for item in self.workouts if item.is_key)

    @property
    def strength_sessions(self) -> tuple[UUID, ...]:
        return self._of_type(WorkoutType.STRENGTH)

    @property
    def tennis_sessions(self) -> tuple[UUID, ...]:
        return self._of_type(WorkoutType.TENNIS)

    @property
    def recovery_days(self) -> tuple[date, ...]:
        """Days planned for rest or recovery only (an empty day is simply free)."""
        found = []
        for index, workouts in enumerate(self.days):
            kinds = {item.type for item in workouts}
            if kinds and kinds <= {WorkoutType.REST, WorkoutType.RECOVERY}:
                found.append(self.start + timedelta(days=index))
        return tuple(found)


@dataclass(frozen=True, slots=True, kw_only=True)
class PlanContent:
    """What never changes after a version is created."""

    start: date
    end: date
    phases: tuple[PhaseBlock, ...]
    weeks: tuple[TrainingWeek, ...]
    provisional: bool = False
    assumptions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise DomainValidationError("a plan ends on or after its start")
        if not self.phases or not self.weeks:
            raise DomainValidationError("a plan has phases and weeks")
        object.__setattr__(
            self,
            "assumptions",
            tuple(
                clean_text(item, max_length=500, field="assumption") for item in self.assumptions
            ),
        )
        if self.provisional and not self.assumptions:
            raise DomainValidationError("a provisional plan states its assumptions (FR-ONB-004)")
        indexes = [week.week_index for week in self.weeks]
        if indexes != list(range(1, len(self.weeks) + 1)):
            raise DomainValidationError("weeks are numbered 1, 2, 3 ... in order")
        for earlier, later in zip(self.weeks, self.weeks[1:], strict=False):
            if later.start != earlier.start + timedelta(days=DAYS):
                raise DomainValidationError("weeks follow each other")
        for workout in self.workouts:
            if not self.start <= workout.training_day <= self.end:
                raise DomainValidationError("every workout lies within the plan's dates")

    @property
    def workouts(self) -> tuple[PlannedWorkout, ...]:
        return tuple(workout for week in self.weeks for workout in week.workouts)

    def workout(self, workout_id: UUID) -> PlannedWorkout | None:
        return next((item for item in self.workouts if item.workout_id == workout_id), None)

    def on(self, day: date) -> tuple[PlannedWorkout, ...]:
        return tuple(item for item in self.workouts if item.training_day == day)


def version_id(plan_id: UUID, number: int) -> UUID:
    return uuid5(VERSION_NAMESPACE, f"{plan_id}/{number}")


_TRANSITIONS: Final = {
    (PlanStatus.DRAFT, PlanStatus.PROPOSED): None,
    (PlanStatus.PROPOSED, PlanStatus.ACTIVE): None,
    (PlanStatus.ACTIVE, PlanStatus.SUPERSEDED): SupersededReason.ACTIVATED_NEWER_VERSION,
    (PlanStatus.PROPOSED, PlanStatus.SUPERSEDED): None,  # REJECTED or REPLACED_...
    (PlanStatus.ACTIVE, PlanStatus.COMPLETED): None,
}


@dataclass(frozen=True, slots=True, kw_only=True)
class PlanVersion:
    plan_id: UUID
    number: int  # the plan's version number (contract ``version``)
    parent_number: int | None
    athlete_id: UUID
    goal_id: UUID
    generated_at: datetime
    generator: Generator
    content: PlanContent
    content_hash: str
    status: PlanStatus = PlanStatus.DRAFT
    superseded_reason: SupersededReason | None = None
    activation_reason: str | None = None
    version: int = 0  # storage revision (optimistic concurrency of status changes)

    def __post_init__(self) -> None:
        object.__setattr__(self, "generated_at", ensure_utc(self.generated_at))
        if self.number < 1 or (
            self.parent_number is not None and self.parent_number >= self.number
        ):
            raise DomainValidationError("versions count from 1; a parent is an earlier version")
        if not _SHA256.match(self.content_hash):
            raise DomainValidationError("the content hash is a SHA-256")
        if (self.status is PlanStatus.SUPERSEDED) != (self.superseded_reason is not None):
            raise DomainValidationError("a superseded version, and only it, states why")
        object.__setattr__(
            self,
            "activation_reason",
            clean_optional_text(self.activation_reason, max_length=1000, field="activation reason"),
        )

    @property
    def entity_id(self) -> UUID:
        return version_id(self.plan_id, self.number)

    def with_version(self, version: int) -> Self:
        return replace(self, version=version)

    def moved(
        self,
        status: PlanStatus,
        *,
        reason: SupersededReason | None = None,
        activation_reason: str | None = None,
    ) -> PlanVersion:
        """The same content in another status (planning spec 2)."""
        if (self.status, status) not in _TRANSITIONS:
            raise InvalidTransitionError(
                f"a plan version cannot move from {self.status} to {status}"
            )
        fixed = _TRANSITIONS[(self.status, status)]
        if status is PlanStatus.SUPERSEDED:
            reason = fixed or reason
            allowed = {SupersededReason.REJECTED, SupersededReason.REPLACED_BY_NEWER_PROPOSAL}
            if reason is None or (fixed is None and reason not in allowed):
                raise InvalidTransitionError("a superseded proposal is rejected or replaced")
        else:
            reason = None
        return replace(
            self,
            status=status,
            superseded_reason=reason,
            activation_reason=activation_reason
            if status is PlanStatus.ACTIVE
            else self.activation_reason,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class TrainingPlan:
    """The plan header: one per plan, the anchor of its versions' concurrency."""

    plan_id: UUID
    athlete_id: UUID
    goal_id: UUID
    created_at: datetime
    latest_number: int = 1
    active_number: int | None = None
    version: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))
        if self.latest_number < 1 or (
            self.active_number is not None and not 1 <= self.active_number <= self.latest_number
        ):
            raise DomainValidationError("version numbers count from 1 up to the latest")

    @property
    def entity_id(self) -> UUID:
        return self.plan_id

    def with_version(self, version: int) -> Self:
        return replace(self, version=version)
