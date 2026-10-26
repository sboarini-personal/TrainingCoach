"""File ingestion pipeline (INT-FIL-001 to 004, NFR-REL-001) with the parser run in-process."""

import re
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from activity_world import SCOPE
from coach.application.consent import ConsentRequiredError
from coach.application.contracts import TIME_SERIES_CODEC
from coach.application.events import EventName
from coach.application.files import FileRejectedError
from coach.application.ingestion import IdempotencyConflictError
from coach.domain.activity.activity import LinkKind
from coach.domain.activity.issues import IssueKind
from coach.domain.activity.observation import SourceKey, SourceKind
from coach.domain.common.sport import Sport
from coach.domain.common.time import TrainingDayBasis
from coach.domain.ingestion.limits import IngestionLimits
from coach.domain.ingestion.record import ActivityOutcome, IngestionOutcome, RejectionReason
from coach.domain.raw.source import DetectedType, MalwareScan, SourceOrigin
from coach.infrastructure.sqlite.database import loads
from coach.metrics.derived import DerivedStatus, MetricSubject
from file_samples import (
    START,
    csv_file,
    fit_file,
    fit_session,
    gpx_file,
    tcx_file,
    wellness_fit,
    zip_file,
)
from ingestion_world import IngestionWorld
from samples import sample_observation

FIT = fit_file()


@pytest.fixture
def world(tmp_path: Path) -> IngestionWorld:
    return IngestionWorld(tmp_path)


def _nothing_stored(world: IngestionWorld) -> None:
    assert world.raw.list_all(SCOPE) == []
    assert world.activities.list_all(SCOPE) == []
    assert world.stored_blobs() == []
    assert world.quarantined() == []


# --------------------------------------------------------------------------- imports


@pytest.mark.req("INT-FIL-001", "INT-FIL-002", "FR-ACT-002", "SEC-UPL-005", "SEC-UPL-006")
def test_fit_upload_creates_an_activity_with_its_time_series(world: IngestionWorld) -> None:
    result = world.upload(
        FIT,
        filename="..\\Downloads\\Morning Run.fit",
        declared_mime_type="application/octet-stream",
    )
    record = result.record
    assert (record.outcome, record.detected_type, record.malware_scan) == (
        IngestionOutcome.IMPORTED,
        DetectedType.FIT,
        MalwareScan.NOT_SCANNED,
    )
    assert (record.original_filename, record.byte_size) == ("Morning Run.fit", len(FIT))
    (stored,) = record.files
    (imported,) = record.activities
    assert (stored.sessions, imported.outcome) == (1, ActivityOutcome.CREATED)
    (raw,) = world.raw.list_all(SCOPE)
    assert (raw.raw_source_id, raw.origin, raw.detected_type, raw.malware_scan) == (
        stored.raw_source_id,
        SourceOrigin.FILE_UPLOAD,
        DetectedType.FIT,
        MalwareScan.NOT_SCANNED,
    )
    assert raw.original_filename == "Morning Run.fit"
    blob_names = [path.name for path in world.stored_blobs()]
    assert raw.sha256 in blob_names
    assert all(re.fullmatch(r"[0-9a-f]{64}", name) for name in blob_names)
    assert not any("Morning" in str(path) for path in world.stored_blobs())
    activity = world.activities.get(SCOPE, imported.activity_id)
    assert activity.sport is Sport.RUNNING
    assert activity.values["timing.trainingDayBasis"] is TrainingDayBasis.ACTIVITY_TIMEZONE
    observation = world.observations.get(SCOPE, SourceKey(raw.raw_source_id))
    assert observation.kind is SourceKind.FIT_FILE
    (stream,) = observation.streams
    series = TIME_SERIES_CODEC.load(loads(world.blobs.get(SCOPE, stream.blob_sha256).decode()))
    assert len(series) == 601
    assert world.quarantined() == []
    names = world.events.names()
    assert names.index(EventName.INGESTION_RECEIVED) < names.index(EventName.INGESTION_PARSED)
    assert world.records.get(SCOPE, record.ingestion_id) == record


@pytest.mark.req("NFR-REL-001", "FR-ACT-007")
@pytest.mark.acceptance("AT-ACT-001")
def test_uploading_the_same_bytes_again_is_a_duplicate(world: IngestionWorld) -> None:
    first = world.upload(FIT).record
    second = world.upload(FIT, filename="copy.fit").record
    assert (second.outcome, second.duplicate_of) == (IngestionOutcome.DUPLICATE, first.ingestion_id)
    assert [item.outcome for item in second.activities] == [ActivityOutcome.DUPLICATE]
    assert len(world.raw.list_all(SCOPE)) == 1
    assert len(world.activities.list_all(SCOPE)) == 1
    assert EventName.INGESTION_DUPLICATE in world.events.names()


@pytest.mark.req("NFR-REL-001")
def test_idempotency_key_returns_the_first_result(world: IngestionWorld) -> None:
    first = world.upload(FIT, idempotency_key="upload-1")
    again = world.upload(FIT, idempotency_key="upload-1")
    assert again.record == first.record
    with pytest.raises(IdempotencyConflictError):
        world.upload(tcx_file(), filename="other.tcx", idempotency_key="upload-1")
    assert len(world.records.recent(SCOPE, 10)) == 1


@pytest.mark.req("NFR-REL-001", "INT-FIL-002")
def test_an_interrupted_import_is_completed_by_uploading_again(world: IngestionWorld) -> None:
    original = world.service.ingest

    def crash(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("power cut")

    world.service.ingest = crash  # type: ignore[method-assign]
    failed = world.upload(FIT).record
    assert (failed.outcome, failed.reason) == (
        IngestionOutcome.FAILED,
        RejectionReason.PERSISTENCE_FAILED,
    )
    assert len(world.raw.list_all(SCOPE)) == 1  # bytes stored before the crash
    world.service.ingest = original  # type: ignore[method-assign]
    retried = world.upload(FIT).record
    assert retried.outcome is IngestionOutcome.IMPORTED
    assert len(world.activities.list_all(SCOPE)) == 1
    (issue,) = [item for item in world.open_issues() if item.kind is IssueKind.IMPORT_FAILED]
    assert (issue.ingestion_id, issue.code) == (failed.ingestion_id, "PERSISTENCE_FAILED")


@pytest.mark.req("INT-FIL-001", "FR-ACT-005", "FR-ACT-011")
def test_csv_rows_are_athlete_entries_with_session_rpe_load(world: IngestionWorld) -> None:
    record = world.upload(csv_file(), filename="tennis.csv").record
    (imported,) = record.activities
    activity = world.activities.get(SCOPE, imported.activity_id)
    assert (activity.sport, activity.values["subjective.rpe"]) == (Sport.TENNIS, 7.0)
    observation = world.observations.get(SCOPE, SourceKey(imported.raw_source_id))
    assert (observation.kind, observation.origin) == (
        SourceKind.ATHLETE_ENTRY,
        SourceOrigin.FILE_UPLOAD,
    )
    load = world.derived.current(
        SCOPE, "any.srpe_load", MetricSubject.activity(activity.activity_id)
    )
    assert load is not None
    assert (load.status, load.value) == (DerivedStatus.COMPUTED, 7.0 * 90)


@pytest.mark.req("INT-FIL-001", "SEC-UPL-003")
def test_zip_members_become_their_own_raw_sources(world: IngestionWorld) -> None:
    ride = fit_file(
        [
            fit_session(
                start=START + timedelta(days=1), sport="cycling", sub_sport="road", seconds=60
            )
        ]
    )
    record = world.upload(zip_file({"a.fit": FIT, "b.fit": ride}), filename="export.zip").record
    assert (record.outcome, record.detected_type) == (IngestionOutcome.IMPORTED, DetectedType.ZIP)
    assert [(item.index, item.detected_type) for item in record.files] == [
        (0, DetectedType.FIT),
        (1, DetectedType.FIT),
    ]
    assert len(world.raw.list_all(SCOPE)) == 2
    assert {raw.original_filename for raw in world.raw.list_all(SCOPE)} == {None}
    unzipped = world.upload(FIT).record  # same bytes as member 0
    assert [item.outcome for item in unzipped.activities] == [ActivityOutcome.DUPLICATE]
    assert len(world.raw.list_all(SCOPE)) == 2


# --------------------------------------------------------------------------- rejections


@pytest.mark.req("INT-FIL-002", "INT-FIL-003", "INT-FIL-004", "SEC-UPL-001")
@pytest.mark.acceptance("AT-UPL-001")
@pytest.mark.parametrize(
    ("data", "filename", "limits", "reason", "detail"),
    [
        (
            FIT,
            "run.fit",
            IngestionLimits(fit_bytes=1024, xml_bytes=1024, csv_bytes=1024, zip_bytes=1024),
            RejectionReason.TOO_LARGE,
            "STREAM_LIMIT",
        ),
        (
            FIT,
            "run.fit",
            IngestionLimits(fit_bytes=1024),
            RejectionReason.TOO_LARGE,
            "FORMAT_LIMIT",
        ),
        (FIT, "run.exe", None, RejectionReason.EXTENSION_NOT_ALLOWED, None),
        (gpx_file(), "run.fit", None, RejectionReason.CONTENT_MISMATCH, "FIT_HEADER"),
        (FIT, "run.fit", IngestionLimits(records=50), RejectionReason.LIMIT_EXCEEDED, "RECORDS"),
        (
            gpx_file(prolog='<!DOCTYPE gpx SYSTEM "http://127.0.0.1:9/x.dtd">'),
            "a.gpx",
            None,
            RejectionReason.PARSE_FAILED,
            "XML_DTD_FORBIDDEN",
        ),
        (
            fit_file(file_type="course"),
            "c.fit",
            None,
            RejectionReason.SEMANTIC_INVALID,
            "NOT_AN_ACTIVITY",
        ),
        (
            wellness_fit(),
            "monitoring.fit",
            None,
            RejectionReason.SEMANTIC_INVALID,
            "NOT_AN_ACTIVITY",
        ),
        (
            wellness_fit(44),
            "metrics.fit",
            None,
            RejectionReason.SEMANTIC_INVALID,
            "NOT_AN_ACTIVITY",
        ),
    ],
)
def test_rejections_store_only_the_record_and_an_issue(
    tmp_path: Path,
    data: bytes,
    filename: str,
    limits: IngestionLimits | None,
    reason: RejectionReason,
    detail: str | None,
) -> None:
    world = IngestionWorld(tmp_path, limits=limits)
    record = world.upload(data, filename=filename).record
    assert (record.outcome, record.reason, record.detail) == (
        IngestionOutcome.REJECTED,
        reason,
        detail,
    )
    assert record.activities == ()
    _nothing_stored(world)
    (issue,) = world.open_issues()
    assert (issue.kind, issue.ingestion_id, issue.code) == (
        IssueKind.IMPORT_FAILED,
        record.ingestion_id,
        reason.value,
    )
    assert EventName.INGESTION_REJECTED in world.events.names()
    if detail == "STREAM_LIMIT":
        assert record.sha256 is None


@pytest.mark.req("SEC-UPL-006")
def test_malware_suspicion_rejects_the_upload(world: IngestionWorld) -> None:
    def suspicious(path: Path) -> MalwareScan:
        raise FileRejectedError(RejectionReason.MALWARE_SUSPECTED, "SCANNER")

    world.scanner.scan = suspicious  # type: ignore[method-assign]
    record = world.upload(FIT).record
    assert (record.outcome, record.reason) == (
        IngestionOutcome.REJECTED,
        RejectionReason.MALWARE_SUSPECTED,
    )
    _nothing_stored(world)


@pytest.mark.req("SEC-UPL-003", "INT-FIL-004")
@pytest.mark.acceptance("AT-ZIP-001")
def test_a_rejected_member_rejects_the_whole_archive(world: IngestionWorld) -> None:
    record = world.upload(zip_file({"a.fit": FIT, "b.gpx": FIT}), filename="export.zip").record
    assert (record.reason, record.detail, record.failed_file_index) == (
        RejectionReason.CONTENT_MISMATCH,
        "XML_ENCODING",
        1,
    )
    _nothing_stored(world)


@pytest.mark.req("INT-FIL-003")
def test_activity_limit_counts_every_member(tmp_path: Path) -> None:
    world = IngestionWorld(tmp_path, limits=IngestionLimits(activities=1))
    later = fit_file([fit_session(start=START + timedelta(days=1), seconds=60)])
    record = world.upload(zip_file({"a.fit": FIT, "b.fit": later}), filename="x.zip").record
    assert (record.reason, record.detail) == (RejectionReason.LIMIT_EXCEEDED, "ACTIVITIES")
    _nothing_stored(world)


@pytest.mark.req("PRIV-CON-002")
def test_without_consent_nothing_is_read_or_recorded(tmp_path: Path) -> None:
    world = IngestionWorld(tmp_path, consent=False)
    with pytest.raises(ConsentRequiredError):
        world.upload(FIT)
    assert world.records.recent(SCOPE, 10) == []
    _nothing_stored(world)


@pytest.mark.req("SEC-SEC-005", "SEC-INJ-001")
def test_secrets_in_file_text_are_redacted_before_storage(world: IngestionWorld) -> None:
    name = "token=Zx81Qp0LmN4vB7cT2wYe9KdR"  # pragma: allowlist secret
    result = world.upload(gpx_file(name=name), filename="secret.gpx")
    (imported,) = result.record.activities
    activity = world.activities.get(SCOPE, imported.activity_id)
    assert activity.values["metadata.name"] == "token=[REDACTED-SECRET]"
    assert result.secret_notice is not None
    assert EventName.SECRET_DETECTED in world.events.names()


# --------------------------------------------------------------------------- AT-ACT-001 with files


@pytest.mark.req("FR-ACT-005", "FR-ACT-007", "FR-ACT-008", "INT-FIL-001", "INT-GAR-007")
@pytest.mark.acceptance("AT-ACT-001")
def test_garmin_activity_enriched_by_an_uploaded_fit(world: IngestionWorld) -> None:
    garmin = sample_observation(
        SourceKind.PROVIDER_RECORD,
        values={
            "classification.sport": Sport.RUNNING,
            "timing.startTimeUtc": START,
            "timing.elapsedSeconds": 600.0,
            "distance.meters": 1790.0,
            "metadata.name": "Garmin run",
        },
    )
    world.register(garmin)
    world.service.ingest(SCOPE, garmin)
    volume_before = world.volume()
    (imported,) = world.upload(FIT).record.activities
    assert imported.outcome is ActivityOutcome.LINKED
    (activity,) = world.live()
    assert [link.link_kind for link in activity.sources] == [LinkKind.PRIMARY, LinkKind.ENRICHMENT]
    assert activity.number("distance.meters") == 1800.0  # FIT totals preferred
    assert activity.values["metadata.name"] == "Garmin run"
    (week,) = world.volume().values()
    (before,) = volume_before.values()
    assert (week.activities, before.activities) == (1, 1)
    assert world.upload(FIT).record.outcome is IngestionOutcome.DUPLICATE


@pytest.mark.req("FR-ACT-005", "FR-ACT-007", "FR-ACT-008", "INT-FIL-001")
@pytest.mark.acceptance("AT-ACT-001")
def test_two_file_sources_give_one_activity_in_either_order(tmp_path: Path) -> None:
    gpx = gpx_file(
        points=[
            (-34.6, -58.4, "2026-10-20T21:00:00Z", 150),
            (-34.6, -58.38, "2026-10-20T21:10:00Z", 150),
        ]
    )
    results: list[tuple[dict[str, object], object]] = []
    for order in (((FIT, "a.fit"), (gpx, "a.gpx")), ((gpx, "a.gpx"), (FIT, "a.fit"))):
        world = IngestionWorld(tmp_path / str(len(results)))
        for data, name in order:
            world.upload(data, filename=name)
        (activity,) = world.live()
        results.append((dict(activity.values), world.volume()))
    assert results[0] == results[1]
    assert results[0][0]["distance.meters"] == 1800.0
