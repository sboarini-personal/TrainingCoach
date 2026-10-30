"""Training monotony and strain (Foster 1998; metrics spec 9.5). Synthetic sessions only."""

from __future__ import annotations

import math
import statistics
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest

from activity_world import SCOPE, World
from coach.application.analytics import AnalyticsService, NoMonotony
from coach.domain.activity.observation import Observation, SourceKind
from coach.domain.common.evidence import EvidenceKind
from coach.domain.common.provenance import MetricOrigin
from coach.metrics.catalog import (
    CATALOG,
    DAY_LOADS,
    TRAINING_MONOTONY,
    TRAINING_STRAIN,
)
from coach.metrics.derived import DerivedStatus, MetricSubject, SubjectKind
from samples import START, sample_observation, tennis_values

WEEK = "2026-W43"  # Monday 2026-10-19 to Sunday 2026-10-25; START is Tuesday 2026-10-20
AFTER = date(2026, 10, 26)
IMPORTED = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


def analytics(world: World) -> AnalyticsService:
    return AnalyticsService(
        activities=world.service,
        store=world.activities,
        overrides=world.overrides,
        metrics=world.metrics,
        consents=world.consents_gate,
    )


def run(day_offset: int, rpe: float | None) -> Observation:
    """A 90-minute session the athlete entered, with its RPE when given."""
    number = 50 + day_offset
    values = tennis_values(**{"timing.startTimeUtc": START + timedelta(days=day_offset)})
    if rpe is not None:
        values["subjective.rpe"] = rpe
    return sample_observation(
        SourceKind.ATHLETE_ENTRY,
        raw_source_id=UUID(int=number),
        sha256=f"{number:064x}",
        values=values,
        received_at=IMPORTED,
    )


def world_after_the_week() -> World:
    world = World()
    world.clock.now = IMPORTED  # every session is in the past when imported
    return world


@pytest.mark.req("FR-MET-002", "FR-MET-003", "FR-MET-005")
def test_monotony_and_strain_follow_foster_and_cite_him() -> None:
    loads = [0.0, 310.0, 0.0, 434.0, 0.0, 620.0, 0.0]
    inputs = {spec.name: value for spec, value in zip(DAY_LOADS, loads, strict=True)}
    assert TRAINING_MONOTONY.calculate is not None
    assert TRAINING_STRAIN.calculate is not None
    monotony = TRAINING_MONOTONY.calculate(inputs)
    assert monotony == pytest.approx(statistics.fmean(loads) / statistics.pstdev(loads))
    assert TRAINING_STRAIN.calculate(inputs) == pytest.approx(math.fsum(loads) * monotony)
    for metric in (TRAINING_MONOTONY, TRAINING_STRAIN):
        assert CATALOG.get(metric.metric_id) is metric
        assert metric.origin is MetricOrigin.STANDARD_CALCULATION
        assert metric.reference is not None
        assert "Foster C" in metric.reference
        assert "1998;30(7):1164-1168" in metric.reference
        assert metric.subject_kinds == {SubjectKind.WEEK}


@pytest.mark.req("FR-MET-004", "FR-MET-005", "NFR-TRC-001")
def test_an_ended_week_has_monotony_from_its_seven_daily_loads() -> None:
    world = world_after_the_week()
    for offset, rpe in ((0, 5.0), (2, 7.0), (4, 10.0)):  # Tuesday, Thursday, Saturday
        world.ingest(run(offset, rpe))
    found = analytics(world).monotony(SCOPE, WEEK, today=AFTER)
    loads = [0.0, 450.0, 0.0, 630.0, 0.0, 900.0, 0.0]  # RPE x 90 minutes
    assert found.daily_loads == tuple(loads)
    assert found.monotony is not None
    assert found.strain is not None
    assert found.monotony.status is DerivedStatus.COMPUTED
    expected = statistics.fmean(loads) / statistics.pstdev(loads)
    assert found.monotony.value == pytest.approx(expected)
    assert found.strain.value == pytest.approx(math.fsum(loads) * expected)
    assert found.monotony.subject == MetricSubject(SubjectKind.WEEK, WEEK)
    rest = next(item for item in found.monotony.inputs if item.name == "day1_load")
    assert rest.evidence.kind is EvidenceKind.DERIVED_METRIC  # the week's total lists them all
    assert (rest.evidence.field, rest.evidence.note) == ("2026-10-19", "no activity: load 0")
    tuesday = next(item for item in found.monotony.inputs if item.name == "day2_load")
    day_total = world.metrics.current(
        SCOPE, "any.srpe_load_total", MetricSubject(SubjectKind.DAY, "2026-10-20")
    )
    assert day_total is not None
    assert tuesday.evidence.ref_id == day_total.value_id
    again = analytics(world).monotony(SCOPE, WEEK, today=AFTER)
    assert again.monotony is not None
    assert again.monotony.value_id == found.monotony.value_id  # unchanged: not stored again


@pytest.mark.req("FR-MET-004")
def test_without_a_complete_and_varying_week_there_is_no_monotony() -> None:
    world = world_after_the_week()
    service = analytics(world)
    assert service.monotony(SCOPE, WEEK, today=AFTER).without is NoMonotony.NO_ACTIVITY
    world.ingest(run(0, 5.0))
    early = service.monotony(SCOPE, WEEK, today=date(2026, 10, 25))
    assert (early.without, early.monotony) == (NoMonotony.WEEK_NOT_OVER, None)
    world.ingest(run(1, None))  # Wednesday without RPE: its load is unknown
    found = service.monotony(SCOPE, WEEK, today=AFTER)
    assert found.monotony is not None
    assert (found.monotony.status, found.monotony.missing_inputs) == (
        DerivedStatus.MISSING_INPUT,
        ("day3_load",),
    )
    assert found.daily_loads[2] is None


@pytest.mark.req("FR-MET-004")
def test_equal_daily_loads_leave_monotony_undefined() -> None:
    world = world_after_the_week()
    for offset in range(-1, 6):  # Monday to Sunday, the same session every day
        world.ingest(run(offset, 5.0))
    found = analytics(world).monotony(SCOPE, WEEK, today=AFTER)
    assert (found.without, found.monotony, found.strain) == (NoMonotony.NO_VARIATION, None, None)
    assert (
        world.metrics.current(SCOPE, "any.training_monotony", MetricSubject(SubjectKind.WEEK, WEEK))
        is None
    )
