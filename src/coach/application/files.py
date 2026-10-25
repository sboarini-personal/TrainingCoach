"""File ingestion port types shared by the ingestion service and the file adapters.

The adapters (coach.integrations.files) know file formats; the ingestion service
(coach.application.ingestion) knows the pipeline order, limits and persistence
(spec/integration/files.spec.md sections 2 and 6).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from coach.application.errors import ApplicationError
from coach.domain.activity.streams import TimeSeries
from coach.domain.common.quality import Anomaly
from coach.domain.ingestion.limits import IngestionLimits
from coach.domain.ingestion.record import RejectionReason, check_code
from coach.domain.raw.source import DetectedType, MalwareScan


class FileRejectedError(ApplicationError):
    """A pipeline stage rejected the upload; carries codes only, never file content."""

    def __init__(
        self,
        reason: RejectionReason,
        detail: str | None = None,
        *,
        member: int | None = None,
    ) -> None:
        super().__init__(f"{reason.value}{'' if detail is None else f' ({detail})'}")
        self.reason = reason
        self.detail = check_code(detail, "detail")
        self.member = member

    def in_member(self, member: int) -> FileRejectedError:
        return FileRejectedError(self.reason, self.detail, member=member)


@dataclass(frozen=True, slots=True)
class ArchiveMember:
    """An archive member's bytes and extension; its name is never kept (untrusted)."""

    index: int
    extension: str
    data: bytes


@dataclass(frozen=True, slots=True)
class DecodedSession:
    """One session normalised to canonical field values, ready to become an observation."""

    part: int
    values: Mapping[str, object]
    normaliser: str
    normaliser_version: str
    source_paths: Mapping[str, str] = field(default_factory=dict)
    anomalies: tuple[Anomaly, ...] = ()
    time_series: TimeSeries | None = None


class FileFormats(Protocol):
    """Format knowledge: content signatures, archives and sandboxed decoding.

    Every method raises FileRejectedError with the stage's reason code.
    """

    def detect(self, filename: str | None, data: bytes) -> DetectedType:
        """Stages 2 and 3: allowed extension and matching content."""
        ...

    def members(self, data: bytes, limits: IngestionLimits) -> Sequence[ArchiveMember]:
        """Archive members read in memory within the archive limits."""
        ...

    def member_type(self, member: ArchiveMember) -> DetectedType:
        """Stage 3 for an archive member (its extension was checked when it was read)."""
        ...

    def decode(
        self,
        path: Path,
        detected_type: DetectedType,
        limits: IngestionLimits,
        *,
        received_at: datetime,
    ) -> Sequence[DecodedSession]:
        """Stages 8 to 11 for a quarantined file."""
        ...


class Quarantine(Protocol):
    """Holding area outside the blob store, under random names (INT-FIL-002 stage 5)."""

    def put(self, data: bytes) -> Path: ...

    def discard(self, path: Path) -> None: ...


class MalwareScanner(Protocol):
    """Scan hook (SEC-UPL-006); returns CLEAN or NOT_SCANNED, or raises FileRejectedError."""

    def scan(self, path: Path) -> MalwareScan: ...
