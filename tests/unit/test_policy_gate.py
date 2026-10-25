"""The application enforces policy decisions and records denials (FR-POL-001/002, AT-CON-001)."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from coach.application.consent import ConsentGate, ConsentService
from coach.application.events import EventName
from coach.application.ownership import OwnerScope
from coach.application.policy_gate import (
    ConfirmationRequiredError,
    PolicyDeniedError,
    PolicyGate,
)
from coach.domain.coaching.state import CoachingState, Condition, LifecycleState
from coach.domain.consent.ledger import ConsentLedger, ConsentType
from coach.infrastructure.memory import InMemoryAuditLog, InMemoryRepository, RecordingEventSink
from coach.policy.engine import Action, ActionKind, Outcome
from samples import ATHLETE, OTHER_ATHLETE

SCOPE = OwnerScope(ATHLETE)
NOW = datetime(2026, 10, 25, 10, 0, tzinfo=UTC)
READ_HRV = Action(
    kind=ActionKind.READ, name="recovery.read_hrv", consents=frozenset({ConsentType.HRV})
)


class World:
    def __init__(self) -> None:
        self.ledgers = InMemoryRepository[ConsentLedger]()
        self.states = InMemoryRepository[CoachingState]()
        self.audit, self.events = InMemoryAuditLog(), RecordingEventSink()
        counter = iter(range(1, 100))
        self.consents = ConsentService(
            self.ledgers,
            policy_version="1.0",
            audit=self.audit,
            events=self.events,
            clock=lambda: NOW,
            ids=lambda: UUID(int=next(counter)),
        )
        self.gate = PolicyGate(
            ConsentGate(self.ledgers, policy_version="1.0"),
            self.states,
            audit=self.audit,
            events=self.events,
            clock=lambda: NOW,
        )


@pytest.mark.req("FR-POL-002", "PRIV-CON-002")
@pytest.mark.acceptance("AT-CON-001")
def test_processing_without_consent_is_denied_and_recorded() -> None:
    world = World()
    with pytest.raises(PolicyDeniedError) as denied:
        world.gate.enforce(SCOPE, READ_HRV)
    assert [reason.code for reason in denied.value.decision.reasons] == ["CONSENT_REQUIRED"]
    assert world.events.events[-1] == (
        EventName.AUTHZ_DENIED,
        {"action": "recovery.read_hrv", "policy": "POL-CONSENT"},
    )
    record = world.audit.all_records()[-1].document
    assert (record["action"], record["outcome"], record["reasonCodes"]) == (
        "policy.evaluate",
        "DENIED",
        ["CONSENT_REQUIRED"],
    )


@pytest.mark.req("FR-POL-001", "PRIV-CON-002")
def test_granted_consent_allows_processing() -> None:
    world = World()
    world.consents.grant(SCOPE, ConsentType.HRV, purpose="Use HRV as a recovery signal.")
    assert world.gate.enforce(SCOPE, READ_HRV).outcome is Outcome.ALLOW


@pytest.mark.req("FR-POL-002", "SAFE-MED-003")
def test_stored_safety_hold_blocks_intensive_proposals() -> None:
    world = World()
    world.states.add(
        SCOPE,
        CoachingState(
            athlete_id=ATHLETE,
            lifecycle=LifecycleState.TRAINING,
            conditions=frozenset({Condition.SAFETY_HOLD}),
        ),
    )
    proposal = Action(kind=ActionKind.PROPOSE, name="plan.propose", intensive_training=True)
    with pytest.raises(PolicyDeniedError):
        world.gate.enforce(SCOPE, proposal)


@pytest.mark.req("FR-POL-002", "SEC-AUTHZ-001")
def test_actions_on_another_athletes_resource_are_denied() -> None:
    world = World()
    foreign = Action(kind=ActionKind.READ, name="goal.read", resource_owner=OTHER_ATHLETE)
    with pytest.raises(PolicyDeniedError):
        world.gate.enforce(SCOPE, foreign)


@pytest.mark.req("FR-POL-002")
def test_unconfirmed_external_write_is_not_executed() -> None:
    world = World()
    publish = Action(kind=ActionKind.EXECUTE_EXTERNAL, name="garmin.publish_workout")
    with pytest.raises(ConfirmationRequiredError) as needed:
        world.gate.enforce(SCOPE, publish)
    assert needed.value.decision.outcome is Outcome.REQUIRE_CONFIRMATION
