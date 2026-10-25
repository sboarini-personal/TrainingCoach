"""Append-only derived metric values (FR-MET-004).

The stored document is the value as computed; only the ``superseded_by`` column is ever set,
once. A partial unique index allows a single current value per athlete, metric and subject,
and a trigger rejects any other change.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from uuid import UUID

from coach.application.contracts import DERIVED_VALUE_CODEC
from coach.application.errors import ConcurrencyConflictError, NotFoundError
from coach.application.ownership import OwnerScope, check_owner
from coach.infrastructure.sqlite.database import SqliteDatabase, dumps, loads
from coach.metrics.derived import (
    DerivedMetricValue,
    MetricSubject,
    check_new_value,
    check_supersession,
)


def _load(row: sqlite3.Row) -> DerivedMetricValue:
    value = DERIVED_VALUE_CODEC.load(loads(row["document"]))
    superseded_by = row["superseded_by"]
    return replace(value, superseded_by=None if superseded_by is None else UUID(superseded_by))


class SqliteDerivedValueRepository:
    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database

    def add(self, scope: OwnerScope, value: DerivedMetricValue) -> DerivedMetricValue:
        check_owner(scope, value.athlete_id)
        check_new_value(value)
        try:
            with self._database.write() as connection:
                _insert(connection, value)
        except sqlite3.IntegrityError as error:
            raise ConcurrencyConflictError("a current value already exists") from error
        return value

    def supersede(
        self, scope: OwnerScope, old_value_id: UUID, new: DerivedMetricValue
    ) -> DerivedMetricValue:
        check_owner(scope, new.athlete_id)
        check_new_value(new)
        try:
            with self._database.write() as connection:
                old = _owned(connection, scope, old_value_id)
                check_supersession(old, new)
                cursor = connection.execute(
                    "UPDATE derived_metric_values SET superseded_by = ? "
                    "WHERE value_id = ? AND athlete_id = ? AND superseded_by IS NULL",
                    (str(new.value_id), str(old_value_id), str(scope.athlete_id)),
                )
                if cursor.rowcount != 1:
                    raise ConcurrencyConflictError("value was already superseded")
                _insert(connection, new)
        except sqlite3.IntegrityError as error:
            raise ConcurrencyConflictError("value id already used") from error
        return new

    def get(self, scope: OwnerScope, value_id: UUID) -> DerivedMetricValue:
        with self._database.read() as connection:
            return _owned(connection, scope, value_id)

    def current(
        self, scope: OwnerScope, metric_id: str, subject: MetricSubject
    ) -> DerivedMetricValue | None:
        with self._database.read() as connection:
            row = connection.execute(
                "SELECT document, superseded_by FROM derived_metric_values "
                "WHERE athlete_id = ? AND metric_id = ? AND subject_kind = ? AND subject_key = ? "
                "AND superseded_by IS NULL",
                (str(scope.athlete_id), metric_id, subject.kind.value, subject.key),
            ).fetchone()
        return None if row is None else _load(row)

    def history(
        self, scope: OwnerScope, metric_id: str, subject: MetricSubject
    ) -> list[DerivedMetricValue]:
        with self._database.read() as connection:
            rows = connection.execute(
                "SELECT document, superseded_by FROM derived_metric_values "
                "WHERE athlete_id = ? AND metric_id = ? AND subject_kind = ? AND subject_key = ? "
                "ORDER BY rowid",
                (str(scope.athlete_id), metric_id, subject.kind.value, subject.key),
            ).fetchall()
        return [_load(row) for row in rows]


def _owned(connection: sqlite3.Connection, scope: OwnerScope, value_id: UUID) -> DerivedMetricValue:
    row = connection.execute(
        "SELECT document, superseded_by FROM derived_metric_values "
        "WHERE value_id = ? AND athlete_id = ?",
        (str(value_id), str(scope.athlete_id)),
    ).fetchone()
    if row is None:
        raise NotFoundError
    return _load(row)


def _insert(connection: sqlite3.Connection, value: DerivedMetricValue) -> None:
    document = DERIVED_VALUE_CODEC.dump(value)
    connection.execute(
        "INSERT INTO derived_metric_values "
        "(value_id, athlete_id, metric_id, subject_kind, subject_key, superseded_by, "
        "schema_version, document) VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
        (
            str(value.value_id),
            str(value.athlete_id),
            value.metric_id,
            value.subject.kind.value,
            value.subject.key,
            document["schemaVersion"],
            dumps(document),
        ),
    )
