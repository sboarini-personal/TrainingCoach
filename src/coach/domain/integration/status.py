"""Integration status and sync cursor (INT-GAR-007, INT-GAR-008; provider-capabilities spec 5).

One status per athlete and provider: the capability snapshot, the state of synchronisation
with its failure reason (codes only), freshness (last success), the postponement after a rate
limit and the cursor that lets an interrupted sync resume (garmin spec section 4.1).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.text import clean_text
from coach.domain.common.time import DateRange, ensure_utc
from coach.domain.integration.capabilities import ProviderCapabilities

_PROVIDER: Final = re.compile(r"^[a-z0-9_]+$")
_CODE: Final = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
MAX_PROCESSED: Final = 1000
MAX_PROVIDER_ID_LENGTH: Final = 100


def _provider_id(value: str) -> str:
    cleaned = clean_text(value, max_length=MAX_PROVIDER_ID_LENGTH, field="provider object id")
    if cleaned != value:
        raise DomainValidationError("provider object ids are plain identifiers")
    return value


@dataclass(frozen=True, slots=True)
class SyncCursor:
    """Last completed date and the IDs already processed in the window in progress."""

    completed_through: date | None = None
    window: DateRange | None = None
    processed: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.processed and self.window is None:
            raise DomainValidationError("processed IDs belong to a window in progress")
        if len(set(self.processed)) != len(self.processed):
            raise DomainValidationError("processed IDs are unique")
        if len(self.processed) > MAX_PROCESSED:
            raise DomainValidationError(f"at most {MAX_PROCESSED} processed IDs per window")
        for item in self.processed:
            _provider_id(item)

    def start(self, window: DateRange) -> SyncCursor:
        """Begin ``window``; resuming the same window keeps its processed IDs."""
        if window == self.window:
            return self
        return replace(self, window=window, processed=())

    def processed_id(self, provider_id: str) -> SyncCursor:
        if self.window is None:
            raise DomainValidationError("no window in progress")
        if provider_id in self.processed:
            return self
        return replace(self, processed=(*self.processed, provider_id))

    def complete(self, *, through: date) -> SyncCursor:
        """End the window in progress; ``completed_through`` never moves back."""
        latest = through if self.completed_through is None else max(through, self.completed_through)
        return SyncCursor(completed_through=latest)


class SyncState(StrEnum):
    NEVER_SYNCED = "NEVER_SYNCED"
    SYNCED = "SYNCED"
    PENDING = "PENDING"


class IntegrationReason(StrEnum):
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_AUTH_FAILED = "PROVIDER_AUTH_FAILED"
    PROVIDER_RATE_LIMITED = "PROVIDER_RATE_LIMITED"
    PROVIDER_PROTOCOL_ERROR = "PROVIDER_PROTOCOL_ERROR"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"


def _optional_utc(value: datetime | None) -> datetime | None:
    return None if value is None else ensure_utc(value)


@dataclass(frozen=True, slots=True, kw_only=True)
class IntegrationStatus:
    integration_id: UUID
    athlete_id: UUID
    provider: str
    capabilities: ProviderCapabilities
    sync_state: SyncState = SyncState.NEVER_SYNCED
    reason: IntegrationReason | None = None
    detail: str | None = None
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    not_before: datetime | None = None
    cursor: SyncCursor = field(default_factory=SyncCursor)
    version: int = 1

    def __post_init__(self) -> None:
        if not _PROVIDER.match(self.provider) or self.capabilities.provider != self.provider:
            raise DomainValidationError("the status and its capabilities name the same provider")
        for name in ("last_attempt_at", "last_success_at", "not_before"):
            object.__setattr__(self, name, _optional_utc(getattr(self, name)))
        if self.detail is not None and (self.reason is None or not _CODE.match(self.detail)):
            raise DomainValidationError("a detail is an upper-case code that refines a reason")
        if (self.sync_state is SyncState.PENDING) != (self.reason is not None):
            raise DomainValidationError("pending syncs, and only they, carry a reason")
        if (self.sync_state is SyncState.SYNCED) and self.last_success_at is None:
            raise DomainValidationError("a synced status records its last success")
        if self.sync_state is SyncState.NEVER_SYNCED and self.last_success_at is not None:
            raise DomainValidationError("a status that never synced has no last success")

    @property
    def entity_id(self) -> UUID:
        return self.integration_id

    def with_version(self, version: int) -> IntegrationStatus:
        return replace(self, version=version)

    def postponed(self, now: datetime) -> bool:
        return self.not_before is not None and ensure_utc(now) < self.not_before

    def started(self, at: datetime) -> IntegrationStatus:
        return replace(self, last_attempt_at=at)

    def progressed(self, cursor: SyncCursor) -> IntegrationStatus:
        return replace(self, cursor=cursor)

    def succeeded(self, at: datetime) -> IntegrationStatus:
        return replace(
            self,
            sync_state=SyncState.SYNCED,
            reason=None,
            detail=None,
            not_before=None,
            last_success_at=at,
        )

    def failed(
        self,
        at: datetime,
        reason: IntegrationReason,
        detail: str | None,
        *,
        not_before: datetime | None = None,
    ) -> IntegrationStatus:
        return replace(
            self,
            sync_state=SyncState.PENDING,
            reason=reason,
            detail=detail,
            last_attempt_at=at,
            not_before=not_before,
        )

    def with_capabilities(self, capabilities: ProviderCapabilities) -> IntegrationStatus:
        return replace(self, capabilities=capabilities)
