"""Applies the policy engine before an action is executed or persisted (FR-POL-002, ADR-017).

The context comes from stored state (consents, coaching conditions, provider capabilities,
and for plan actions the planning context: availability, resources, health constraints,
recovery block), never from the caller, so neither an interface nor the AI can loosen it.
"""

from __future__ import annotations

from collections.abc import Callable

from coach.application.audit import ActorKind, AuditEvent, AuditOutcome
from coach.application.consent import ConsentGate
from coach.application.errors import ApplicationError, NotFoundError
from coach.application.events import EventName
from coach.application.ownership import OwnerScope
from coach.application.ports import AuditLog, Clock, EventSink, OwnedRepository
from coach.domain.coaching.state import CoachingState, Condition
from coach.domain.integration.capabilities import Capability
from coach.domain.integration.status import IntegrationStatus
from coach.policy.engine import Action, Outcome, PolicyContext, PolicyDecision, evaluate
from coach.policy.plans import PlanningContext


class PolicyDeniedError(ApplicationError):
    def __init__(self, decision: PolicyDecision) -> None:
        super().__init__("denied by policy: " + ", ".join(r.code for r in decision.reasons))
        self.decision = decision


class ConfirmationRequiredError(ApplicationError):
    def __init__(self, decision: PolicyDecision) -> None:
        super().__init__("human confirmation required")
        self.decision = decision


class PolicyGate:
    def __init__(
        self,
        consents: ConsentGate,
        states: OwnedRepository[CoachingState],
        *,
        audit: AuditLog,
        events: EventSink,
        clock: Clock,
        integrations: OwnedRepository[IntegrationStatus] | None = None,
        planning: Callable[[OwnerScope], PlanningContext] | None = None,
    ) -> None:
        self._consents = consents
        self._states = states
        self._integrations = integrations
        self._planning = planning
        self._audit = audit
        self._events = events
        self._clock = clock

    def enforce(
        self, scope: OwnerScope, action: Action, *, actor: ActorKind = ActorKind.USER
    ) -> PolicyDecision:
        decision = evaluate(action, self._context(scope, action))
        if decision.outcome is Outcome.DENY:
            self._record_denial(scope, action, decision, actor)
            raise PolicyDeniedError(decision)
        if decision.outcome is Outcome.REQUIRE_CONFIRMATION:
            raise ConfirmationRequiredError(decision)
        return decision

    def _context(self, scope: OwnerScope, action: Action) -> PolicyContext:
        try:
            conditions: frozenset[Condition] = self._states.get(scope, scope.athlete_id).conditions
        except NotFoundError:
            conditions = frozenset()
        return PolicyContext(
            scope.athlete_id,
            self._consents.granted(scope),
            conditions,
            supported_capabilities=self._supported(scope),
            planning=None
            if action.plan is None or self._planning is None
            else self._planning(scope),
        )

    def _supported(self, scope: OwnerScope) -> frozenset[Capability]:
        if self._integrations is None:
            return frozenset()
        statuses = self._integrations.list_all(scope)
        return frozenset().union(*(status.capabilities.supported for status in statuses))

    def _record_denial(
        self, scope: OwnerScope, action: Action, decision: PolicyDecision, actor: ActorKind
    ) -> None:
        codes = tuple(dict.fromkeys(reason.code for reason in decision.reasons))
        self._audit.append(
            AuditEvent(
                occurred_at=self._clock(),
                actor=actor,
                action="policy.evaluate",
                outcome=AuditOutcome.DENIED,
                athlete_id=scope.athlete_id,
                reason_codes=codes,
            )
        )
        for policy in dict.fromkeys(reason.policy for reason in decision.reasons):
            self._events.emit(EventName.AUTHZ_DENIED, {"action": action.name, "policy": policy})
