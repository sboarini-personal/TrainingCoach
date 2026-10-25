"""Activity service: ingestion tiers, review of ambiguous matches, corrections and issues."""

from dataclasses import replace
from datetime import date, timedelta
from uuid import UUID

import pytest

from activity_world import SCOPE, World
from coach.application.activities import IngestOutcome
from coach.application.consent import ConsentRequiredError
from coach.application.errors import InvalidOperationError, NotFoundError, OwnershipError
from coach.application.events import EventName
from coach.application.identity import Principal
from coach.application.ownership import OwnerScope
from coach.domain.activity.activity import ActivityStatus, LinkKind
from coach.domain.activity.issues import DataQualityIssue, IssueKind, IssueResolution
from coach.domain.activity.observation import Observation, SourceKey, SourceKind
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.sport import Sport
from coach.domain.common.time import TrainingDayBasis
from coach.metrics.derived import DerivedStatus, MetricSubject
from samples import (
    FIT_SOURCE,
    GARMIN_SOURCE,
    MANUAL_SOURCE,
    NOW,
    OTHER_ATHLETE,
    SHA_C,
    SHA_D,
    START,
    USER,
    sample_observation,
    tennis_values,
)

PRINCIPAL = Principal(USER)
SECOND_FIT = UUID("0193b2a0-0000-7000-8000-000000000204")
NEW_PAYLOAD = UUID("0193b2a0-0000-7000-8000-000000000205")


def _garmin(**updates: object) -> Observation:
    return sample_observation(
        SourceKind.PROVIDER_RECORD,
        updates={"distance.meters": 10000.0, "metadata.name": "Morning run", **updates},
    )


def _fit(**updates: object) -> Observation:
    return sample_observation(
        updates={"timing.startTimeUtc": START + timedelta(seconds=2), **updates}
    )


def _tennis_entry(**updates: object) -> Observation:
    return sample_observation(
        SourceKind.ATHLETE_ENTRY,
        values=tennis_values(
            **{
                "timing.startTimeUtc": START + timedelta(minutes=5),
                "subjective.rpe": 7.0,
                **updates,
            }
        ),
    )


def _tennis_record() -> Observation:
    return sample_observation(
        SourceKind.PROVIDER_RECORD,
        values=tennis_values(**{"timing.elapsedSeconds": 5100.0, "cardiovascular.averageHr": 131}),
    )


def _srpe(world: World, activity_id: UUID) -> list[object]:
    history = world.derived.history(SCOPE, "any.srpe_load", MetricSubject.activity(activity_id))
    return [(value.status, value.value, value.superseded_by is None) for value in history]


# --------------------------------------------------------------------------- ingestion tiers


@pytest.mark.req("FR-ACT-001", "FR-ACT-007")
def test_first_source_creates_an_activity() -> None:
    world = World()
    result = world.ingest(_garmin())
    assert result.outcome is IngestOutcome.CREATED
    activity = result.activity
    assert activity.status is ActivityStatus.ACTIVE
    assert activity.sources[0].link_kind is LinkKind.PRIMARY
    assert activity.number("distance.meters") == 10000.0
    assert EventName.ACTIVITY_CREATED in world.events.names()
    assert _srpe(world, activity.activity_id) == [(DerivedStatus.MISSING_INPUT, None, True)]


@pytest.mark.req("FR-ACT-007", "NFR-REL-001")
@pytest.mark.acceptance("AT-ACT-001")
def test_same_source_again_changes_nothing() -> None:
    world = World()
    first = world.ingest(_fit())
    again = world.service.ingest(SCOPE, _fit())
    assert again.outcome is IngestOutcome.DUPLICATE
    assert again.activity == first.activity
    assert len(world.activities.list_all(SCOPE)) == 1
    assert world.events.names()[-1] is EventName.INGESTION_DUPLICATE


@pytest.mark.req("FR-ACT-005", "FR-ACT-007", "FR-ACT-008", "INT-GAR-007", "INT-FIL-001")
@pytest.mark.acceptance("AT-ACT-001")
def test_garmin_activity_enriched_by_a_later_fit_file() -> None:
    world = World()
    world.ingest(_garmin())
    volume_before = world.volume()
    world.clock.advance(hours=10)
    linked = world.ingest(_fit())
    assert linked.outcome is IngestOutcome.LINKED
    assert linked.match_score == 1.0
    (activity,) = world.live()
    assert [link.source_id for link in activity.sources] == [GARMIN_SOURCE, FIT_SOURCE]
    assert activity.sources[1].link_kind is LinkKind.ENRICHMENT
    assert activity.sources[1].dedup_rules_version == "1.0.0"
    assert activity.number("distance.meters") == 10050.5
    assert activity.provenance["distance.meters"].source_id == FIT_SOURCE
    assert activity.values["metadata.name"] == "Morning run"
    (week,) = world.volume().values()
    (week_before,) = volume_before.values()
    assert week.activities == week_before.activities == 1
    assert week.distance_meters == 10050.5
    assert world.service.ingest(SCOPE, _fit()).outcome is IngestOutcome.DUPLICATE
    assert len(world.activities.list_all(SCOPE)) == 1


@pytest.mark.req("FR-ACT-005", "FR-ACT-007", "FR-ACT-008")
@pytest.mark.acceptance("AT-ACT-001")
def test_fit_first_then_garmin_gives_the_same_activity() -> None:
    garmin_first, fit_first = World(), World()
    garmin_first.ingest(_garmin())
    garmin_first.ingest(_fit())
    fit_first.ingest(_fit())
    fit_first.ingest(_garmin())
    (one,), (other,) = garmin_first.live(), fit_first.live()
    assert one.values == other.values
    assert one.provenance == other.provenance
    assert garmin_first.volume() == fit_first.volume()


@pytest.mark.req("FR-ACT-007", "INT-GAR-007")
def test_new_payload_of_a_provider_record_is_a_new_source_version() -> None:
    world = World()
    world.ingest(_garmin())
    world.clock.advance(days=1)
    renamed = sample_observation(
        SourceKind.PROVIDER_RECORD,
        raw_source_id=NEW_PAYLOAD,
        sha256=SHA_D,
        received_at=NOW + timedelta(days=1),
        updates={
            "distance.meters": 10000.0,
            "metadata.name": "Renamed run",
            "timing.startTimeUtc": START + timedelta(hours=3),
        },
    )
    result = world.ingest(renamed)
    assert result.outcome is IngestOutcome.NEW_VERSION
    (activity,) = world.live()
    assert len(activity.sources) == 2
    assert activity.values["metadata.name"] == "Renamed run"


@pytest.mark.req("FR-ACT-007")
def test_far_apart_or_different_sport_sessions_stay_distinct() -> None:
    world = World()
    world.ingest(_garmin())
    later = _fit(**{"timing.startTimeUtc": START + timedelta(minutes=10)})
    assert world.ingest(later).outcome is IngestOutcome.CREATED
    ride = sample_observation(
        SourceKind.OTHER_FILE,
        raw_source_id=SECOND_FIT,
        sha256=SHA_C,
        updates={"classification.sport": Sport.CYCLING, "classification.subtype": "ROAD"},
    )
    assert world.ingest(ride).outcome is IngestOutcome.CREATED
    assert len(world.live()) == 3


# --------------------------------------------------------------------------- ambiguous matches


@pytest.mark.req("FR-ACT-007", "FR-ACT-009", "FR-ACT-011")
def test_ambiguous_match_waits_for_the_athlete_and_is_excluded_from_totals() -> None:
    world = World()
    recorded = world.ingest(_tennis_record()).activity
    result = world.ingest(_tennis_entry())
    assert result.outcome is IngestOutcome.AMBIGUOUS
    assert result.activity.status is ActivityStatus.PENDING_REVIEW
    (issue,) = world.open_issues()
    assert issue.kind is IssueKind.AMBIGUOUS_MATCH
    assert issue.activity_id == result.activity.activity_id
    assert issue.related_activity_id == recorded.activity_id
    assert issue.match_score is not None
    assert 0.6 <= issue.match_score < 0.85
    (week,) = world.volume().values()
    assert week.activities == 1
    assert week.duration_seconds == 5100.0
    assert EventName.DEDUP_AMBIGUOUS in world.events.names()


@pytest.mark.req("FR-ACT-007", "SEC-AUD-001", "FR-ACT-011")
def test_confirmed_match_merges_sources_and_is_audited() -> None:
    world = World()
    recorded = world.ingest(_tennis_record()).activity
    pending = world.ingest(_tennis_entry()).activity
    (issue,) = world.open_issues()
    merged = world.service.confirm_match(SCOPE, issue.issue_id)
    assert merged.activity_id == recorded.activity_id
    assert {link.source_id for link in merged.sources} == {GARMIN_SOURCE, MANUAL_SOURCE}
    assert merged.values["subjective.rpe"] == 7.0
    tombstone = world.activities.get(SCOPE, pending.activity_id)
    assert tombstone.status is ActivityStatus.MERGED
    assert tombstone.merged_into == recorded.activity_id
    assert world.open_issues() == []
    assert _srpe(world, recorded.activity_id)[-1] == (DerivedStatus.COMPUTED, 7.0 * 85, True)
    assert [record.document["action"] for record in world.audit.all_records()][-1] == (
        "dedup.merged"
    )
    audits = len(world.audit.all_records())
    assert world.service.confirm_match(SCOPE, issue.issue_id) == merged
    assert len(world.audit.all_records()) == audits
    with pytest.raises(InvalidOperationError):
        world.service.keep_separate(SCOPE, issue.issue_id)
    (week,) = world.volume().values()
    assert (week.activities, week.duration_seconds) == (1, 5100.0)


@pytest.mark.req("FR-ACT-007", "SEC-AUD-001")
def test_rejected_match_keeps_both_sessions() -> None:
    world = World()
    world.ingest(_tennis_record())
    pending = world.ingest(_tennis_entry()).activity
    (issue,) = world.open_issues()
    kept = world.service.keep_separate(SCOPE, issue.issue_id)
    assert kept.activity_id == pending.activity_id
    assert kept.status is ActivityStatus.ACTIVE
    (week,) = world.volume().values()
    assert week.activities == 2
    closed = world.issues.get(SCOPE, issue.issue_id)
    assert closed.resolution is IssueResolution.KEPT_SEPARATE
    assert world.audit.all_records()[-1].document["action"] == "dedup.kept_separate"
    assert world.service.keep_separate(SCOPE, issue.issue_id) == kept


@pytest.mark.req("NFR-REL-001", "FR-ACT-007")
def test_retry_repairs_an_interrupted_ambiguous_ingestion() -> None:
    world = World()
    world.ingest(_tennis_record())
    original_add = world.issues.add
    failures = iter([True])

    def flaky_add(scope: OwnerScope, entity: DataQualityIssue) -> DataQualityIssue:
        if next(failures, False):
            raise RuntimeError("interrupted")
        return original_add(scope, entity)

    world.issues.add = flaky_add  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        world.ingest(_tennis_entry())
    assert world.open_issues() == []
    retry = world.service.ingest(SCOPE, _tennis_entry())
    assert retry.outcome is IngestOutcome.DUPLICATE
    (issue,) = world.open_issues()
    assert issue.activity_id == retry.activity.activity_id


# --------------------------------------------------------------------------- split


@pytest.mark.req("FR-ACT-007", "SEC-AUD-001")
def test_split_moves_a_source_to_its_own_activity() -> None:
    world = World()
    world.ingest(_garmin())
    linked = world.ingest(_fit()).activity
    separated = world.service.split(SCOPE, linked.activity_id, SourceKey(FIT_SOURCE))
    assert [link.source_id for link in separated.sources] == [FIT_SOURCE]
    assert separated.sources[0].link_kind is LinkKind.PRIMARY
    remaining = world.activities.get(SCOPE, linked.activity_id)
    assert [link.source_id for link in remaining.sources] == [GARMIN_SOURCE]
    assert remaining.number("distance.meters") == 10000.0
    assert world.audit.all_records()[-1].document["action"] == "dedup.split"
    assert world.service.ingest(SCOPE, _fit()).activity.activity_id == separated.activity_id
    with pytest.raises(InvalidOperationError):
        world.service.split(SCOPE, separated.activity_id, SourceKey(FIT_SOURCE))


@pytest.mark.req("FR-ACT-007")
def test_splitting_the_primary_promotes_the_next_source() -> None:
    world = World()
    world.ingest(_garmin())
    linked = world.ingest(_fit()).activity
    world.service.split(SCOPE, linked.activity_id, SourceKey(GARMIN_SOURCE))
    remaining = world.activities.get(SCOPE, linked.activity_id)
    assert [(link.source_id, link.link_kind) for link in remaining.sources] == [
        (FIT_SOURCE, LinkKind.PRIMARY)
    ]
    with pytest.raises(NotFoundError):
        world.service.split(SCOPE, linked.activity_id, SourceKey(MANUAL_SOURCE))


@pytest.mark.req("FR-ACT-007")
def test_versions_of_one_provider_record_stay_together() -> None:
    world = World()
    world.ingest(_garmin())
    world.ingest(
        sample_observation(SourceKind.PROVIDER_RECORD, raw_source_id=NEW_PAYLOAD, sha256=SHA_D)
    )
    (activity,) = world.live()
    with pytest.raises(InvalidOperationError):
        world.service.split(SCOPE, activity.activity_id, SourceKey(NEW_PAYLOAD))


# --------------------------------------------------------------------------- corrections


@pytest.mark.req("FR-ACT-002", "FR-ACT-006", "PRIV-ACC-002", "SEC-AUD-001")
@pytest.mark.acceptance("AT-RAW-001")
def test_correction_changes_the_effective_value_only() -> None:
    world = World()
    activity = world.ingest(_fit()).activity
    observation_before = world.observations.get(SCOPE, SourceKey(FIT_SOURCE))
    override = world.service.correct(
        SCOPE, PRINCIPAL, activity.activity_id, "distance.meters", 10000.0, "Measured course"
    ).override
    assert (override.original_value, override.corrected_value) == (10050.5, 10000.0)
    assert (override.reason, override.corrected_by, override.corrected_at) == (
        "Measured course",
        USER,
        NOW,
    )
    assert world.activities.get(SCOPE, activity.activity_id) == activity
    assert world.observations.get(SCOPE, SourceKey(FIT_SOURCE)) == observation_before
    effective = world.service.effective(SCOPE, activity.activity_id)
    assert effective.activity.number("distance.meters") == 10000.0
    assert effective.normalized.number("distance.meters") == 10050.5
    assert effective.activity.provenance["distance.meters"].override_id == override.override_id
    record = world.audit.all_records()[-1].document
    assert (record["action"], record["target"]["type"]) == ("activity.corrected", "DATA_OVERRIDE")
    assert "Measured course" not in str(record)
    assert world.events.events[-1] == (EventName.ACTIVITY_CORRECTED, {"field": "distance.meters"})


@pytest.mark.req("FR-ACT-006", "FR-MET-004", "NFR-TRC-001")
@pytest.mark.acceptance("AT-RAW-001")
def test_correcting_an_input_recomputes_the_dependent_metric() -> None:
    world = World()
    activity = world.ingest(_tennis_entry()).activity
    assert _srpe(world, activity.activity_id) == [(DerivedStatus.COMPUTED, 7.0 * 90, True)]
    world.service.correct(
        SCOPE, PRINCIPAL, activity.activity_id, "subjective.rpe", 5.0, "Typed the wrong value"
    )
    assert _srpe(world, activity.activity_id) == [
        (DerivedStatus.COMPUTED, 7.0 * 90, False),
        (DerivedStatus.COMPUTED, 5.0 * 90, True),
    ]
    view = world.service.effective(SCOPE, activity.activity_id).activity
    assert view.number("training.load.internal.sessionRpeLoad") == 450.0
    world.service.correct(
        SCOPE, PRINCIPAL, activity.activity_id, "timing.elapsedSeconds", 0.0, "Watch stopped"
    )
    last = world.derived.current(
        SCOPE, "any.srpe_load", MetricSubject.activity(activity.activity_id)
    )
    assert last is not None
    assert last.status is DerivedStatus.MISSING_INPUT
    assert last.missing_inputs == ("session_duration_seconds",)


@pytest.mark.req("FR-ACT-004", "FR-ACT-006")
def test_training_day_correction_is_configured_and_corrections_chain() -> None:
    world = World()
    activity = world.ingest(_fit()).activity
    first = world.service.correct(
        SCOPE,
        PRINCIPAL,
        activity.activity_id,
        "timing.trainingDay",
        date(2026, 10, 21),
        "Night run",
    ).override
    view = world.service.effective(SCOPE, activity.activity_id).activity
    assert view.values["timing.trainingDayBasis"] is TrainingDayBasis.CONFIGURED
    revert = world.service.correct(
        SCOPE, PRINCIPAL, activity.activity_id, "timing.trainingDay", date(2026, 10, 20), "Undo"
    ).override
    assert revert.supersedes_override_id == first.override_id
    assert revert.original_value == date(2026, 10, 20)
    view = world.service.effective(SCOPE, activity.activity_id).activity
    assert view.training_day == date(2026, 10, 20)


@pytest.mark.req("FR-ACT-006")
def test_invalid_corrections_are_refused() -> None:
    world = World()
    activity = world.ingest(_fit()).activity
    for field, value in (
        ("timing.startTimeUtc", NOW),
        ("classification.subtype", "MATCH"),
        ("cardiovascular.maxHr", 300),
    ):
        with pytest.raises(DomainValidationError):
            world.service.correct(SCOPE, PRINCIPAL, activity.activity_id, field, value, "x")
    with pytest.raises(DomainValidationError):
        world.service.correct(SCOPE, PRINCIPAL, activity.activity_id, "distance.meters", 1.0, " ")
    world.service.correct(
        SCOPE, PRINCIPAL, activity.activity_id, "classification.sport", Sport.TENNIS, "Tennis"
    )
    world.service.correct(
        SCOPE, PRINCIPAL, activity.activity_id, "classification.subtype", "MATCH", "Singles"
    )
    view = world.service.effective(SCOPE, activity.activity_id).activity
    assert (view.sport, view.values["classification.subtype"]) == (Sport.TENNIS, "MATCH")


@pytest.mark.req("FR-ACT-006", "FR-ACT-007")
def test_merged_activities_are_corrected_through_their_target() -> None:
    world = World()
    world.ingest(_tennis_record())
    pending = world.ingest(_tennis_entry()).activity
    (issue,) = world.open_issues()
    world.service.confirm_match(SCOPE, issue.issue_id)
    with pytest.raises(InvalidOperationError):
        world.service.correct(SCOPE, PRINCIPAL, pending.activity_id, "subjective.rpe", 6.0, "x")
    with pytest.raises(InvalidOperationError):
        world.service.split(SCOPE, pending.activity_id, SourceKey(MANUAL_SOURCE))


# --------------------------------------------------------------------------- anomaly issues


@pytest.mark.req("FR-ACT-009")
def test_anomalies_raise_issues_that_close_when_corrected() -> None:
    world = World()
    activity = world.ingest(_fit(**{"cardiovascular.maxHr": 245})).activity
    (issue,) = world.open_issues()
    assert (issue.kind, issue.code, issue.field) == (
        IssueKind.ANOMALY,
        "HR_OUT_OF_RANGE",
        "cardiovascular.maxHr",
    )
    assert world.events.names().count(EventName.QUALITY_ISSUE_RAISED) == 1
    world.service.correct(
        SCOPE, PRINCIPAL, activity.activity_id, "cardiovascular.maxHr", 175, "Strap glitch"
    )
    assert world.open_issues() == []
    closed = world.issues.get(SCOPE, issue.issue_id)
    assert closed.resolution is IssueResolution.NO_LONGER_DETECTED
    view = world.service.effective(SCOPE, activity.activity_id).activity
    assert view.quality.anomalies == ()


@pytest.mark.req("FR-ACT-009")
def test_acknowledged_anomalies_are_not_raised_again() -> None:
    world = World()
    activity = world.ingest(_fit(**{"cardiovascular.maxHr": 245})).activity
    (issue,) = world.open_issues()
    acknowledged = world.service.acknowledge(SCOPE, issue.issue_id)
    assert acknowledged.resolution is IssueResolution.ACKNOWLEDGED
    world.service.correct(
        SCOPE, PRINCIPAL, activity.activity_id, "metadata.name", "Hills", "Rename"
    )
    assert world.open_issues() == []
    assert world.service.acknowledge(SCOPE, issue.issue_id) == acknowledged


# --------------------------------------------------------------------------- consent and ownership


@pytest.mark.req("PRIV-CON-002", "PRIV-CON-001")
def test_without_activities_consent_nothing_is_stored() -> None:
    world = World(consent=False)
    with pytest.raises(ConsentRequiredError):
        world.ingest(_fit())
    assert world.activities.list_all(SCOPE) == []
    with pytest.raises(NotFoundError):
        world.observations.get(SCOPE, SourceKey(FIT_SOURCE))


@pytest.mark.req("SEC-IDOR-001", "SEC-AUTHZ-001")
def test_another_athletes_activity_cannot_be_reached() -> None:
    world = World()
    activity = world.ingest(_fit(**{"cardiovascular.maxHr": 245})).activity
    (issue,) = world.open_issues()
    world.grant(OTHER_ATHLETE)
    intruder = OwnerScope(OTHER_ATHLETE)
    with pytest.raises(NotFoundError):
        world.service.effective(intruder, activity.activity_id)
    with pytest.raises(NotFoundError):
        world.service.correct(
            intruder, PRINCIPAL, activity.activity_id, "distance.meters", 1.0, "x"
        )
    with pytest.raises(NotFoundError):
        world.service.acknowledge(intruder, issue.issue_id)
    with pytest.raises(NotFoundError):
        world.service.split(intruder, activity.activity_id, SourceKey(FIT_SOURCE))
    with pytest.raises(OwnershipError):
        world.service.ingest(intruder, _fit())
    stolen = replace(_fit(), athlete_id=OTHER_ATHLETE)
    with pytest.raises(NotFoundError):
        world.service.ingest(intruder, stolen)
    assert world.service.issues(intruder) == []


@pytest.mark.req("FR-ACT-002", "SEC-UPL-005")
def test_observation_must_describe_its_raw_source() -> None:
    world = World()
    world.register(_fit())
    for changed in (
        replace(_fit(), raw_sha256=SHA_C),
        replace(_fit(), received_at=NOW + timedelta(seconds=1)),
        replace(_fit(), kind=SourceKind.OTHER_FILE),
    ):
        with pytest.raises(DomainValidationError):
            world.service.ingest(SCOPE, changed)
    assert world.activities.list_all(SCOPE) == []
