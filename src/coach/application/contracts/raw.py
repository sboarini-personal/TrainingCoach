"""RawSource <-> raw-source contract (urn:coach:schema:raw-source:1)."""

from __future__ import annotations

from uuid import UUID

from coach.application.contracts._json import (
    SCHEMA_VERSION,
    Document,
    Json,
    instant,
    parse_instant,
)
from coach.domain.raw.source import DetectedType, MalwareScan, RawSource, SourceOrigin


def raw_source_to_contract(source: RawSource) -> Json:
    return {
        "schemaVersion": SCHEMA_VERSION,
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
    )
