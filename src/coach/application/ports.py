"""Application ports implemented by infrastructure adapters.

Every repository and store method takes the owner scope first; there is no unscoped access
(ADR-016, enforced by tests/contract/test_owner_scope_guard.py). A resource owned by another
athlete is reported exactly like a missing one.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Protocol, Self
from uuid import UUID

from coach.application.ownership import OwnerScope
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
