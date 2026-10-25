"""IngestionRecord <-> ingestion-record contract; TimeSeries <-> time-series contract."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from coach.application.contracts._json import (
    SCHEMA_VERSION,
    Document,
    Json,
    instant,
    optional_uuid,
    parse_instant,
    parse_optional_uuid,
)
from coach.domain.activity.fields import CadenceUnit
from coach.domain.activity.streams import Column, TimeSeries
from coach.domain.ingestion.record import (
    ActivityOutcome,
    ImportedActivity,
    IngestionOutcome,
    IngestionRecord,
    RejectionReason,
    StoredFile,
)
from coach.domain.raw.source import DetectedType, MalwareScan


def ingestion_record_to_contract(record: IngestionRecord) -> Json:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "ingestionId": str(record.ingestion_id),
        "athleteId": str(record.athlete_id),
        "receivedAt": instant(record.received_at),
        "completedAt": instant(record.completed_at),
        "originalFilename": record.original_filename,
        "declaredMimeType": record.declared_mime_type,
        "detectedType": record.detected_type.value,
        "byteSize": record.byte_size,
        "sha256": record.sha256,
        "malwareScan": None if record.malware_scan is None else record.malware_scan.value,
        "outcome": record.outcome.value,
        "reasonCode": None if record.reason is None else record.reason.value,
        "detail": record.detail,
        "failedFileIndex": record.failed_file_index,
        "files": [
            {
                "index": item.index,
                "detectedType": item.detected_type.value,
                "byteSize": item.byte_size,
                "sha256": item.sha256,
                "rawSourceId": str(item.raw_source_id),
                "sessions": item.sessions,
            }
            for item in record.files
        ],
        "activities": [
            {
                "activityId": str(item.activity_id),
                "rawSourceId": str(item.raw_source_id),
                "part": item.part,
                "outcome": item.outcome.value,
            }
            for item in record.activities
        ],
        "duplicateOf": optional_uuid(record.duplicate_of),
        "idempotencyKey": record.idempotency_key,
    }


def ingestion_record_from_contract(document: Document) -> IngestionRecord:
    scan = document["malwareScan"]
    reason = document["reasonCode"]
    return IngestionRecord(
        ingestion_id=UUID(document["ingestionId"]),
        athlete_id=UUID(document["athleteId"]),
        received_at=parse_instant(document["receivedAt"]),
        completed_at=parse_instant(document["completedAt"]),
        original_filename=document["originalFilename"],
        declared_mime_type=document["declaredMimeType"],
        detected_type=DetectedType(document["detectedType"]),
        byte_size=document["byteSize"],
        sha256=document["sha256"],
        malware_scan=None if scan is None else MalwareScan(scan),
        outcome=IngestionOutcome(document["outcome"]),
        reason=None if reason is None else RejectionReason(reason),
        detail=document["detail"],
        failed_file_index=document["failedFileIndex"],
        files=tuple(
            StoredFile(
                index=item["index"],
                detected_type=DetectedType(item["detectedType"]),
                byte_size=item["byteSize"],
                sha256=item["sha256"],
                raw_source_id=UUID(item["rawSourceId"]),
                sessions=item["sessions"],
            )
            for item in document["files"]
        ),
        activities=tuple(
            ImportedActivity(
                activity_id=UUID(item["activityId"]),
                raw_source_id=UUID(item["rawSourceId"]),
                part=item["part"],
                outcome=ActivityOutcome(item["outcome"]),
            )
            for item in document["activities"]
        ),
        duplicate_of=parse_optional_uuid(document["duplicateOf"]),
        idempotency_key=document["idempotencyKey"],
    )


_COLUMNS: dict[str, str] = {
    "distanceMeters": "distance_meters",
    "heartRateBpm": "heart_rate_bpm",
    "speedMps": "speed_mps",
    "cadence": "cadence",
    "powerWatts": "power_watts",
    "altitudeMeters": "altitude_meters",
}


def time_series_to_contract(series: TimeSeries) -> Json:
    document: Json = {
        "schemaVersion": SCHEMA_VERSION,
        "startTimeUtc": instant(series.start_time_utc),
        "offsetSeconds": list(series.offset_seconds),
    }
    for key, attribute in _COLUMNS.items():
        column: Column | None = getattr(series, attribute)
        if column is not None:
            document[key] = list(column)
    if series.cadence_unit is not None:
        document["cadenceUnit"] = series.cadence_unit.value
    return document


def _column(document: Document, key: str) -> Column | None:
    values: list[Any] | None = document.get(key)
    if values is None:
        return None
    return tuple(None if value is None else float(value) for value in values)


def time_series_from_contract(document: Document) -> TimeSeries:
    unit = document.get("cadenceUnit")
    return TimeSeries(
        start_time_utc=parse_instant(document["startTimeUtc"]),
        offset_seconds=tuple(float(value) for value in document["offsetSeconds"]),
        distance_meters=_column(document, "distanceMeters"),
        heart_rate_bpm=_column(document, "heartRateBpm"),
        speed_mps=_column(document, "speedMps"),
        cadence=_column(document, "cadence"),
        cadence_unit=None if unit is None else CadenceUnit(unit),
        power_watts=_column(document, "powerWatts"),
        altitude_meters=_column(document, "altitudeMeters"),
    )
