"""Contract for RAW source repositories: immutable, idempotent, owner-scoped (FR-ACT-002)."""

import sqlite3
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

import pytest

from coach.application.errors import NotFoundError, OwnershipError
from coach.application.ownership import OwnerScope
from coach.application.ports import RawSourceRepository
from coach.infrastructure.memory import InMemoryRawSourceRepository
from coach.infrastructure.sqlite.database import SqliteDatabase
from coach.infrastructure.sqlite.raw_sources import SqliteRawSourceRepository
from samples import ATHLETE, OTHER_ATHLETE, SHA_A, sample_raw_source

SCOPE_A = OwnerScope(ATHLETE)
SCOPE_B = OwnerScope(OTHER_ATHLETE)
SHA_B = "b" * 64
SECOND_ID = UUID("0193b2a0-0000-7000-8000-000000000299")


def _in_memory(_: Path) -> RawSourceRepository:
    return InMemoryRawSourceRepository()


def _sqlite(directory: Path) -> RawSourceRepository:
    return SqliteRawSourceRepository(SqliteDatabase.open(directory / "coach.sqlite3"))


@pytest.fixture(params=[_in_memory, _sqlite], ids=["in-memory", "sqlite"])
def repository(request: pytest.FixtureRequest, tmp_path: Path) -> RawSourceRepository:
    factory: Callable[[Path], RawSourceRepository] = request.param
    return factory(tmp_path)


@pytest.mark.req("NFR-REL-001", "FR-ACT-007")
def test_same_bytes_are_stored_once(repository: RawSourceRepository) -> None:
    first, created = repository.add(SCOPE_A, sample_raw_source())
    assert created
    again, created_again = repository.add(SCOPE_A, sample_raw_source(raw_source_id=SECOND_ID))
    assert not created_again
    assert again == first
    assert repository.list_all(SCOPE_A) == [first]


@pytest.mark.req("SEC-IDOR-001")
def test_lookups_are_owner_scoped(repository: RawSourceRepository) -> None:
    mine, _ = repository.add(SCOPE_A, sample_raw_source())
    theirs, created = repository.add(
        SCOPE_B, sample_raw_source(OTHER_ATHLETE, raw_source_id=SECOND_ID)
    )
    assert created, "the same file uploaded by another athlete is a separate source"
    assert repository.find_by_sha256(SCOPE_A, SHA_A) == mine
    assert repository.find_by_sha256(SCOPE_B, SHA_A) == theirs
    assert repository.find_by_sha256(SCOPE_A, SHA_B) is None
    with pytest.raises(NotFoundError):
        repository.get(SCOPE_A, theirs.raw_source_id)
    assert repository.get(SCOPE_B, theirs.raw_source_id) == theirs


@pytest.mark.req("SEC-AUTHZ-001")
def test_sources_cannot_be_added_for_another_athlete(repository: RawSourceRepository) -> None:
    with pytest.raises(OwnershipError):
        repository.add(SCOPE_A, sample_raw_source(OTHER_ATHLETE))


@pytest.mark.req("FR-ACT-002")
def test_repository_offers_no_way_to_change_a_source(repository: RawSourceRepository) -> None:
    assert not {"update", "replace", "delete", "save"} & set(dir(repository))


@pytest.mark.req("FR-ACT-002")
def test_database_rejects_changes_to_raw_sources(tmp_path: Path) -> None:
    database = SqliteDatabase.open(tmp_path / "coach.sqlite3")
    SqliteRawSourceRepository(database).add(SCOPE_A, sample_raw_source())
    with pytest.raises(sqlite3.IntegrityError, match="immutable"), database.write() as connection:
        connection.execute("UPDATE raw_sources SET sha256 = ?", (SHA_B,))
