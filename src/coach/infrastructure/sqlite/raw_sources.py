"""Immutable RAW source metadata (FR-ACT-002). A database trigger forbids updates."""

from __future__ import annotations

import sqlite3
from uuid import UUID

from coach.application.contracts import RAW_SOURCE_CODEC
from coach.application.errors import ConcurrencyConflictError, NotFoundError
from coach.application.ownership import OwnerScope, check_owner
from coach.domain.raw.source import RawSource
from coach.infrastructure.sqlite.database import SqliteDatabase, dumps, loads


def _load(row: sqlite3.Row) -> RawSource:
    return RAW_SOURCE_CODEC.load(loads(row["document"]))


class SqliteRawSourceRepository:
    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database

    def add(self, scope: OwnerScope, source: RawSource) -> tuple[RawSource, bool]:
        check_owner(scope, source.athlete_id)
        document = RAW_SOURCE_CODEC.dump(source)
        with self._database.write() as connection:
            existing = connection.execute(
                "SELECT document FROM raw_sources WHERE athlete_id = ? AND sha256 = ?",
                (str(scope.athlete_id), source.sha256),
            ).fetchone()
            if existing is not None:
                return _load(existing), False
            try:
                connection.execute(
                    "INSERT INTO raw_sources "
                    "(raw_source_id, athlete_id, sha256, schema_version, document) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        str(source.raw_source_id),
                        str(source.athlete_id),
                        source.sha256,
                        document["schemaVersion"],
                        dumps(document),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ConcurrencyConflictError("raw source id already used") from error
        return source, True

    def get(self, scope: OwnerScope, raw_source_id: UUID) -> RawSource:
        with self._database.read() as connection:
            row = connection.execute(
                "SELECT document FROM raw_sources WHERE raw_source_id = ? AND athlete_id = ?",
                (str(raw_source_id), str(scope.athlete_id)),
            ).fetchone()
        if row is None:
            raise NotFoundError
        return _load(row)

    def find_by_sha256(self, scope: OwnerScope, sha256: str) -> RawSource | None:
        with self._database.read() as connection:
            row = connection.execute(
                "SELECT document FROM raw_sources WHERE athlete_id = ? AND sha256 = ?",
                (str(scope.athlete_id), sha256),
            ).fetchone()
        return None if row is None else _load(row)

    def list_all(self, scope: OwnerScope) -> list[RawSource]:
        with self._database.read() as connection:
            rows = connection.execute(
                "SELECT document FROM raw_sources WHERE athlete_id = ? ORDER BY rowid",
                (str(scope.athlete_id),),
            ).fetchall()
        return [_load(row) for row in rows]
