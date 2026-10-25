from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from coach.domain.common.errors import DomainValidationError
from coach.domain.consent.ledger import ConsentLedger, ConsentStatus, ConsentType

ATHLETE = UUID("0193b2a0-0000-7000-8000-000000000001")
T1 = datetime(2026, 10, 24, 18, 0, tzinfo=UTC)
T2 = T1 + timedelta(days=9)
POLICY = "1.0"


def _id(number: int) -> UUID:
    return UUID(f"0193b2a0-0000-7000-8000-00000000100{number}")


def granted(consent_type: ConsentType = ConsentType.HRV) -> ConsentLedger:
    return ConsentLedger.new(ATHLETE).grant(
        consent_id=_id(1),
        consent_type=consent_type,
        policy_version=POLICY,
        purpose="Use HRV as a recovery signal.",
        at=T1,
    )


@pytest.mark.req("PRIV-CON-001", "PRIV-CON-002")
def test_without_a_record_consent_is_not_granted() -> None:
    ledger = ConsentLedger.new(ATHLETE)
    assert ledger.status(ConsentType.HRV, policy_version=POLICY) is ConsentStatus.NOT_GRANTED
    assert not ledger.is_granted(ConsentType.HRV, policy_version=POLICY)


@pytest.mark.req("PRIV-CON-001", "PRIV-CON-002")
def test_grant_then_revoke() -> None:
    ledger = granted()
    assert ledger.is_granted(ConsentType.HRV, policy_version=POLICY)

    revoked = ledger.revoke(consent_id=_id(2), consent_type=ConsentType.HRV, at=T2)
    assert revoked.status(ConsentType.HRV, policy_version=POLICY) is ConsentStatus.REVOKED
    assert not revoked.is_granted(ConsentType.HRV, policy_version=POLICY)
    first, second = revoked.records
    assert (second.granted, second.granted_at, second.revoked_at) == (False, first.granted_at, T2)


@pytest.mark.req("PRIV-CON-001")
def test_ledger_is_append_only() -> None:
    ledger = granted()
    ledger.revoke(consent_id=_id(2), consent_type=ConsentType.HRV, at=T2)
    assert len(ledger.records) == 1


@pytest.mark.req("PRIV-CON-002")
def test_new_policy_version_requires_new_consent() -> None:
    ledger = granted()
    assert ledger.status(ConsentType.HRV, policy_version="2.0") is ConsentStatus.OUTDATED_POLICY
    assert not ledger.is_granted(ConsentType.HRV, policy_version="2.0")


@pytest.mark.req("PRIV-CON-003")
def test_ai_processing_can_be_declined_before_any_grant() -> None:
    ledger = ConsentLedger.new(ATHLETE).decline(
        consent_id=_id(1),
        consent_type=ConsentType.AI_PROCESSING,
        policy_version=POLICY,
        purpose="Coaching conversation with an external LLM.",
        at=T1,
    )
    assert ledger.status(ConsentType.AI_PROCESSING, policy_version=POLICY) is ConsentStatus.DECLINED
    assert ledger.has_decision(ConsentType.AI_PROCESSING)
    (record,) = ledger.records
    assert (record.granted, record.granted_at, record.revoked_at) == (False, None, T1)


@pytest.mark.req("PRIV-CON-004")
def test_womens_health_cannot_be_granted_in_v1() -> None:
    with pytest.raises(DomainValidationError):
        granted(ConsentType.WOMENS_HEALTH)


@pytest.mark.req("PRIV-CON-001")
def test_revoking_without_a_grant_is_an_error() -> None:
    with pytest.raises(DomainValidationError):
        ConsentLedger.new(ATHLETE).revoke(consent_id=_id(1), consent_type=ConsentType.HRV, at=T1)


@pytest.mark.req("PRIV-CON-001")
def test_regranting_under_the_same_policy_is_a_no_op() -> None:
    ledger = granted()
    again = ledger.grant(
        consent_id=_id(2),
        consent_type=ConsentType.HRV,
        policy_version=POLICY,
        purpose="Use HRV as a recovery signal.",
        at=T2,
    )
    assert again.records == ledger.records


@pytest.mark.req("PRIV-CON-001")
def test_record_timestamps_never_go_backwards() -> None:
    with pytest.raises(DomainValidationError):
        granted().revoke(
            consent_id=_id(2), consent_type=ConsentType.HRV, at=T1 - timedelta(seconds=1)
        )
