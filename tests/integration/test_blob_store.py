"""Content-addressed RAW blob store, partitioned per athlete (FR-ACT-002, SEC-UPL-005)."""

import hashlib
import stat
from pathlib import Path

import pytest

from coach.application.errors import NotFoundError
from coach.application.ownership import OwnerScope
from coach.domain.common.errors import DomainValidationError
from coach.infrastructure.blobs import BlobIntegrityError, FileBlobStore
from samples import ATHLETE, OTHER_ATHLETE

SCOPE_A = OwnerScope(ATHLETE)
SCOPE_B = OwnerScope(OTHER_ATHLETE)
DATA = b"synthetic FIT-like payload \x00\x01\x02"
SHA = hashlib.sha256(DATA).hexdigest()


@pytest.fixture
def store(tmp_path: Path) -> FileBlobStore:
    return FileBlobStore(tmp_path / "raw")


@pytest.mark.req("FR-ACT-002")
def test_blobs_are_content_addressed_per_athlete(store: FileBlobStore, tmp_path: Path) -> None:
    assert store.put(SCOPE_A, DATA) == SHA
    assert store.get(SCOPE_A, SHA) == DATA
    assert (tmp_path / "raw" / str(ATHLETE) / SHA[:2] / SHA).is_file()


@pytest.mark.req("FR-ACT-002", "NFR-REL-001")
def test_storing_the_same_bytes_twice_keeps_one_unchanged_file(
    store: FileBlobStore, tmp_path: Path
) -> None:
    store.put(SCOPE_A, DATA)
    path = tmp_path / "raw" / str(ATHLETE) / SHA[:2] / SHA
    before = path.stat().st_mtime_ns
    assert store.put(SCOPE_A, DATA) == SHA
    assert path.stat().st_mtime_ns == before
    assert len(list(path.parent.iterdir())) == 1


@pytest.mark.req("FR-ACT-002")
def test_blobs_are_read_only_once_written(store: FileBlobStore, tmp_path: Path) -> None:
    store.put(SCOPE_A, DATA)
    mode = (tmp_path / "raw" / str(ATHLETE) / SHA[:2] / SHA).stat().st_mode
    assert not mode & stat.S_IWUSR


@pytest.mark.req("SEC-IDOR-001")
def test_another_athlete_cannot_read_or_detect_a_blob(store: FileBlobStore) -> None:
    store.put(SCOPE_A, DATA)
    assert not store.exists(SCOPE_B, SHA)
    with pytest.raises(NotFoundError):
        store.get(SCOPE_B, SHA)


@pytest.mark.req("SEC-UPL-005")
@pytest.mark.parametrize("reference", ["../" + "a" * 61, "A" * 64, "a" * 63, "..", ""])
def test_malformed_references_never_reach_the_file_system(
    store: FileBlobStore, reference: str
) -> None:
    with pytest.raises(DomainValidationError):
        store.get(SCOPE_A, reference)


@pytest.mark.req("FR-ACT-002")
def test_corrupted_blob_is_detected_on_read(store: FileBlobStore, tmp_path: Path) -> None:
    store.put(SCOPE_A, DATA)
    path = tmp_path / "raw" / str(ATHLETE) / SHA[:2] / SHA
    path.chmod(stat.S_IWUSR | stat.S_IRUSR)
    path.write_bytes(b"tampered")
    with pytest.raises(BlobIntegrityError):
        store.get(SCOPE_A, SHA)


def test_missing_blob_is_not_found(store: FileBlobStore) -> None:
    with pytest.raises(NotFoundError):
        store.get(SCOPE_A, SHA)
