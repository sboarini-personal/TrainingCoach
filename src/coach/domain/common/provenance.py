"""Provenance of recorded values and the UNKNOWN convention.

A ``FieldValue`` records who provided a value and when. ``value is None`` means the value
was recorded as UNKNOWN; a field that was never recorded is represented by the absence of a
``FieldValue`` (``None`` at the attribute level).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.time import ensure_utc


class ValueSource(StrEnum):
    ATHLETE_DECLARED = "ATHLETE_DECLARED"
    PROVIDER = "PROVIDER"
    DERIVED = "DERIVED"


class MetricOrigin(StrEnum):
    RAW_PROVIDER = "RAW_PROVIDER"
    PROVIDER_DERIVED = "PROVIDER_DERIVED"
    STANDARD_CALCULATION = "STANDARD_CALCULATION"
    APPLICATION_DERIVED = "APPLICATION_DERIVED"


@dataclass(frozen=True, slots=True)
class Provenance:
    source: ValueSource
    recorded_at: datetime
    provider: str | None = None
    metric_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "recorded_at", ensure_utc(self.recorded_at))
        if self.source is ValueSource.PROVIDER and not self.provider:
            raise DomainValidationError("provider values must name their provider")
        if self.source is ValueSource.DERIVED and not self.metric_id:
            raise DomainValidationError("derived values must name their metric")


@dataclass(frozen=True, slots=True)
class FieldValue[T]:
    value: T | None
    provenance: Provenance

    @property
    def is_known(self) -> bool:
        return self.value is not None

    @classmethod
    def declared(cls, value: T, *, at: datetime) -> FieldValue[T]:
        return cls(value, Provenance(ValueSource.ATHLETE_DECLARED, at))

    @classmethod
    def unknown(cls, *, at: datetime) -> FieldValue[T]:
        return cls(None, Provenance(ValueSource.ATHLETE_DECLARED, at))

    def recorded_within(self, window: timedelta | None, *, now: datetime) -> bool:
        """True when recorded within ``window`` before ``now``; ``None`` never expires."""
        if window is None:
            return True
        return ensure_utc(now) - self.provenance.recorded_at <= window
