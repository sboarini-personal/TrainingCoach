"""Shared helpers of the sandboxed parsers (spec/integration/files.spec.md section 7).

Parsers run inside the sandbox subprocess and build the parser-output document in the
file's own vocabulary and units. They report problems with ``ParserRejection`` codes only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final

OUTPUT_SCHEMA_VERSION: Final = "1.0.0"
SAMPLE_COLUMNS: Final = (
    "distanceMeters",
    "heartRate",
    "speedMps",
    "cadence",
    "powerWatts",
    "altitudeMeters",
)


class ParserReason(StrEnum):
    PARSE_FAILED = "PARSE_FAILED"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    SEMANTIC_INVALID = "SEMANTIC_INVALID"


class ParserRejection(Exception):  # noqa: N818 - a rejection, not an error of the parser
    def __init__(self, reason: ParserReason, detail: str) -> None:
        super().__init__(f"{reason.value} ({detail})")
        self.reason = reason
        self.detail = detail


def parse_failed(detail: str) -> ParserRejection:
    return ParserRejection(ParserReason.PARSE_FAILED, detail)


def limit_exceeded(detail: str) -> ParserRejection:
    return ParserRejection(ParserReason.LIMIT_EXCEEDED, detail)


def semantic_invalid(detail: str) -> ParserRejection:
    return ParserRejection(ParserReason.SEMANTIC_INVALID, detail)


@dataclass(frozen=True, slots=True)
class ParserLimits:
    records: int
    activities: int
    xml_depth: int
    xml_elements: int


class RecordCounter:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.count = 0

    def add(self, amount: int = 1) -> None:
        self.count += amount
        if self.count > self.limit:
            raise limit_exceeded("RECORDS")


def number(value: Any) -> float | None:
    """A finite number, or ``None`` for anything else (missing, invalid or non-numeric)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def text_number(text: str | None) -> float | None:
    if text is None or not text.strip():
        return None
    try:
        value = float(text.strip())
    except ValueError:
        raise parse_failed("INVALID_NUMBER") from None
    if not math.isfinite(value):
        raise parse_failed("INVALID_NUMBER")
    return value


def parse_time(text: str | None) -> tuple[datetime, int | None]:
    """An ISO 8601 instant and its UTC offset in minutes (``None`` for ``Z``).

    Naive times are refused: the local offset cannot be known (files spec 8.2).
    """
    if text is None or not text.strip():
        raise semantic_invalid("MISSING_TIME")
    raw = text.strip()
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        raise parse_failed("INVALID_TIME") from None
    offset = value.utcoffset()
    if offset is None:
        raise semantic_invalid("NAIVE_TIME")
    minutes = None if raw.endswith(("Z", "z")) else int(offset.total_seconds()) // 60
    return value.astimezone(UTC), minutes


def instant(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class SessionData:
    index: int
    start: datetime
    sport: str | None = None
    sub_sport: str | None = None
    manufacturer: str | None = None
    utc_offset_minutes: int | None = None
    summary: dict[str, float | None] = field(default_factory=dict)
    labels: dict[str, str | None] = field(default_factory=dict)
    texts: dict[str, str | None] = field(default_factory=dict)
    source_paths: dict[str, str] = field(default_factory=dict)
    offsets: list[float] = field(default_factory=list)
    columns: dict[str, list[float | None]] = field(
        default_factory=lambda: {name: [] for name in SAMPLE_COLUMNS}
    )

    def put(self, key: str, value: float | None, path: str) -> None:
        if value is not None:
            self.summary[key] = value
            self.source_paths[key] = path

    def sample(self, offset: float, **values: float | None) -> None:
        self.offsets.append(offset)
        for name in SAMPLE_COLUMNS:
            self.columns[name].append(values.get(name))

    def document(self) -> dict[str, Any]:
        samples: dict[str, Any] = {"offsetSeconds": self.offsets}
        for name, column in self.columns.items():
            if any(value is not None for value in column):
                samples[name] = column
        return {
            "index": self.index,
            "sport": _label(self.sport),
            "subSport": _label(self.sub_sport),
            "manufacturer": self.manufacturer,
            "startTime": instant(self.start),
            "utcOffsetMinutes": self.utc_offset_minutes,
            "summary": self.summary,
            "labels": self.labels,
            "texts": self.texts,
            "sourcePaths": self.source_paths,
            "samples": samples,
        }


def _label(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return value.strip()[:64]


def output(
    fmt: str,
    parser: str,
    version: str,
    *,
    records: int,
    sessions: list[SessionData],
    limits: ParserLimits,
    file_type: str | None = None,
) -> dict[str, Any]:
    if len(sessions) > limits.activities:
        raise limit_exceeded("ACTIVITIES")
    return {
        "schemaVersion": OUTPUT_SCHEMA_VERSION,
        "format": fmt,
        "parser": {"name": parser, "version": version},
        "recordCount": records,
        "fileType": file_type,
        "sessions": [session.document() for session in sessions],
    }
