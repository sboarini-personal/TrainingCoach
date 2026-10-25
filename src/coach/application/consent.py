"""Consent management and enforcement (PRIV-CON-001 to PRIV-CON-003).

Every change is persisted with optimistic concurrency, audited and emitted as an event. Only
consent granted under the privacy policy version in force counts.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Final

from coach.application.audit import ActorKind, AuditEvent, AuditOutcome
from coach.application.errors import ApplicationError, NotFoundError
from coach.application.events import EventName
from coach.application.ownership import OwnerScope
from coach.application.ports import AuditLog, Clock, EventSink, IdFactory, OwnedRepository
from coach.domain.consent.ledger import ConsentLedger, ConsentRecord, ConsentType

_GRANTABLE: Final = tuple(ConsentType)


class ConsentRequiredError(ApplicationError):
    def __init__(self, missing: tuple[ConsentType, ...]) -> None:
        super().__init__("consent required: " + ", ".join(item.value for item in missing))
        self.missing = missing


def _load(ledgers: OwnedRepository[ConsentLedger], scope: OwnerScope) -> ConsentLedger | None:
    try:
        return ledgers.get(scope, scope.athlete_id)
    except NotFoundError:
        return None


class ConsentGate:
    def __init__(self, ledgers: OwnedRepository[ConsentLedger], *, policy_version: str) -> None:
        self._ledgers = ledgers
        self._policy_version = policy_version

    def granted(self, scope: OwnerScope) -> frozenset[ConsentType]:
        ledger = _load(self._ledgers, scope)
        if ledger is None:
            return frozenset()
        return frozenset(
            item
            for item in _GRANTABLE
            if ledger.is_granted(item, policy_version=self._policy_version)
        )

    def require(self, scope: OwnerScope, *consent_types: ConsentType) -> None:
        granted = self.granted(scope)
        missing = tuple(item for item in consent_types if item not in granted)
        if missing:
            raise ConsentRequiredError(missing)


class ConsentService:
    def __init__(
        self,
        ledgers: OwnedRepository[ConsentLedger],
        *,
        policy_version: str,
        audit: AuditLog,
        events: EventSink,
        clock: Clock,
        ids: IdFactory,
    ) -> None:
        self._ledgers = ledgers
        self._policy_version = policy_version
        self._audit = audit
        self._events = events
        self._clock = clock
        self._ids = ids

    @property
    def ledgers(self) -> OwnedRepository[ConsentLedger]:
        return self._ledgers

    def ledger(self, scope: OwnerScope) -> ConsentLedger:
        return _load(self._ledgers, scope) or ConsentLedger.new(scope.athlete_id)

    def grant(self, scope: OwnerScope, consent_type: ConsentType, *, purpose: str) -> ConsentLedger:
        return self._change(
            scope,
            lambda ledger, now: ledger.grant(
                consent_id=self._ids(),
                consent_type=consent_type,
                policy_version=self._policy_version,
                purpose=purpose,
                at=now,
            ),
            "consent.granted",
            EventName.CONSENT_GRANTED,
        )

    def revoke(self, scope: OwnerScope, consent_type: ConsentType) -> ConsentLedger:
        return self._change(
            scope,
            lambda ledger, now: ledger.revoke(
                consent_id=self._ids(), consent_type=consent_type, at=now
            ),
            "consent.revoked",
            EventName.CONSENT_REVOKED,
        )

    def decline(
        self, scope: OwnerScope, consent_type: ConsentType, *, purpose: str
    ) -> ConsentLedger:
        return self._change(
            scope,
            lambda ledger, now: ledger.decline(
                consent_id=self._ids(),
                consent_type=consent_type,
                policy_version=self._policy_version,
                purpose=purpose,
                at=now,
            ),
            "consent.declined",
            EventName.CONSENT_DECLINED,
        )

    def _change(
        self,
        scope: OwnerScope,
        apply: Callable[[ConsentLedger, datetime], ConsentLedger],
        action: str,
        event: EventName,
    ) -> ConsentLedger:
        now = self._clock()
        current = _load(self._ledgers, scope)
        base = current or ConsentLedger.new(scope.athlete_id)
        changed = apply(base, now)
        if changed.records == base.records:
            return base
        if current is None:
            stored = self._ledgers.add(scope, changed)
        else:
            stored = self._ledgers.update(scope, changed, expected_version=current.version)
        record: ConsentRecord = stored.records[-1]
        self._audit.append(
            AuditEvent(
                occurred_at=now,
                actor=ActorKind.USER,
                action=action,
                outcome=AuditOutcome.SUCCESS,
                athlete_id=scope.athlete_id,
                target_type=record.consent_type.value,
                target_id=str(record.consent_id),
            )
        )
        self._events.emit(event, {"consent_type": record.consent_type.value})
        return stored
