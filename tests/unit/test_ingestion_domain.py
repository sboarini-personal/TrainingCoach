"""Ingestion limits and records, time series, failed-import issues (INT-FIL-002 to 004)."""

import math
from dataclasses import replace
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest

from coach.application.contracts import (
    DATA_QUALITY_ISSUE_CODEC,
    INGESTION_RECORD_CODEC,
    OBSERVATION_CODEC,
    RAW_SOURCE_CODEC,
)
from coach.domain.activity.fields import CadenceUnit
from coach.domain.activity.issues import DataQualityIssue, IssueKind, IssueResolution
from coach.domain.activity.observation import SourceKind, source_kind_of
from coach.domain.activity.streams import TimeSeries
from coach.domain.common.errors import DomainValidationError, InvalidTransitionError
from coach.domain.ingestion.limits import MIB, IngestionLimits
from coach.domain.ingestion.record import (
    ActivityOutcome,
    ImportedActivity,
    IngestionOutcome,
    IngestionRecord,
    RejectionReason,
    StoredFile,
    clean_mime_type,
)
from coach.domain.raw.source import DetectedType, MalwareScan, SourceOrigin
from samples import ACTIVITY, ATHLETE, FIT_SOURCE, NOW, SHA_A, sample_observation, tennis_values
from schemas import EXAMPLES_DIR, load_json

INGESTION = UUID("0193b2a0-0000-7000-8000-000000000701")


def _record(**changes: Any) -> IngestionRecord:
    values: dict[str, Any] = {
        "ingestion_id": INGESTION,
        "athlete_id": ATHLETE,
        "received_at": NOW,
        "completed_at": NOW + timedelta(seconds=2),
        "outcome": IngestionOutcome.IMPORTED,
        "detected_type": DetectedType.FIT,
        "byte_size": 1024,
        "sha256": SHA_A,
        "malware_scan": MalwareScan.NOT_SCANNED,
        "files": (StoredFile(0, DetectedType.FIT, 1024, SHA_A, FIT_SOURCE, 1),),
        "activities": (ImportedActivity(ACTIVITY, FIT_SOURCE, 0, ActivityOutcome.CREATED),),
    }
    values.update(changes)
    return IngestionRecord(**values)


# --------------------------------------------------------------------------- limits


@pytest.mark.req("INT-FIL-003")
def test_default_limits_follow_the_spec() -> None:
    limits = IngestionLimits()
    assert limits.max_bytes(DetectedType.FIT) == 32 * MIB
    assert limits.max_bytes(DetectedType.GPX) == limits.max_bytes(DetectedType.TCX) == 64 * MIB
    assert limits.max_bytes(DetectedType.CSV) == 16 * MIB
    assert limits.max_bytes(DetectedType.ZIP) == 64 * MIB
    assert limits.max_upload_bytes == 64 * MIB
    assert (limits.records, limits.activities, limits.zip_members, limits.zip_ratio) == (
        2_000_000,
        500,
        50,
        100,
    )
    assert (limits.parser_timeout_seconds, limits.parser_memory_bytes) == (60.0, 1024 * MIB)
    with pytest.raises(DomainValidationError):
        limits.max_bytes(DetectedType.UNKNOWN)


@pytest.mark.req("INT-FIL-003")
@pytest.mark.parametrize("value", [0, -1, math.inf * 0, True])
def test_there_is_never_an_unlimited_mode(value: Any) -> None:
    with pytest.raises(DomainValidationError):
        IngestionLimits(records=value)
    assert IngestionLimits(records=10).records == 10


# --------------------------------------------------------------------------- records


@pytest.mark.req("INT-FIL-004", "INT-FIL-002")
def test_record_outcome_rules() -> None:
    assert _record().succeeded
    with pytest.raises(DomainValidationError):
        _record(reason=RejectionReason.PARSE_FAILED)
    with pytest.raises(DomainValidationError):
        _record(outcome=IngestionOutcome.REJECTED, activities=(), reason=None)
    with pytest.raises(DomainValidationError):
        _record(outcome=IngestionOutcome.REJECTED, reason=RejectionReason.PARSE_FAILED)
    with pytest.raises(DomainValidationError):
        _record(outcome=IngestionOutcome.FAILED, reason=RejectionReason.PARSE_FAILED)
    with pytest.raises(DomainValidationError):
        _record(outcome=IngestionOutcome.REJECTED, reason=RejectionReason.PERSISTENCE_FAILED)
    with pytest.raises(DomainValidationError):
        _record(outcome=IngestionOutcome.DUPLICATE)
    with pytest.raises(DomainValidationError):
        _record(outcome=IngestionOutcome.DUPLICATE, duplicate_of=INGESTION)
    with pytest.raises(DomainValidationError):
        _record(sha256=None)
    with pytest.raises(DomainValidationError):
        _record(failed_file_index=0)
    rejected = _record(
        outcome=IngestionOutcome.REJECTED,
        reason=RejectionReason.TOO_LARGE,
        sha256=None,
        activities=(),
        files=(),
        detail="STREAM_LIMIT",
    )
    assert not rejected.succeeded


@pytest.mark.req("INT-GAR-007", "INT-GAR-009")
def test_provider_payload_records_name_their_provider_and_object() -> None:
    record = _record(
        origin=SourceOrigin.GARMIN_PERSONAL_API,
        provider="garmin",
        provider_object_id="21004587311",
        malware_scan=MalwareScan.NOT_APPLICABLE,
        detected_type=DetectedType.JSON,
        declared_mime_type="application/json",
        files=(StoredFile(0, DetectedType.JSON, 1024, SHA_A, FIT_SOURCE, 1),),
    )
    assert (record.origin, record.provider, record.provider_object_id) == (
        SourceOrigin.GARMIN_PERSONAL_API,
        "garmin",
        "21004587311",
    )
    provider = {
        "origin": SourceOrigin.GARMIN_PERSONAL_API,
        "provider": "garmin",
        "provider_object_id": "21004587311",
        "malware_scan": MalwareScan.NOT_APPLICABLE,
    }
    for changes in (
        {**provider, "provider_object_id": None},
        {**provider, "provider": None},
        {**provider, "malware_scan": MalwareScan.NOT_SCANNED},
        {**provider, "original_filename": "activity.zip"},
        {**provider, "idempotency_key": "key-1"},
        {"provider": "garmin"},
        {"provider_object_id": "21004587311"},
        {"origin": SourceOrigin.MANUAL_ENTRY, "malware_scan": None},
    ):
        with pytest.raises(DomainValidationError):
            _record(**changes)
    with pytest.raises(DomainValidationError):
        StoredFile(0, DetectedType.ZIP, 1, SHA_A, FIT_SOURCE, 0)


@pytest.mark.req("SEC-UPL-005", "SEC-INJ-001", "SEC-UPL-001")
def test_untrusted_upload_metadata_is_sanitised() -> None:
    record = _record(original_filename="..\\..\\evil\x00.fit", declared_mime_type="text/xml; x=1")
    assert record.original_filename == "evil.fit"
    assert record.declared_mime_type == "text/xml"
    assert clean_mime_type("<script>/alert") is None
    with pytest.raises(DomainValidationError):
        _record(detail="free text detail")
    with pytest.raises(DomainValidationError):
        _record(idempotency_key="has spaces")
    with pytest.raises(DomainValidationError):
        _record(malware_scan=MalwareScan.NOT_APPLICABLE)
    with pytest.raises(DomainValidationError):
        _record(completed_at=NOW - timedelta(seconds=1))
    with pytest.raises(DomainValidationError):
        StoredFile(0, DetectedType.ZIP, 10, SHA_A, FIT_SOURCE, 0)


# --------------------------------------------------------------------------- time series


@pytest.mark.req("INT-FIL-001")
def test_time_series_columns_share_the_offsets() -> None:
    series = TimeSeries(
        start_time_utc=NOW,
        offset_seconds=(0.0, 1.0, 1.0, 3.0),
        distance_meters=(0.0, 3.0, None, 9.0),
        cadence=(170.0, 171.0, 172.0, None),
        cadence_unit=CadenceUnit.STEPS_PER_MIN,
    )
    assert len(series) == 4
    assert series.distance_samples() == [(0.0, 0.0), (1.0, 3.0), (3.0, 9.0)]
    for changes in (
        {"offset_seconds": (0.0, 2.0, 1.0, 3.0)},
        {"offset_seconds": (-1.0, 0.0, 1.0, 2.0)},
        {"distance_meters": (0.0, 1.0)},
        {"distance_meters": (0.0, -1.0, 2.0, 3.0)},
        {"heart_rate_bpm": (math.nan, None, None, None)},
        {"cadence_unit": None},
        {"altitude_meters": (9001.0, None, None, None)},
    ):
        with pytest.raises(DomainValidationError):
            replace(series, **changes)


# --------------------------------------------------------------------------- failed-import issues


@pytest.mark.req("INT-FIL-004", "FR-ACT-009")
def test_failed_import_issue_names_the_ingestion() -> None:
    issue = DataQualityIssue.for_import_failure(
        issue_id=UUID(int=1),
        athlete_id=ATHLETE,
        ingestion_id=INGESTION,
        reason=RejectionReason.CONTENT_MISMATCH,
        detail="FIT_HEADER",
        created_at=NOW,
    )
    assert (issue.kind, issue.activity_id, issue.code) == (
        IssueKind.IMPORT_FAILED,
        None,
        "CONTENT_MISMATCH",
    )
    with pytest.raises(DomainValidationError):
        replace(issue, activity_id=ACTIVITY)
    with pytest.raises(DomainValidationError):
        replace(issue, ingestion_id=None)
    with pytest.raises(DomainValidationError):
        replace(issue, field="distance.meters")
    with pytest.raises(InvalidTransitionError):
        issue.close(IssueResolution.MERGED, at=NOW)
    assert issue.close(IssueResolution.ACKNOWLEDGED, at=NOW).resolution is (
        IssueResolution.ACKNOWLEDGED
    )
    document = DATA_QUALITY_ISSUE_CODEC.dump(issue)
    assert "activityId" not in document
    assert DATA_QUALITY_ISSUE_CODEC.load(document) == issue


# --------------------------------------------------------------------------- CSV observations


@pytest.mark.req("FR-ACT-005", "INT-FIL-001")
def test_csv_rows_are_athlete_entries_of_an_upload() -> None:
    assert source_kind_of(SourceOrigin.FILE_UPLOAD, DetectedType.CSV) is SourceKind.ATHLETE_ENTRY
    row = sample_observation(
        SourceKind.ATHLETE_ENTRY, values=tennis_values(**{"subjective.rpe": 7.0})
    )
    uploaded = replace(row, origin=SourceOrigin.FILE_UPLOAD)
    assert uploaded.values["subjective.rpe"] == 7.0
    with pytest.raises(DomainValidationError):
        replace(row, origin=SourceOrigin.GARMIN_PERSONAL_API)


# --------------------------------------------------------------------------- contract versions


@pytest.mark.req("NFR-REL-004")
def test_documents_of_the_previous_minor_version_still_load() -> None:
    issue = load_json(EXAMPLES_DIR / "data-quality-issue.example.json")
    issue["schemaVersion"] = "1.0.0"
    assert DATA_QUALITY_ISSUE_CODEC.load(issue).kind is IssueKind.ANOMALY
    observation = load_json(EXAMPLES_DIR / "source-observation.example.json")
    observation["schemaVersion"] = "1.0.0"
    loaded = OBSERVATION_CODEC.load(observation)
    assert OBSERVATION_CODEC.dump(loaded)["schemaVersion"] == "1.2.0"
    record = load_json(EXAMPLES_DIR / "ingestion-record.example.json")
    for added in ("origin", "provider", "providerObjectId"):
        del record[added]
    record["schemaVersion"] = "1.0.0"
    assert INGESTION_RECORD_CODEC.load(record).origin is SourceOrigin.FILE_UPLOAD
    raw = load_json(EXAMPLES_DIR / "raw-source.example.json")
    del raw["requestWindow"]
    raw["schemaVersion"] = "1.0.0"
    assert RAW_SOURCE_CODEC.dump(RAW_SOURCE_CODEC.load(raw))["schemaVersion"] == "1.1.0"
