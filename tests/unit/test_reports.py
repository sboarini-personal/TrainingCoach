"""The data part of the reports (reporting spec; FR-RPT-001 to FR-RPT-003). Synthetic
sessions only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from activity_world import SCOPE, World
from coach.application.analytics import AnalyticsService
from coach.application.coaching import CoachingConditions
from coach.application.contracts.reports import report_to_contract
from coach.application.insights import InsightService
from coach.application.recovery import RecoveryService
from coach.application.recovery_assessment import RecoveryAssessmentService
from coach.application.reports import ReportService, month_period
from coach.application.screening import SecretScreen
from coach.domain.activity.observation import Observation, SourceKind
from coach.domain.coaching.state import CoachingState
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.evidence import EvidenceKind, EvidenceRef
from coach.domain.common.quality import Confidence, UncertaintyReason
from coach.domain.common.time import DateRange
from coach.domain.consent.ledger import ConsentType
from coach.domain.insights import Insight
from coach.domain.recovery.assessment import RecoveryAssessment, RecoveryPrescription
from coach.domain.recovery.checkin import DailyCheckIn
from coach.domain.recovery.daily import RecoveryObservation
from coach.domain.reports import (
    MissingData,
    ReportData,
    ReportItem,
    ReportKind,
    Section,
    Unavailable,
)
from coach.infrastructure.memory import InMemoryRepository
from samples import ATHLETE, BUENOS_AIRES, NOW, START, sample_observation, tennis_values
from schemas import validation_errors

IMPORTED = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)  # Tuesday of 2026-W44


def session(day_offset: int, number: int) -> Observation:
    values = tennis_values(
        **{"timing.startTimeUtc": START + timedelta(days=day_offset), "subjective.rpe": 5.0}
    )
    return sample_observation(
        SourceKind.ATHLETE_ENTRY,
        raw_source_id=UUID(int=number),
        sha256=f"{number:064x}",
        values=values,
        received_at=IMPORTED,
    )


def reports(world: World) -> ReportService:
    return built(world)[0]


def built(world: World) -> tuple[ReportService, InsightService]:
    conditions = CoachingConditions(
        InMemoryRepository[CoachingState](),
        audit=world.audit,
        events=world.events,
        clock=world.clock,
    )
    recovery = RecoveryService(
        InMemoryRepository[DailyCheckIn](),
        conditions=conditions,
        screen=SecretScreen(world.events),
        timezone_of=lambda _scope: BUENOS_AIRES,
        events=world.events,
        clock=world.clock,
        ids=world.ids,
        observations=InMemoryRepository[RecoveryObservation](),
        consents=world.consents_gate,
    )
    assessments = RecoveryAssessmentService(
        recovery,
        InMemoryRepository[RecoveryAssessment](),
        InMemoryRepository[RecoveryPrescription](),
        consents=world.consents_gate,
        conditions=conditions,
        events=world.events,
        clock=world.clock,
        ids=world.ids,
    )
    analytics = AnalyticsService(
        activities=world.service,
        store=world.activities,
        overrides=world.overrides,
        metrics=world.metrics,
        consents=world.consents_gate,
    )
    insights = InsightService(
        analytics,
        recovery,
        assessments,
        InMemoryRepository[Insight](),
        consents=world.consents_gate,
        events=world.events,
        clock=world.clock,
        ids=world.ids,
    )
    report_service = ReportService(
        activities=world.service,
        analytics=analytics,
        assessments=assessments,
        insights=insights,
        conditions=conditions,
        consents=world.consents_gate,
        today=recovery.today,
        clock=world.clock,
    )
    return report_service, insights


def trained() -> World:
    """Tennis sessions of 450 AU: one a week in 2026-W39 to W42, then Monday to Saturday."""
    world = World()
    world.clock.now = IMPORTED
    number = 400
    for week in range(1, 5):
        world.ingest(session(-7 * week, number := number + 1))
    for day in range(-1, 5):
        world.ingest(session(day, number := number + 1))
    return world


def values(report: ReportData, section: str) -> dict[str, object]:
    return {item.label: item.value for item in report.section(section).items}


def valid(report: ReportData) -> None:
    assert validation_errors("report-data", report_to_contract(report)) == []


# --------------------------------------------------------------------------- model


@pytest.mark.req("FR-RPT-002", "FR-AGT-004")
def test_every_number_of_a_report_has_its_evidence() -> None:
    with pytest.raises(DomainValidationError):
        ReportItem(label="sessions", value=3.0)
    with pytest.raises(DomainValidationError):
        ReportItem(label="flag", value=True)
    with pytest.raises(DomainValidationError):
        Section("Adherence", (ReportItem(label="x", value="y"),), Unavailable.NEEDS_PLANS)
    evidence = (EvidenceRef(EvidenceKind.ACTIVITY, UUID(int=1)),)
    report = ReportData(
        kind=ReportKind.WEEKLY,
        athlete_id=ATHLETE,
        period=DateRange(NOW.date(), NOW.date()),
        subject="2026-W43",
        sections=(
            Section(
                "Volume by sport",
                (
                    ReportItem(label="a", value=2, evidence=evidence, confidence=Confidence.HIGH),
                    ReportItem(
                        label="b",
                        value=None,
                        reasons=frozenset({UncertaintyReason.SENSOR_QUALITY}),
                    ),
                    ReportItem(label="c", value=1.0, evidence=evidence, confidence=Confidence.LOW),
                ),
            ),
            Section.not_available("Adherence", Unavailable.NEEDS_PLANS),
        ),
        generated_at=NOW,
    )
    assert report.confidence is Confidence.LOW  # the lowest of its numbers
    assert report.missing_data == (
        MissingData("Volume by sport", None, "b"),
        MissingData("Adherence", Unavailable.NEEDS_PLANS),
    )
    assert report.uncertainty_reasons == {
        UncertaintyReason.SENSOR_QUALITY,
        UncertaintyReason.MISSING_DATA,
    }
    valid(report)


# --------------------------------------------------------------------------- reports


@pytest.mark.req("FR-RPT-002", "FR-MET-005", "FR-MET-006")
def test_the_weekly_report_data_part() -> None:
    report = reports(trained()).weekly(SCOPE, "2026-W43")
    assert [section.name for section in report.sections] == [
        "Adherence",
        "Volume by sport",
        "Intensity distribution",
        "Strength by muscle group",
        "Tennis load",
        "Recovery",
        "Trends",
        "Alerts",
        "Progression toward the A goal",
        "Changes for the following week",
    ]
    volume = values(report, "Volume by sport")
    assert volume["TENNIS activities"] == 6.0
    assert volume["TENNIS any.srpe_load_total"] == 2700.0
    assert volume["all sports any.srpe_load_total"] == 2700.0
    assert "TENNIS any.distance_total" not in volume  # no distance for tennis
    trends = values(report, "Trends")
    assert trends["any.training_monotony"] == pytest.approx(2.449, abs=0.001)
    tennis = values(report, "Tennis load")
    assert (tennis["tennis sessions"], tennis["tennis session RPE load"]) == (6.0, 2700.0)
    assert report.section("Adherence").unavailable is Unavailable.NEEDS_PLANS
    assert report.section("Progression toward the A goal").unavailable is (
        Unavailable.NEEDS_GOAL_METRICS
    )
    assert report.section("Recovery").unavailable is Unavailable.NO_DATA
    assert report.section("Intensity distribution").unavailable is Unavailable.NO_DATA
    assert MissingData("Adherence", Unavailable.NEEDS_PLANS) in report.missing_data
    valid(report)


@pytest.mark.req("FR-RPT-001")
def test_the_post_activity_report_data_part() -> None:
    world = trained()
    (first, *_) = sorted(world.live(), key=lambda item: item.start_time_utc)
    report = reports(world).post_activity(SCOPE, first.activity_id)
    summary = {item.label: item for item in report.section("Summary").items}
    assert (summary["sport"].value, summary["training day"].value) == ("TENNIS", "2026-09-22")
    elapsed = summary["elapsed time"]
    assert (elapsed.value, elapsed.unit) == (5400.0, "s")
    assert elapsed.evidence[0].field == "timing.elapsedSeconds"
    assert summary["any.srpe_load"].value == 450.0
    for name in ("Planned vs Actual", "Deviations", "Plan Impact"):
        assert report.section(name).unavailable is Unavailable.NEEDS_PLANS
    assert report.section("What Went Well").unavailable is Unavailable.NARRATIVE
    valid(report)


@pytest.mark.req("FR-RPT-003", "FR-GOL-005")
def test_the_monthly_report_data_part() -> None:
    report = reports(trained()).monthly(SCOPE, "2026-10")
    assert report.period == month_period("2026-10")
    comparison = values(report, "Comparison with the previous period")
    assert comparison["this month activities"] == 8.0  # 6 and 13 October, 19 to 24 October
    assert comparison["this month any.srpe_load_total"] == 3600.0
    assert comparison["previous month activities"] == 2.0  # 22 and 29 September
    assert comparison["previous month any.srpe_load_total"] == 900.0
    consistency = values(report, "Consistency")
    assert consistency == {
        "sessions": 8.0,
        "training days": 8.0,
        "longest gap between training days": 6.0,
    }
    load = values(report, "Training load")
    assert load["2026-W43 session RPE load"] == 2700.0
    assert report.section("Cautious forecast").unavailable is Unavailable.NEEDS_GOAL_METRICS
    assert report.section("Recommendations").unavailable is Unavailable.NARRATIVE
    valid(report)
    with pytest.raises(DomainValidationError):
        month_period("2026-13")


@pytest.mark.req("FR-RPT-002", "PRIV-CON-002")
def test_without_the_activities_consent_activity_sections_are_not_available() -> None:
    world = trained()
    world.consents.revoke(SCOPE, ConsentType.ACTIVITIES)
    report = reports(world).weekly(SCOPE, "2026-W43")
    for name in ("Volume by sport", "Intensity distribution", "Strength by muscle group"):
        assert report.section(name).unavailable is Unavailable.NO_CONSENT
    valid(report)


@pytest.mark.req("FR-RPT-003")
def test_one_training_day_has_no_longest_gap() -> None:
    world = World()
    world.clock.now = IMPORTED
    world.ingest(session(0, 501))
    consistency = values(reports(world).monthly(SCOPE, "2026-10"), "Consistency")
    assert consistency == {"sessions": 1.0, "training days": 1.0}  # never an invented 0


@pytest.mark.req("FR-RPT-002", "PRIV-CON-002")
def test_a_revoked_consent_hides_insights_from_the_reports() -> None:
    world = trained()
    service, insights = built(world)
    insights.detect(SCOPE)
    trends = values(service.weekly(SCOPE, "2026-W43"), "Trends")
    assert {"PATTERN.MONOTONY", "PATTERN.AGGRESSIVE_PROGRESSION"} <= set(trends)
    world.consents.revoke(SCOPE, ConsentType.ACTIVITIES)
    report = service.weekly(SCOPE, "2026-W43")
    assert report.section("Trends").unavailable is Unavailable.NO_DATA  # hidden at once
    month = service.monthly(SCOPE, "2026-10")
    assert month.section("Risks").unavailable is Unavailable.NO_DATA
