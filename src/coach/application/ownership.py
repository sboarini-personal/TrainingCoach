"""Owner scope: the authenticated athlete every repository call is restricted to (ADR-016)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class OwnerScope:
    athlete_id: UUID
