"""Period totals, the week report and recomputation (metrics spec 9.3 to 9.5)."""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

import pytest

from activity_world import SCOPE, World
from coach.application.analytics import AnalyticsService, PeriodReport
from coach.application.consent import ConsentRequiredError
from coach.application.identity import Principal
from coach.application.lineage import LineageService
from coach.domain.activity.observation import Observation, SourceKind
from coach.domain.athlete.physiology import HeartRateZoneModel, ZoneMethod, ZoneSource
from coach.domain.common.evidence import EvidenceKind
from coach.domain.common.quality import UncertaintyReason
from coach.domain.common.sport import Sport
from coach.domain.consent.ledger import ConsentType
from coach.domain.raw.source import RawSource
from coach.metrics.derived import DerivedStatus, MetricSubject, SubjectKind
from samples import (
    MANUAL_SOURCE,
    SHA_C,
    START,
    USER,
    sample_observation,
    tennis_values,
)

WEEK = "2026-W43"  # 2026-10-19 to 2026-10-25; the samples start on 2026-10-20
PRINCIPAL = Principal(USER)


def analytics(world: World) -> AnalyticsService:
    return AnalyticsService(
        activities=world.service,
        store=world.activities,
        overrides=world.overrides,
        metrics=world.metrics,
        consents=world.consents_gate,
    )


def run(day_offset: int = 0, /, **changes: object) -> Observation:
    number = 10 + day_offset
    return sample_observation(
        raw_source_id=UUID(int=number),
        sha256=f"{number:064x}",
        updates={"timing.startTimeUtc": START + timedelta(days=day_offset), **changes},
    )


def tennis() -> Observation:
    return sample_observation(
        SourceKind.ATHLETE_ENTRY,
        raw_source_id=MANUAL_SOURCE,
        sha256=SHA_C,
        values=tennis_values(**{"subjective.rpe": 7.0}),
    )


def sports(report: PeriodReport) -> dict[Sport, dict[str, object]]:
    return {
        item.sport: {metric: total.value.value for metric, total in item.totals.items()}
        for item in report.sports
    }


# ---------------------------------------------------------------- week report


@pytest.mark.req("FR-MET-005", "FR-MET-006", "FR-ACT-008")
def test_the_week_report_keeps_sports_apart_and_totals_only_time_and_load_across_them() -> None:
    world = World()
    world.ingest(run())
    world.ingest(tennis())
    world.ingest(run(7))  # next week
    report = analytics(world).week(SCOPE, WEEK)
    assert report.activities == 2
    assert report.period.start == date(2026, 10, 19)
    by_sport = sports(report)
    assert by_sport[Sport.RUNNING] == {
        "any.duration_elapsed": 3720.0,
        "any.duration_moving": 3650.0,
        "any.distance_total": 10050.5,
        "any.elevation_gain_total": 85.0,
        "any.srpe_load_total": None,  # no RPE for the run
    }
    assert set(by_sport[Sport.TENNIS]) == {
        "any.duration_elapsed",
        "any.duration_moving",
        "any.srpe_load_total",
    }  # no distance for tennis, not even an UNKNOWN one
    assert by_sport[Sport.TENNIS]["any.srpe_load_total"] == 630.0
    assert set(report.cross_sport) == {
        "any.duration_elapsed",
        "any.duration_moving",
        "any.srpe_load_total",
    }  # distances are never summed across sports
    elapsed = report.cross_sport["any.duration_elapsed"]
    assert (elapsed.value.value, elapsed.complete) == (9120.0, True)
    assert elapsed.value.subject == MetricSubject(SubjectKind.WEEK, WEEK)
    load = report.cross_sport["any.srpe_load_total"]
    assert (load.value.status, load.known_total, load.unknown) == (
        DerivedStatus.MISSING_INPUT,
        630.0,
        1,
    )


@pytest.mark.req("FR-MET-004", "FR-ACT-008")
def test_a_partial_total_is_unknown_and_says_what_is_known() -> None:
    world = World()
    world.ingest(run())
    world.ingest(run(1, **{"distance.meters": None}))
    report = analytics(world).week(SCOPE, WEEK)
    (running,) = report.sports
    distance = running.totals["any.distance_total"]
    assert (distance.value.value, distance.value.status) == (None, DerivedStatus.MISSING_INPUT)
    assert (distance.known_total, distance.unknown) == (10050.5, 1)
    assert distance.value.missing_inputs == ("distance_meters",)
    assert running.totals["any.duration_elapsed"].value.value == 7440.0


@pytest.mark.req("FR-MET-004")
def test_reading_twice_stores_once_and_a_change_supersedes() -> None:
    world = World()
    activity_id = world.ingest(run()).activity.activity_id
    service = analytics(world)
    first = service.week(SCOPE, WEEK).sports[0].totals["any.distance_total"].value
    again = service.week(SCOPE, WEEK).sports[0].totals["any.distance_total"].value
    assert again.value_id == first.value_id
    world.service.correct(SCOPE, PRINCIPAL, activity_id, "distance.meters", 10000.0, "Measured")
    changed = service.week(SCOPE, WEEK).sports[0].totals["any.distance_total"].value
    assert changed.value == 10000.0
    history = world.derived.history(SCOPE, "any.distance_total", changed.subject)
    assert [item.value_id for item in history] == [first.value_id, changed.value_id]


@pytest.mark.req("FR-ACT-004", "FR-ACT-006")
def test_a_corrected_training_day_moves_the_activity_to_its_new_week() -> None:
    world = World()
    activity_id = world.ingest(run()).activity.activity_id
    service = analytics(world)
    world.service.correct(
        SCOPE, PRINCIPAL, activity_id, "timing.trainingDay", date(2026, 11, 3), "Logged late"
    )
    assert service.week(SCOPE, WEEK).activities == 0
    moved = service.week(SCOPE, "2026-W45")
    assert moved.activities == 1
    assert moved.sports[0].totals["any.distance_total"].value.value == 10050.5


@pytest.mark.req("FR-ACT-008")
def test_pending_review_activities_do_not_count() -> None:
    world = World()
    # A provider record and an athlete entry five minutes apart: an ambiguous match (P5).
    recorded = sample_observation(
        SourceKind.PROVIDER_RECORD,
        values=tennis_values(**{"timing.elapsedSeconds": 5100.0}),
    )
    world.ingest(recorded)
    entry = sample_observation(
        SourceKind.ATHLETE_ENTRY,
        raw_source_id=MANUAL_SOURCE,
        sha256=SHA_C,
        values=tennis_values(
            **{"timing.startTimeUtc": START + timedelta(minutes=5), "subjective.rpe": 7.0}
        ),
    )
    world.ingest(entry)
    statuses = sorted(item.status.value for item in world.live())
    assert statuses == ["ACTIVE", "PENDING_REVIEW"]
    report = analytics(world).week(SCOPE, WEEK)
    assert report.activities == 1
    assert report.cross_sport["any.duration_elapsed"].value.value == 5100.0


@pytest.mark.req("FR-MET-004")
def test_estimated_training_days_and_anomalies_are_visible_in_totals() -> None:
    world = World()
    world.ingest(run(**{"timing.utcOffset": None}))
    report = analytics(world).week(SCOPE, WEEK)
    elapsed = report.cross_sport["any.duration_elapsed"]
    assert UncertaintyReason.ESTIMATED_VALUE in elapsed.value.uncertainty_reasons


@pytest.mark.req("FR-MET-004")
def test_days_are_periods_too_and_an_empty_period_has_no_values() -> None:
    world = World()
    world.ingest(run())
    service = analytics(world)
    day = service.period(SCOPE, MetricSubject.day(date(2026, 10, 20)))
    assert day.activities == 1
    empty = service.period(SCOPE, MetricSubject.day(date(2026, 10, 21)))
    assert (empty.activities, empty.sports, dict(empty.cross_sport)) == (0, (), {})
    with pytest.raises(ValueError, match="every sport"):
        service.period(SCOPE, MetricSubject.week(date(2026, 10, 20), sport=Sport.RUNNING))


@pytest.mark.req("PRIV-CON-002")
def test_analytics_reads_need_the_activities_consent() -> None:
    world = World()
    world.ingest(run())
    world.consents.revoke(SCOPE, ConsentType.ACTIVITIES)
    service = analytics(world)
    with pytest.raises(ConsentRequiredError):
        service.week(SCOPE, WEEK)
    with pytest.raises(ConsentRequiredError):
        service.recompute(SCOPE)
    with pytest.raises(ConsentRequiredError):
        service.activity_metrics(SCOPE, world.live()[0].activity_id)


# ---------------------------------------------------------------- lineage and recomputation


@pytest.mark.req("NFR-TRC-001", "FR-MET-004")
@pytest.mark.acceptance("AT-LIN-001")
def test_a_load_total_traces_to_session_values_activities_and_raw_sources() -> None:
    world = World()
    world.ingest(tennis())
    report = analytics(world).week(SCOPE, WEEK)
    total = report.cross_sport["any.srpe_load_total"].value
    lineage = LineageService(world.derived, world.raw, world.activities)
    root = lineage.trace(SCOPE, total.value_id)
    (session,) = root.children
    assert session.reference.kind is EvidenceKind.DERIVED_METRIC
    assert {child.reference.kind for child in session.children} == {EvidenceKind.ACTIVITY}
    sources = lineage.raw_sources(root)
    assert [source.raw_source_id for source in sources] == [MANUAL_SOURCE]
    assert all(isinstance(source, RawSource) for source in sources)


@pytest.mark.req("FR-MET-004", "FR-PRF-001")
def test_recomputation_brings_old_activities_up_to_date() -> None:
    world = World()
    activity_id = world.ingest(run()).activity.activity_id
    service = analytics(world)
    assert [value.metric_id for value in service.activity_metrics(SCOPE, activity_id)] == [
        "any.srpe_load",
        "any.speed_avg",
        "run.pace_avg",
    ]
    world.zone_models.append(
        HeartRateZoneModel(
            zone_model_id=UUID(int=6),
            method=ZoneMethod.CUSTOM,
            source=ZoneSource.ATHLETE_DECLARED,
            boundaries_bpm=(100, 140, 180),
            valid_from=date(2026, 1, 1),
        )
    )
    stored_before = len(world.derived._items)
    assert service.recompute(SCOPE) == 1
    metrics = [value.metric_id for value in service.activity_metrics(SCOPE, activity_id)]
    assert metrics.count("any.hr_zone_time") == 2  # MISSING_INPUT: the sample has no series
    assert len(world.derived._items) == stored_before + 2
    assert service.recompute(SCOPE) == 1
    assert len(world.derived._items) == stored_before + 2


# ---------------------------------------------------------------- review findings (P8)


@pytest.mark.req("FR-MET-004", "FR-MET-006")
def test_a_total_whose_period_lost_its_activities_is_retired() -> None:
    world = World()
    activity_id = world.ingest(run()).activity.activity_id
    service = analytics(world)
    running = MetricSubject(SubjectKind.WEEK, WEEK, "RUNNING")
    service.week(SCOPE, WEEK)
    assert world.metrics.current(SCOPE, "any.distance_total", running) is not None
    world.service.correct(
        SCOPE, PRINCIPAL, activity_id, "classification.sport", Sport.TENNIS, "It was tennis"
    )
    service.week(SCOPE, WEEK)
    assert world.metrics.current(SCOPE, "any.distance_total", running) is None
    retired = world.derived.current(SCOPE, "any.distance_total", running)
    assert retired is not None
    assert retired.status is DerivedStatus.NOT_APPLICABLE
    world.service.correct(
        SCOPE, PRINCIPAL, activity_id, "timing.trainingDay", date(2026, 11, 3), "Logged late"
    )
    service.week(SCOPE, WEEK)
    everything = MetricSubject(SubjectKind.WEEK, WEEK)
    assert world.metrics.current(SCOPE, "any.duration_elapsed", everything) is None


@pytest.mark.req("FR-MET-004")
def test_a_change_of_uncertainty_alone_is_recorded() -> None:
    world = World()
    activity_id = world.ingest(run(**{"timing.utcOffset": None})).activity.activity_id
    service = analytics(world)
    before = service.week(SCOPE, WEEK).cross_sport["any.duration_elapsed"].value
    assert UncertaintyReason.ESTIMATED_VALUE in before.uncertainty_reasons
    view = world.service.effective(SCOPE, activity_id).activity
    world.service.correct(
        SCOPE, PRINCIPAL, activity_id, "timing.trainingDay", view.training_day, "Confirmed"
    )
    after = service.week(SCOPE, WEEK).cross_sport["any.duration_elapsed"].value
    assert after.value_id != before.value_id
    assert UncertaintyReason.ESTIMATED_VALUE not in after.uncertainty_reasons


@pytest.mark.req("FR-MET-004", "NFR-TRC-001")
def test_an_unknown_total_names_the_activities_it_lacks() -> None:
    world = World()
    world.ingest(run())
    missing_id = world.ingest(run(1, **{"distance.meters": None})).activity.activity_id
    total = analytics(world).week(SCOPE, WEEK).sports[0].totals["any.distance_total"].value
    assert [(item.kind, item.ref_id) for item in total.missing_evidence] == [
        (EvidenceKind.ACTIVITY, missing_id)
    ]
