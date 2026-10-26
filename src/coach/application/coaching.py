"""Coaching state conditions set by the application (FR-ONB-006, ADR-021).

Conditions overlay the lifecycle state. The sync activates INTEGRATION_ERROR on a provider
failure and clears it after a successful sync (onboarding spec 4.2, garmin spec section 5).
Every change is audited with codes only.
"""

from __future__ import annotations

import re
from typing import Final

from coach.application.audit import ActorKind, AuditEvent, AuditOutcome
from coach.application.errors import NotFoundError
from coach.application.events import EventName
from coach.application.ownership import OwnerScope
from coach.application.ports import AuditLog, Clock, EventSink, OwnedRepository
from coach.domain.coaching.state import Actor, CoachingState, CoachingStateChanged, Condition
from coach.domain.common.errors import DomainValidationError

_CODE: Final = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_ACTORS: Final = {Actor.ATHLETE: ActorKind.USER, Actor.SYSTEM: ActorKind.SYSTEM}


class CoachingConditions:
    def __init__(
        self,
        states: OwnedRepository[CoachingState],
        *,
        audit: AuditLog,
        events: EventSink,
        clock: Clock,
    ) -> None:
        self._states = states
        self._audit = audit
        self._events = events
        self._clock = clock

    def current(self, scope: OwnerScope) -> CoachingState:
        try:
            return self._states.get(scope, scope.athlete_id)
        except NotFoundError:
            return CoachingState.new(scope.athlete_id)

    def activate(
        self, scope: OwnerScope, condition: Condition, *, reason: str, actor: Actor = Actor.SYSTEM
    ) -> CoachingState:
        return self._change(scope, condition, reason, actor, active=True)

    def clear(
        self, scope: OwnerScope, condition: Condition, *, reason: str, actor: Actor = Actor.SYSTEM
    ) -> CoachingState:
        return self._change(scope, condition, reason, actor, active=False)

    def _change(
        self, scope: OwnerScope, condition: Condition, reason: str, actor: Actor, *, active: bool
    ) -> CoachingState:
        if not _CODE.match(reason):
            raise DomainValidationError("a condition change gives a reason code")
        if actor not in _ACTORS:
            raise DomainValidationError("the agent does not change conditions through this path")
        stored: CoachingState | None
        try:
            stored = self._states.get(scope, scope.athlete_id)
        except NotFoundError:
            stored = None
        state = stored or CoachingState.new(scope.athlete_id)
        now = self._clock()
        change = state.activate if active else state.clear
        changed, event = change(condition, actor=actor, at=now, reason=reason)
        if event is None:
            return state
        if stored is None:
            saved = self._states.add(scope, changed)
        else:
            saved = self._states.update(scope, changed, expected_version=stored.version)
        self._record(scope, event, actor)
        return saved

    def _record(self, scope: OwnerScope, event: CoachingStateChanged, actor: Actor) -> None:
        condition = event.condition
        if condition is None:  # pragma: no cover - condition changes always name one
            return
        action = "coaching.condition_activated" if event.activated else "coaching.condition_cleared"
        self._audit.append(
            AuditEvent(
                occurred_at=event.at,
                actor=_ACTORS[actor],
                action=action,
                outcome=AuditOutcome.SUCCESS,
                athlete_id=scope.athlete_id,
                target_type="CONDITION",
                target_id=condition.value,
                reason_codes=() if event.reason is None else (event.reason,),
            )
        )
        name = EventName.CONDITION_ACTIVATED if event.activated else EventName.CONDITION_CLEARED
        self._events.emit(
            name, {"condition": condition.value, "reason": event.reason or "UNSPECIFIED"}
        )
