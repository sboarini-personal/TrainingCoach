"""Storage-level tamper resistance of the SQLite audit log (AT-AUD-001)."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from coach.application.audit import ActorKind, AuditEvent, AuditOutcome
from coach.application.ownership import OwnerScope
from coach.infrastructure.sqlite.audit import SqliteAuditLog
from coach.infrastructure.sqlite.database import SqliteDatabase
from samples import ATHLETE

pytestmark = [pytest.mark.req("SEC-AUD-001"), pytest.mark.acceptance("AT-AUD-001")]

NOW = datetime(2026, 10, 25, 10, 0, tzinfo=UTC)


@pytest.fixture
def stored(tmp_path: Path) -> tuple[SqliteDatabase, SqliteAuditLog]:
    database = SqliteDatabase.open(tmp_path / "coach.sqlite3")
    audit = SqliteAuditLog(database)
    for action in ("consent.granted", "consent.revoked", "consent.granted"):
        audit.append(
            AuditEvent(
                occurred_at=NOW,
                actor=ActorKind.USER,
                action=action,
                outcome=AuditOutcome.SUCCESS,
                athlete_id=ATHLETE,
            )
        )
    return database, audit


def _execute(database: SqliteDatabase, sql: str, *params: object) -> None:
    with database.write() as connection:
        connection.execute(sql, params)


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE audit_log SET document = json_set(document, '$.outcome', 'DENIED') "
        "WHERE sequence = 2",
        "UPDATE audit_log SET hash = '" + "0" * 64 + "'",
        "UPDATE audit_log SET athlete_id = 'someone-else'",
        # Forgetting is the only allowed update; it must not carry any other change.
        "UPDATE audit_log SET athlete_id = NULL, "
        "document = json_set(document, '$.outcome', 'DENIED')",
        "UPDATE audit_log SET athlete_id = NULL, hash = '" + "1" * 64 + "'",
        "UPDATE audit_log SET athlete_id = NULL, event_id = 'forged'",
        "UPDATE audit_log SET athlete_id = NULL, sequence = sequence + 100",
        "DELETE FROM audit_log WHERE sequence = 3",
        "UPDATE audit_settings SET value = 'x'",
        "DELETE FROM audit_settings",
    ],
)
def test_database_rejects_changes(stored: tuple[SqliteDatabase, SqliteAuditLog], sql: str) -> None:
    database, _ = stored
    with pytest.raises(sqlite3.IntegrityError, match=r"append-only|immutable"):
        _execute(database, sql)


def _drop_protection(database: SqliteDatabase) -> None:
    """Simulate an attacker with direct write access to the database file."""
    with database.write() as connection:
        for trigger in ("audit_log_no_delete", "audit_log_only_forget"):
            connection.execute(f"DROP TRIGGER {trigger}")


def test_tampering_behind_the_triggers_is_detected(
    stored: tuple[SqliteDatabase, SqliteAuditLog],
) -> None:
    database, audit = stored
    _drop_protection(database)
    _execute(
        database,
        "UPDATE audit_log SET document = json_set(document, '$.outcome', 'DENIED') "
        "WHERE sequence = 2",
    )
    result = audit.verify()
    assert (result.valid, result.first_invalid_sequence) == (False, 2)


def test_deleted_rows_behind_the_triggers_are_detected(
    stored: tuple[SqliteDatabase, SqliteAuditLog],
) -> None:
    database, audit = stored
    _drop_protection(database)
    _execute(database, "DELETE FROM audit_log WHERE sequence = 2")
    result = audit.verify()
    assert (result.valid, result.first_invalid_sequence, result.problem) == (
        False,
        3,
        "SEQUENCE_GAP",
    )


def test_forgetting_nulls_only_the_plain_column(
    stored: tuple[SqliteDatabase, SqliteAuditLog],
) -> None:
    database, audit = stored
    audit.forget(OwnerScope(ATHLETE))
    with database.read() as connection:
        rows = connection.execute("SELECT athlete_id, document FROM audit_log").fetchall()
    assert all(row["athlete_id"] is None for row in rows)
    assert all(str(ATHLETE) not in row["document"] for row in rows)
    assert audit.verify().valid
