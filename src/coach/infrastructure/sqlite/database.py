"""SQLite access and the migration runner (ADR-022, ADR-027, NFR-REL-004).

One short-lived connection per operation; write transactions take the write lock up front
(``BEGIN IMMEDIATE``) so concurrent writers serialise instead of deadlocking. A batch
(ADR-027) runs a multi-step command on one connection and one transaction: reads and writes
inside it join it, each write is a savepoint (a failed write rolls back only itself), and the
whole batch commits once at the end or rolls back on an exception. Batches are per thread.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from coach.infrastructure.sqlite.migrations import MIGRATIONS, Migration

BUSY_TIMEOUT_SECONDS: Final = 10.0
_SAVEPOINT: Final = "coach_write"
_MIGRATIONS_TABLE: Final = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
) STRICT
"""


class MigrationError(RuntimeError):
    """The database cannot be brought to the schema this code expects."""


class DestructiveMigrationRefusedError(MigrationError):
    """A pending migration drops or deletes data and was not explicitly approved."""


class SchemaNewerThanCodeError(MigrationError):
    """The database was migrated by newer code; downgrades are never performed."""


class MigrationChecksumError(MigrationError):
    """An applied migration differs from the code: applied migrations are never edited."""


def dumps(document: dict[str, Any]) -> str:
    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def loads(text: str) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(text)
    return document


class SqliteDatabase:
    def __init__(self, path: Path, *, migrations: Sequence[Migration] = MIGRATIONS) -> None:
        versions = [migration.version for migration in migrations]
        if versions != list(range(1, len(versions) + 1)):
            raise ValueError("migration versions must be consecutive from 1")
        self._path = path
        self._migrations = tuple(migrations)
        self._local = threading.local()

    @classmethod
    def open(cls, path: Path) -> SqliteDatabase:
        """Open the database at ``path``, applying pending non-destructive migrations."""
        database = cls(path)
        database.migrate()
        return database

    def _batch_connection(self) -> sqlite3.Connection | None:
        connection: sqlite3.Connection | None = getattr(self._local, "connection", None)
        return connection

    @contextmanager
    def batch(self) -> Iterator[None]:
        """One connection and one transaction for everything inside (a nested batch joins)."""
        if self._batch_connection() is not None:
            with self.write():  # a nested batch is a savepoint of the outer one
                yield
            return
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._local.connection = connection
            try:
                yield
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            finally:
                self._local.connection = None

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        batch = self._batch_connection()
        if batch is not None:
            yield batch
            return
        with closing(self._connect()) as connection:
            yield connection

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        batch = self._batch_connection()
        if batch is not None:
            batch.execute(f"SAVEPOINT {_SAVEPOINT}")
            try:
                yield batch
                batch.execute(f"RELEASE {_SAVEPOINT}")
            except BaseException:
                if batch.in_transaction:  # SQLite may already have rolled everything back
                    batch.execute(f"ROLLBACK TO {_SAVEPOINT}")
                    batch.execute(f"RELEASE {_SAVEPOINT}")
                raise
            return
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def schema_version(self) -> int:
        with self.read() as connection:
            row = connection.execute("SELECT max(version) FROM schema_migrations").fetchone()
        return int(row[0] or 0)

    def migrate(self, *, allow_destructive: bool = False) -> list[int]:
        """Apply pending migrations; return the versions applied by this call."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self.read() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(_MIGRATIONS_TABLE)
            rows = connection.execute("SELECT version, checksum FROM schema_migrations").fetchall()
        applied = {int(row["version"]): str(row["checksum"]) for row in rows}
        if max(applied, default=0) > len(self._migrations):
            raise SchemaNewerThanCodeError(
                f"database schema {max(applied)} is newer than this code ({len(self._migrations)})"
            )
        for version, checksum in applied.items():
            if self._migrations[version - 1].checksum != checksum:
                raise MigrationChecksumError(f"applied migration {version} was edited")
        pending = [migration for migration in self._migrations if migration.version not in applied]
        destructive = [migration.version for migration in pending if migration.destructive]
        if destructive and not allow_destructive:
            raise DestructiveMigrationRefusedError(
                f"migrations {destructive} remove data and need explicit approval"
            )
        return [migration.version for migration in pending if self._apply(migration)]

    def _apply(self, migration: Migration) -> bool:
        with self.write() as connection:
            already = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = ?", (migration.version,)
            ).fetchone()
            if already is not None:
                return False
            connection.executescript(migration.sql)
            connection.execute(
                "INSERT INTO schema_migrations (version, name, checksum, applied_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    migration.version,
                    migration.name,
                    migration.checksum,
                    datetime.now(UTC).isoformat(),
                ),
            )
        return True

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=BUSY_TIMEOUT_SECONDS, autocommit=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = FULL")
        return connection
