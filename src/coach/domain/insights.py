"""Performance insights (insights spec; FR-INS-001 to FR-INS-003).

An insight keeps four statements apart. The observation is written in code from derived
values and states only numbers present in, or computed in code from, its evidence. The
interpretation, hypothesis and recommendation come from the AI coach (P13) and stay null
until then. A relationship between variables is an ASSOCIATION, with its variables, sample
size and window; there is no causal relationship (FR-INS-003). Insights are immutable: a
detection that finds the same thing again is not stored twice.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Final, Self
from uuid import UUID

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.evidence import EvidenceRef
from coach.domain.common.quality import Confidence, UncertaintyReason
from coach.domain.common.text import clean_optional_text, clean_text
from coach.domain.common.time import DateRange, ensure_utc

PATTERN_RULES_VERSION: Final = "1.0.0"
MAX_STATEMENT: Final = 1000
_METRIC_ID: Final = re.compile(r"^[a-z]+\.[a-z0-9_]+$")
_SEMVER: Final = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


class Pattern(StrEnum):
    PACE_DETERIORATION = "PATTERN.PACE_DETERIORATION"
    HR_DRIFT = "PATTERN.HR_DRIFT"
    POWER_DEGRADATION = "PATTERN.POWER_DEGRADATION"
    EXCESSIVE_INTENSITY = "PATTERN.EXCESSIVE_INTENSITY"
    INSUFFICIENT_EASY = "PATTERN.INSUFFICIENT_EASY"
    ACCUMULATED_FATIGUE = "PATTERN.ACCUMULATED_FATIGUE"
    INSUFFICIENT_RECOVERY = "PATTERN.INSUFFICIENT_RECOVERY"
    MONOTONY = "PATTERN.MONOTONY"
    AGGRESSIVE_PROGRESSION = "PATTERN.AGGRESSIVE_PROGRESSION"
    STRENGTH_ENDURANCE_INTERFERENCE = "PATTERN.STRENGTH_ENDURANCE_INTERFERENCE"
    TENNIS_INTERFERENCE = "PATTERN.TENNIS_INTERFERENCE"
    SLEEP_PERFORMANCE = "PATTERN.SLEEP_PERFORMANCE"
    PLATEAU = "PATTERN.PLATEAU"
    TECHNICAL_EFFICIENCY = "PATTERN.TECHNICAL_EFFICIENCY"
    ADHERENCE = "PATTERN.ADHERENCE"
    PLANNED_VS_ACTUAL = "PATTERN.PLANNED_VS_ACTUAL"


class Relationship(StrEnum):
    NONE = "NONE"
    ASSOCIATION = "ASSOCIATION"  # never causation (FR-INS-003)


@dataclass(frozen=True, slots=True)
class Association:
    variables: tuple[str, ...]  # metric IDs
    sample_size: int
    window_days: int

    def __post_init__(self) -> None:
        if len(self.variables) < 2 or not all(_METRIC_ID.match(item) for item in self.variables):
            raise DomainValidationError("an association names two or more metric IDs")
        for name in ("sample_size", "window_days"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise DomainValidationError(f"an association's {name} is a positive integer")


@dataclass(frozen=True, slots=True)
class AgentVersion:
    prompt_version: str
    model_id: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class Insight:
    insight_id: UUID
    athlete_id: UUID
    kind: Pattern
    period: DateRange  # the days the observation covers
    observation: str
    evidence: tuple[EvidenceRef, ...]
    confidence: Confidence
    uncertainty_reasons: frozenset[UncertaintyReason]
    generated_at: datetime
    calculation_versions: tuple[tuple[str, str], ...] = ()  # metric ID, version; sorted
    relationship: Relationship = Relationship.NONE
    association: Association | None = None
    interpretation: str | None = None  # AI coach (P13)
    hypothesis: str | None = None
    recommendation: str | None = None
    agent_version: AgentVersion | None = None
    rules_version: str = PATTERN_RULES_VERSION
    version: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "generated_at", ensure_utc(self.generated_at))
        object.__setattr__(
            self,
            "observation",
            clean_text(self.observation, max_length=MAX_STATEMENT, field="observation"),
        )
        for name in ("interpretation", "hypothesis", "recommendation"):
            text = clean_optional_text(getattr(self, name), max_length=MAX_STATEMENT, field=name)
            object.__setattr__(self, name, text)
        if not self.evidence:
            raise DomainValidationError("an insight states its evidence")
        if (self.relationship is Relationship.ASSOCIATION) != (self.association is not None):
            raise DomainValidationError("an association, and only it, states its variables")
        names = [name for name, _ in self.calculation_versions]
        if names != sorted(set(names)) or not all(
            _METRIC_ID.match(name) and _SEMVER.match(version)
            for name, version in self.calculation_versions
        ):
            raise DomainValidationError("calculation versions are unique metric IDs, sorted")
        if not _SEMVER.match(self.rules_version):
            raise DomainValidationError("pattern rules are versioned MAJOR.MINOR.PATCH")

    @property
    def entity_id(self) -> UUID:
        return self.insight_id

    def with_version(self, version: int) -> Self:
        return replace(self, version=version)

    def same_finding(self, other: Insight) -> bool:
        """The same pattern, days, observation and evidence: found again, not new."""
        mine = (self.kind, self.period, self.observation, self.evidence, self.rules_version)
        return mine == (
            other.kind,
            other.period,
            other.observation,
            other.evidence,
            other.rules_version,
        )


def versions(pairs: Iterable[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    """Calculation versions as the insight keeps them: unique, sorted by metric ID."""
    return tuple(sorted(dict(pairs).items()))
