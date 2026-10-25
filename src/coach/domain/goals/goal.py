"""SportEvent and Goal aggregates (FR-GOL-001, FR-GOL-002)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from coach.domain.common.errors import DomainValidationError, InvalidTransitionError
from coach.domain.common.evidence import EvidenceRef
from coach.domain.common.numbers import require_finite
from coach.domain.common.sport import Sport
from coach.domain.common.text import clean_optional_text, clean_text
from coach.domain.common.time import ensure_utc


class GoalPriority(StrEnum):
    A = "A"
    B = "B"
    C = "C"


class GoalStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ACHIEVED = "ACHIEVED"
    MISSED = "MISSED"
    ABANDONED = "ABANDONED"
    SUPERSEDED = "SUPERSEDED"


_ALLOWED_TRANSITIONS: Final[dict[GoalStatus, frozenset[GoalStatus]]] = {
    GoalStatus.DRAFT: frozenset({GoalStatus.ACTIVE, GoalStatus.ABANDONED}),
    GoalStatus.ACTIVE: frozenset(
        {GoalStatus.ACHIEVED, GoalStatus.MISSED, GoalStatus.ABANDONED, GoalStatus.SUPERSEDED}
    ),
}


def _positive(value: float | None, name: str) -> None:
    if value is not None and require_finite(value, name) <= 0:
        raise DomainValidationError(f"{name} must be positive")


@dataclass(frozen=True, slots=True, kw_only=True)
class SportEvent:
    event_id: UUID
    athlete_id: UUID
    name: str
    sport: Sport
    event_date: date
    priority: GoalPriority
    distance_meters: float | None = None
    location: str | None = None
    expected_conditions: str | None = None
    version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", clean_text(self.name, max_length=200, field="event name"))
        object.__setattr__(
            self, "location", clean_optional_text(self.location, max_length=200, field="location")
        )
        object.__setattr__(
            self,
            "expected_conditions",
            clean_optional_text(self.expected_conditions, max_length=1000, field="conditions"),
        )
        _positive(self.distance_meters, "event distance")

    @property
    def entity_id(self) -> UUID:
        return self.event_id

    def with_version(self, version: int) -> SportEvent:
        return replace(self, version=version)


@dataclass(frozen=True, slots=True)
class GoalBaseline:
    """Immutable snapshot of current-level evidence at goal creation."""

    captured_at: datetime
    evidence: tuple[EvidenceRef, ...] = ()
    summary: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "captured_at", ensure_utc(self.captured_at))
        object.__setattr__(
            self, "summary", clean_optional_text(self.summary, max_length=1000, field="summary")
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Goal:
    goal_id: UUID
    athlete_id: UUID
    sport: Sport
    target_date: date
    priority: GoalPriority
    created_at: datetime
    status: GoalStatus = GoalStatus.DRAFT
    distance_meters: float | None = None
    target_time_seconds: float | None = None
    event_id: UUID | None = None
    baseline: GoalBaseline | None = None
    version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))
        _positive(self.distance_meters, "goal distance")
        _positive(self.target_time_seconds, "target time")

    @classmethod
    def new(
        cls,
        *,
        goal_id: UUID,
        athlete_id: UUID,
        sport: Sport,
        target_date: date,
        priority: GoalPriority,
        created_at: datetime,
        distance_meters: float | None = None,
        target_time_seconds: float | None = None,
        event_id: UUID | None = None,
        baseline: GoalBaseline | None = None,
    ) -> Goal:
        return cls(
            goal_id=goal_id,
            athlete_id=athlete_id,
            sport=sport,
            target_date=target_date,
            priority=priority,
            created_at=created_at,
            distance_meters=distance_meters,
            target_time_seconds=target_time_seconds,
            event_id=event_id,
            baseline=baseline,
        )

    @property
    def entity_id(self) -> UUID:
        return self.goal_id

    def with_version(self, version: int) -> Goal:
        return replace(self, version=version)

    def transition(self, status: GoalStatus) -> Goal:
        if status not in _ALLOWED_TRANSITIONS.get(self.status, frozenset()):
            raise InvalidTransitionError(f"goal cannot move from {self.status} to {status}")
        return replace(self, status=status)

    @property
    def is_active_primary(self) -> bool:
        return self.status is GoalStatus.ACTIVE and self.priority is GoalPriority.A


def ensure_single_active_primary(goals: Iterable[Goal], candidate: Goal) -> None:
    """At most one ACTIVE priority-A goal (assumption A-19)."""
    if not candidate.is_active_primary:
        return
    if any(goal.is_active_primary and goal.goal_id != candidate.goal_id for goal in goals):
        raise DomainValidationError("another ACTIVE priority-A goal already exists")
