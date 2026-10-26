"""Contract for ingestion record repositories (INT-FIL-004, NFR-REL-001, ADR-016)."""

import sqlite3
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from coach.application.errors import ConcurrencyConflictError, NotFoundError, OwnershipError
from coach.application.ownership import OwnerScope
from coach.application.ports import IngestionRecordRepository
from coach.domain.ingestion.record import (
    IngestionOutcome,
    IngestionRecord,
    RejectionReason,
)
from coach.domain.raw.source import DetectedType, MalwareScan, SourceOrigin
from coach.infrastructure.memory import InMemoryIngestionRecordRepository
from coach.infrastructure.sqlite.database import SqliteDatabase
from coach.infrastructure.sqlite.ingestion import SqliteIngestionRecordRepository
from samples import ATHLETE, NOW, OTHER_ATHLETE, SHA_A, SHA_B

SCOPE_A = OwnerScope(ATHLETE)
SCOPE_B = OwnerScope(OTHER_ATHLETE)


def _record(number: int, **changes: Any) -> IngestionRecord:
    values: dict[str, Any] = {
        "ingestion_id": UUID(int=number),
        "athlete_id": ATHLETE,
        "received_at": NOW + timedelta(minutes=number),
        "completed_at": NOW + timedelta(minutes=number, seconds=1),
        "outcome": IngestionOutcome.IMPORTED,
        "detected_type": DetectedType.FIT,
        "byte_size": 10,
        "sha256": SHA_A,
        "malware_scan": MalwareScan.NOT_SCANNED,
    }
    values.update(changes)
    return IngestionRecord(**values)


def _in_memory(_: Path) -> IngestionRecordRepository:
    return InMemoryIngestionRecordRepository()


def _sqlite(directory: Path) -> IngestionRecordRepository:
    return SqliteIngestionRecordRepository(SqliteDatabase.open(directory / "coach.sqlite3"))


@pytest.fixture(params=[_in_memory, _sqlite], ids=["in-memory", "sqlite"])
def repository(request: pytest.FixtureRequest, tmp_path: Path) -> IngestionRecordRepository:
    factory: Callable[[Path], IngestionRecordRepository] = request.param
    return factory(tmp_path)


@pytest.mark.req("INT-FIL-004", "NFR-REL-001")
def test_records_are_stored_and_found_by_hash(repository: IngestionRecordRepository) -> None:
    rejected = _record(
        1,
        outcome=IngestionOutcome.REJECTED,
        reason=RejectionReason.PARSE_FAILED,
        detail="CRC_ERROR",
    )
    imported = _record(2)
    duplicate = _record(3, outcome=IngestionOutcome.DUPLICATE, duplicate_of=UUID(int=2))
    for record in (rejected, imported, duplicate):
        repository.add(SCOPE_A, record)
    assert repository.get(SCOPE_A, UUID(int=1)) == rejected
    assert repository.find_imported(SCOPE_A, SHA_A) == imported
    assert repository.find_imported(SCOPE_A, SHA_B) is None
    assert repository.recent(SCOPE_A, 2) == [duplicate, imported]
    with pytest.raises(ConcurrencyConflictError):
        repository.add(SCOPE_A, imported)


@pytest.mark.req("NFR-REL-001")
def test_idempotency_keys_are_unique_per_athlete(repository: IngestionRecordRepository) -> None:
    keyed = repository.add(SCOPE_A, _record(1, idempotency_key="upload-1"))
    assert repository.find_by_idempotency_key(SCOPE_A, "upload-1") == keyed
    assert repository.find_by_idempotency_key(SCOPE_A, "upload-2") is None
    with pytest.raises(ConcurrencyConflictError):
        repository.add(SCOPE_A, _record(2, idempotency_key="upload-1"))
    theirs = _record(3, athlete_id=OTHER_ATHLETE, idempotency_key="upload-1")
    assert repository.add(SCOPE_B, theirs) == theirs


@pytest.mark.req("SEC-IDOR-001", "SEC-AUTHZ-001")
def test_records_are_owner_scoped(repository: IngestionRecordRepository) -> None:
    repository.add(SCOPE_B, _record(1, athlete_id=OTHER_ATHLETE, idempotency_key="k"))
    with pytest.raises(NotFoundError):
        repository.get(SCOPE_A, UUID(int=1))
    assert repository.find_imported(SCOPE_A, SHA_A) is None
    assert repository.find_by_idempotency_key(SCOPE_A, "k") is None
    assert repository.recent(SCOPE_A, 10) == []
    with pytest.raises(OwnershipError):
        repository.add(SCOPE_A, _record(2, athlete_id=OTHER_ATHLETE))


def _provider(number: int, object_id: str, **changes: Any) -> IngestionRecord:
    return _record(
        number,
        origin=SourceOrigin.GARMIN_PERSONAL_API,
        provider="garmin",
        provider_object_id=object_id,
        malware_scan=MalwareScan.NOT_APPLICABLE,
        detected_type=DetectedType.JSON,
        **changes,
    )


@pytest.mark.req("INT-GAR-007", "NFR-REL-001")
def test_records_of_a_provider_object_are_found_oldest_first(
    repository: IngestionRecordRepository,
) -> None:
    rejected = _provider(
        1, "1001", outcome=IngestionOutcome.REJECTED, reason=RejectionReason.SCHEMA_INVALID
    )
    other = _provider(2, "1002")
    again = _provider(3, "1001", sha256=SHA_B)
    for record in (rejected, other, again):
        repository.add(SCOPE_A, record)
    repository.add(SCOPE_A, _record(4))
    assert repository.for_provider_object(SCOPE_A, "garmin", "1001") == [rejected, again]
    assert repository.for_provider_object(SCOPE_A, "garmin", "9999") == []
    assert repository.for_provider_object(SCOPE_B, "garmin", "1001") == []


@pytest.mark.req("INT-FIL-004")
def test_database_rejects_rewriting_a_record(tmp_path: Path) -> None:
    database = SqliteDatabase.open(tmp_path / "coach.sqlite3")
    SqliteIngestionRecordRepository(database).add(SCOPE_A, _record(1))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"), database.write() as connection:
        connection.execute("UPDATE ingestion_records SET outcome = 'REJECTED'")
    assert replace(_record(1)).outcome is IngestionOutcome.IMPORTED
