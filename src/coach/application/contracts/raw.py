"""RawSource <-> raw-source contract (urn:coach:schema:raw-source:1)."""

from __future__ import annotations

from datetime import date
from typing import Any, Final
from uuid import UUID

from coach.application.contracts._json import (
    Document,
    Json,
    instant,
    parse_instant,
)
from coach.domain.common.time import DateRange
from coach.domain.raw.source import DetectedType, MalwareScan, RawSource, SourceOrigin

# 1.1.0 added the request window of provider payloads (INT-GAR-009).
RAW_SOURCE_SCHEMA_VERSION: Final = "1.1.0"


def date_window(window: DateRange | None) -> Json | None:
    if window is None:
        return None
    return {"start": window.start.isoformat(), "end": window.end.isoformat()}


def parse_date_window(document: Any) -> DateRange | None:
    if document is None:
        return None
    return DateRange(date.fromisoformat(document["start"]), date.fromisoformat(document["end"]))


def raw_source_to_contract(source: RawSource) -> Json:
    return {
        "schemaVersion": RAW_SOURCE_SCHEMA_VERSION,
        "rawSourceId": str(source.raw_source_id),
        "athleteId": str(source.athlete_id),
        "origin": source.origin.value,
        "provider": source.provider,
        "providerObjectId": source.provider_object_id,
        "category": source.category,
        "receivedAt": instant(source.received_at),
        "sha256": source.sha256,
        "byteSize": source.byte_size,
        "detectedType": source.detected_type.value,
        "originalFilename": source.original_filename,
        "malwareScan": source.malware_scan.value,
        "requestWindow": date_window(source.request_window),
    }


def raw_source_from_contract(document: Document) -> RawSource:
    return RawSource(
        raw_source_id=UUID(document["rawSourceId"]),
        athlete_id=UUID(document["athleteId"]),
        origin=SourceOrigin(document["origin"]),
        provider=document["provider"],
        provider_object_id=document["providerObjectId"],
        category=document["category"],
        received_at=parse_instant(document["receivedAt"]),
        sha256=document["sha256"],
        byte_size=document["byteSize"],
        detected_type=DetectedType(document["detectedType"]),
        original_filename=document["originalFilename"],
        malware_scan=MalwareScan(document["malwareScan"]),
        request_window=parse_date_window(document.get("requestWindow")),
    )
