"""Hash-chained audit log in SQLite (SEC-AUD-001, ADR-023).

Appends run under the database write lock, so sequence and chain stay linear. Triggers reject
deletes and every update except nulling the plain athlete ID (privacy deletion).
"""

from __future__ import annotations

import secrets
import sqlite3
from uuid import UUID

from coach.application.audit import (
    GENESIS_HASH,
    AuditEvent,
    AuditRecord,
    AuditVerification,
    seal,
    verify_chain,
)
from coach.application.ids import new_id
from coach.application.ownership import OwnerScope
from coach.application.ports import IdFactory
from coach.infrastructure.sqlite.database import SqliteDatabase, dumps, loads

_SALT_KEY = "salt"


def _record(row: sqlite3.Row) -> AuditRecord:
    athlete_id = row["athlete_id"]
    return AuditRecord(loads(row["document"]), None if athlete_id is None else UUID(athlete_id))


def _read_salt(connection: sqlite3.Connection) -> str | None:
    row = connection.execute(
        "SELECT value FROM audit_settings WHERE key = ?", (_SALT_KEY,)
    ).fetchone()
    return None if row is None else str(row["value"])


class SqliteAuditLog:
    def __init__(self, database: SqliteDatabase, ids: IdFactory = new_id) -> None:
        self._database = database
        self._ids = ids

    def append(self, event: AuditEvent) -> AuditRecord:
        with self._database.write() as connection:
            salt = _read_salt(connection)
            if salt is None:
                salt = secrets.token_hex(32)
                connection.execute(
                    "INSERT INTO audit_settings (key, value) VALUES (?, ?)", (_SALT_KEY, salt)
                )
            last = connection.execute(
                "SELECT sequence, hash FROM audit_log ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            record = seal(
                event,
                event_id=self._ids(),
                sequence=1 if last is None else int(last["sequence"]) + 1,
                previous_hash=GENESIS_HASH if last is None else str(last["hash"]),
                salt=salt,
            )
            connection.execute(
                "INSERT INTO audit_log (sequence, event_id, athlete_id, document, hash) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    record.sequence,
                    record.document["eventId"],
                    None if record.athlete_id is None else str(record.athlete_id),
                    dumps(dict(record.document)),
                    record.hash,
                ),
            )
        return record

    def events_for(self, scope: OwnerScope) -> list[AuditRecord]:
        with self._database.read() as connection:
            rows = connection.execute(
                "SELECT athlete_id, document FROM audit_log WHERE athlete_id = ? ORDER BY sequence",
                (str(scope.athlete_id),),
            ).fetchall()
        return [_record(row) for row in rows]

    def verify(self) -> AuditVerification:
        with self._database.read() as connection:
            salt = _read_salt(connection)
            rows = connection.execute(
                "SELECT athlete_id, document FROM audit_log ORDER BY sequence"
            ).fetchall()
        if salt is None:
            return AuditVerification(0) if not rows else AuditVerification(0, 1, "MISSING_SALT")
        return verify_chain([_record(row) for row in rows], salt)

    def forget(self, scope: OwnerScope) -> int:
        with self._database.write() as connection:
            cursor = connection.execute(
                "UPDATE audit_log SET athlete_id = NULL WHERE athlete_id = ?",
                (str(scope.athlete_id),),
            )
            return cursor.rowcount
