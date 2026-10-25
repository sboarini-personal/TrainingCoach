"""Identity directory in SQLite: one installation owner (SEC-AUTHZ-001).

Not owner-scoped by design: it is how an authenticated user becomes an owner scope.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from uuid import UUID

from coach.application.identity import Identity, OwnerAlreadyRegisteredError
from coach.infrastructure.sqlite.database import SqliteDatabase


def _identity(row: sqlite3.Row) -> Identity:
    return Identity(
        user_id=UUID(row["user_id"]),
        athlete_id=UUID(row["athlete_id"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


class SqliteIdentityDirectory:
    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database

    def owner(self) -> Identity | None:
        with self._database.read() as connection:
            row = connection.execute(
                "SELECT user_id, athlete_id, created_at FROM identities WHERE is_owner = 1"
            ).fetchone()
        return None if row is None else _identity(row)

    def athlete_for(self, user_id: UUID) -> UUID | None:
        with self._database.read() as connection:
            row = connection.execute(
                "SELECT athlete_id FROM identities WHERE user_id = ?", (str(user_id),)
            ).fetchone()
        return None if row is None else UUID(row["athlete_id"])

    def register_owner(self, identity: Identity) -> Identity:
        try:
            with self._database.write() as connection:
                connection.execute(
                    "INSERT INTO identities (user_id, athlete_id, is_owner, created_at) "
                    "VALUES (?, ?, 1, ?)",
                    (
                        str(identity.user_id),
                        str(identity.athlete_id),
                        identity.created_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise OwnerAlreadyRegisteredError from error
        return identity
