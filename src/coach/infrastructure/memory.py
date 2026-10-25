"""In-memory owner-scoped repositories (test doubles and reference implementations).

The SQLite adapters must behave exactly like these; the contract tests in tests/contract run
against both.
"""

from __future__ import annotations

import threading
from uuid import UUID

from coach.application.errors import ConcurrencyConflictError, NotFoundError
from coach.application.ownership import OwnerScope, check_owner
from coach.application.ports import OwnedAggregate
from coach.domain.raw.source import RawSource
from coach.metrics.derived import (
    DerivedMetricValue,
    MetricSubject,
    check_new_value,
    check_supersession,
)


class InMemoryRepository[T: OwnedAggregate]:
    def __init__(self) -> None:
        self._items: dict[UUID, T] = {}
        self._lock = threading.Lock()

    def get(self, scope: OwnerScope, entity_id: UUID) -> T:
        with self._lock:
            return self._owned(scope, entity_id)

    def list_all(self, scope: OwnerScope) -> list[T]:
        with self._lock:
            return [item for item in self._items.values() if item.athlete_id == scope.athlete_id]

    def add(self, scope: OwnerScope, entity: T) -> T:
        check_owner(scope, entity.athlete_id)
        with self._lock:
            if entity.entity_id in self._items:
                raise ConcurrencyConflictError("resource already exists")
            stored = entity.with_version(1)
            self._items[stored.entity_id] = stored
            return stored

    def update(self, scope: OwnerScope, entity: T, *, expected_version: int) -> T:
        with self._lock:
            current = self._owned(scope, entity.entity_id)
            check_owner(scope, entity.athlete_id)
            if current.version != expected_version:
                raise ConcurrencyConflictError("resource was modified concurrently")
            stored = entity.with_version(expected_version + 1)
            self._items[stored.entity_id] = stored
            return stored

    def _owned(self, scope: OwnerScope, entity_id: UUID) -> T:
        item = self._items.get(entity_id)
        if item is None or item.athlete_id != scope.athlete_id:
            raise NotFoundError
        return item


class InMemoryRawSourceRepository:
    def __init__(self) -> None:
        self._items: dict[UUID, RawSource] = {}
        self._lock = threading.Lock()

    def add(self, scope: OwnerScope, source: RawSource) -> tuple[RawSource, bool]:
        check_owner(scope, source.athlete_id)
        with self._lock:
            existing = self._by_sha256(scope, source.sha256)
            if existing is not None:
                return existing, False
            if source.raw_source_id in self._items:
                raise ConcurrencyConflictError("raw source id already used")
            self._items[source.raw_source_id] = source
            return source, True

    def get(self, scope: OwnerScope, raw_source_id: UUID) -> RawSource:
        with self._lock:
            item = self._items.get(raw_source_id)
            if item is None or item.athlete_id != scope.athlete_id:
                raise NotFoundError
            return item

    def find_by_sha256(self, scope: OwnerScope, sha256: str) -> RawSource | None:
        with self._lock:
            return self._by_sha256(scope, sha256)

    def list_all(self, scope: OwnerScope) -> list[RawSource]:
        with self._lock:
            return [item for item in self._items.values() if item.athlete_id == scope.athlete_id]

    def _by_sha256(self, scope: OwnerScope, sha256: str) -> RawSource | None:
        return next(
            (
                item
                for item in self._items.values()
                if item.athlete_id == scope.athlete_id and item.sha256 == sha256
            ),
            None,
        )


class InMemoryDerivedValueRepository:
    def __init__(self) -> None:
        self._items: dict[UUID, DerivedMetricValue] = {}
        self._lock = threading.Lock()

    def add(self, scope: OwnerScope, value: DerivedMetricValue) -> DerivedMetricValue:
        check_owner(scope, value.athlete_id)
        check_new_value(value)
        with self._lock:
            self._insert(scope, value)
            return value

    def supersede(
        self, scope: OwnerScope, old_value_id: UUID, new: DerivedMetricValue
    ) -> DerivedMetricValue:
        check_owner(scope, new.athlete_id)
        check_new_value(new)
        with self._lock:
            old = self._owned(scope, old_value_id)
            check_supersession(old, new)
            if not old.is_current:
                raise ConcurrencyConflictError("value was already superseded")
            self._items[old.value_id] = old.superseded(new.value_id)
            self._insert(scope, new)
            return new

    def get(self, scope: OwnerScope, value_id: UUID) -> DerivedMetricValue:
        with self._lock:
            return self._owned(scope, value_id)

    def current(
        self, scope: OwnerScope, metric_id: str, subject: MetricSubject
    ) -> DerivedMetricValue | None:
        with self._lock:
            return self._current(scope, metric_id, subject)

    def history(
        self, scope: OwnerScope, metric_id: str, subject: MetricSubject
    ) -> list[DerivedMetricValue]:
        with self._lock:
            return [
                item
                for item in self._items.values()
                if item.athlete_id == scope.athlete_id
                and (item.metric_id, item.subject) == (metric_id, subject)
            ]

    def _insert(self, scope: OwnerScope, value: DerivedMetricValue) -> None:
        if value.value_id in self._items:
            raise ConcurrencyConflictError("value id already used")
        if self._current(scope, value.metric_id, value.subject) is not None:
            raise ConcurrencyConflictError("a current value already exists")
        self._items[value.value_id] = value

    def _current(
        self, scope: OwnerScope, metric_id: str, subject: MetricSubject
    ) -> DerivedMetricValue | None:
        return next(
            (
                item
                for item in self._items.values()
                if item.athlete_id == scope.athlete_id
                and (item.metric_id, item.subject) == (metric_id, subject)
                and item.is_current
            ),
            None,
        )

    def _owned(self, scope: OwnerScope, value_id: UUID) -> DerivedMetricValue:
        item = self._items.get(value_id)
        if item is None or item.athlete_id != scope.athlete_id:
            raise NotFoundError
        return item
