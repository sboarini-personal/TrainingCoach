"""Authorization chain: authenticated principal -> athlete -> owner scope (SEC-AUTHZ-001)."""

from __future__ import annotations

from coach.application.audit import ActorKind, AuditEvent, AuditOutcome
from coach.application.errors import ApplicationError
from coach.application.events import EventName
from coach.application.identity import Principal
from coach.application.ownership import OwnerScope
from coach.application.ports import AuditLog, Clock, EventSink, IdentityDirectory


class AuthorizationError(ApplicationError):
    def __init__(self) -> None:
        super().__init__("not authorized")


class AuthorizationService:
    def __init__(
        self, identities: IdentityDirectory, *, audit: AuditLog, events: EventSink, clock: Clock
    ) -> None:
        self._identities = identities
        self._audit = audit
        self._events = events
        self._clock = clock

    def scope_for(self, principal: Principal) -> OwnerScope:
        athlete_id = self._identities.athlete_for(principal.user_id)
        if athlete_id is None:
            self._audit.append(
                AuditEvent(
                    occurred_at=self._clock(),
                    actor=ActorKind.USER,
                    action="auth.authorize",
                    outcome=AuditOutcome.DENIED,
                    reason_codes=("NO_ATHLETE_LINK",),
                )
            )
            self._events.emit(EventName.AUTHZ_DENIED, {"reason": "NO_ATHLETE_LINK"})
            raise AuthorizationError
        return OwnerScope(athlete_id)
