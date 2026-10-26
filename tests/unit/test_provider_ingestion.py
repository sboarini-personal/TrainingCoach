"""Provider payloads through the ingestion pipeline (files spec 9.1; INT-GAR-007, INT-GAR-009)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import pytest

from activity_world import SCOPE
from coach.application.consent import ConsentRequiredError
from coach.application.files import DecodedSession, FileRejectedError
from coach.application.providers import JSON_MEDIA_TYPE, ZIP_MEDIA_TYPE, RawPayload
from coach.domain.activity.issues import IssueKind
from coach.domain.activity.observation import SourceKey, SourceKind
from coach.domain.common.sport import Sport
from coach.domain.ingestion.record import (
    ActivityOutcome,
    IngestionOutcome,
    RejectionReason,
)
from coach.domain.raw.source import DetectedType, MalwareScan, SourceOrigin
from file_samples import START, csv_file, fit_file, zip_file
from ingestion_world import IngestionWorld

ACTIVITY_ID = "21004587311"


class FakeGarminRecords:
    """Stands in for the Garmin record normaliser: ``{"activityId", "sport", "seconds"}``."""

    def __init__(self) -> None:
        self.calls = 0

    def decode(
        self, data: bytes, *, provider_object_id: str, received_at: datetime
    ) -> Sequence[DecodedSession]:
        self.calls += 1
        document = json.loads(data)
        if document.get("activityId") != int(provider_object_id):
            raise FileRejectedError(RejectionReason.SCHEMA_INVALID, "ACTIVITY_ID_MISMATCH")
        values: dict[str, object] = {
            "classification.sport": Sport(document["sport"]),
            "timing.startTimeUtc": START,
            "timing.elapsedSeconds": float(document["seconds"]),
            "distance.meters": float(document["seconds"]) * 3.0,
            "metadata.name": document.get("name"),
        }
        return [
            DecodedSession(
                part=0,
                values={path: value for path, value in values.items() if value is not None},
                normaliser="garmin_activity",
                normaliser_version="1.0.0",
            )
        ]


class ScannerSpy:
    def __init__(self) -> None:
        self.scanned = 0

    def scan(self, path: Path) -> MalwareScan:
        self.scanned += 1
        return MalwareScan.NOT_SCANNED


@pytest.fixture
def world(tmp_path: Path) -> IngestionWorld:
    created = IngestionWorld(tmp_path, provider_records={"garmin": FakeGarminRecords()})
    created.ingestion._scanner = ScannerSpy()  # observe that no scan runs
    return created


def record_payload(**fields: object) -> RawPayload:
    document = {"activityId": int(ACTIVITY_ID), "sport": "RUNNING", "seconds": 600, **fields}
    return RawPayload(
        "garmin",
        "GARMIN_ACTIVITY",
        JSON_MEDIA_TYPE,
        json.dumps(document).encode(),
        provider_object_id=ACTIVITY_ID,
    )


def file_payload(data: bytes) -> RawPayload:
    return RawPayload(
        "garmin", "GARMIN_ACTIVITY_FILE", ZIP_MEDIA_TYPE, data, provider_object_id=ACTIVITY_ID
    )


@pytest.mark.req("INT-GAR-007", "INT-GAR-009", "FR-ACT-002")
def test_a_provider_record_becomes_a_provider_record_observation(world: IngestionWorld) -> None:
    result = world.ingestion.import_provider_payload(SCOPE, record_payload())
    record = result.record
    assert (record.outcome, record.origin, record.provider, record.provider_object_id) == (
        IngestionOutcome.IMPORTED,
        SourceOrigin.GARMIN_PERSONAL_API,
        "garmin",
        ACTIVITY_ID,
    )
    assert (record.detected_type, record.malware_scan, record.declared_mime_type) == (
        DetectedType.JSON,
        MalwareScan.NOT_APPLICABLE,
        JSON_MEDIA_TYPE,
    )
    assert world.ingestion._scanner.scanned == 0  # type: ignore[attr-defined]
    (stored,) = record.files
    raw = world.raw.get(SCOPE, stored.raw_source_id)
    assert (raw.origin, raw.category, raw.provider, raw.provider_object_id) == (
        SourceOrigin.GARMIN_PERSONAL_API,
        "GARMIN_ACTIVITY",
        "garmin",
        ACTIVITY_ID,
    )
    assert world.blobs.get(SCOPE, raw.sha256) == record_payload().data
    observation = world.observations.get(SCOPE, SourceKey(raw.raw_source_id))
    assert (observation.kind, observation.provider_record) == (
        SourceKind.PROVIDER_RECORD,
        ("garmin", ACTIVITY_ID),
    )
    (activity,) = record.activities
    assert activity.outcome is ActivityOutcome.CREATED
    assert world.activities.find_by_provider_record(SCOPE, "garmin", ACTIVITY_ID) is not None


@pytest.mark.req("INT-GAR-007", "NFR-REL-001")
def test_the_same_record_twice_is_a_duplicate(world: IngestionWorld) -> None:
    first = world.ingestion.import_provider_payload(SCOPE, record_payload())
    again = world.ingestion.import_provider_payload(SCOPE, record_payload())
    assert again.record.outcome is IngestionOutcome.DUPLICATE
    assert again.record.duplicate_of == first.record.ingestion_id
    assert len(world.activities.list_all(SCOPE)) == 1


@pytest.mark.req("INT-GAR-007", "INT-GAR-009", "SEC-UPL-003")
def test_a_downloaded_file_is_stored_with_its_archive_and_links_to_the_record(
    world: IngestionWorld,
) -> None:
    record = world.ingestion.import_provider_payload(SCOPE, record_payload())
    archive = zip_file({f"{ACTIVITY_ID}_ACTIVITY.fit": fit_file()})
    result = world.ingestion.import_provider_payload(SCOPE, file_payload(archive))
    assert result.record.outcome is IngestionOutcome.IMPORTED
    assert result.record.detected_type is DetectedType.ZIP
    container = world.raw.find_by_sha256(SCOPE, result.record.sha256 or "")
    assert container is not None
    assert (container.category, container.detected_type, container.provider_object_id) == (
        "GARMIN_ACTIVITY_FILE",
        DetectedType.ZIP,
        ACTIVITY_ID,
    )
    (member,) = result.record.files
    raw = world.raw.get(SCOPE, member.raw_source_id)
    assert (raw.origin, raw.category, raw.detected_type, raw.provider) == (
        SourceOrigin.GARMIN_PERSONAL_API,
        "ACTIVITY_FILE",
        DetectedType.FIT,
        "garmin",
    )
    observation = world.observations.get(SCOPE, SourceKey(raw.raw_source_id))
    assert (observation.kind, observation.provider_record) == (SourceKind.FIT_FILE, None)
    (linked,) = result.record.activities
    assert linked.outcome is ActivityOutcome.LINKED
    assert linked.activity_id == record.record.activities[0].activity_id


@pytest.mark.req("INT-GAR-007", "FR-ACT-007")
def test_a_file_already_uploaded_by_the_athlete_is_a_tier_one_duplicate(
    world: IngestionWorld,
) -> None:
    fit = fit_file()
    uploaded = world.upload(fit)
    result = world.ingestion.import_provider_payload(
        SCOPE, file_payload(zip_file({"activity.fit": fit}))
    )
    (activity,) = result.record.activities
    assert activity.outcome is ActivityOutcome.DUPLICATE
    assert activity.activity_id == uploaded.record.activities[0].activity_id
    (member,) = result.record.files
    assert world.raw.get(SCOPE, member.raw_source_id).origin is SourceOrigin.FILE_UPLOAD


@pytest.mark.req("INT-GAR-007", "FR-ACT-005")
def test_a_csv_from_a_provider_is_rejected(world: IngestionWorld) -> None:
    result = world.ingestion.import_provider_payload(
        SCOPE, file_payload(zip_file({"activity.csv": csv_file()}))
    )
    assert (result.record.outcome, result.record.reason, result.record.detail) == (
        IngestionOutcome.REJECTED,
        RejectionReason.CONTENT_MISMATCH,
        "PROVIDER_CSV",
    )
    (issue,) = world.issues.list_all(SCOPE)
    assert (issue.kind, issue.ingestion_id) == (IssueKind.IMPORT_FAILED, result.record.ingestion_id)


@pytest.mark.req("INT-GAR-007", "SEC-UPL-002")
def test_a_download_that_is_not_a_zip_is_rejected(world: IngestionWorld) -> None:
    result = world.ingestion.import_provider_payload(SCOPE, file_payload(fit_file()))
    assert (result.record.outcome, result.record.reason, result.record.detail) == (
        IngestionOutcome.REJECTED,
        RejectionReason.CONTENT_MISMATCH,
        "ZIP_MAGIC",
    )
    (kept,) = world.raw.list_all(SCOPE)  # INT-GAR-009: kept unmodified, typed as what it is
    assert (kept.sha256, kept.detected_type, kept.category) == (
        result.record.sha256,
        DetectedType.UNKNOWN,
        "GARMIN_ACTIVITY_FILE",
    )


@pytest.mark.req("INT-GAR-007", "INT-FIL-004")
def test_a_record_the_normaliser_rejects_opens_an_issue(world: IngestionWorld) -> None:
    payload = RawPayload(
        "garmin",
        "GARMIN_ACTIVITY",
        JSON_MEDIA_TYPE,
        json.dumps({"activityId": 1, "sport": "RUNNING", "seconds": 1}).encode(),
        provider_object_id=ACTIVITY_ID,
    )
    result = world.ingestion.import_provider_payload(SCOPE, payload)
    assert (result.record.outcome, result.record.reason, result.record.detail) == (
        IngestionOutcome.REJECTED,
        RejectionReason.SCHEMA_INVALID,
        "ACTIVITY_ID_MISMATCH",
    )
    assert [issue.kind for issue in world.issues.list_all(SCOPE)] == [IssueKind.IMPORT_FAILED]
    (kept,) = world.raw.list_all(SCOPE)  # INT-GAR-009: reprocessable once the rules change
    assert (kept.detected_type, kept.provider_object_id) == (DetectedType.JSON, ACTIVITY_ID)
    assert world.blobs.get(SCOPE, kept.sha256) == payload.data


@pytest.mark.req("INT-GAR-007", "INT-FIL-003")
def test_provider_json_has_its_own_size_limit(world: IngestionWorld) -> None:
    big = record_payload(name="x" * (4 * 1024 * 1024))
    result = world.ingestion.import_provider_payload(SCOPE, big)
    assert (result.record.outcome, result.record.reason, result.record.detail) == (
        IngestionOutcome.REJECTED,
        RejectionReason.TOO_LARGE,
        "FORMAT_LIMIT",
    )


@pytest.mark.req("INT-GAR-007", "SEC-SEC-005")
def test_provider_text_is_screened_for_secrets(world: IngestionWorld) -> None:
    pasted = "api_key=sk-ant-api03-" + "A" * 40  # pragma: allowlist secret
    result = world.ingestion.import_provider_payload(SCOPE, record_payload(name=pasted))
    assert result.secret_notice is not None
    activity = world.activities.list_all(SCOPE)[0]
    assert "A" * 40 not in str(activity.values.get("metadata.name"))


@pytest.mark.req("PRIV-CON-002")
def test_provider_payloads_need_the_activities_consent(tmp_path: Path) -> None:
    world = IngestionWorld(
        tmp_path, consent=False, provider_records={"garmin": FakeGarminRecords()}
    )
    with pytest.raises(ConsentRequiredError):
        world.ingestion.import_provider_payload(SCOPE, record_payload())


def test_a_provider_without_a_record_normaliser_is_a_configuration_error(
    tmp_path: Path,
) -> None:
    world = IngestionWorld(tmp_path)
    with pytest.raises(LookupError):
        world.ingestion.import_provider_payload(SCOPE, record_payload())


@pytest.mark.req("INT-GAR-007", "INT-FIL-004")
def test_a_payload_that_never_arrived_whole_is_recorded_as_rejected(world: IngestionWorld) -> None:
    result = world.ingestion.reject_provider_payload(
        SCOPE,
        provider="garmin",
        provider_object_id=ACTIVITY_ID,
        media_type=ZIP_MEDIA_TYPE,
        rejection=FileRejectedError(RejectionReason.TOO_LARGE, "RESPONSE_LIMIT"),
    )
    record = result.record
    assert (record.outcome, record.reason, record.detected_type, record.sha256) == (
        IngestionOutcome.REJECTED,
        RejectionReason.TOO_LARGE,
        DetectedType.ZIP,
        None,
    )
    assert [issue.kind for issue in world.issues.list_all(SCOPE)] == [IssueKind.IMPORT_FAILED]
