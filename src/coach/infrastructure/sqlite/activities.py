"""Activities, source observations and data overrides in SQLite (ADR-004, ADR-011, ADR-022).

``activity_sources`` indexes the sources of live activities: its primary key lets a source
belong to at most one live activity. Observations are immutable and overrides append-only;
database triggers reject every update.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

from coach.application.contracts import ACTIVITY_CODEC, DATA_OVERRIDE_CODEC, OBSERVATION_CODEC
from coach.application.errors import ConcurrencyConflictError, NotFoundError
from coach.application.ownership import OwnerScope, check_owner
from coach.application.ports import ActivityChange
from coach.domain.activity.activity import Activity
from coach.domain.activity.observation import Observation, SourceKey
from coach.domain.activity.overrides import (
    DataOverride,
    EntityType,
    StaleOverrideError,
    check_new_override,
)
from coach.domain.common.time import ensure_utc
from coach.infrastructure.sqlite.database import SqliteDatabase, dumps, loads

_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECOND: Final = timedelta(microseconds=1)


def _epoch_us(value: datetime) -> int:
    return (ensure_utc(value) - _EPOCH) // _MICROSECOND


def _activity(row: sqlite3.Row) -> Activity:
    return ACTIVITY_CODEC.load(loads(row["document"]))


class SqliteActivityRepository:
    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database

    def get(self, scope: OwnerScope, activity_id: UUID) -> Activity:
        with self._database.read() as connection:
            row = connection.execute(
                "SELECT document FROM activities WHERE activity_id = ? AND athlete_id = ?",
                (str(activity_id), str(scope.athlete_id)),
            ).fetchone()
        if row is None:
            raise NotFoundError
        return _activity(row)

    def list_all(self, scope: OwnerScope) -> list[Activity]:
        with self._database.read() as connection:
            rows = connection.execute(
                "SELECT document FROM activities WHERE athlete_id = ? ORDER BY rowid",
                (str(scope.athlete_id),),
            ).fetchall()
        return [_activity(row) for row in rows]

    def find_by_source(self, scope: OwnerScope, key: SourceKey) -> Activity | None:
        with self._database.read() as connection:
            row = connection.execute(
                "SELECT a.document FROM activity_sources s "
                "JOIN activities a ON a.activity_id = s.activity_id "
                "WHERE s.athlete_id = ? AND s.raw_source_id = ? AND s.part = ? "
                "AND a.athlete_id = ?",
                (str(scope.athlete_id), str(key.raw_source_id), key.part, str(scope.athlete_id)),
            ).fetchone()
        return None if row is None else _activity(row)

    def find_by_provider_record(
        self, scope: OwnerScope, provider: str, provider_activity_id: str
    ) -> Activity | None:
        with self._database.read() as connection:
            row = connection.execute(
                "SELECT a.document FROM activity_sources s "
                "JOIN activities a ON a.activity_id = s.activity_id "
                "WHERE s.athlete_id = ? AND s.provider = ? AND s.provider_activity_id = ? "
                "AND a.athlete_id = ? ORDER BY a.activity_id LIMIT 1",
                (str(scope.athlete_id), provider, provider_activity_id, str(scope.athlete_id)),
            ).fetchone()
        return None if row is None else _activity(row)

    def starting_between(self, scope: OwnerScope, start: datetime, end: datetime) -> list[Activity]:
        with self._database.read() as connection:
            rows = connection.execute(
                "SELECT document FROM activities WHERE athlete_id = ? AND status != 'MERGED' "
                "AND start_us BETWEEN ? AND ? ORDER BY start_us, rowid",
                (str(scope.athlete_id), _epoch_us(start), _epoch_us(end)),
            ).fetchall()
        return [_activity(row) for row in rows]

    def save(self, scope: OwnerScope, changes: Sequence[ActivityChange]) -> list[Activity]:
        stored: list[Activity] = []
        with self._database.write() as connection:
            for change in changes:
                if change.expected_version is None:
                    check_owner(scope, change.activity.athlete_id)
                    saved = change.activity.with_version(1)
                    self._insert(connection, saved)
                else:
                    saved = change.activity.with_version(change.expected_version + 1)
                    self._update(connection, scope, saved, change.expected_version)
                stored.append(saved)
            for saved in stored:
                connection.execute(
                    "DELETE FROM activity_sources WHERE activity_id = ?", (str(saved.activity_id),)
                )
            for saved in stored:
                self._index(connection, saved)
        return stored

    @staticmethod
    def _insert(connection: sqlite3.Connection, activity: Activity) -> None:
        document = ACTIVITY_CODEC.dump(activity)
        try:
            connection.execute(
                "INSERT INTO activities "
                "(activity_id, athlete_id, version, status, start_us, schema_version, document) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(activity.activity_id),
                    str(activity.athlete_id),
                    activity.version,
                    activity.status.value,
                    _epoch_us(activity.start_time_utc),
                    document["schemaVersion"],
                    dumps(document),
                ),
            )
        except sqlite3.IntegrityError as error:
            raise ConcurrencyConflictError("resource already exists") from error

    @staticmethod
    def _update(
        connection: sqlite3.Connection, scope: OwnerScope, activity: Activity, expected: int
    ) -> None:
        row = connection.execute(
            "SELECT athlete_id, version FROM activities WHERE activity_id = ?",
            (str(activity.activity_id),),
        ).fetchone()
        if row is None or row["athlete_id"] != str(scope.athlete_id):
            raise NotFoundError
        check_owner(scope, activity.athlete_id)
        if row["version"] != expected:
            raise ConcurrencyConflictError("resource was modified concurrently")
        document = ACTIVITY_CODEC.dump(activity)
        connection.execute(
            "UPDATE activities SET version = ?, status = ?, start_us = ?, schema_version = ?, "
            "document = ? WHERE activity_id = ? AND athlete_id = ? AND version = ?",
            (
                activity.version,
                activity.status.value,
                _epoch_us(activity.start_time_utc),
                document["schemaVersion"],
                dumps(document),
                str(activity.activity_id),
                str(scope.athlete_id),
                expected,
            ),
        )

    @staticmethod
    def _index(connection: sqlite3.Connection, activity: Activity) -> None:
        if not activity.is_live:
            return
        try:
            connection.executemany(
                "INSERT INTO activity_sources "
                "(athlete_id, raw_source_id, part, activity_id, provider, provider_activity_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        str(activity.athlete_id),
                        str(link.source_id),
                        link.part,
                        str(activity.activity_id),
                        link.provider,
                        link.provider_activity_id,
                    )
                    for link in activity.sources
                ],
            )
        except sqlite3.IntegrityError as error:
            raise ConcurrencyConflictError("source already linked to another activity") from error


class SqliteObservationRepository:
    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database

    def add(self, scope: OwnerScope, observation: Observation) -> tuple[Observation, bool]:
        check_owner(scope, observation.athlete_id)
        document = OBSERVATION_CODEC.dump(observation)
        with self._database.write() as connection:
            row = connection.execute(
                "SELECT document FROM source_observations WHERE raw_source_id = ? AND part = ?",
                (str(observation.raw_source_id), observation.part),
            ).fetchone()
            if row is not None:
                existing = OBSERVATION_CODEC.load(loads(row["document"]))
                if existing != observation:
                    raise ConcurrencyConflictError("a different observation exists for this source")
                return existing, False
            try:
                connection.execute(
                    "INSERT INTO source_observations "
                    "(raw_source_id, part, athlete_id, schema_version, document) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        str(observation.raw_source_id),
                        observation.part,
                        str(observation.athlete_id),
                        document["schemaVersion"],
                        dumps(document),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise NotFoundError from error
        return observation, True

    def get(self, scope: OwnerScope, key: SourceKey) -> Observation:
        with self._database.read() as connection:
            row = connection.execute(
                "SELECT document FROM source_observations "
                "WHERE raw_source_id = ? AND part = ? AND athlete_id = ?",
                (str(key.raw_source_id), key.part, str(scope.athlete_id)),
            ).fetchone()
        if row is None:
            raise NotFoundError
        return OBSERVATION_CODEC.load(loads(row["document"]))


class SqliteOverrideRepository:
    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database

    def add(self, scope: OwnerScope, override: DataOverride) -> DataOverride:
        check_owner(scope, override.athlete_id)
        document = DATA_OVERRIDE_CODEC.dump(override)
        try:
            with self._database.write() as connection:
                existing = self._for_entity(
                    connection, scope, override.entity_type, override.entity_id
                )
                check_new_override(existing, override)
                connection.execute(
                    "INSERT INTO data_overrides "
                    "(override_id, athlete_id, entity_type, entity_id, field, "
                    "supersedes_override_id, schema_version, document) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(override.override_id),
                        str(override.athlete_id),
                        override.entity_type.value,
                        str(override.entity_id),
                        override.field,
                        None
                        if override.supersedes_override_id is None
                        else str(override.supersedes_override_id),
                        document["schemaVersion"],
                        dumps(document),
                    ),
                )
        except (sqlite3.IntegrityError, StaleOverrideError) as error:
            raise ConcurrencyConflictError(str(error)) from error
        return override

    def get(self, scope: OwnerScope, override_id: UUID) -> DataOverride:
        with self._database.read() as connection:
            row = connection.execute(
                "SELECT document FROM data_overrides WHERE override_id = ? AND athlete_id = ?",
                (str(override_id), str(scope.athlete_id)),
            ).fetchone()
        if row is None:
            raise NotFoundError
        return DATA_OVERRIDE_CODEC.load(loads(row["document"]))

    def for_entity(
        self, scope: OwnerScope, entity_type: EntityType, entity_id: UUID
    ) -> list[DataOverride]:
        with self._database.read() as connection:
            return self._for_entity(connection, scope, entity_type, entity_id)

    def entities_with_field(
        self, scope: OwnerScope, entity_type: EntityType, field: str
    ) -> list[UUID]:
        with self._database.read() as connection:
            rows = connection.execute(
                "SELECT DISTINCT entity_id FROM data_overrides "
                "WHERE athlete_id = ? AND entity_type = ? AND field = ? ORDER BY entity_id",
                (str(scope.athlete_id), entity_type.value, field),
            ).fetchall()
        return [UUID(row["entity_id"]) for row in rows]

    @staticmethod
    def _for_entity(
        connection: sqlite3.Connection,
        scope: OwnerScope,
        entity_type: EntityType,
        entity_id: UUID,
    ) -> list[DataOverride]:
        rows = connection.execute(
            "SELECT document FROM data_overrides "
            "WHERE athlete_id = ? AND entity_type = ? AND entity_id = ? ORDER BY rowid",
            (str(scope.athlete_id), entity_type.value, str(entity_id)),
        ).fetchall()
        return [DATA_OVERRIDE_CODEC.load(loads(row["document"])) for row in rows]
