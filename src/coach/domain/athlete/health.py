"""Athlete-declared health constraints (FR-PRF-006). The system never diagnoses them."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.sport import Sport
from coach.domain.common.text import clean_text
from coach.domain.common.time import ensure_utc


class RestrictionKind(StrEnum):
    NO_IMPACT = "NO_IMPACT"
    NO_OVERHEAD = "NO_OVERHEAD"
    NO_HEAVY_LOWER_BODY = "NO_HEAVY_LOWER_BODY"
    MAX_RPE = "MAX_RPE"
    NO_SPORT = "NO_SPORT"
    OTHER = "OTHER"


@dataclass(frozen=True, slots=True)
class Restriction:
    kind: RestrictionKind
    max_rpe: int | None = None
    sport: Sport | None = None

    def __post_init__(self) -> None:
        if self.kind is RestrictionKind.MAX_RPE and (
            self.max_rpe is None or not 0 <= self.max_rpe <= 10
        ):
            raise DomainValidationError("MAX_RPE needs a maximum RPE between 0 and 10")
        if self.kind is RestrictionKind.NO_SPORT and self.sport is None:
            raise DomainValidationError("NO_SPORT needs the sport it excludes")


class ConstraintSource(StrEnum):
    ATHLETE = "ATHLETE"
    PROFESSIONAL_ADVICE_DECLARED = "PROFESSIONAL_ADVICE_DECLARED"


@dataclass(frozen=True, slots=True, kw_only=True)
class HealthConstraint:
    constraint_id: UUID
    description: str
    affected_regions: tuple[str, ...] = ()
    affected_sports: frozenset[Sport] = field(default_factory=frozenset)
    restrictions: tuple[Restriction, ...] = ()
    declared_at: datetime
    valid_until: date | None = None
    source: ConstraintSource = ConstraintSource.ATHLETE

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "description", clean_text(self.description, max_length=500, field="description")
        )
        regions = tuple(
            clean_text(region, max_length=50, field="affected region")
            for region in self.affected_regions
        )
        object.__setattr__(self, "affected_regions", regions)
        object.__setattr__(self, "declared_at", ensure_utc(self.declared_at))

    def is_active(self, *, on: date) -> bool:
        return self.valid_until is None or on <= self.valid_until


@dataclass(frozen=True, slots=True)
class HealthDeclaration:
    """The athlete's current declaration. No constraints is an explicit "none"."""

    declared_at: datetime
    constraints: tuple[HealthConstraint, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "declared_at", ensure_utc(self.declared_at))

    @property
    def declares_none(self) -> bool:
        return not self.constraints

    def active_constraints(self, *, on: date) -> tuple[HealthConstraint, ...]:
        return tuple(constraint for constraint in self.constraints if constraint.is_active(on=on))
