"""Deterministic policy engine, core set 1.1.0 (FR-POL-001; spec/functional/policy.spec.md).

Pure function of an action and a context assembled by the application. Deny overrides
confirmation, which overrides allow. Reasons are produced in a fixed order so equal inputs
always give equal decisions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID

from coach.domain.coaching.state import Condition
from coach.domain.common.errors import DomainValidationError
from coach.domain.consent.ledger import ConsentType
from coach.domain.integration.capabilities import Capability

POLICY_SET_VERSION: Final = "1.1.0"
_ACTION_NAME: Final = re.compile(r"^[a-z]+(\.[a-z_]+)+$")


class ActionKind(StrEnum):
    READ = "READ"
    WRITE_INTERNAL = "WRITE_INTERNAL"
    PROPOSE = "PROPOSE"
    EXECUTE_EXTERNAL = "EXECUTE_EXTERNAL"
    DELETE = "DELETE"


class Outcome(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_CONFIRMATION = "REQUIRE_CONFIRMATION"


@dataclass(frozen=True, slots=True, kw_only=True)
class Action:
    """What is about to happen. ``confirmed`` is set only after a human confirmation is verified."""

    kind: ActionKind
    name: str
    resource_owner: UUID | None = None
    consents: frozenset[ConsentType] = frozenset()
    intensive_training: bool = False
    capabilities: frozenset[Capability] = frozenset()
    confirmed: bool = False

    def __post_init__(self) -> None:
        if not _ACTION_NAME.match(self.name):
            raise DomainValidationError(f"invalid action name: {self.name!r}")


@dataclass(frozen=True, slots=True)
class PolicyContext:
    athlete_id: UUID
    granted_consents: frozenset[ConsentType]
    conditions: frozenset[Condition]
    supported_capabilities: frozenset[Capability] = frozenset()


@dataclass(frozen=True, slots=True)
class PolicyReason:
    policy: str
    code: str
    parameters: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    outcome: Outcome
    reasons: tuple[PolicyReason, ...]
    policy_set_version: str = POLICY_SET_VERSION


def _denials(action: Action, context: PolicyContext) -> list[PolicyReason]:
    reasons: list[PolicyReason] = []
    if action.resource_owner is not None and action.resource_owner != context.athlete_id:
        reasons.append(PolicyReason("POL-OWNERSHIP", "OWNERSHIP_MISMATCH"))
    for consent in sorted(action.consents - context.granted_consents, key=lambda item: item.value):
        reasons.append(
            PolicyReason("POL-CONSENT", "CONSENT_REQUIRED", (("consentType", consent.value),))
        )
    if action.intensive_training and Condition.SAFETY_HOLD in context.conditions:
        reasons.append(PolicyReason("POL-SAFETY-HOLD", "SAFETY_HOLD_ACTIVE"))
    missing = action.capabilities - context.supported_capabilities
    for capability in sorted(missing, key=lambda item: item.value):
        reasons.append(
            PolicyReason(
                "POL-CAPABILITY", "CAPABILITY_UNAVAILABLE", (("capability", capability.value),)
            )
        )
    return reasons


def _confirmations(action: Action) -> list[PolicyReason]:
    if action.confirmed:
        return []
    if action.kind is ActionKind.EXECUTE_EXTERNAL:
        return [PolicyReason("POL-EXTERNAL-WRITE", "CONFIRMATION_REQUIRED")]
    if action.kind is ActionKind.DELETE:
        return [PolicyReason("POL-DESTRUCTIVE", "CONFIRMATION_REQUIRED")]
    return []


def evaluate(action: Action, context: PolicyContext) -> PolicyDecision:
    denials = _denials(action, context)
    if denials:
        return PolicyDecision(Outcome.DENY, tuple(denials))
    confirmations = _confirmations(action)
    if confirmations:
        return PolicyDecision(Outcome.REQUIRE_CONFIRMATION, tuple(confirmations))
    return PolicyDecision(Outcome.ALLOW, ())
