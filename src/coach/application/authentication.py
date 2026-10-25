"""Authentication with the local API token, plus failed-attempt lockout (SEC-AUTH-001/003).

Failures look identical to the caller whatever the reason. While a client is locked out its
credentials are not even checked.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import re
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final

from coach.application.audit import ActorKind, AuditEvent, AuditOutcome
from coach.application.credentials import CredentialKind, credential_handle
from coach.application.errors import ApplicationError, RateLimitedError
from coach.application.events import EventName
from coach.application.identity import Principal
from coach.application.ports import (
    AuditLog,
    Clock,
    CredentialProvider,
    EventSink,
    IdentityDirectory,
)
from coach.application.secrets import SecretValue

__all__ = [
    "AuthenticationError",
    "AuthenticationService",
    "FailedAuthLockout",
    "LocalTokenAuthenticator",
    "RateLimitedError",
]

_CLIENT_ID: Final = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


class AuthenticationError(ApplicationError):
    def __init__(self) -> None:
        super().__init__("authentication failed")


def _digest(value: SecretValue) -> bytes:
    return hashlib.sha256(value.reveal().encode("utf-8")).digest()


class LocalTokenAuthenticator:
    """Checks a presented token against the owner's token in the secret store, every time."""

    def __init__(self, credentials: CredentialProvider, identities: IdentityDirectory) -> None:
        self._credentials = credentials
        self._identities = identities

    def verify(self, presented: SecretValue) -> Principal | None:
        owner = self._identities.owner()
        if owner is None:
            return None
        expected = self._credentials.get(
            credential_handle(CredentialKind.LOCAL_API_TOKEN, owner.athlete_id)
        )
        if expected is None:
            return None
        if hmac.compare_digest(_digest(presented), _digest(expected)):
            return Principal(owner.user_id)
        return None


@dataclass
class _ClientState:
    failures: deque[datetime] = field(default_factory=deque)
    level: int = 0
    locked_until: datetime | None = None


class FailedAuthLockout:
    """N failures within a window lock the client; each further lockout doubles, capped."""

    def __init__(
        self,
        *,
        max_failures: int = 5,
        window: timedelta = timedelta(seconds=60),
        base: timedelta = timedelta(seconds=60),
        maximum: timedelta = timedelta(hours=1),
    ) -> None:
        self._max_failures = max_failures
        self._window = window
        self._base = base
        self._maximum = maximum
        self._states: dict[str, _ClientState] = {}
        self._lock = threading.Lock()

    def locked_until(self, client: str, now: datetime) -> datetime | None:
        with self._lock:
            state = self._states.get(client)
            if state is None or state.locked_until is None or now >= state.locked_until:
                return None
            return state.locked_until

    def record_failure(self, client: str, now: datetime) -> datetime | None:
        """Count a failure; return the end of the lockout it triggers, if any."""
        with self._lock:
            state = self._states.setdefault(client, _ClientState())
            if state.locked_until is not None and now < state.locked_until:
                return state.locked_until
            cutoff = now - self._window
            while state.failures and state.failures[0] <= cutoff:
                state.failures.popleft()
            state.failures.append(now)
            if len(state.failures) < self._max_failures:
                return None
            duration = min(self._base * 2**state.level, self._maximum)
            state.level += 1
            state.failures.clear()
            state.locked_until = now + duration
            return state.locked_until

    def record_success(self, client: str) -> None:
        with self._lock:
            self._states.pop(client, None)


class AuthenticationService:
    def __init__(
        self,
        authenticator: LocalTokenAuthenticator,
        lockout: FailedAuthLockout,
        *,
        audit: AuditLog,
        events: EventSink,
        clock: Clock,
    ) -> None:
        self._authenticator = authenticator
        self._lockout = lockout
        self._audit = audit
        self._events = events
        self._clock = clock

    def authenticate(self, client_id: str, presented: SecretValue | None) -> Principal:
        if not _CLIENT_ID.match(client_id):
            raise ValueError("invalid client identifier")
        now = self._clock()
        locked_until = self._lockout.locked_until(client_id, now)
        if locked_until is not None:
            self._events.emit(
                EventName.RATE_LIMITED, {"client": client_id, "reason": "AUTH_LOCKOUT"}
            )
            raise RateLimitedError(math.ceil((locked_until - now).total_seconds()))
        principal = None if presented is None else self._authenticator.verify(presented)
        if principal is None:
            self._lockout.record_failure(client_id, now)
            self._audit.append(
                AuditEvent(
                    occurred_at=now,
                    actor=ActorKind.USER,
                    action="auth.authenticate",
                    outcome=AuditOutcome.FAILED,
                    reason_codes=("INVALID_CREDENTIALS",),
                )
            )
            self._events.emit(EventName.AUTHN_FAILED, {"client": client_id})
            raise AuthenticationError
        self._lockout.record_success(client_id)
        return principal
