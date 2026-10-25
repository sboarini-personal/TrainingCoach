"""File format adapter: detection, archives and sandboxed decoding (implements FileFormats).

Stages 2 and 3 (detection), archive member reading, and stages 8 to 11 (sandboxed parser,
strict output decoding, semantic validation and normalisation).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from coach.application.files import ArchiveMember, DecodedSession, FileRejectedError
from coach.domain.ingestion.limits import IngestionLimits
from coach.domain.ingestion.record import RejectionReason
from coach.domain.raw.source import DetectedType
from coach.integrations.files import archive, detection
from coach.integrations.files.normalise import normalise
from coach.integrations.files.output import decode_response
from coach.integrations.files.worker import PROTOCOL


class ParserRunner(Protocol):
    """Runs a parser request: the process sandbox in production (ParserSandbox)."""

    def run(
        self,
        request: Mapping[str, Any],
        *,
        timeout_seconds: float,
        memory_bytes: int,
        max_output_bytes: int,
    ) -> dict[str, Any]: ...


class LocalFileFormats:
    def __init__(self, sandbox: ParserRunner) -> None:
        self._sandbox = sandbox

    def detect(self, filename: str | None, data: bytes) -> DetectedType:
        return detection.detect(filename, data)

    def member_type(self, member: ArchiveMember) -> DetectedType:
        detected = detection.EXTENSIONS[member.extension]
        detection.check_content(detected, member.data)
        return detected

    def members(self, data: bytes, limits: IngestionLimits) -> Sequence[ArchiveMember]:
        return archive.read_members(data, limits)

    def decode(
        self,
        path: Path,
        detected_type: DetectedType,
        limits: IngestionLimits,
        *,
        received_at: datetime,
    ) -> Sequence[DecodedSession]:
        if detected_type not in {
            DetectedType.FIT,
            DetectedType.TCX,
            DetectedType.GPX,
            DetectedType.CSV,
        }:
            raise FileRejectedError(RejectionReason.CONTENT_MISMATCH, "NOT_A_FILE_FORMAT")
        response = self._sandbox.run(
            {
                "protocol": PROTOCOL,
                "format": detected_type.value,
                "path": str(path.resolve()),
                "limits": {
                    "records": limits.records,
                    "activities": limits.activities,
                    "xmlDepth": limits.xml_depth,
                    "xmlElements": limits.xml_elements,
                    "memoryBytes": limits.parser_memory_bytes,
                },
            },
            timeout_seconds=limits.parser_timeout_seconds,
            memory_bytes=limits.parser_memory_bytes,
            max_output_bytes=limits.parser_output_bytes,
        )
        parsed = decode_response(response, detected_type, max_sessions=limits.activities)
        return normalise(parsed, received_at=received_at)
