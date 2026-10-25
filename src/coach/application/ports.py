"""Application ports implemented by infrastructure adapters."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, Self
from uuid import UUID

from coach.application.ownership import OwnerScope


class OwnedAggregate(Protocol):
    @property
    def athlete_id(self) -> UUID: ...

    @property
    def entity_id(self) -> UUID: ...

    @property
    def version(self) -> int: ...

    def with_version(self, version: int) -> Self: ...


class OwnedRepository[T: OwnedAggregate](Protocol):
    """Every method takes the owner scope; there is no unscoped access (ADR-016).

    A resource owned by another athlete is reported exactly like a missing one.
    Updates state the version they were based on (optimistic concurrency, NFR-REL-003).
    """

    def get(self, scope: OwnerScope, entity_id: UUID) -> T: ...

    def list_all(self, scope: OwnerScope) -> Sequence[T]: ...

    def add(self, scope: OwnerScope, entity: T) -> T: ...

    def update(self, scope: OwnerScope, entity: T, *, expected_version: int) -> T: ...
