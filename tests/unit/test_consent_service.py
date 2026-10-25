"""Consent changes are persisted, audited and enforced (PRIV-CON-001..003, AT-CON-001)."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from coach.application.consent import ConsentGate, ConsentRequiredError, ConsentService
from coach.application.events import EventName
from coach.application.ownership import OwnerScope
from coach.domain.common.errors import DomainValidationError
from coach.domain.consent.ledger import ConsentLedger, ConsentStatus, ConsentType
from coach.infrastructure.memory import InMemoryAuditLog, InMemoryRepository, RecordingEventSink
from samples import ATHLETE

SCOPE = OwnerScope(ATHLETE)
NOW = datetime(2026, 10, 25, 10, 0, tzinfo=UTC)
PURPOSE = "Use heart-rate variability as a recovery signal."


@pytest.fixture
def setup() -> tuple[ConsentService, ConsentGate, InMemoryAuditLog, RecordingEventSink]:
    ledgers = InMemoryRepository[ConsentLedger]()
    audit, events = InMemoryAuditLog(), RecordingEventSink()
    counter = iter(range(1, 100))
    service = ConsentService(
        ledgers,
        policy_version="1.0",
        audit=audit,
        events=events,
        clock=lambda: NOW,
        ids=lambda: UUID(int=next(counter)),
    )
    return service, ConsentGate(ledgers, policy_version="1.0"), audit, events


Setup = tuple[ConsentService, ConsentGate, InMemoryAuditLog, RecordingEventSink]


@pytest.mark.req("PRIV-CON-001", "PRIV-CON-002")
@pytest.mark.acceptance("AT-CON-001")
def test_grant_and_revoke_are_persisted_audited_and_enforced(setup: Setup) -> None:
    service, gate, audit, events = setup
    assert gate.granted(SCOPE) == frozenset()
    service.grant(SCOPE, ConsentType.HRV, purpose=PURPOSE)
    assert gate.granted(SCOPE) == {ConsentType.HRV}
    gate.require(SCOPE, ConsentType.HRV)
    service.revoke(SCOPE, ConsentType.HRV)
    assert gate.granted(SCOPE) == frozenset()
    with pytest.raises(ConsentRequiredError) as missing:
        gate.require(SCOPE, ConsentType.HRV, ConsentType.SLEEP)
    assert missing.value.missing == (ConsentType.HRV, ConsentType.SLEEP)
    assert events.names() == [EventName.CONSENT_GRANTED, EventName.CONSENT_REVOKED]
    actions = [(r.document["action"], r.document["target"]["type"]) for r in audit.all_records()]
    assert actions == [("consent.granted", "HRV"), ("consent.revoked", "HRV")]


@pytest.mark.req("PRIV-CON-003")
@pytest.mark.acceptance("AT-CON-001")
def test_ai_processing_can_be_declined_and_blocks_processing(setup: Setup) -> None:
    service, gate, audit, events = setup
    ledger = service.decline(SCOPE, ConsentType.AI_PROCESSING, purpose="Coaching with an LLM.")
    assert ledger.status(ConsentType.AI_PROCESSING, policy_version="1.0") is ConsentStatus.DECLINED
    with pytest.raises(ConsentRequiredError):
        gate.require(SCOPE, ConsentType.AI_PROCESSING)
    assert events.names() == [EventName.CONSENT_DECLINED]
    assert audit.all_records()[0].document["action"] == "consent.declined"


@pytest.mark.req("PRIV-CON-001")
def test_regranting_records_and_audits_nothing(setup: Setup) -> None:
    service, _, audit, events = setup
    first = service.grant(SCOPE, ConsentType.HRV, purpose=PURPOSE)
    again = service.grant(SCOPE, ConsentType.HRV, purpose=PURPOSE)
    assert again.records == first.records
    assert len(audit.all_records()) == len(events.events) == 1


@pytest.mark.req("PRIV-CON-002")
def test_consent_under_another_policy_version_does_not_count(setup: Setup) -> None:
    service, _, _, _ = setup
    service.grant(SCOPE, ConsentType.HRV, purpose=PURPOSE)
    newer_policy = ConsentGate(service.ledgers, policy_version="2.0")
    assert newer_policy.granted(SCOPE) == frozenset()


@pytest.mark.req("PRIV-CON-004")
def test_womens_health_still_cannot_be_granted(setup: Setup) -> None:
    with pytest.raises(DomainValidationError):
        setup[0].grant(SCOPE, ConsentType.WOMENS_HEALTH, purpose="Not offered in V1.")
