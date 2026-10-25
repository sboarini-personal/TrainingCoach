"""ZIP containers read in memory with streaming limits (SEC-UPL-003, AT-ZIP-001).

Nothing is extracted to a path. Member names are inspected only to refuse absolute paths,
traversal, nested archives and disallowed extensions; they are never kept. Sizes and the
compression ratio are checked while decompressing, not only from the headers.
"""

from __future__ import annotations

import io
import re
import stat
import zipfile
from pathlib import PurePosixPath
from typing import Final

from coach.application.files import ArchiveMember, FileRejectedError
from coach.domain.ingestion.limits import IngestionLimits
from coach.domain.ingestion.record import RejectionReason
from coach.integrations.files.detection import EXTENSIONS

CHUNK: Final = 64 * 1024
ARCHIVE_EXTENSIONS: Final = frozenset(
    {".zip", ".gz", ".tgz", ".tar", ".7z", ".rar", ".bz2", ".xz", ".zst", ".jar"}
)
_DRIVE: Final = re.compile(r"^[A-Za-z]:")
_ENCRYPTED_FLAG: Final = 0x1
_END_OF_DIRECTORY: Final = b"PK"
_END_OF_DIRECTORY_SEARCH: Final = 22 + 65535


def declared_entries(data: bytes) -> int:
    """Entry count from the end-of-central-directory record, read before opening the archive.

    A large count is refused before ``zipfile`` builds its member list; ZIP64 archives
    (count 0xFFFF) are therefore refused too.
    """
    position = data.rfind(_END_OF_DIRECTORY, max(0, len(data) - _END_OF_DIRECTORY_SEARCH))
    if position < 0 or len(data) < position + 22:
        raise FileRejectedError(RejectionReason.CONTENT_MISMATCH, "ZIP_INVALID")
    return int.from_bytes(data[position + 10 : position + 12], "little")


def _reject(reason: RejectionReason, detail: str, member: int) -> FileRejectedError:
    return FileRejectedError(reason, detail, member=member)


def _member_extension(index: int, info: zipfile.ZipInfo) -> str:
    name = info.filename.replace("\\", "/")
    parts = name.split("/")
    if name.startswith("/") or _DRIVE.match(name) or ".." in parts:
        raise _reject(RejectionReason.CONTENT_MISMATCH, "ZIP_PATH", index)
    if stat.S_ISLNK(info.external_attr >> 16):
        raise _reject(RejectionReason.CONTENT_MISMATCH, "ZIP_SYMLINK", index)
    if info.flag_bits & _ENCRYPTED_FLAG:
        raise _reject(RejectionReason.CONTENT_MISMATCH, "ZIP_ENCRYPTED", index)
    extension = PurePosixPath(parts[-1]).suffix.lower()
    if extension in ARCHIVE_EXTENSIONS:
        raise _reject(RejectionReason.CONTENT_MISMATCH, "ZIP_NESTED", index)
    if extension not in EXTENSIONS:
        raise _reject(RejectionReason.EXTENSION_NOT_ALLOWED, "ZIP_MEMBER", index)
    return extension


def _read_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    index: int,
    *,
    member_limit: int,
    remaining_total: int,
    limits: IngestionLimits,
) -> bytes:
    ratio_limit = limits.zip_ratio * max(info.compress_size, 1)
    if info.file_size > member_limit:
        raise _reject(RejectionReason.TOO_LARGE, "ZIP_MEMBER_SIZE", index)
    if info.file_size > remaining_total:
        raise _reject(RejectionReason.LIMIT_EXCEEDED, "ZIP_TOTAL", index)
    if info.file_size > ratio_limit:
        raise _reject(RejectionReason.LIMIT_EXCEEDED, "ZIP_RATIO", index)
    buffer = bytearray()
    with archive.open(info) as stream:
        while chunk := stream.read(CHUNK):
            buffer.extend(chunk)
            if len(buffer) > member_limit:
                raise _reject(RejectionReason.TOO_LARGE, "ZIP_MEMBER_SIZE", index)
            if len(buffer) > remaining_total:
                raise _reject(RejectionReason.LIMIT_EXCEEDED, "ZIP_TOTAL", index)
            if len(buffer) > ratio_limit:
                raise _reject(RejectionReason.LIMIT_EXCEEDED, "ZIP_RATIO", index)
    return bytes(buffer)


def read_members(data: bytes, limits: IngestionLimits) -> list[ArchiveMember]:
    if declared_entries(data) > limits.zip_members:
        raise FileRejectedError(RejectionReason.LIMIT_EXCEEDED, "ZIP_MEMBERS")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError):
        raise FileRejectedError(RejectionReason.CONTENT_MISMATCH, "ZIP_INVALID") from None
    with archive:
        infos = archive.infolist()
        if len(infos) > limits.zip_members:
            raise FileRejectedError(RejectionReason.LIMIT_EXCEEDED, "ZIP_MEMBERS")
        members: list[ArchiveMember] = []
        total = 0
        for index, info in enumerate(infos):
            if info.is_dir():
                continue
            extension = _member_extension(index, info)
            try:
                content = _read_member(
                    archive,
                    info,
                    index,
                    member_limit=limits.max_bytes(EXTENSIONS[extension]),
                    remaining_total=limits.zip_uncompressed_bytes - total,
                    limits=limits,
                )
            except (zipfile.BadZipFile, EOFError, ValueError):
                raise _reject(RejectionReason.CONTENT_MISMATCH, "ZIP_INVALID", index) from None
            except NotImplementedError:
                raise _reject(RejectionReason.CONTENT_MISMATCH, "ZIP_COMPRESSION", index) from None
            total += len(content)
            members.append(ArchiveMember(index, extension, content))
    if not members:
        raise FileRejectedError(RejectionReason.CONTENT_MISMATCH, "ZIP_EMPTY")
    return members
