"""Conditions set by the application, audited with codes (FR-ONB-006, INT-GAR-008)."""

from datetime import UTC, datetime

import pytest

from coach.application.coaching import CoachingConditions
from coach.application.events import EventName
from coach.application.ownership import OwnerScope
from coach.domain.coaching.state import Actor, CoachingState, Condition, LifecycleState
from coach.domain.common.errors import DomainValidationError
from coach.infrastructure.memory import InMemoryAuditLog, InMemoryRepository, RecordingEventSink
from samples import ATHLETE

SCOPE = OwnerScope(ATHLETE)
NOW = datetime(2026, 10, 26, 20, 0, tzinfo=UTC)


def service() -> tuple[
    CoachingConditions, InMemoryRepository[CoachingState], InMemoryAuditLog, RecordingEventSink
]:
    states = InMemoryRepository[CoachingState]()
    audit, events = InMemoryAuditLog(), RecordingEventSink()
    return (
        CoachingConditions(states, audit=audit, events=events, clock=lambda: NOW),
        states,
        audit,
        events,
    )


@pytest.mark.req("FR-ONB-006", "INT-GAR-008")
def test_integration_error_is_activated_and_cleared_with_audit() -> None:
    conditions, states, audit, events = service()
    assert conditions.current(SCOPE) == CoachingState.new(ATHLETE)
    active = conditions.activate(SCOPE, Condition.INTEGRATION_ERROR, reason="PROVIDER_UNAVAILABLE")
    assert active.conditions == frozenset({Condition.INTEGRATION_ERROR})
    assert active.lifecycle is LifecycleState.NEW_USER
    assert (
        conditions.activate(SCOPE, Condition.INTEGRATION_ERROR, reason="PROVIDER_UNAVAILABLE")
        == active
    )
    cleared = conditions.clear(SCOPE, Condition.INTEGRATION_ERROR, reason="SYNC_SUCCEEDED")
    assert cleared.conditions == frozenset()
    assert states.get(SCOPE, ATHLETE).version == 2
    documents = [record.document for record in audit.events_for(SCOPE)]
    assert [(item["action"], item["actor"], item["reasonCodes"]) for item in documents] == [
        ("coaching.condition_activated", "SYSTEM", ["PROVIDER_UNAVAILABLE"]),
        ("coaching.condition_cleared", "SYSTEM", ["SYNC_SUCCEEDED"]),
    ]
    assert events.names() == [EventName.CONDITION_ACTIVATED, EventName.CONDITION_CLEARED]


@pytest.mark.req("FR-ONB-006")
def test_clearing_an_inactive_condition_changes_nothing() -> None:
    conditions, states, audit, _ = service()
    conditions.clear(SCOPE, Condition.INTEGRATION_ERROR, reason="SYNC_SUCCEEDED")
    assert states.list_all(SCOPE) == []
    assert audit.events_for(SCOPE) == []


@pytest.mark.req("FR-ONB-006", "SAFE-MED-005")
def test_reasons_are_codes_and_safety_hold_stays_with_the_athlete() -> None:
    conditions, _, _, _ = service()
    with pytest.raises(DomainValidationError):
        conditions.activate(SCOPE, Condition.INTEGRATION_ERROR, reason="garmin is down")
    with pytest.raises(DomainValidationError):
        conditions.activate(SCOPE, Condition.SAFETY_HOLD, reason="CHECK_IN", actor=Actor.AGENT)
    conditions.activate(SCOPE, Condition.SAFETY_HOLD, reason="CHECK_IN")
    with pytest.raises(Exception, match="athlete"):
        conditions.clear(SCOPE, Condition.SAFETY_HOLD, reason="SYNC_SUCCEEDED")
