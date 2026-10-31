"""Workout execution (planning spec 6; FR-PLN-007, FR-ACT-011): what was done against what
was planned, and the athlete's manual links.

An execution is a read model computed from the ACTIVE plan version, the athlete's activities
and their manual links (execution rules 1.0.0, ``coach.planning.execution``); only the links
are stored.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from enum import StrEnum
from typing import Final, Self
from uuid import UUID

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.sport import Sport
from coach.domain.common.time import ensure_utc

EXECUTION_RULES_VERSION: Final = "1.0.0"
COMPLIANCE_BAND: Final = (0.8, 1.2)
GRACE_DAYS: Final = 1  # a workout is SKIPPED once the day after it has ended too


class ExecutionStatus(StrEnum):
    PLANNED = "PLANNED"  # not due yet (P12 precision)
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    SKIPPED = "SKIPPED"
    REPLACED = "REPLACED"
    UNPLANNED = "UNPLANNED"


_DONE: Final = frozenset(
    {
        ExecutionStatus.COMPLETE,
        ExecutionStatus.PARTIAL,
        ExecutionStatus.REPLACED,
        ExecutionStatus.UNPLANNED,
    }
)


@dataclass(frozen=True, slots=True)
class Compliance:
    """Ratios of done to planned; ``None`` when not prescribed or not measured."""

    duration_ratio: float | None = None
    distance_ratio: float | None = None
    intensity_share: float | None = None  # time in the target range: not measured in 1.0.0
    structure_share: float | None = None  # planned steps completed: not measured in 1.0.0

    @property
    def measured(self) -> tuple[float, ...]:
        return tuple(
            value
            for value in (
                self.duration_ratio,
                self.distance_ratio,
                self.intensity_share,
                self.structure_share,
            )
            if value is not None
        )

    @property
    def within_band(self) -> bool:
        low, high = COMPLIANCE_BAND
        return all(low <= value <= high for value in self.measured)


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkoutExecution:
    training_day: date
    planned_workout_id: UUID | None  # None: UNPLANNED
    completed_activity_ids: tuple[UUID, ...]
    status: ExecutionStatus
    compliance: Compliance = field(default_factory=Compliance)
    session_rpe: float | None = None  # the highest RPE the athlete recorded
    manual: bool = False  # placed by the athlete's link
    rules_version: str = EXECUTION_RULES_VERSION

    def __post_init__(self) -> None:
        if (self.planned_workout_id is None) != (self.status is ExecutionStatus.UNPLANNED):
            raise DomainValidationError("only an UNPLANNED execution has no planned workout")
        if (self.status in _DONE) != bool(self.completed_activity_ids):
            raise DomainValidationError("a done execution has its activities, a pending one none")


@dataclass(frozen=True, slots=True)
class DoneActivity:
    """What the execution rules read of a completed activity."""

    activity_id: UUID
    training_day: date
    start: datetime
    sport: Sport
    elapsed_seconds: float | None = None
    distance_meters: float | None = None
    rpe: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionLink:
    """The athlete's link of an activity to a planned workout, or to none (UNPLANNED)."""

    athlete_id: UUID
    activity_id: UUID
    planned_workout_id: UUID | None
    linked_at: datetime
    version: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "linked_at", ensure_utc(self.linked_at))

    @property
    def entity_id(self) -> UUID:
        return self.activity_id

    def with_version(self, version: int) -> Self:
        return replace(self, version=version)
