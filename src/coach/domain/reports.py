"""The data part of a report (reporting spec; FR-RPT-001 to FR-RPT-003).

A report's data part is computed in code: sections of items, every number with its evidence.
A section the data cannot fill yet (plans, goals, the AI coach's narrative) is listed as not
available with its reason, never filled with an invented value. Every report states its
Confidence (the lowest of its numbers' confidence, with their uncertainty reasons) and its
Missing data (the sections not available and the items whose value is unknown).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.evidence import EvidenceRef
from coach.domain.common.numbers import require_finite
from coach.domain.common.quality import Confidence, UncertaintyReason, lowest_confidence
from coach.domain.common.text import clean_text
from coach.domain.common.time import DateRange, ensure_utc

REPORT_DATA_VERSION: Final = "1.1.0"


class ReportKind(StrEnum):
    POST_ACTIVITY = "POST_ACTIVITY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"


class Unavailable(StrEnum):
    NEEDS_PLANS = "NEEDS_PLANS"  # plans, workouts and executions: P12
    NEEDS_GOAL_METRICS = "NEEDS_GOAL_METRICS"  # goal-relevant metrics and feasibility: P12
    NARRATIVE = "NARRATIVE"  # written by the AI coach from the data part: P13
    NO_CONSENT = "NO_CONSENT"
    NO_DATA = "NO_DATA"


@dataclass(frozen=True, slots=True, kw_only=True)
class ReportItem:
    label: str
    value: float | str | None  # a number, a code or text, or None: unknown
    unit: str | None = None
    evidence: tuple[EvidenceRef, ...] = ()
    confidence: Confidence | None = None
    reasons: frozenset[UncertaintyReason] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(self, "label", clean_text(self.label, max_length=200, field="label"))
        if isinstance(self.value, bool):
            raise DomainValidationError("a report value is a number, a text or unknown")
        if isinstance(self.value, int | float):
            object.__setattr__(self, "value", require_finite(float(self.value), self.label))
            if not self.evidence:
                raise DomainValidationError(f"the number of {self.label!r} needs its evidence")

    @property
    def numeric(self) -> bool:
        return isinstance(self.value, float)


@dataclass(frozen=True, slots=True)
class Section:
    name: str
    items: tuple[ReportItem, ...] = ()
    unavailable: Unavailable | None = None  # set when the data cannot fill it yet

    def __post_init__(self) -> None:
        clean_text(self.name, max_length=100, field="section")
        if self.unavailable is not None and self.items:
            raise DomainValidationError("a section not available has no items")

    @classmethod
    def not_available(cls, name: str, reason: Unavailable) -> Section:
        return cls(name, (), reason)


@dataclass(frozen=True, slots=True)
class MissingData:
    section: str
    reason: Unavailable | None  # None: an item whose value is unknown
    item: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ReportData:
    kind: ReportKind
    athlete_id: UUID
    period: DateRange
    subject: str  # the activity ID, the ISO week or the month (YYYY-MM)
    sections: tuple[Section, ...]
    generated_at: datetime
    version: str = field(default=REPORT_DATA_VERSION)

    def __post_init__(self) -> None:
        object.__setattr__(self, "generated_at", ensure_utc(self.generated_at))
        names = [section.name for section in self.sections]
        if len(names) != len(set(names)):
            raise DomainValidationError("report sections are unique")

    def _numbers(self) -> list[ReportItem]:
        return [item for section in self.sections for item in section.items if item.numeric]

    @property
    def confidence(self) -> Confidence:
        """The lowest confidence of the report's numbers; LOW without any."""
        found = lowest_confidence(
            item.confidence for item in self._numbers() if item.confidence is not None
        )
        return Confidence.LOW if found is None else found

    @property
    def uncertainty_reasons(self) -> frozenset[UncertaintyReason]:
        reasons: set[UncertaintyReason] = set()
        for section in self.sections:
            for item in section.items:
                reasons |= item.reasons
        if self.missing_data:
            reasons.add(UncertaintyReason.MISSING_DATA)
        return frozenset(reasons)

    @property
    def missing_data(self) -> tuple[MissingData, ...]:
        found = []
        for section in self.sections:
            if section.unavailable is not None:
                found.append(MissingData(section.name, section.unavailable))
            found.extend(
                MissingData(section.name, None, item.label)
                for item in section.items
                if item.value is None
            )
        return tuple(found)

    def section(self, name: str) -> Section:
        return next(item for item in self.sections if item.name == name)


def lowest(items: Iterable[Confidence | None]) -> Confidence | None:
    return lowest_confidence(item for item in items if item is not None)


def union(reasons: Sequence[frozenset[UncertaintyReason]]) -> frozenset[UncertaintyReason]:
    return frozenset().union(*reasons)
