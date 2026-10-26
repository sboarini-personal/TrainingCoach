"""Immutable ingestion records (INT-FIL-004; migration 4). A trigger rejects updates."""

from __future__ import annotations

import sqlite3
from uuid import UUID

from coach.application.contracts import INGESTION_RECORD_CODEC
from coach.application.contracts._json import instant
from coach.application.errors import ConcurrencyConflictError, NotFoundError
from coach.application.ownership import OwnerScope, check_owner
from coach.domain.ingestion.record import IngestionRecord
from coach.infrastructure.sqlite.database import SqliteDatabase, dumps, loads


def _load(row: sqlite3.Row) -> IngestionRecord:
    return INGESTION_RECORD_CODEC.load(loads(row["document"]))


class SqliteIngestionRecordRepository:
    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database

    def add(self, scope: OwnerScope, record: IngestionRecord) -> IngestionRecord:
        check_owner(scope, record.athlete_id)
        document = INGESTION_RECORD_CODEC.dump(record)
        try:
            with self._database.write() as connection:
                connection.execute(
                    "INSERT INTO ingestion_records (ingestion_id, athlete_id, sha256, outcome, "
                    "idempotency_key, received_at, schema_version, document) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(record.ingestion_id),
                        str(record.athlete_id),
                        record.sha256,
                        record.outcome.value,
                        record.idempotency_key,
                        instant(record.received_at),
                        document["schemaVersion"],
                        dumps(document),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise ConcurrencyConflictError("ingestion record already exists") from error
        return record

    def get(self, scope: OwnerScope, ingestion_id: UUID) -> IngestionRecord:
        with self._database.read() as connection:
            row = connection.execute(
                "SELECT document FROM ingestion_records WHERE ingestion_id = ? AND athlete_id = ?",
                (str(ingestion_id), str(scope.athlete_id)),
            ).fetchone()
        if row is None:
            raise NotFoundError
        return _load(row)

    def find_imported(self, scope: OwnerScope, sha256: str) -> IngestionRecord | None:
        with self._database.read() as connection:
            row = connection.execute(
                "SELECT document FROM ingestion_records "
                "WHERE athlete_id = ? AND sha256 = ? AND outcome = 'IMPORTED' "
                "ORDER BY received_at, rowid LIMIT 1",
                (str(scope.athlete_id), sha256),
            ).fetchone()
        return None if row is None else _load(row)

    def find_by_idempotency_key(self, scope: OwnerScope, key: str) -> IngestionRecord | None:
        with self._database.read() as connection:
            row = connection.execute(
                "SELECT document FROM ingestion_records "
                "WHERE athlete_id = ? AND idempotency_key = ?",
                (str(scope.athlete_id), key),
            ).fetchone()
        return None if row is None else _load(row)

    def recent(self, scope: OwnerScope, limit: int) -> list[IngestionRecord]:
        with self._database.read() as connection:
            rows = connection.execute(
                "SELECT document FROM ingestion_records WHERE athlete_id = ? "
                "ORDER BY received_at DESC, rowid DESC LIMIT ?",
                (str(scope.athlete_id), limit),
            ).fetchall()
        return [_load(row) for row in rows]

    def for_provider_object(
        self, scope: OwnerScope, provider: str, provider_object_id: str
    ) -> list[IngestionRecord]:
        with self._database.read() as connection:
            rows = connection.execute(
                "SELECT document FROM ingestion_records WHERE athlete_id = ? "
                "AND json_extract(document, '$.provider') = ? "
                "AND json_extract(document, '$.providerObjectId') = ? "
                "ORDER BY received_at, rowid",
                (str(scope.athlete_id), provider, provider_object_id),
            ).fetchall()
        return [_load(row) for row in rows]
