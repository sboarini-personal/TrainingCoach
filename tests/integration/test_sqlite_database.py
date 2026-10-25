"""SQLite database and migrations (NFR-REL-004, AT-MIG-001, ADR-022)."""

import re
import sqlite3
from pathlib import Path

import pytest

from coach.infrastructure.sqlite.database import (
    DestructiveMigrationRefusedError,
    MigrationChecksumError,
    SchemaNewerThanCodeError,
    SqliteDatabase,
)
from coach.infrastructure.sqlite.migrations import MIGRATIONS, Migration

pytestmark = pytest.mark.req("NFR-REL-004")

EXTRA = Migration(len(MIGRATIONS) + 1, "add notes table", "CREATE TABLE notes (id TEXT) STRICT;")
DROP = Migration(len(MIGRATIONS) + 1, "drop notes", "DROP TABLE aggregates;", destructive=True)


def _applied(path: Path) -> list[tuple[int, str]]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute("SELECT version, name FROM schema_migrations ORDER BY version")
        return [(int(version), str(name)) for version, name in rows]


@pytest.mark.acceptance("AT-MIG-001")
def test_fresh_database_is_migrated_once(tmp_path: Path) -> None:
    path = tmp_path / "coach.sqlite3"
    database = SqliteDatabase.open(path)
    assert database.schema_version() == len(MIGRATIONS)
    applied = _applied(path)
    assert [version for version, _ in applied] == list(range(1, len(MIGRATIONS) + 1))
    SqliteDatabase.open(path)
    assert _applied(path) == applied


def test_database_uses_wal_and_foreign_keys(tmp_path: Path) -> None:
    database = SqliteDatabase.open(tmp_path / "coach.sqlite3")
    with database.read() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


@pytest.mark.acceptance("AT-MIG-001")
def test_pending_non_destructive_migration_is_applied(tmp_path: Path) -> None:
    path = tmp_path / "coach.sqlite3"
    SqliteDatabase.open(path)
    upgraded = SqliteDatabase(path, migrations=(*MIGRATIONS, EXTRA))
    assert upgraded.migrate() == [EXTRA.version]
    assert upgraded.schema_version() == EXTRA.version


@pytest.mark.acceptance("AT-MIG-001")
def test_destructive_migration_needs_explicit_approval(tmp_path: Path) -> None:
    path = tmp_path / "coach.sqlite3"
    SqliteDatabase.open(path)
    database = SqliteDatabase(path, migrations=(*MIGRATIONS, DROP))
    with pytest.raises(DestructiveMigrationRefusedError):
        database.migrate()
    assert database.schema_version() == len(MIGRATIONS)
    with database.read() as connection:
        assert connection.execute("SELECT count(*) FROM aggregates").fetchone()[0] == 0
    assert database.migrate(allow_destructive=True) == [DROP.version]


@pytest.mark.acceptance("AT-MIG-001")
def test_database_newer_than_code_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "coach.sqlite3"
    SqliteDatabase(path, migrations=(*MIGRATIONS, EXTRA)).migrate()
    with pytest.raises(SchemaNewerThanCodeError):
        SqliteDatabase.open(path)


@pytest.mark.acceptance("AT-MIG-001")
def test_edited_applied_migration_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "coach.sqlite3"
    SqliteDatabase.open(path)
    edited = Migration(1, MIGRATIONS[0].name, MIGRATIONS[0].sql + "\n-- edited\n")
    with pytest.raises(MigrationChecksumError):
        SqliteDatabase(path, migrations=(edited, *MIGRATIONS[1:])).migrate()


def test_migration_versions_must_be_consecutive() -> None:
    with pytest.raises(ValueError, match="consecutive"):
        SqliteDatabase(Path("unused.sqlite3"), migrations=(EXTRA,))


@pytest.mark.parametrize("migration", MIGRATIONS, ids=lambda migration: migration.name)
def test_any_drop_or_delete_is_flagged_destructive(migration: Migration) -> None:
    removes_data = re.search(r"\b(DROP|DELETE)\b", migration.sql, re.IGNORECASE)
    assert migration.destructive or not removes_data


def test_failed_write_rolls_back(tmp_path: Path) -> None:
    database = SqliteDatabase.open(tmp_path / "coach.sqlite3")
    row = "INSERT INTO aggregates VALUES ('goal', 'x', 'a', 1, '1.0.0', '{}')"

    def insert_twice() -> None:
        with database.write() as connection:
            connection.execute(row)
            connection.execute(row)

    with pytest.raises(sqlite3.IntegrityError):
        insert_twice()
    with database.read() as connection:
        assert connection.execute("SELECT count(*) FROM aggregates").fetchone()[0] == 0
