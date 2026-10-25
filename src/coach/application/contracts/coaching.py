"""CoachingState <-> coaching-state contract (urn:coach:schema:coaching-state:1)."""

from __future__ import annotations

from uuid import UUID

from coach.application.contracts._json import SCHEMA_VERSION, Document, Json, sorted_values
from coach.domain.coaching.state import CoachingState, Condition, LifecycleState


def coaching_state_to_contract(state: CoachingState) -> Json:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "athleteId": str(state.athlete_id),
        "version": state.version,
        "lifecycle": state.lifecycle.value,
        "conditions": sorted_values(state.conditions),
    }


def coaching_state_from_contract(document: Document) -> CoachingState:
    return CoachingState(
        athlete_id=UUID(document["athleteId"]),
        version=document["version"],
        lifecycle=LifecycleState(document["lifecycle"]),
        conditions=frozenset(Condition(item) for item in document["conditions"]),
    )
