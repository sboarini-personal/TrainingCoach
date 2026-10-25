"""Owner-scoped aggregate repository storing contract documents (ADR-016, ADR-022)."""

from __future__ import annotations

import sqlite3
from uuid import UUID

from coach.application.contracts import DocumentCodec
from coach.application.errors import ConcurrencyConflictError, NotFoundError
from coach.application.ownership import OwnerScope, check_owner
from coach.application.ports import OwnedAggregate
from coach.infrastructure.sqlite.database import SqliteDatabase, dumps, loads


class SqliteAggregateRepository[T: OwnedAggregate]:
    def __init__(self, database: SqliteDatabase, codec: DocumentCodec[T]) -> None:
        self._database = database
        self._codec = codec

    def get(self, scope: OwnerScope, entity_id: UUID) -> T:
        with self._database.read() as connection:
            row = connection.execute(
                "SELECT document FROM aggregates "
                "WHERE kind = ? AND entity_id = ? AND athlete_id = ?",
                (self._codec.kind, str(entity_id), str(scope.athlete_id)),
            ).fetchone()
        if row is None:
            raise NotFoundError
        return self._codec.load(loads(row["document"]))

    def list_all(self, scope: OwnerScope) -> list[T]:
        with self._database.read() as connection:
            rows = connection.execute(
                "SELECT document FROM aggregates WHERE kind = ? AND athlete_id = ? ORDER BY rowid",
                (self._codec.kind, str(scope.athlete_id)),
            ).fetchall()
        return [self._codec.load(loads(row["document"])) for row in rows]

    def add(self, scope: OwnerScope, entity: T) -> T:
        check_owner(scope, entity.athlete_id)
        stored = entity.with_version(1)
        document = self._codec.dump(stored)
        try:
            with self._database.write() as connection:
                connection.execute(
                    "INSERT INTO aggregates "
                    "(kind, entity_id, athlete_id, version, schema_version, document) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        self._codec.kind,
                        str(stored.entity_id),
                        str(stored.athlete_id),
                        stored.version,
                        document["schemaVersion"],
                        dumps(document),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise ConcurrencyConflictError("resource already exists") from error
        return stored

    def update(self, scope: OwnerScope, entity: T, *, expected_version: int) -> T:
        stored = entity.with_version(expected_version + 1)
        document = self._codec.dump(stored)
        with self._database.write() as connection:
            row = connection.execute(
                "SELECT athlete_id, version FROM aggregates WHERE kind = ? AND entity_id = ?",
                (self._codec.kind, str(entity.entity_id)),
            ).fetchone()
            if row is None or row["athlete_id"] != str(scope.athlete_id):
                raise NotFoundError
            check_owner(scope, entity.athlete_id)
            if row["version"] != expected_version:
                raise ConcurrencyConflictError("resource was modified concurrently")
            connection.execute(
                "UPDATE aggregates SET version = ?, schema_version = ?, document = ? "
                "WHERE kind = ? AND entity_id = ? AND athlete_id = ? AND version = ?",
                (
                    stored.version,
                    document["schemaVersion"],
                    dumps(document),
                    self._codec.kind,
                    str(entity.entity_id),
                    str(scope.athlete_id),
                    expected_version,
                ),
            )
        return stored
