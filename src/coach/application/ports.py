"""Application ports implemented by infrastructure adapters.

Every repository and store method takes the owner scope first; there is no unscoped access
(ADR-016, enforced by tests/contract/test_owner_scope_guard.py). A resource owned by another
athlete is reported exactly like a missing one.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Protocol, Self
from uuid import UUID

from coach.application.audit import AuditEvent, AuditRecord, AuditVerification
from coach.application.events import EventName
from coach.application.identity import Identity
from coach.application.ownership import OwnerScope
from coach.application.secrets import SecretValue
from coach.domain.raw.source import RawSource
from coach.metrics.derived import DerivedMetricValue, MetricSubject

type Clock = Callable[[], datetime]
type IdFactory = Callable[[], UUID]


class OwnedAggregate(Protocol):
    @property
    def athlete_id(self) -> UUID: ...

    @property
    def entity_id(self) -> UUID: ...

    @property
    def version(self) -> int: ...

    def with_version(self, version: int) -> Self: ...


class OwnedRepository[T: OwnedAggregate](Protocol):
    """Mutable aggregates with optimistic concurrency (NFR-REL-003)."""

    def get(self, scope: OwnerScope, entity_id: UUID) -> T: ...

    def list_all(self, scope: OwnerScope) -> Sequence[T]: ...

    def add(self, scope: OwnerScope, entity: T) -> T: ...

    def update(self, scope: OwnerScope, entity: T, *, expected_version: int) -> T: ...


class RawSourceRepository(Protocol):
    """Immutable RAW metadata; adding the same bytes again returns the existing source."""

    def add(self, scope: OwnerScope, source: RawSource) -> tuple[RawSource, bool]: ...

    def get(self, scope: OwnerScope, raw_source_id: UUID) -> RawSource: ...

    def find_by_sha256(self, scope: OwnerScope, sha256: str) -> RawSource | None: ...

    def list_all(self, scope: OwnerScope) -> Sequence[RawSource]: ...


class DerivedValueRepository(Protocol):
    """Append-only derived values with one current value per metric and subject."""

    def add(self, scope: OwnerScope, value: DerivedMetricValue) -> DerivedMetricValue: ...

    def supersede(
        self, scope: OwnerScope, old_value_id: UUID, new: DerivedMetricValue
    ) -> DerivedMetricValue: ...

    def get(self, scope: OwnerScope, value_id: UUID) -> DerivedMetricValue: ...

    def current(
        self, scope: OwnerScope, metric_id: str, subject: MetricSubject
    ) -> DerivedMetricValue | None: ...

    def history(
        self, scope: OwnerScope, metric_id: str, subject: MetricSubject
    ) -> Sequence[DerivedMetricValue]: ...


class BlobStore(Protocol):
    """Content-addressed bytes, partitioned per athlete; returns and takes SHA-256 hex digests."""

    def put(self, scope: OwnerScope, data: bytes) -> str: ...

    def get(self, scope: OwnerScope, sha256: str) -> bytes: ...

    def exists(self, scope: OwnerScope, sha256: str) -> bool: ...


class CredentialProvider(Protocol):
    """OS or managed secret store, addressed by derived handles only (SEC-SEC-001)."""

    def get(self, handle: str) -> SecretValue | None: ...

    def put(self, handle: str, value: SecretValue) -> None: ...

    def delete(self, handle: str) -> None: ...


class IdentityDirectory(Protocol):
    """User to athlete links; V1 has one installation owner (SEC-AUTHZ-001)."""

    def owner(self) -> Identity | None: ...

    def athlete_for(self, user_id: UUID) -> UUID | None: ...

    def register_owner(self, identity: Identity) -> Identity: ...


class AuditLog(Protocol):
    """Append-only, hash-chained audit log; record reads are owner-scoped (SEC-AUD-001)."""

    def append(self, event: AuditEvent) -> AuditRecord: ...

    def events_for(self, scope: OwnerScope) -> Sequence[AuditRecord]: ...

    def verify(self) -> AuditVerification: ...

    def forget(self, scope: OwnerScope) -> int: ...


class EventSink(Protocol):
    """Structured observability events: codes and identifiers only (NFR-OBS-001)."""

    def emit(self, name: EventName, fields: Mapping[str, str]) -> None: ...
