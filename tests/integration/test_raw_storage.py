"""Application service storing raw bytes: blob + immutable metadata, idempotent (NFR-REL-001)."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from coach.application.ownership import OwnerScope
from coach.application.raw_storage import RawStorage
from coach.domain.raw.source import DetectedType, MalwareScan, SourceOrigin
from coach.infrastructure.blobs import FileBlobStore
from coach.infrastructure.sqlite.database import SqliteDatabase
from coach.infrastructure.sqlite.raw_sources import SqliteRawSourceRepository
from samples import ATHLETE, OTHER_ATHLETE

NOW = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)
DATA = b"synthetic activity bytes"


@pytest.fixture
def storage(tmp_path: Path) -> RawStorage:
    counter = iter(range(1, 100))
    return RawStorage(
        FileBlobStore(tmp_path / "raw"),
        SqliteRawSourceRepository(SqliteDatabase.open(tmp_path / "coach.sqlite3")),
        clock=lambda: NOW,
        ids=lambda: UUID(int=next(counter)),
    )


def _store(storage: RawStorage, athlete: UUID, data: bytes = DATA) -> tuple[object, bool]:
    stored = storage.store(
        OwnerScope(athlete),
        data,
        origin=SourceOrigin.FILE_UPLOAD,
        category="ACTIVITY_FILE",
        detected_type=DetectedType.FIT,
        malware_scan=MalwareScan.NOT_SCANNED,
        original_filename="../../run.fit",
    )
    return stored.source, stored.created


@pytest.mark.req("FR-ACT-002", "INT-GAR-009")
def test_bytes_and_metadata_are_stored_together(storage: RawStorage) -> None:
    stored = storage.store(
        OwnerScope(ATHLETE),
        DATA,
        origin=SourceOrigin.FILE_UPLOAD,
        category="ACTIVITY_FILE",
        detected_type=DetectedType.FIT,
        malware_scan=MalwareScan.NOT_SCANNED,
        original_filename="../../run.fit",
    )
    source = stored.source
    assert stored.created
    assert source.sha256 == hashlib.sha256(DATA).hexdigest()
    assert source.byte_size == len(DATA)
    assert source.received_at == NOW
    assert source.original_filename == "run.fit"
    assert storage.read(OwnerScope(ATHLETE), source.raw_source_id) == DATA


@pytest.mark.req("NFR-REL-001", "FR-ACT-007")
def test_storing_the_same_bytes_again_is_a_no_op(storage: RawStorage) -> None:
    first, created = _store(storage, ATHLETE)
    again, created_again = _store(storage, ATHLETE)
    assert created
    assert not created_again
    assert again == first


@pytest.mark.req("SEC-IDOR-001")
def test_each_athlete_keeps_a_separate_copy(storage: RawStorage) -> None:
    mine, _ = _store(storage, ATHLETE)
    theirs, created = _store(storage, OTHER_ATHLETE)
    assert created
    assert mine != theirs
