"""Shared helpers for contract documents (spec/README.md conventions).

A tracked field that was never recorded is omitted; one recorded as UNKNOWN is
``{"value": null, "provenance": ...}``. Timestamps are UTC with ``Z``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, date, datetime, time
from enum import Enum
from typing import Any, Final
from uuid import UUID

from coach.domain.common.evidence import EvidenceKind, EvidenceRef
from coach.domain.common.provenance import FieldValue, Provenance, ValueSource
from coach.domain.common.time import ensure_utc

SCHEMA_VERSION: Final = "1.0.0"
_SUPPORTED_VERSION = re.compile(r"^1\.[0-9]+\.[0-9]+$")

type Json = dict[str, Any]
type Document = Mapping[str, Any]


class UnsupportedSchemaVersionError(ValueError):
    """The document's schema major version is not supported by this code (NFR-REL-004)."""


def check_schema_version(document: Document) -> None:
    version = document.get("schemaVersion")
    if not isinstance(version, str) or not _SUPPORTED_VERSION.match(version):
        raise UnsupportedSchemaVersionError(f"unsupported schemaVersion: {version!r}")


def instant(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def optional_instant(value: datetime | None) -> str | None:
    return None if value is None else instant(value)


def parse_instant(text: str) -> datetime:
    return ensure_utc(datetime.fromisoformat(text))


def parse_optional_instant(text: str | None) -> datetime | None:
    return None if text is None else parse_instant(text)


def optional_date(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def parse_optional_date(text: str | None) -> date | None:
    return None if text is None else date.fromisoformat(text)


def clock(value: time) -> str:
    return value.strftime("%H:%M")


def parse_clock(text: str) -> time:
    return time.fromisoformat(text)


def minute_of_day(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def parse_minute_of_day(text: str) -> int:
    parsed = parse_clock(text)
    return parsed.hour * 60 + parsed.minute


def sorted_values(values: Iterable[Enum]) -> list[str]:
    return sorted(str(value.value) for value in values)


def optional_uuid(value: UUID | None) -> str | None:
    return None if value is None else str(value)


def parse_optional_uuid(text: str | None) -> UUID | None:
    return None if text is None else UUID(text)


def provenance(value: Provenance) -> Json:
    return {
        "source": value.source.value,
        "recordedAt": instant(value.recorded_at),
        "provider": value.provider,
        "metricId": value.metric_id,
    }


def parse_provenance(document: Document) -> Provenance:
    return Provenance(
        source=ValueSource(document["source"]),
        recorded_at=parse_instant(document["recordedAt"]),
        provider=document["provider"],
        metric_id=document["metricId"],
    )


def _as_is(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _identity(value: Any) -> Any:
    return value


def put[T](
    target: Json, key: str, field: FieldValue[T] | None, convert: Callable[[T], Any] = _as_is
) -> None:
    """Add a tracked field; omit it when it was never recorded."""
    if field is None:
        return
    raw = field.value
    value = None if raw is None else convert(raw)
    target[key] = {"value": value, "provenance": provenance(field.provenance)}


def take[T](
    container: Document, key: str, convert: Callable[[Any], T] = _identity
) -> FieldValue[T] | None:
    """Read a tracked field; ``None`` when it was never recorded."""
    entry = container.get(key)
    if entry is None:
        return None
    raw = entry["value"]
    return FieldValue(None if raw is None else convert(raw), parse_provenance(entry["provenance"]))


def evidence(reference: EvidenceRef) -> Json:
    document: Json = {"kind": reference.kind.value, "id": str(reference.ref_id)}
    if reference.field is not None:
        document["field"] = reference.field
    if reference.note is not None:
        document["note"] = reference.note
    return document


def parse_evidence(document: Document) -> EvidenceRef:
    return EvidenceRef(
        kind=EvidenceKind(document["kind"]),
        ref_id=UUID(document["id"]),
        field=document.get("field"),
        note=document.get("note"),
    )
