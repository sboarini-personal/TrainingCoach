"""Source observations and the canonical Activity aggregate (FR-ACT-001, FR-ACT-002)."""

from dataclasses import replace
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest

from coach.domain.activity.activity import (
    ActivityStatus,
    FieldProvenance,
    LinkKind,
    SourceLink,
)
from coach.domain.activity.fields import StreamKind, StreamRef
from coach.domain.activity.observation import SourceKind, source_kind_of
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.sport import Sport
from coach.domain.raw.source import DetectedType, SourceOrigin
from samples import (
    ACTIVITY,
    FIT_SOURCE,
    GARMIN_SOURCE,
    NOW,
    SHA_A,
    sample_activity,
    sample_observation,
    tennis_values,
)

OTHER_ID = UUID("0193b2a0-0000-7000-8000-000000000999")


@pytest.mark.req("FR-ACT-002")
@pytest.mark.parametrize(
    ("origin", "detected", "kind"),
    [
        (SourceOrigin.FILE_UPLOAD, DetectedType.FIT, SourceKind.FIT_FILE),
        (SourceOrigin.FILE_UPLOAD, DetectedType.GPX, SourceKind.OTHER_FILE),
        (SourceOrigin.FILE_UPLOAD, DetectedType.TCX, SourceKind.OTHER_FILE),
        (SourceOrigin.GARMIN_PERSONAL_API, DetectedType.JSON, SourceKind.PROVIDER_RECORD),
        (SourceOrigin.GARMIN_PERSONAL_API, DetectedType.FIT, SourceKind.FIT_FILE),
        (SourceOrigin.GARMIN_PERSONAL_API, DetectedType.TCX, SourceKind.OTHER_FILE),
        (SourceOrigin.GARMIN_PERSONAL_API, DetectedType.GPX, SourceKind.OTHER_FILE),
        (SourceOrigin.MANUAL_ENTRY, DetectedType.JSON, SourceKind.ATHLETE_ENTRY),
    ],
)
def test_source_kind_follows_the_raw_source(
    origin: SourceOrigin, detected: DetectedType, kind: SourceKind
) -> None:
    assert source_kind_of(origin, detected) is kind


@pytest.mark.req("FR-ACT-002", "INT-GAR-007")
def test_a_provider_never_supplies_athlete_entries_or_unknown_payloads() -> None:
    for detected in (DetectedType.CSV, DetectedType.ZIP, DetectedType.UNKNOWN):
        with pytest.raises(DomainValidationError):
            source_kind_of(SourceOrigin.GARMIN_PERSONAL_API, detected)


@pytest.mark.req("FR-ACT-002", "INT-GAR-007")
def test_a_file_downloaded_from_a_provider_is_a_file_without_a_record_id() -> None:
    downloaded = replace(
        sample_observation(SourceKind.FIT_FILE), origin=SourceOrigin.GARMIN_PERSONAL_API
    )
    assert (downloaded.kind, downloaded.provider_record) == (SourceKind.FIT_FILE, None)
    with pytest.raises(DomainValidationError):
        replace(downloaded, provider="garmin", provider_activity_id="21004587311")


# --------------------------------------------------------------------------- observations


@pytest.mark.req("FR-ACT-005")
@pytest.mark.parametrize("kind", [SourceKind.FIT_FILE, SourceKind.PROVIDER_RECORD])
def test_only_athlete_entries_carry_subjective_fields(kind: SourceKind) -> None:
    with pytest.raises(DomainValidationError):
        sample_observation(kind, updates={"subjective.rpe": 6.0})
    entry = sample_observation(SourceKind.ATHLETE_ENTRY, updates={"subjective.rpe": 6.0})
    assert entry.values["subjective.rpe"] == 6.0


@pytest.mark.req("FR-ACT-001")
@pytest.mark.parametrize(
    "path", ["timing.trainingDay", "performance.averagePaceSecondsPerKm", "cardiovascular.hrZones"]
)
def test_observations_never_provide_computed_fields(path: str) -> None:
    activity = sample_activity()
    with pytest.raises(DomainValidationError):
        sample_observation(updates={path: activity.values.get(path)})


@pytest.mark.req("FR-ACT-001", "FR-ACT-003")
@pytest.mark.parametrize("path", ["classification.sport", "timing.startTimeUtc"])
def test_observations_need_sport_and_start(path: str) -> None:
    with pytest.raises(DomainValidationError):
        sample_observation(without=(path,))
    with pytest.raises(DomainValidationError):
        sample_observation(updates={path: None})


@pytest.mark.req("FR-ACT-007")
def test_provider_identifiers_belong_to_provider_records_only() -> None:
    record = sample_observation(SourceKind.PROVIDER_RECORD)
    assert record.provider_record == ("garmin", "synthetic-1001")
    with pytest.raises(DomainValidationError):
        replace(record, provider_activity_id=None)
    fit = sample_observation()
    assert fit.provider_record is None
    with pytest.raises(DomainValidationError):
        replace(fit, provider="garmin")
    with pytest.raises(DomainValidationError):
        replace(fit, provider_activity_id="synthetic-1001")


@pytest.mark.req("FR-ACT-002")
def test_observation_kind_matches_origin_and_raw_metadata() -> None:
    fit = sample_observation()
    with pytest.raises(DomainValidationError):
        replace(fit, origin=SourceOrigin.MANUAL_ENTRY)
    with pytest.raises(DomainValidationError):
        replace(fit, raw_sha256="A" * 64)
    with pytest.raises(DomainValidationError):
        replace(fit, normaliser="FIT parser")
    with pytest.raises(DomainValidationError):
        replace(fit, part=-1)


@pytest.mark.req("FR-ACT-001")
def test_observation_subtype_must_fit_its_sport() -> None:
    with pytest.raises(DomainValidationError):
        sample_observation(values=tennis_values(**{"classification.subtype": "ROAD"}))


@pytest.mark.req("FR-ACT-005")
def test_source_paths_describe_provided_fields_only() -> None:
    located = sample_observation(source_paths={"distance.meters": "session.total_distance"})
    assert located.source_paths["distance.meters"] == "session.total_distance"
    with pytest.raises(DomainValidationError):
        sample_observation(source_paths={"elevation.lossMeters": "session.total_descent"})


@pytest.mark.req("FR-ACT-001")
def test_athlete_entries_carry_no_streams_and_streams_are_unique_per_kind() -> None:
    stream = StreamRef(StreamKind.TIME_SERIES, SHA_A)
    with pytest.raises(DomainValidationError):
        sample_observation(SourceKind.ATHLETE_ENTRY, streams=(stream,))
    with pytest.raises(DomainValidationError):
        sample_observation(streams=(stream, stream))


@pytest.mark.req("FR-ACT-002")
def test_observation_values_cannot_be_mutated() -> None:
    observation = sample_observation()
    with pytest.raises(TypeError):
        observation.values["distance.meters"] = 1.0  # type: ignore[index]


# --------------------------------------------------------------------------- activities


def _changed(**changes: Any) -> None:
    replace(sample_activity(), **changes)


@pytest.mark.req("FR-ACT-001")
def test_activity_exposes_core_fields() -> None:
    activity = sample_activity()
    assert activity.sport is Sport.RUNNING
    assert activity.start_time_utc == activity.values["timing.startTimeUtc"]
    assert activity.training_day.isoformat() == "2026-10-20"
    assert activity.number("distance.meters") == 10050.5
    assert activity.primary_source.source_id == FIT_SOURCE
    assert activity.counts_toward_totals
    assert activity.is_live
    assert activity.with_version(3).version == 3


@pytest.mark.req("FR-ACT-001")
@pytest.mark.parametrize(
    "path", ["classification.sport", "timing.startTimeUtc", "timing.trainingDay", "distance.meters"]
)
def test_activity_requires_contract_fields(path: str) -> None:
    activity = sample_activity()
    values = {key: value for key, value in activity.values.items() if key != path}
    with pytest.raises(DomainValidationError):
        replace(activity, values=values)


@pytest.mark.req("FR-ACT-007")
def test_activity_has_one_primary_source_and_unique_sources() -> None:
    activity = sample_activity(sample_observation(), sample_observation(SourceKind.PROVIDER_RECORD))
    primary, enrichment = activity.sources
    with pytest.raises(DomainValidationError):
        replace(activity, sources=())
    with pytest.raises(DomainValidationError):
        replace(activity, sources=(primary, replace(enrichment, link_kind=LinkKind.PRIMARY)))
    with pytest.raises(DomainValidationError):
        replace(activity, sources=(primary, replace(primary, link_kind=LinkKind.ENRICHMENT)))
    with pytest.raises(DomainValidationError):
        replace(activity, sources=(primary, replace(enrichment, source_id=FIT_SOURCE, part=1)))


@pytest.mark.req("FR-ACT-007")
def test_only_merged_activities_name_their_target() -> None:
    with pytest.raises(DomainValidationError):
        _changed(status=ActivityStatus.MERGED)
    with pytest.raises(DomainValidationError):
        _changed(merged_into=OTHER_ID)
    with pytest.raises(DomainValidationError):
        _changed(status=ActivityStatus.MERGED, merged_into=ACTIVITY)
    merged = replace(sample_activity(), status=ActivityStatus.MERGED, merged_into=OTHER_ID)
    assert not merged.is_live
    assert not merged.counts_toward_totals
    pending = replace(sample_activity(), status=ActivityStatus.PENDING_REVIEW)
    assert pending.is_live
    assert not pending.counts_toward_totals


@pytest.mark.req("FR-ACT-005")
def test_provenance_names_known_fields_and_linked_sources() -> None:
    with pytest.raises(DomainValidationError):
        _changed(provenance={"distance.miles": FieldProvenance(FIT_SOURCE, "TOTALS_PREFER_FIT")})
    with pytest.raises(DomainValidationError):
        _changed(
            provenance={"distance.meters": FieldProvenance(GARMIN_SOURCE, "TOTALS_PREFER_FIT")}
        )
    with pytest.raises(DomainValidationError):
        FieldProvenance(FIT_SOURCE, "prefer fit")


@pytest.mark.req("FR-ACT-001")
def test_activity_subtype_must_fit_its_sport_and_versions_are_semver() -> None:
    activity = sample_activity()
    with pytest.raises(DomainValidationError):
        replace(activity, values={**activity.values, "classification.sport": Sport.TENNIS})
    with pytest.raises(DomainValidationError):
        replace(activity, resolution_rules_version="v1")


@pytest.mark.req("FR-ACT-007")
def test_source_links_follow_origin_and_scoring_rules() -> None:
    link = SourceLink.of(sample_observation(), link_kind=LinkKind.PRIMARY, linked_at=NOW)
    assert link.file_hash == SHA_A
    record = SourceLink.of(
        sample_observation(SourceKind.PROVIDER_RECORD), link_kind=LinkKind.PRIMARY, linked_at=NOW
    )
    assert record.file_hash is None
    assert record.provider_activity_id == "synthetic-1001"
    with pytest.raises(DomainValidationError):
        replace(link, file_hash=None)
    with pytest.raises(DomainValidationError):
        replace(record, file_hash=SHA_A)
    with pytest.raises(DomainValidationError):
        replace(link, match_score=0.9)
    with pytest.raises(DomainValidationError):
        replace(link, match_score=1.2, dedup_rules_version="1.0.0")
    with pytest.raises(DomainValidationError):
        replace(link, linked_at=NOW.replace(tzinfo=None))
    assert replace(link, linked_at=NOW + timedelta(hours=1)).linked_at > link.linked_at


@pytest.mark.req("FR-ACT-002")
def test_activity_values_and_provenance_cannot_be_mutated() -> None:
    activity = sample_activity()
    with pytest.raises(TypeError):
        activity.values["distance.meters"] = 1.0  # type: ignore[index]
    with pytest.raises(TypeError):
        activity.provenance["distance.meters"] = None  # type: ignore[index]
