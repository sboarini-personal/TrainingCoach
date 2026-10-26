"""IntegrationStatus <-> integration-status contract (urn:coach:schema:integration-status:1)."""

from __future__ import annotations

from uuid import UUID

from coach.application.contracts._json import (
    SCHEMA_VERSION,
    Document,
    Json,
    optional_date,
    optional_instant,
    parse_optional_date,
    parse_optional_instant,
)
from coach.application.contracts.raw import date_window, parse_date_window
from coach.domain.integration.capabilities import (
    AccessTier,
    Capability,
    CapabilityBasis,
    CapabilityState,
    CapabilityValue,
    ProviderCapabilities,
)
from coach.domain.integration.status import (
    IntegrationReason,
    IntegrationStatus,
    SyncCursor,
    SyncState,
)


def _state_to_contract(state: CapabilityState) -> Json:
    return {
        "value": state.value.value,
        "basis": state.basis.value,
        "source": state.source,
        "observedAt": optional_instant(state.observed_at),
    }


def _state_from_contract(document: Document) -> CapabilityState:
    return CapabilityState(
        CapabilityValue(document["value"]),
        CapabilityBasis(document["basis"]),
        document["source"],
        observed_at=parse_optional_instant(document["observedAt"]),
    )


def integration_status_to_contract(status: IntegrationStatus) -> Json:
    capabilities = status.capabilities
    return {
        "schemaVersion": SCHEMA_VERSION,
        "integrationId": str(status.integration_id),
        "athleteId": str(status.athlete_id),
        "version": status.version,
        "provider": status.provider,
        "capabilities": {
            "accessTier": capabilities.access_tier.value,
            "states": {
                capability.value: _state_to_contract(state)
                for capability, state in capabilities.states.items()
            },
        },
        "syncState": status.sync_state.value,
        "reason": None if status.reason is None else status.reason.value,
        "detail": status.detail,
        "lastAttemptAt": optional_instant(status.last_attempt_at),
        "lastSuccessAt": optional_instant(status.last_success_at),
        "notBefore": optional_instant(status.not_before),
        "cursor": {
            "completedThrough": optional_date(status.cursor.completed_through),
            "window": date_window(status.cursor.window),
            "processed": list(status.cursor.processed),
        },
    }


def integration_status_from_contract(document: Document) -> IntegrationStatus:
    capabilities = document["capabilities"]
    cursor = document["cursor"]
    reason = document["reason"]
    return IntegrationStatus(
        integration_id=UUID(document["integrationId"]),
        athlete_id=UUID(document["athleteId"]),
        version=document["version"],
        provider=document["provider"],
        capabilities=ProviderCapabilities(
            document["provider"],
            AccessTier(capabilities["accessTier"]),
            {
                Capability(name): _state_from_contract(state)
                for name, state in capabilities["states"].items()
            },
        ),
        sync_state=SyncState(document["syncState"]),
        reason=None if reason is None else IntegrationReason(reason),
        detail=document["detail"],
        last_attempt_at=parse_optional_instant(document["lastAttemptAt"]),
        last_success_at=parse_optional_instant(document["lastSuccessAt"]),
        not_before=parse_optional_instant(document["notBefore"]),
        cursor=SyncCursor(
            completed_through=parse_optional_date(cursor["completedThrough"]),
            window=parse_date_window(cursor["window"]),
            processed=tuple(cursor["processed"]),
        ),
    )
