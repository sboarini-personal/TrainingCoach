"""Ingestion records: one immutable record per upload attempt (INT-FIL-002, INT-FIL-004).

Records hold codes, hashes and identifiers only; the original filename is sanitised display
text (spec/integration/files.spec.md section 9).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.time import ensure_utc
from coach.domain.raw.source import (
    DetectedType,
    MalwareScan,
    clean_filename,
    require_sha256,
)

_CODE: Final = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_MIME: Final = re.compile(r"^[A-Za-z0-9!#$&^_.+-]{1,64}/[A-Za-z0-9!#$&^_.+-]{1,64}$")
_IDEMPOTENCY_KEY: Final = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
FILE_TYPES: Final = frozenset(
    {DetectedType.FIT, DetectedType.TCX, DetectedType.GPX, DetectedType.CSV}
)


class RejectionReason(StrEnum):
    TOO_LARGE = "TOO_LARGE"
    EXTENSION_NOT_ALLOWED = "EXTENSION_NOT_ALLOWED"
    CONTENT_MISMATCH = "CONTENT_MISMATCH"
    MALWARE_SUSPECTED = "MALWARE_SUSPECTED"
    PARSE_FAILED = "PARSE_FAILED"
    PARSE_TIMEOUT = "PARSE_TIMEOUT"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    SEMANTIC_INVALID = "SEMANTIC_INVALID"
    NORMALIZATION_FAILED = "NORMALIZATION_FAILED"
    PERSISTENCE_FAILED = "PERSISTENCE_FAILED"


class IngestionOutcome(StrEnum):
    IMPORTED = "IMPORTED"
    DUPLICATE = "DUPLICATE"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class ActivityOutcome(StrEnum):
    CREATED = "CREATED"
    DUPLICATE = "DUPLICATE"
    NEW_VERSION = "NEW_VERSION"
    LINKED = "LINKED"
    AMBIGUOUS = "AMBIGUOUS"


def check_code(value: str | None, name: str) -> str | None:
    if value is not None and not _CODE.match(value):
        raise DomainValidationError(f"{name} must be an upper-case code")
    return value


def check_idempotency_key(value: str | None) -> str | None:
    if value is not None and not _IDEMPOTENCY_KEY.match(value):
        raise DomainValidationError("invalid idempotency key")
    return value


def clean_mime_type(value: str | None) -> str | None:
    """A declared MIME type when well-formed; anything else is dropped (never trusted)."""
    if value is None:
        return None
    candidate = value.split(";", 1)[0].strip()
    return candidate if _MIME.match(candidate) else None


@dataclass(frozen=True, slots=True)
class StoredFile:
    """A file of the upload (the upload itself, or an archive member) and its RawSource."""

    index: int
    detected_type: DetectedType
    byte_size: int
    sha256: str
    raw_source_id: UUID
    sessions: int

    def __post_init__(self) -> None:
        if self.index < 0 or self.byte_size < 0 or self.sessions < 0:
            raise DomainValidationError("file index, size and sessions are not negative")
        if self.detected_type not in FILE_TYPES:
            raise DomainValidationError("stored files are FIT, TCX, GPX or CSV")
        require_sha256(self.sha256)


@dataclass(frozen=True, slots=True)
class ImportedActivity:
    activity_id: UUID
    raw_source_id: UUID
    part: int
    outcome: ActivityOutcome

    def __post_init__(self) -> None:
        if self.part < 0:
            raise DomainValidationError("part must not be negative")


@dataclass(frozen=True, slots=True, kw_only=True)
class IngestionRecord:
    ingestion_id: UUID
    athlete_id: UUID
    received_at: datetime
    completed_at: datetime
    outcome: IngestionOutcome
    detected_type: DetectedType
    byte_size: int
    sha256: str | None
    malware_scan: MalwareScan | None = None
    original_filename: str | None = None
    declared_mime_type: str | None = None
    reason: RejectionReason | None = None
    detail: str | None = None
    failed_file_index: int | None = None
    files: tuple[StoredFile, ...] = ()
    activities: tuple[ImportedActivity, ...] = ()
    duplicate_of: UUID | None = None
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "received_at", ensure_utc(self.received_at))
        object.__setattr__(self, "completed_at", ensure_utc(self.completed_at))
        if self.completed_at < self.received_at:
            raise DomainValidationError("an import completes after it was received")
        if self.byte_size < 0:
            raise DomainValidationError("byte size must not be negative")
        if self.sha256 is not None:
            require_sha256(self.sha256)
        if self.malware_scan is MalwareScan.NOT_APPLICABLE:
            raise DomainValidationError("uploads are CLEAN or NOT_SCANNED")
        object.__setattr__(self, "original_filename", clean_filename(self.original_filename))
        object.__setattr__(self, "declared_mime_type", clean_mime_type(self.declared_mime_type))
        check_code(self.detail, "detail")
        check_idempotency_key(self.idempotency_key)
        if self.failed_file_index is not None and self.failed_file_index < 0:
            raise DomainValidationError("failed file index must not be negative")
        self._check_outcome()

    def _check_outcome(self) -> None:
        succeeded = self.outcome in {IngestionOutcome.IMPORTED, IngestionOutcome.DUPLICATE}
        if succeeded:
            if (self.reason, self.detail, self.failed_file_index) != (None, None, None):
                raise DomainValidationError("successful imports carry no reason")
            if self.sha256 is None:
                raise DomainValidationError("successful imports record their hash")
        elif self.reason is None:
            raise DomainValidationError("rejected and failed imports carry a reason")
        failed = self.outcome is IngestionOutcome.FAILED
        if failed != (self.reason is RejectionReason.PERSISTENCE_FAILED):
            raise DomainValidationError("only FAILED imports are PERSISTENCE_FAILED")
        if self.outcome is IngestionOutcome.REJECTED and self.activities:
            raise DomainValidationError("rejected imports create no activities")
        if (self.outcome is IngestionOutcome.DUPLICATE) != (self.duplicate_of is not None):
            raise DomainValidationError("duplicates, and only they, name the earlier import")
        if self.duplicate_of == self.ingestion_id:
            raise DomainValidationError("an import is not a duplicate of itself")

    @property
    def entity_id(self) -> UUID:
        return self.ingestion_id

    @property
    def succeeded(self) -> bool:
        return self.outcome in {IngestionOutcome.IMPORTED, IngestionOutcome.DUPLICATE}
