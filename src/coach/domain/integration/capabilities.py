"""Provider capabilities (INT-GAR-003, INT-GAR-004; provider-capabilities spec sections 3, 4).

Every capability has a value and a basis: CONFIGURED from documentation (with its source) or
OBSERVED at runtime (with its time). Nothing is inferred from a provider's name. A feature is
available only when every capability it needs is SUPPORTED, so UNKNOWN never enables it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.text import clean_text
from coach.domain.common.time import ensure_utc

_PROVIDER: Final = re.compile(r"^[a-z0-9_]+$")
MAX_SOURCE_LENGTH: Final = 300


class Capability(StrEnum):
    ACTIVITY_READ = "activityRead"
    HEALTH_READ = "healthRead"
    FIT_DOWNLOAD = "fitDownload"
    PULL_SUPPORT = "pullSupport"
    PUSH_SUPPORT = "pushSupport"
    HISTORICAL_DATA = "historicalData"
    TRAINING_PUBLISH = "trainingPublish"
    COURSES_PUBLISH = "coursesPublish"


class CapabilityValue(StrEnum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN = "UNKNOWN"


class CapabilityBasis(StrEnum):
    CONFIGURED = "CONFIGURED"
    OBSERVED = "OBSERVED"


class AccessTier(StrEnum):
    PERSONAL = "PERSONAL"
    STARTER_BUSINESS = "STARTER_BUSINESS"
    ENTERPRISE = "ENTERPRISE"
    UNKNOWN = "UNKNOWN"


class Feature(StrEnum):
    ACTIVITY_SYNC = "ACTIVITY_SYNC"
    RECOVERY_SYNC = "RECOVERY_SYNC"
    FIT_ENRICHMENT = "FIT_ENRICHMENT"
    HISTORICAL_BACKFILL = "HISTORICAL_BACKFILL"
    WEBHOOK = "WEBHOOK"
    WORKOUT_PUBLICATION = "WORKOUT_PUBLICATION"
    COURSE_PUBLICATION = "COURSE_PUBLICATION"


_C = Capability
FEATURE_REQUIREMENTS: Final[Mapping[Feature, frozenset[Capability]]] = MappingProxyType(
    {
        Feature.ACTIVITY_SYNC: frozenset({_C.ACTIVITY_READ, _C.PULL_SUPPORT}),
        Feature.RECOVERY_SYNC: frozenset({_C.HEALTH_READ}),
        Feature.FIT_ENRICHMENT: frozenset({_C.FIT_DOWNLOAD}),
        Feature.HISTORICAL_BACKFILL: frozenset({_C.HISTORICAL_DATA}),
        Feature.WEBHOOK: frozenset({_C.PUSH_SUPPORT}),
        Feature.WORKOUT_PUBLICATION: frozenset({_C.TRAINING_PUBLISH}),
        Feature.COURSE_PUBLICATION: frozenset({_C.COURSES_PUBLISH}),
    }
)


@dataclass(frozen=True, slots=True)
class CapabilityState:
    value: CapabilityValue
    basis: CapabilityBasis
    source: str
    observed_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source", clean_text(self.source, max_length=MAX_SOURCE_LENGTH, field="source")
        )
        observed = self.basis is CapabilityBasis.OBSERVED
        if observed != (self.observed_at is not None):
            raise DomainValidationError(
                "observed capabilities, and only they, have an observed time"
            )
        if self.observed_at is not None:
            object.__setattr__(self, "observed_at", ensure_utc(self.observed_at))
        if observed and self.value is CapabilityValue.UNKNOWN:
            raise DomainValidationError("an observation is never UNKNOWN")


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    provider: str
    access_tier: AccessTier
    states: Mapping[Capability, CapabilityState]

    def __post_init__(self) -> None:
        if not _PROVIDER.match(self.provider):
            raise DomainValidationError(f"invalid provider: {self.provider!r}")
        if set(self.states) != set(Capability):
            raise DomainValidationError("capabilities state every capability")
        ordered = {capability: self.states[capability] for capability in Capability}
        object.__setattr__(self, "states", MappingProxyType(ordered))

    def state(self, capability: Capability) -> CapabilityState:
        return self.states[capability]

    def value(self, capability: Capability) -> CapabilityValue:
        return self.states[capability].value

    def supports(self, capability: Capability) -> bool:
        return self.value(capability) is CapabilityValue.SUPPORTED

    @property
    def supported(self) -> frozenset[Capability]:
        return frozenset(capability for capability in Capability if self.supports(capability))

    def missing_for(self, feature: Feature) -> frozenset[Capability]:
        """Capabilities the feature needs that are not SUPPORTED (UNKNOWN included)."""
        return frozenset(c for c in FEATURE_REQUIREMENTS[feature] if not self.supports(c))

    def available(self, feature: Feature) -> bool:
        return not self.missing_for(feature)

    def observe(
        self, capability: Capability, value: CapabilityValue, *, at: datetime, source: str
    ) -> ProviderCapabilities:
        observed = CapabilityState(value, CapabilityBasis.OBSERVED, source, observed_at=at)
        return replace(self, states={**self.states, capability: observed})

    def reconfigured(self, configured: ProviderCapabilities) -> ProviderCapabilities:
        """The adapter's current configuration, keeping every observation (spec section 5)."""
        if configured.provider != self.provider:
            raise DomainValidationError("capabilities of another provider")
        states = {
            capability: (
                current
                if current.basis is CapabilityBasis.OBSERVED
                else configured.state(capability)
            )
            for capability, current in self.states.items()
        }
        return replace(configured, states=states)
