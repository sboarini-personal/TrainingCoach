"""In-memory owner-scoped repository (test double and reference implementation)."""

from __future__ import annotations

import threading
from uuid import UUID

from coach.application.errors import ConcurrencyConflictError, NotFoundError, OwnershipError
from coach.application.ownership import OwnerScope
from coach.application.ports import OwnedAggregate


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
        _check_owner(scope, entity)
        with self._lock:
            if entity.entity_id in self._items:
                raise ConcurrencyConflictError("resource already exists")
            stored = entity.with_version(1)
            self._items[stored.entity_id] = stored
            return stored

    def update(self, scope: OwnerScope, entity: T, *, expected_version: int) -> T:
        with self._lock:
            current = self._owned(scope, entity.entity_id)
            _check_owner(scope, entity)
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


def _check_owner(scope: OwnerScope, entity: OwnedAggregate) -> None:
    if entity.athlete_id != scope.athlete_id:
        raise OwnershipError("entity owner does not match the owner scope")
