"""Owner scope: the authenticated athlete every repository call is restricted to (ADR-016)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from coach.application.errors import OwnershipError


@dataclass(frozen=True, slots=True)
class OwnerScope:
    athlete_id: UUID


def check_owner(scope: OwnerScope, athlete_id: UUID) -> None:
    """Reject writing an entity that belongs to another athlete than the scope."""
    if athlete_id != scope.athlete_id:
        raise OwnershipError("entity owner does not match the owner scope")
