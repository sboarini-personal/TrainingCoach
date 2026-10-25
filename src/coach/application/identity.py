"""Principals and identities (spec/security/authentication.spec.md section 4)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from coach.application.errors import ApplicationError
from coach.domain.common.time import ensure_utc


@dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated user. Becomes an owner scope only through the athlete link."""

    user_id: UUID


@dataclass(frozen=True, slots=True, kw_only=True)
class Identity:
    """Link between a user and the athlete whose data they own."""

    user_id: UUID
    athlete_id: UUID
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))


class OwnerAlreadyRegisteredError(ApplicationError, ValueError):
    def __init__(self) -> None:
        super().__init__("an installation owner is already registered")
