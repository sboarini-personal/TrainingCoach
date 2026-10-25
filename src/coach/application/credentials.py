"""Credentials: secrets in the OS store, metadata in the database (SEC-SEC-001, SEC-SEC-004).

The database never sees a secret or a hash of it. Handles are derived from the credential
kind and the athlete, so no caller can address an arbitrary entry of the secret store.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Final
from uuid import UUID

from coach.application.audit import ActorKind, AuditEvent, AuditOutcome
from coach.application.errors import NotFoundError
from coach.application.events import EventName
from coach.application.ownership import OwnerScope
from coach.application.ports import (
    AuditLog,
    Clock,
    CredentialProvider,
    EventSink,
    IdFactory,
    OwnedRepository,
)
from coach.application.secrets import SecretValue
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.time import ensure_utc

EXPIRY_WARNING: Final = timedelta(days=7)
_LOCAL_TOKEN_BYTES: Final = 32
_HANDLE: Final = re.compile(
    r"^(local_api_token|garmin_personal_api_key|llm_api_key)/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


class CredentialKind(StrEnum):
    """Labels of credential kinds; the values are names, never secrets."""

    LOCAL_API_TOKEN = "LOCAL_API_TOKEN"  # noqa: S105  # pragma: allowlist secret
    GARMIN_PERSONAL_API_KEY = "GARMIN_PERSONAL_API_KEY"  # pragma: allowlist secret
    LLM_API_KEY = "LLM_API_KEY"  # pragma: allowlist secret


def credential_handle(kind: CredentialKind, athlete_id: UUID) -> str:
    return f"{kind.value.lower()}/{athlete_id}"


def check_handle(handle: str) -> str:
    """Reject anything that is not a derived credential handle."""
    if not _HANDLE.match(handle):
        raise DomainValidationError("invalid credential handle")
    return handle


def generate_local_token() -> SecretValue:
    """256 random bits, URL-safe base64 (spec/security/authentication.spec.md 1.1)."""
    return SecretValue(secrets.token_urlsafe(_LOCAL_TOKEN_BYTES))


@dataclass(frozen=True, slots=True, kw_only=True)
class CredentialRecord:
    credential_id: UUID
    athlete_id: UUID
    kind: CredentialKind
    created_at: datetime
    rotated_at: datetime | None = None
    expires_on: date | None = None
    version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))
        if self.rotated_at is not None:
            rotated = ensure_utc(self.rotated_at)
            if rotated < self.created_at:
                raise DomainValidationError("a credential is rotated after it is created")
            object.__setattr__(self, "rotated_at", rotated)

    @property
    def handle(self) -> str:
        return credential_handle(self.kind, self.athlete_id)

    @property
    def entity_id(self) -> UUID:
        return self.credential_id

    def with_version(self, version: int) -> CredentialRecord:
        return replace(self, version=version)


class ExpiryState(StrEnum):
    NO_EXPIRY = "NO_EXPIRY"
    VALID = "VALID"
    EXPIRING = "EXPIRING"
    EXPIRED = "EXPIRED"


def expiry_state(expires_on: date | None, *, today: date) -> ExpiryState:
    if expires_on is None:
        return ExpiryState.NO_EXPIRY
    if expires_on < today:
        return ExpiryState.EXPIRED
    if expires_on - today <= EXPIRY_WARNING:
        return ExpiryState.EXPIRING
    return ExpiryState.VALID


@dataclass(frozen=True, slots=True)
class CredentialStatus:
    kind: CredentialKind
    expires_on: date | None
    state: ExpiryState


class CredentialService:
    def __init__(
        self,
        provider: CredentialProvider,
        records: OwnedRepository[CredentialRecord],
        *,
        audit: AuditLog,
        events: EventSink,
        clock: Clock,
        ids: IdFactory,
    ) -> None:
        self._provider = provider
        self._records = records
        self._audit = audit
        self._events = events
        self._clock = clock
        self._ids = ids

    def store(
        self,
        scope: OwnerScope,
        kind: CredentialKind,
        value: SecretValue,
        *,
        expires_on: date | None = None,
    ) -> CredentialRecord:
        """Store a secret; storing a kind that already exists is a rotation."""
        now = self._clock()
        if expires_on is not None and expires_on < now.date():
            raise DomainValidationError("the expiry date is in the past")
        existing = self._find(scope, kind)
        self._provider.put(credential_handle(kind, scope.athlete_id), value)
        if existing is None:
            record = self._records.add(
                scope,
                CredentialRecord(
                    credential_id=self._ids(),
                    athlete_id=scope.athlete_id,
                    kind=kind,
                    created_at=now,
                    expires_on=expires_on,
                ),
            )
            action, event = "credential.stored", EventName.CREDENTIAL_STORED
        else:
            record = self._records.update(
                scope,
                replace(existing, rotated_at=now, expires_on=expires_on),
                expected_version=existing.version,
            )
            action, event = "credential.rotated", EventName.CREDENTIAL_ROTATED
        self._audit.append(
            AuditEvent(
                occurred_at=now,
                actor=ActorKind.USER,
                action=action,
                outcome=AuditOutcome.SUCCESS,
                athlete_id=scope.athlete_id,
                target_type=kind.value,
                target_id=str(record.credential_id),
            )
        )
        self._events.emit(event, {"kind": kind.value})
        return record

    def rotate_local_token(self, scope: OwnerScope) -> SecretValue:
        token = generate_local_token()
        self.store(scope, CredentialKind.LOCAL_API_TOKEN, token)
        return token

    def secret(self, scope: OwnerScope, kind: CredentialKind) -> SecretValue:
        """The secret itself, for integration adapters only; never for the agent (ADR-001)."""
        if self._find(scope, kind) is None:
            raise NotFoundError
        value = self._provider.get(credential_handle(kind, scope.athlete_id))
        if value is None:
            raise NotFoundError
        return value

    def status(self, scope: OwnerScope) -> list[CredentialStatus]:
        today = self._clock().date()
        records = sorted(self._records.list_all(scope), key=lambda record: record.kind.value)
        return [
            CredentialStatus(
                record.kind, record.expires_on, expiry_state(record.expires_on, today=today)
            )
            for record in records
        ]

    def _find(self, scope: OwnerScope, kind: CredentialKind) -> CredentialRecord | None:
        return next((item for item in self._records.list_all(scope) if item.kind is kind), None)
