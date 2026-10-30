"""Daily recovery metrics from a provider (recovery spec sections 2, 3 and 5; FR-REC-005).

One immutable observation per calendar day, metric and RAW source: the value a provider
reported for the athlete's day, with its provenance. A newer payload for the same day and
metric supersedes an older one in reads (the latest received wins); nothing is ever edited.
Each metric belongs to one consent type: without it the metric is neither fetched nor read.
Provider recovery metrics are training signals, never diagnoses (recovery spec 2).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import StrEnum
from typing import Final, Self
from uuid import UUID, uuid5

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.numbers import require_finite
from coach.domain.common.time import ensure_utc
from coach.domain.consent.ledger import ConsentType

OBSERVATION_NAMESPACE: Final = UUID("0193b2a0-dada-7000-8000-dada00000001")
_PROVIDER: Final = re.compile(r"[a-z0-9_]{1,40}")
_PATH: Final = re.compile(r"[A-Za-z0-9_.\[\]]{1,200}")


class RecoveryMetric(StrEnum):
    RESTING_HR = "RESTING_HR"  # beats per minute
    HRV_NIGHTLY = "HRV_NIGHTLY"  # average overnight heart-rate variability, ms
    SLEEP_DURATION = "SLEEP_DURATION"  # measured sleep, naps excluded, s
    BODY_BATTERY_CHARGED = "BODY_BATTERY_CHARGED"  # proprietary 0 to 100 (PROVIDER_DERIVED)


@dataclass(frozen=True, slots=True)
class MetricSpec:
    consent: ConsentType
    unit: str
    low: float
    high: float
    higher_is_better: bool


METRICS: Final[Mapping[RecoveryMetric, MetricSpec]] = {
    RecoveryMetric.RESTING_HR: MetricSpec(ConsentType.RECOVERY, "bpm", 25, 150, False),
    RecoveryMetric.HRV_NIGHTLY: MetricSpec(ConsentType.HRV, "ms", 1, 300, True),
    RecoveryMetric.SLEEP_DURATION: MetricSpec(ConsentType.SLEEP, "s", 0, 86_400, True),
    RecoveryMetric.BODY_BATTERY_CHARGED: MetricSpec(
        ConsentType.PROVIDER_RECOVERY_METRICS, "level", 0, 100, True
    ),
}


def observation_id(
    athlete_id: UUID, day: date, metric: RecoveryMetric, raw_source_id: UUID
) -> UUID:
    """Deterministic: the same payload stored twice is the same observation (idempotent)."""
    return uuid5(OBSERVATION_NAMESPACE, f"{athlete_id}/{day}/{metric.value}/{raw_source_id}")


@dataclass(frozen=True, slots=True, kw_only=True)
class RecoveryObservation:
    observation_id: UUID
    athlete_id: UUID
    day: date
    metric: RecoveryMetric
    value: float
    provider: str
    raw_source_id: UUID
    source_path: str  # where the value is in the payload, for lineage
    received_at: datetime
    version: int = 0

    def __post_init__(self) -> None:
        spec = METRICS[self.metric]
        if not spec.low <= require_finite(self.value, self.metric.value) <= spec.high:
            raise DomainValidationError(
                f"{self.metric.value} is {spec.low:g} to {spec.high:g} {spec.unit}"
            )
        if not _PROVIDER.fullmatch(self.provider):
            raise DomainValidationError("invalid provider")
        if not _PATH.fullmatch(self.source_path):
            raise DomainValidationError("invalid source path")
        object.__setattr__(self, "received_at", ensure_utc(self.received_at))
        expected = observation_id(self.athlete_id, self.day, self.metric, self.raw_source_id)
        if self.observation_id != expected:
            raise DomainValidationError(
                "an observation's ID derives from its day, metric and source"
            )

    @property
    def entity_id(self) -> UUID:
        return self.observation_id

    @property
    def consent(self) -> ConsentType:
        return METRICS[self.metric].consent

    def with_version(self, version: int) -> Self:
        return replace(self, version=version)


def in_force(
    observations: Iterable[RecoveryObservation], *, allowed: frozenset[ConsentType]
) -> dict[date, dict[RecoveryMetric, RecoveryObservation]]:
    """Per day and metric, the latest observation of a metric whose consent is granted."""
    found: dict[date, dict[RecoveryMetric, RecoveryObservation]] = {}
    ordered = sorted(observations, key=lambda item: (item.received_at, str(item.observation_id)))
    for item in ordered:
        if item.consent in allowed:
            found.setdefault(item.day, {})[item.metric] = item
    return found
