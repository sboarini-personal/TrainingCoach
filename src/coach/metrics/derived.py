"""Derived metric values (FR-MET-001, FR-MET-004; spec/functional/metrics.spec.md section 4).

``derive`` is pure: the caller passes the value id and the computation time. Every value
records the method version and a snapshot of the inputs it used; UNKNOWN values carry a
status that explains them instead of an invented number.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.evidence import EvidenceRef
from coach.domain.common.numbers import require_finite
from coach.domain.common.quality import (
    Confidence,
    UncertaintyReason,
    lowest_confidence,
    meets_minimum,
)
from coach.domain.common.time import ensure_utc
from coach.metrics.registry import Aggregation, MetricDefinition
from coach.metrics.subjects import DimensionKind, MetricSubject, SubjectKind

__all__ = [
    "DerivedMetricValue",
    "DerivedStatus",
    "DimensionKind",
    "InputRecord",
    "MetricInput",
    "MetricSubject",
    "SubjectKind",
    "derive",
    "derive_total",
    "not_applicable",
]


class DerivedStatus(StrEnum):
    COMPUTED = "COMPUTED"
    MISSING_INPUT = "MISSING_INPUT"
    BELOW_MINIMUM_QUALITY = "BELOW_MINIMUM_QUALITY"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True, slots=True)
class MetricInput:
    """An input offered to a calculation; ``value=None`` means UNKNOWN."""

    value: float | None
    evidence: EvidenceRef
    confidence: Confidence
    uncertainty_reasons: frozenset[UncertaintyReason] = frozenset()


@dataclass(frozen=True, slots=True)
class InputRecord:
    """The input actually used: name, value, evidence and confidence."""

    name: str
    value: float
    evidence: EvidenceRef
    confidence: Confidence


@dataclass(frozen=True, slots=True, kw_only=True)
class DerivedMetricValue:
    value_id: UUID
    athlete_id: UUID
    metric_id: str
    calculation_version: str
    subject: MetricSubject
    status: DerivedStatus
    value: float | None
    unit: str
    inputs: tuple[InputRecord, ...]
    missing_inputs: tuple[str, ...]
    input_confidence: Confidence | None
    uncertainty_reasons: frozenset[UncertaintyReason] = field(default_factory=frozenset)
    computed_at: datetime
    superseded_by: UUID | None = None
    missing_evidence: tuple[EvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "computed_at", ensure_utc(self.computed_at))
        computed = self.status is DerivedStatus.COMPUTED
        if computed != (self.value is not None):
            raise DomainValidationError("only COMPUTED values carry a number")
        if self.value is not None:
            require_finite(self.value, self.metric_id)
        if (self.status is DerivedStatus.MISSING_INPUT) != bool(self.missing_inputs):
            raise DomainValidationError("MISSING_INPUT values list their missing inputs")
        if self.status is DerivedStatus.MISSING_INPUT and (
            UncertaintyReason.MISSING_DATA not in self.uncertainty_reasons
        ):
            raise DomainValidationError("MISSING_INPUT values carry MISSING_DATA")
        measured = {DerivedStatus.COMPUTED, DerivedStatus.BELOW_MINIMUM_QUALITY}
        if self.status in measured and self.input_confidence is None:
            raise DomainValidationError("values with all inputs record their input confidence")
        if self.missing_evidence and self.status is not DerivedStatus.MISSING_INPUT:
            raise DomainValidationError("only MISSING_INPUT values name missing contributions")
        if self.status is DerivedStatus.NOT_APPLICABLE and (
            self.inputs or self.input_confidence is not None or self.uncertainty_reasons
        ):
            raise DomainValidationError("a NOT_APPLICABLE value has no inputs")

    @property
    def entity_id(self) -> UUID:
        return self.value_id

    @property
    def is_current(self) -> bool:
        return self.superseded_by is None

    def same_computation(self, other: DerivedMetricValue) -> bool:
        """Same metric, version, subject and input snapshot: recomputing would change nothing."""
        return (
            self.metric_id,
            self.calculation_version,
            self.subject,
            self.status,
            frozenset(self.inputs),
            self.missing_inputs,
            frozenset(self.missing_evidence),
            self.uncertainty_reasons,
        ) == (
            other.metric_id,
            other.calculation_version,
            other.subject,
            other.status,
            frozenset(other.inputs),
            other.missing_inputs,
            frozenset(other.missing_evidence),
            other.uncertainty_reasons,
        )

    def superseded(self, by: UUID) -> DerivedMetricValue:
        if self.superseded_by is not None:
            raise DomainValidationError("a value is superseded only once")
        return replace(self, superseded_by=by)


def check_new_value(value: DerivedMetricValue) -> None:
    if not value.is_current:
        raise DomainValidationError("a new derived value cannot already be superseded")


def check_supersession(old: DerivedMetricValue, new: DerivedMetricValue) -> None:
    """A value is superseded only by a value of the same metric and subject."""
    if (old.athlete_id, old.metric_id, old.subject) != (new.athlete_id, new.metric_id, new.subject):
        raise DomainValidationError("a value is superseded only by the same metric and subject")


def derive(
    definition: MetricDefinition,
    inputs: Mapping[str, MetricInput],
    *,
    value_id: UUID,
    athlete_id: UUID,
    subject: MetricSubject,
    computed_at: datetime,
) -> DerivedMetricValue:
    calculate = definition.calculate
    if calculate is None:
        raise DomainValidationError(f"{definition.metric_id} is not calculated by the application")
    definition.check_subject(subject)
    specs = {spec.name: spec for spec in definition.required_inputs}
    unexpected = sorted(set(inputs) - specs.keys())
    if unexpected:
        raise DomainValidationError(f"unexpected inputs for {definition.metric_id}: {unexpected}")

    records: list[InputRecord] = []
    missing: list[str] = []
    reasons: set[UncertaintyReason] = set()
    for name, spec in specs.items():
        offered = inputs.get(name)
        if offered is None or offered.value is None:
            missing.append(name)
            continue
        records.append(
            InputRecord(name, spec.check(offered.value), offered.evidence, offered.confidence)
        )
        reasons |= offered.uncertainty_reasons
    for name in missing:
        offered = inputs.get(name)
        if offered is not None:
            reasons |= offered.uncertainty_reasons

    confidence = lowest_confidence(record.confidence for record in records)
    value: float | None = None
    if missing:
        status = DerivedStatus.MISSING_INPUT
        reasons.add(UncertaintyReason.MISSING_DATA)
    elif confidence is not None and not meets_minimum(confidence, definition.minimum_data_quality):
        status = DerivedStatus.BELOW_MINIMUM_QUALITY
    else:
        status = DerivedStatus.COMPUTED
        value = require_finite(
            calculate({record.name: record.value for record in records}), definition.metric_id
        )

    return DerivedMetricValue(
        value_id=value_id,
        athlete_id=athlete_id,
        metric_id=definition.metric_id,
        calculation_version=definition.calculation_version,
        subject=subject,
        status=status,
        value=value,
        unit=definition.unit,
        inputs=tuple(records),
        missing_inputs=tuple(missing),
        input_confidence=confidence,
        uncertainty_reasons=frozenset(reasons),
        computed_at=computed_at,
    )


def derive_total(
    definition: MetricDefinition,
    contributions: Sequence[MetricInput],
    *,
    value_id: UUID,
    athlete_id: UUID,
    subject: MetricSubject,
    computed_at: datetime,
) -> DerivedMetricValue:
    """A period total (metrics spec section 4, P8): one UNKNOWN contribution makes it UNKNOWN."""
    if definition.aggregation is not Aggregation.SUM:
        raise DomainValidationError(f"{definition.metric_id} is not an aggregation")
    definition.check_subject(subject)
    if not contributions:
        raise DomainValidationError("a total needs at least one contribution")
    evidence = [contribution.evidence for contribution in contributions]
    if len(evidence) != len(set(evidence)):
        raise DomainValidationError("each contribution has its own evidence")
    (spec,) = definition.required_inputs
    records: list[InputRecord] = []
    reasons: set[UncertaintyReason] = set()
    missing: list[EvidenceRef] = []
    for contribution in contributions:
        reasons |= contribution.uncertainty_reasons
        if contribution.value is None:
            missing.append(contribution.evidence)
            continue
        records.append(
            InputRecord(
                spec.name,
                spec.check(contribution.value),
                contribution.evidence,
                contribution.confidence,
            )
        )
    confidence = lowest_confidence(record.confidence for record in records)
    value: float | None = None
    if missing:
        status = DerivedStatus.MISSING_INPUT
        reasons.add(UncertaintyReason.MISSING_DATA)
    elif confidence is not None and not meets_minimum(confidence, definition.minimum_data_quality):
        status = DerivedStatus.BELOW_MINIMUM_QUALITY
    else:
        status = DerivedStatus.COMPUTED
        value = require_finite(math.fsum(record.value for record in records), definition.metric_id)
    return DerivedMetricValue(
        value_id=value_id,
        athlete_id=athlete_id,
        metric_id=definition.metric_id,
        calculation_version=definition.calculation_version,
        subject=subject,
        status=status,
        value=value,
        unit=definition.unit,
        inputs=tuple(records),
        missing_inputs=(spec.name,) if missing else (),
        input_confidence=confidence,
        uncertainty_reasons=frozenset(reasons),
        computed_at=computed_at,
        missing_evidence=tuple(missing),
    )


def not_applicable(
    definition: MetricDefinition,
    *,
    value_id: UUID,
    athlete_id: UUID,
    subject: MetricSubject,
    computed_at: datetime,
) -> DerivedMetricValue:
    """A value that retires the current one: the metric no longer applies to the subject."""
    definition.check_subject(subject)
    return DerivedMetricValue(
        value_id=value_id,
        athlete_id=athlete_id,
        metric_id=definition.metric_id,
        calculation_version=definition.calculation_version,
        subject=subject,
        status=DerivedStatus.NOT_APPLICABLE,
        value=None,
        unit=definition.unit,
        inputs=(),
        missing_inputs=(),
        input_confidence=None,
        computed_at=computed_at,
    )
