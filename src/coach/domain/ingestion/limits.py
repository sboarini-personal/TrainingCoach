"""File ingestion limits (INT-FIL-003; spec/integration/files.spec.md section 3).

Every limit is configurable and must be positive: there is never an unlimited mode.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Final

from coach.domain.common.errors import DomainValidationError
from coach.domain.raw.source import DetectedType

MIB: Final = 1024 * 1024


@dataclass(frozen=True, slots=True, kw_only=True)
class IngestionLimits:
    fit_bytes: int = 32 * MIB
    xml_bytes: int = 64 * MIB
    csv_bytes: int = 16 * MIB
    zip_bytes: int = 64 * MIB
    zip_uncompressed_bytes: int = 256 * MIB
    zip_ratio: int = 100
    zip_members: int = 50
    records: int = 2_000_000
    activities: int = 500
    xml_depth: int = 64
    xml_elements: int = 5_000_000
    parser_output_bytes: int = 256 * MIB
    parser_timeout_seconds: float = 60.0
    parser_memory_bytes: int = 1024 * MIB

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if isinstance(value, bool) or not isinstance(value, int | float) or not value > 0:
                raise DomainValidationError(f"limit {item.name} must be a positive number")

    def max_bytes(self, detected_type: DetectedType) -> int:
        """Stage 4: the size limit of a detected format."""
        sizes = {
            DetectedType.FIT: self.fit_bytes,
            DetectedType.TCX: self.xml_bytes,
            DetectedType.GPX: self.xml_bytes,
            DetectedType.CSV: self.csv_bytes,
            DetectedType.ZIP: self.zip_bytes,
        }
        if detected_type not in sizes:
            raise DomainValidationError(f"no size limit for {detected_type}")
        return sizes[detected_type]

    @property
    def max_upload_bytes(self) -> int:
        """Stage 1: the largest per-format limit."""
        return max(self.fit_bytes, self.xml_bytes, self.csv_bytes, self.zip_bytes)
