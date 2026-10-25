"""RawSource (FR-ACT-002; spec/functional/activities.spec.md section 1).

Fully immutable: the bytes are content-addressed and the metadata never changes. Import
outcomes are tracked by a separate ingestion record (P6).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.text import clean_optional_text
from coach.domain.common.time import ensure_utc

SHA256: Final = re.compile(r"^[a-f0-9]{64}$")
_CATEGORY = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_PROVIDER = re.compile(r"^[a-z0-9_]+$")
_PATH_SEPARATORS = re.compile(r"[\\/]")
MAX_FILENAME_LENGTH: Final = 255


class SourceOrigin(StrEnum):
    FILE_UPLOAD = "FILE_UPLOAD"
    GARMIN_PERSONAL_API = "GARMIN_PERSONAL_API"
    MANUAL_ENTRY = "MANUAL_ENTRY"


class DetectedType(StrEnum):
    FIT = "FIT"
    TCX = "TCX"
    GPX = "GPX"
    CSV = "CSV"
    ZIP = "ZIP"
    JSON = "JSON"
    UNKNOWN = "UNKNOWN"


class MalwareScan(StrEnum):
    CLEAN = "CLEAN"
    NOT_SCANNED = "NOT_SCANNED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


_ORIGIN_PROVIDER: Final = {SourceOrigin.GARMIN_PERSONAL_API: "garmin"}
_ORIGIN_SCANS: Final = {
    SourceOrigin.FILE_UPLOAD: frozenset({MalwareScan.CLEAN, MalwareScan.NOT_SCANNED}),
    SourceOrigin.GARMIN_PERSONAL_API: frozenset({MalwareScan.NOT_APPLICABLE}),
    SourceOrigin.MANUAL_ENTRY: frozenset({MalwareScan.NOT_APPLICABLE}),
}


def require_sha256(value: str) -> str:
    if not SHA256.match(value):
        raise DomainValidationError("invalid SHA-256 digest")
    return value


def clean_filename(name: str | None) -> str | None:
    """Display-only filename: last path component, no control characters, truncated."""
    if name is None:
        return None
    last = _PATH_SEPARATORS.split(name)[-1]
    cleaned = clean_optional_text(last, max_length=len(last) or 1, field="filename")
    if cleaned is None or cleaned in {".", ".."}:
        return None
    return cleaned[:MAX_FILENAME_LENGTH]


@dataclass(frozen=True, slots=True, kw_only=True)
class RawSource:
    raw_source_id: UUID
    athlete_id: UUID
    origin: SourceOrigin
    category: str
    received_at: datetime
    sha256: str
    byte_size: int
    detected_type: DetectedType
    malware_scan: MalwareScan
    provider: str | None = None
    provider_object_id: str | None = None
    original_filename: str | None = None

    def __post_init__(self) -> None:
        require_sha256(self.sha256)
        object.__setattr__(self, "received_at", ensure_utc(self.received_at))
        if self.byte_size < 0:
            raise DomainValidationError("byte size must not be negative")
        if not _CATEGORY.match(self.category):
            raise DomainValidationError(f"invalid category: {self.category!r}")
        if self.provider is not None and not _PROVIDER.match(self.provider):
            raise DomainValidationError(f"invalid provider: {self.provider!r}")
        if self.provider != _ORIGIN_PROVIDER.get(self.origin):
            raise DomainValidationError(
                f"{self.origin} has provider {_ORIGIN_PROVIDER.get(self.origin)}"
            )
        if self.malware_scan not in _ORIGIN_SCANS[self.origin]:
            raise DomainValidationError(f"{self.malware_scan} is not valid for {self.origin}")
        object.__setattr__(
            self,
            "provider_object_id",
            clean_optional_text(
                self.provider_object_id, max_length=100, field="provider object id"
            ),
        )
        object.__setattr__(self, "original_filename", clean_filename(self.original_filename))

    @property
    def entity_id(self) -> UUID:
        return self.raw_source_id
