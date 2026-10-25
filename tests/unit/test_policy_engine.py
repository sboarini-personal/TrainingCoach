"""Policy engine core: deterministic and deny-overrides (FR-POL-001, policy.spec.md)."""

from uuid import UUID

import pytest

from coach.domain.coaching.state import Condition
from coach.domain.common.errors import DomainValidationError
from coach.domain.consent.ledger import ConsentType
from coach.policy.engine import (
    POLICY_SET_VERSION,
    Action,
    ActionKind,
    Outcome,
    PolicyContext,
    PolicyReason,
    evaluate,
)
from samples import ATHLETE, OTHER_ATHLETE

ALL_CONSENTS = frozenset({ConsentType.ACTIVITIES, ConsentType.HRV, ConsentType.AI_PROCESSING})
CONTEXT = PolicyContext(athlete_id=ATHLETE, granted_consents=ALL_CONSENTS, conditions=frozenset())


def action(**overrides: object) -> Action:
    values: dict[str, object] = {"kind": ActionKind.READ, "name": "recovery.read_hrv"}
    values.update(overrides)
    return Action(**values)  # type: ignore[arg-type]


@pytest.mark.req("FR-POL-001")
def test_compliant_action_is_allowed_with_version() -> None:
    decision = evaluate(
        action(resource_owner=ATHLETE, consents=frozenset({ConsentType.HRV})), CONTEXT
    )
    assert (decision.outcome, decision.reasons) == (Outcome.ALLOW, ())
    assert decision.policy_set_version == POLICY_SET_VERSION == "1.0.0"


@pytest.mark.req("FR-POL-001", "SEC-AUTHZ-001")
def test_foreign_resource_is_denied() -> None:
    decision = evaluate(action(resource_owner=OTHER_ATHLETE), CONTEXT)
    assert decision.outcome is Outcome.DENY
    assert decision.reasons == (PolicyReason("POL-OWNERSHIP", "OWNERSHIP_MISMATCH"),)


@pytest.mark.req("FR-POL-001", "PRIV-CON-002")
def test_each_missing_consent_is_a_reason() -> None:
    context = PolicyContext(
        athlete_id=ATHLETE, granted_consents=frozenset(), conditions=frozenset()
    )
    decision = evaluate(action(consents=frozenset({ConsentType.SLEEP, ConsentType.HRV})), context)
    assert decision.outcome is Outcome.DENY
    assert decision.reasons == (
        PolicyReason("POL-CONSENT", "CONSENT_REQUIRED", (("consentType", "HRV"),)),
        PolicyReason("POL-CONSENT", "CONSENT_REQUIRED", (("consentType", "SLEEP"),)),
    )


@pytest.mark.req("FR-POL-001", "SAFE-MED-003")
def test_safety_hold_denies_intensive_training_only() -> None:
    held = PolicyContext(ATHLETE, ALL_CONSENTS, frozenset({Condition.SAFETY_HOLD}))
    intensive = action(kind=ActionKind.PROPOSE, name="plan.propose", intensive_training=True)
    assert evaluate(intensive, held).reasons == (
        PolicyReason("POL-SAFETY-HOLD", "SAFETY_HOLD_ACTIVE"),
    )
    assert evaluate(action(), held).outcome is Outcome.ALLOW


@pytest.mark.req("FR-POL-001", "FR-POL-002")
@pytest.mark.parametrize(
    ("kind", "policy"),
    [(ActionKind.EXECUTE_EXTERNAL, "POL-EXTERNAL-WRITE"), (ActionKind.DELETE, "POL-DESTRUCTIVE")],
)
def test_external_writes_and_deletions_need_confirmation(kind: ActionKind, policy: str) -> None:
    unconfirmed = evaluate(action(kind=kind, name="garmin.publish_workout"), CONTEXT)
    assert unconfirmed.outcome is Outcome.REQUIRE_CONFIRMATION
    assert unconfirmed.reasons == (PolicyReason(policy, "CONFIRMATION_REQUIRED"),)
    confirmed = evaluate(action(kind=kind, name="garmin.publish_workout", confirmed=True), CONTEXT)
    assert confirmed.outcome is Outcome.ALLOW


@pytest.mark.req("FR-POL-001")
def test_deny_overrides_confirmation() -> None:
    decision = evaluate(
        action(kind=ActionKind.DELETE, name="athlete.delete_data", resource_owner=OTHER_ATHLETE),
        CONTEXT,
    )
    assert decision.outcome is Outcome.DENY
    assert [reason.policy for reason in decision.reasons] == ["POL-OWNERSHIP"]


@pytest.mark.req("FR-POL-001")
def test_evaluation_is_deterministic() -> None:
    context = PolicyContext(ATHLETE, frozenset(), frozenset({Condition.SAFETY_HOLD}))
    request = action(
        kind=ActionKind.PROPOSE,
        name="plan.propose",
        resource_owner=OTHER_ATHLETE,
        consents=frozenset(ConsentType) - {ConsentType.WOMENS_HEALTH},
        intensive_training=True,
    )
    assert evaluate(request, context) == evaluate(request, context)


def test_action_names_are_codes() -> None:
    with pytest.raises(DomainValidationError):
        action(name="Read HRV please")
    assert action(resource_owner=UUID(int=1)).resource_owner == UUID(int=1)
