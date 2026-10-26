"""Workout publication port and the V1 local publisher (INT-GAR-002; AT-GAR-001).

Proposing is local and has no external effect. Publishing is an external write: the policy
engine decides first, and a capability that is not SUPPORTED denies it before confirmation is
considered (provider-capabilities spec section 2). The V1 ``LocalWorkoutPublisher`` holds no
HTTP client at all: every publication is refused with CAPABILITY_UNAVAILABLE and the workout
stays local.

Until the planning model exists (P12) a proposal holds the workout as its contract document
(``urn:coach:schema:workout:1``).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final, Protocol
from uuid import UUID, uuid5

from coach.application.audit import ActorKind, AuditEvent, AuditOutcome
from coach.application.errors import NotFoundError
from coach.application.events import EventName
from coach.application.ownership import OwnerScope
from coach.application.policy_gate import (
    ConfirmationRequiredError,
    PolicyDeniedError,
    PolicyGate,
)
from coach.application.ports import AuditLog, Clock, EventSink
from coach.domain.common.errors import DomainValidationError
from coach.domain.integration.capabilities import Capability
from coach.policy.engine import Action, ActionKind

_PROPOSALS: Final = UUID("0193b2a0-6b1c-7d2e-8f30-4a5b6c7d8e9f")
WORKOUT_TYPES: Final = frozenset(
    {"ENDURANCE", "STRENGTH", "TENNIS", "RECOVERY", "REST", "EVALUATION"}
)
PUBLISH_ACTION: Final = "workout.publish"


class PublishStatus(StrEnum):
    PUBLISHED = "PUBLISHED"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"


@dataclass(frozen=True, slots=True)
class ConfirmationToken:
    """Issued only to a human action, bound to one proposal (policy spec section 4)."""

    proposal_id: UUID
    token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class WorkoutProposal:
    proposal_id: UUID
    athlete_id: UUID
    workout_id: UUID
    sha256: str
    proposed_at: datetime
    document: Mapping[str, Any] = field(repr=False)


@dataclass(frozen=True, slots=True)
class PublishResult:
    proposal_id: UUID
    status: PublishStatus
    reason_codes: tuple[str, ...] = ()


class WorkoutPublisher(Protocol):
    def propose_workout(self, scope: OwnerScope, workout: Mapping[str, Any]) -> WorkoutProposal: ...

    def publish_workout(
        self,
        scope: OwnerScope,
        proposal_id: UUID,
        confirmation: ConfirmationToken | None = None,
    ) -> PublishResult: ...


def _canonical(document: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError) as error:
        raise DomainValidationError("a workout is a JSON document") from error


def _workout_id(document: Mapping[str, Any]) -> UUID:
    if not isinstance(document.get("schemaVersion"), str):
        raise DomainValidationError("a workout document states its schemaVersion")
    if document.get("type") not in WORKOUT_TYPES:
        raise DomainValidationError("a workout document has a known type")
    try:
        return UUID(str(document.get("id")))
    except ValueError as error:
        raise DomainValidationError("a workout document has an id") from error


class LocalWorkoutPublisher:
    """Keeps workouts local; publication is always CAPABILITY_UNAVAILABLE (AT-GAR-001)."""

    def __init__(
        self, gate: PolicyGate, *, audit: AuditLog, events: EventSink, clock: Clock
    ) -> None:
        self._gate = gate
        self._audit = audit
        self._events = events
        self._clock = clock
        self._proposals: dict[UUID, WorkoutProposal] = {}

    def propose_workout(self, scope: OwnerScope, workout: Mapping[str, Any]) -> WorkoutProposal:
        workout_id = _workout_id(workout)
        data = _canonical(workout)
        sha256 = hashlib.sha256(data).hexdigest()
        proposal_id = uuid5(_PROPOSALS, f"{scope.athlete_id}:{workout_id}:{sha256}")
        existing = self._proposals.get(proposal_id)
        if existing is not None:
            return existing
        proposal = WorkoutProposal(
            proposal_id=proposal_id,
            athlete_id=scope.athlete_id,
            workout_id=workout_id,
            sha256=sha256,
            proposed_at=self._clock(),
            document=MappingProxyType(json.loads(data)),
        )
        self._proposals[proposal_id] = proposal
        self._events.emit(EventName.EXTERNAL_WRITE_PROPOSED, {"action": PUBLISH_ACTION})
        return proposal

    def publish_workout(
        self,
        scope: OwnerScope,
        proposal_id: UUID,
        confirmation: ConfirmationToken | None = None,
    ) -> PublishResult:
        proposal = self._proposals.get(proposal_id)
        if proposal is None or proposal.athlete_id != scope.athlete_id:
            raise NotFoundError
        action = Action(
            kind=ActionKind.EXECUTE_EXTERNAL,
            name=PUBLISH_ACTION,
            resource_owner=proposal.athlete_id,
            capabilities=frozenset({Capability.TRAINING_PUBLISH}),
        )
        try:
            self._gate.enforce(scope, action)
            codes: tuple[str, ...] = ("CAPABILITY_UNAVAILABLE",)
        except PolicyDeniedError as denied:
            codes = tuple(dict.fromkeys(reason.code for reason in denied.decision.reasons))
        except ConfirmationRequiredError:
            # A tier with trainingPublish would still need a publisher with a transport.
            codes = ("CAPABILITY_UNAVAILABLE",)
        self._audit.append(
            AuditEvent(
                occurred_at=self._clock(),
                actor=ActorKind.USER,
                action=PUBLISH_ACTION,
                outcome=AuditOutcome.DENIED,
                athlete_id=scope.athlete_id,
                target_type="WORKOUT_PROPOSAL",
                target_id=str(proposal_id),
                reason_codes=codes,
            )
        )
        self._events.emit(
            EventName.EXTERNAL_WRITE_REFUSED,
            {"action": PUBLISH_ACTION, "reason": "CAPABILITY_UNAVAILABLE"},
        )
        return PublishResult(proposal_id, PublishStatus.CAPABILITY_UNAVAILABLE, codes)
