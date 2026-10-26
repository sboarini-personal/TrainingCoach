"""Configured capabilities of the Garmin Personal tier (garmin spec section 2, ADR-012).

Each value cites the documentation it comes from and the date it was checked. Nothing here
is inferred from the provider's name; ``historicalData`` stays UNKNOWN until observed.
"""

from __future__ import annotations

from typing import Final

from coach.domain.integration.capabilities import (
    AccessTier,
    Capability,
    CapabilityBasis,
    CapabilityState,
    CapabilityValue,
    ProviderCapabilities,
)

PROVIDER: Final = "garmin"
CHECKED: Final = "checked 2026-10-26"
OVERVIEW: Final = f"https://developer.garmin.com/gc-developer-program/overview/ ({CHECKED})"
SWAGGER: Final = (
    f"https://developer.garmin.com/gc-developer-program/personal/swagger-docs/ ({CHECKED})"
)
NOT_DOCUMENTED: Final = f"not documented by Garmin ({CHECKED})"

_S, _U, _K = CapabilityValue.SUPPORTED, CapabilityValue.UNSUPPORTED, CapabilityValue.UNKNOWN
_PERSONAL: Final = {
    Capability.ACTIVITY_READ: (_S, OVERVIEW),
    Capability.HEALTH_READ: (_S, OVERVIEW),
    Capability.FIT_DOWNLOAD: (_S, SWAGGER),
    Capability.PULL_SUPPORT: (_S, OVERVIEW),
    Capability.PUSH_SUPPORT: (_U, OVERVIEW),
    Capability.HISTORICAL_DATA: (_K, NOT_DOCUMENTED),
    Capability.TRAINING_PUBLISH: (_U, OVERVIEW),
    Capability.COURSES_PUBLISH: (_U, OVERVIEW),
}


def personal_capabilities() -> ProviderCapabilities:
    return ProviderCapabilities(
        PROVIDER,
        AccessTier.PERSONAL,
        {
            capability: CapabilityState(value, CapabilityBasis.CONFIGURED, source)
            for capability, (value, source) in _PERSONAL.items()
        },
    )
