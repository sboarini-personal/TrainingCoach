"""Data quality and uncertainty (spec/functional/activities.spec.md sections 5 and 6)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from coach.domain.common.errors import DomainValidationError

_ANOMALY_CODE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Reliability(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class UncertaintyReason(StrEnum):
    MISSING_DATA = "MISSING_DATA"
    SENSOR_QUALITY = "SENSOR_QUALITY"
    NON_COMPARABLE_SESSION = "NON_COMPARABLE_SESSION"
    ENVIRONMENT = "ENVIRONMENT"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    ESTIMATED_VALUE = "ESTIMATED_VALUE"


@dataclass(frozen=True, slots=True)
class Anomaly:
    code: str
    field: str
    detail: str = ""

    def __post_init__(self) -> None:
        if not _ANOMALY_CODE.match(self.code):
            raise DomainValidationError(f"anomaly code must be UPPER_SNAKE_CASE: {self.code!r}")


def _check_completeness(completeness: float) -> None:
    if not 0.0 <= completeness <= 1.0:
        raise DomainValidationError("completeness must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class DataQuality:
    completeness: float
    reliability: Reliability
    confidence: Confidence
    anomalies: tuple[Anomaly, ...] = ()
    uncertainty_reasons: frozenset[UncertaintyReason] = field(default_factory=frozenset)
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _check_completeness(self.completeness)


def derive_confidence(
    *, completeness: float, reliability: Reliability, anomalies: int, blocking_anomaly: bool
) -> Confidence:
    _check_completeness(completeness)
    if completeness < 0.5 or reliability is Reliability.LOW or blocking_anomaly:
        return Confidence.LOW
    if completeness >= 0.8 and reliability is Reliability.HIGH and anomalies == 0:
        return Confidence.HIGH
    return Confidence.MEDIUM
