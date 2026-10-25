"""AT-ZIP-001: hostile archives are rejected in memory, without extraction (SEC-UPL-003)."""

import io
import stat
import zipfile
from dataclasses import fields

import pytest

from coach.application.files import ArchiveMember, FileRejectedError
from coach.domain.ingestion.limits import IngestionLimits
from coach.domain.ingestion.record import RejectionReason
from coach.integrations.files.archive import declared_entries, read_members
from file_samples import fit_file, gpx_file, zip_file

FIT = fit_file()
LIMITS = IngestionLimits()


def _rejected(data: bytes, limits: IngestionLimits = LIMITS) -> FileRejectedError:
    with pytest.raises(FileRejectedError) as raised:
        read_members(data, limits)
    return raised.value


def _zip_with(info: zipfile.ZipInfo, data: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(info, data)
    return buffer.getvalue()


def _encrypted(data: bytes) -> bytes:
    """Set the 'encrypted' flag in the local and central headers (zipfile cannot write it)."""
    patched = bytearray(data)
    for signature, flag_offset in ((b"PK", 6), (b"PK", 8)):
        position = patched.find(signature)
        patched[position + flag_offset] |= 0x1
    return bytes(patched)


@pytest.mark.req("SEC-UPL-003", "INT-FIL-001")
def test_members_are_returned_in_memory_without_their_names() -> None:
    members = read_members(zip_file({"folder/a.fit": FIT, "b.GPX": gpx_file()}), LIMITS)
    assert [(member.index, member.extension) for member in members] == [(0, ".fit"), (1, ".gpx")]
    assert members[0].data == FIT
    assert {item.name for item in fields(ArchiveMember)} == {"index", "extension", "data"}


@pytest.mark.req("SEC-UPL-003")
@pytest.mark.acceptance("AT-ZIP-001")
@pytest.mark.parametrize(
    ("name", "detail"),
    [
        ("../escape.fit", "ZIP_PATH"),
        ("a/../../escape.fit", "ZIP_PATH"),
        ("/etc/escape.fit", "ZIP_PATH"),
        ("C:/Windows/escape.fit", "ZIP_PATH"),
        ("..\\escape.fit", "ZIP_PATH"),
        ("inner.zip", "ZIP_NESTED"),
        ("inner.tar", "ZIP_NESTED"),
        ("inner.7z", "ZIP_NESTED"),
    ],
)
def test_traversal_and_nested_archives_are_refused(name: str, detail: str) -> None:
    rejection = _rejected(zip_file({"ok.fit": FIT, name: FIT}))
    assert (rejection.reason, rejection.detail, rejection.member) == (
        RejectionReason.CONTENT_MISMATCH,
        detail,
        1,
    )


@pytest.mark.req("SEC-UPL-003")
def test_disallowed_member_extensions_reject_the_archive() -> None:
    rejection = _rejected(zip_file({"run.fit": FIT, "readme.exe": b"MZ"}))
    assert (rejection.reason, rejection.detail) == (
        RejectionReason.EXTENSION_NOT_ALLOWED,
        "ZIP_MEMBER",
    )


@pytest.mark.req("SEC-UPL-003")
@pytest.mark.acceptance("AT-ZIP-001")
def test_symlinks_and_encrypted_members_are_refused() -> None:
    link = zipfile.ZipInfo("link.fit")
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    assert _rejected(_zip_with(link, b"/etc/passwd")).detail == "ZIP_SYMLINK"
    assert _rejected(_encrypted(zip_file({"secret.fit": FIT}))).detail == "ZIP_ENCRYPTED"


@pytest.mark.req("SEC-UPL-003", "INT-FIL-003")
@pytest.mark.acceptance("AT-ZIP-001")
def test_zip_bomb_is_refused_before_decompressing() -> None:
    bomb = zip_file({"bomb.gpx": b"\x00" * (20 * 1024 * 1024)})
    assert len(bomb) < 64 * 1024
    rejection = _rejected(bomb)
    assert (rejection.reason, rejection.detail) == (RejectionReason.LIMIT_EXCEEDED, "ZIP_RATIO")


@pytest.mark.req("SEC-UPL-003", "INT-FIL-003")
def test_ratio_and_sizes_are_also_checked_while_reading() -> None:
    padded = zip_file({"a.gpx": gpx_file() * 50}, compression=zipfile.ZIP_STORED)
    lying = bytearray(padded)
    # Shrink the declared size in the local and central headers: zipfile stops at the
    # declared size and its CRC check fails, so nothing beyond it is ever returned.
    size = len(gpx_file() * 50).to_bytes(4, "little")
    lying = bytearray(bytes(lying).replace(size, (100).to_bytes(4, "little")))
    assert _rejected(bytes(lying)).detail == "ZIP_INVALID"
    small = IngestionLimits(zip_uncompressed_bytes=1024)
    assert _rejected(zip_file({"a.fit": FIT}), small).detail == "ZIP_TOTAL"
    tight = IngestionLimits(fit_bytes=1024)
    assert _rejected(zip_file({"a.fit": FIT}), tight).reason is RejectionReason.TOO_LARGE


@pytest.mark.req("SEC-UPL-003", "INT-FIL-003")
@pytest.mark.acceptance("AT-ZIP-001")
def test_too_many_members_are_refused_from_the_directory_record() -> None:
    crowded = zip_file({f"run{index}.fit": FIT for index in range(51)})
    assert declared_entries(crowded) == 51
    rejection = _rejected(crowded)
    assert (rejection.reason, rejection.detail) == (RejectionReason.LIMIT_EXCEEDED, "ZIP_MEMBERS")
    assert read_members(crowded, IngestionLimits(zip_members=60))


@pytest.mark.req("SEC-UPL-003")
@pytest.mark.parametrize(
    ("data", "detail"),
    [
        (b"PK\x03\x04 not really a zip", "ZIP_INVALID"),
        (zip_file({}), "ZIP_EMPTY"),
        (zip_file({"folder/": b""}), "ZIP_EMPTY"),
    ],
)
def test_broken_or_empty_archives_are_refused(data: bytes, detail: str) -> None:
    assert _rejected(data).detail == detail


@pytest.mark.req("SEC-UPL-003")
@pytest.mark.acceptance("AT-ZIP-001")
def test_bomb_is_refused_from_its_headers_without_decompressing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bomb = zip_file({"bomb.gpx": b"\x00" * (20 * 1024 * 1024)})

    def no_decompression(*args: object, **kwargs: object) -> None:
        raise AssertionError("member was decompressed")

    monkeypatch.setattr(zipfile.ZipFile, "open", no_decompression)
    assert _rejected(bomb).detail == "ZIP_RATIO"


@pytest.mark.req("SEC-UPL-003")
@pytest.mark.acceptance("AT-ZIP-001")
def test_member_count_is_refused_before_reading_the_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crowded = zip_file({f"run{index}.fit": b"x" for index in range(51)})

    def no_directory(*args: object, **kwargs: object) -> None:
        raise AssertionError("central directory was parsed")

    monkeypatch.setattr(zipfile.ZipFile, "__init__", no_directory)
    assert _rejected(crowded).detail == "ZIP_MEMBERS"
