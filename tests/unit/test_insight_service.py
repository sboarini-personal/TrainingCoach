"""Insight detection over stored activities (insights spec; FR-INS-001, FR-INS-002).
Synthetic sessions only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from activity_world import SCOPE, World
from coach.application.analytics import AnalyticsService
from coach.application.coaching import CoachingConditions
from coach.application.errors import NotFoundError
from coach.application.events import EventName
from coach.application.identity import Principal
from coach.application.insights import InsightService
from coach.application.lineage import LineageService
from coach.application.recovery import RecoveryService
from coach.application.recovery_assessment import RecoveryAssessmentService
from coach.application.screening import SecretScreen
from coach.domain.activity.observation import Observation, SourceKind
from coach.domain.coaching.state import CoachingState
from coach.domain.common.evidence import EvidenceKind
from coach.domain.consent.ledger import ConsentType
from coach.domain.insights import Insight, Pattern, Relationship
from coach.domain.raw.source import RawSource
from coach.domain.recovery.assessment import RecoveryAssessment, RecoveryPrescription
from coach.domain.recovery.checkin import DailyCheckIn
from coach.domain.recovery.daily import RecoveryObservation
from coach.infrastructure.memory import InMemoryRepository
from samples import BUENOS_AIRES, START, USER, sample_observation, tennis_values

IMPORTED = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)  # Tuesday of 2026-W44


def session(day_offset: int, rpe: float, number: int) -> Observation:
    values = tennis_values(
        **{"timing.startTimeUtc": START + timedelta(days=day_offset), "subjective.rpe": rpe}
    )
    return sample_observation(
        SourceKind.ATHLETE_ENTRY,
        raw_source_id=UUID(int=number),
        sha256=f"{number:064x}",
        values=values,
        received_at=IMPORTED,
    )


def service(world: World, stored: InMemoryRepository[Insight] | None = None) -> InsightService:
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
    return InsightService(
        analytics,
        recovery,
        assessments,
        InMemoryRepository[Insight]() if stored is None else stored,
        consents=world.consents_gate,
        events=world.events,
        clock=world.clock,
        ids=world.ids,
    )


@pytest.mark.req("FR-INS-001", "FR-INS-002", "FR-MET-005")
def test_a_monotonous_week_after_lighter_ones_is_found_from_stored_sessions() -> None:
    world = World()
    world.clock.now = IMPORTED
    number = 300
    for week in range(1, 5):  # 2026-W39 to 2026-W42: one session a week
        world.ingest(session(-7 * week, 5.0, number := number + 1))
    for day in range(-1, 5):  # 2026-W43, Monday to Saturday: the same session every day
        world.ingest(session(day, 5.0, number := number + 1))
    insights = service(world)
    found = insights.detect(SCOPE)
    kinds = {item.kind: item for item in found.insights}
    assert set(kinds) == {Pattern.MONOTONY, Pattern.AGGRESSIVE_PROGRESSION}
    assert found.new == 2
    monotony = kinds[Pattern.MONOTONY]
    assert monotony.observation.startswith("Training monotony in 2026-W43 was 2.45")
    assert "450 450 450 450 450 450 0 AU" in monotony.observation
    assert {ref.kind for ref in monotony.evidence} == {EvidenceKind.DERIVED_METRIC}
    assert (monotony.interpretation, monotony.hypothesis, monotony.recommendation) == (
        None,
        None,
        None,
    )  # the AI coach's parts (P13)
    assert monotony.relationship is Relationship.NONE
    progression = kinds[Pattern.AGGRESSIVE_PROGRESSION]
    assert progression.observation.startswith(
        "Session RPE load in 2026-W43 was 2700 AU, 6.0 times the mean of the four weeks before"
    )
    assert (EventName.INSIGHT_DETECTED, {"kind": "PATTERN.MONOTONY"}) in world.events.events
    again = insights.detect(SCOPE)  # found again: not stored twice
    assert (again.new, {item.insight_id for item in again.insights}) == (
        0,
        {item.insight_id for item in found.insights},
    )


def trained() -> World:
    world = World()
    world.clock.now = IMPORTED
    number = 500
    for week in range(1, 5):
        world.ingest(session(-7 * week, 5.0, number := number + 1))
    for day in range(-1, 5):
        world.ingest(session(day, 5.0, number := number + 1))
    return world


def unresolved(node: object) -> int:
    resolved = getattr(node, "resolved", None)
    children = getattr(node, "children", ())
    return (resolved is None) + sum(unresolved(child) for child in children)


@pytest.mark.req("NFR-TRC-001", "FR-INS-001")
@pytest.mark.acceptance("AT-LIN-001")
def test_an_insight_traces_down_to_its_raw_sources_even_after_a_correction() -> None:
    world = trained()
    stored = InMemoryRepository[Insight]()
    found = service(world, stored).detect(SCOPE)
    monotony = next(item for item in found.insights if item.kind is Pattern.MONOTONY)
    lineage = LineageService(world.derived, world.raw, world.activities, insights=stored)
    node = lineage.trace_insight(SCOPE, monotony.insight_id)
    assert node.resolved == monotony
    assert unresolved(node) == 0  # insight, derived values, activities and RAW sources
    sources = lineage.raw_sources(node)
    week = [item for item in world.live() if item.training_day.isocalendar()[1] == 43]
    expected = {link.source_id for item in week for link in item.sources}
    assert {item.raw_source_id for item in sources} == expected
    assert all(isinstance(item, RawSource) for item in sources)
    corrected = week[0]
    world.service.correct(
        SCOPE, Principal(USER), corrected.activity_id, "subjective.rpe", 6.0, "felt harder"
    )
    again = lineage.trace_insight(SCOPE, monotony.insight_id)
    assert unresolved(again) == 0  # values keep their snapshot: the chain still resolves


@pytest.mark.req("PRIV-CON-002", "FR-INS-002")
@pytest.mark.acceptance("AT-CON-001")
def test_a_revoked_consent_hides_the_insights_built_from_its_data() -> None:
    world = trained()
    stored = InMemoryRepository[Insight]()
    insights = service(world, stored)
    found = insights.detect(SCOPE)
    monotony = next(item for item in found.insights if item.kind is Pattern.MONOTONY)
    until = IMPORTED.date()
    assert {item.kind for item in insights.recent(SCOPE, until=until, days=28)} == {
        Pattern.MONOTONY,
        Pattern.AGGRESSIVE_PROGRESSION,
    }
    world.consents.revoke(SCOPE, ConsentType.ACTIVITIES)
    assert insights.recent(SCOPE, until=until, days=28) == []  # hidden at once
    lineage = LineageService(
        world.derived, world.raw, world.activities, insights=stored, consents=world.consents_gate
    )
    with pytest.raises(NotFoundError):
        lineage.trace_insight(SCOPE, monotony.insight_id)
    assert len(stored.list_all(SCOPE)) == 2  # kept, never shown: deletion is offered (P15)


@pytest.mark.req("FR-INS-002")
def test_a_new_athlete_has_no_load_spike_against_weeks_before_any_record() -> None:
    world = World()
    world.clock.now = IMPORTED
    number = 600
    world.ingest(session(-7, 5.0, number := number + 1))  # 2026-W42: the first record
    for day in range(-1, 5):  # 2026-W43
        world.ingest(session(day, 5.0, number := number + 1))
    kinds = {item.kind for item in service(world).detect(SCOPE).insights}
    assert Pattern.AGGRESSIVE_PROGRESSION not in kinds  # W39 to W41 are unknown, not rest
