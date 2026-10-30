"""Metrics engine building blocks (metrics spec sections 2, 4 and 9; FR-MET-001 to 006)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import pytest

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.evidence import EvidenceKind, EvidenceRef
from coach.domain.common.provenance import MetricOrigin
from coach.domain.common.quality import Confidence, UncertaintyReason
from coach.domain.common.sport import Sport
from coach.domain.common.time import DateRange
from coach.metrics.catalog import (
    ACTIVITY_BINDINGS,
    CATALOG,
    DISTANCE_TOTAL,
    DURATION_ELAPSED,
    HR_ZONE_TIME,
    PERIOD_TOTALS,
    RUN_PACE_AVG,
    SPEED_AVG,
    SRPE_LOAD_TOTAL,
    SWIM_PACE_100M_AVG,
)
from coach.metrics.derived import (
    DerivedStatus,
    MetricInput,
    MetricSubject,
    SubjectKind,
    derive,
    derive_total,
)
from coach.metrics.registry import Aggregation, InputSpec, MetricDefinition
from coach.metrics.subjects import DimensionKind, dimension_kind, zone_code
from coach.metrics.zones import MAX_SAMPLE_GAP_SECONDS, time_in_zones

NOW = datetime(2026, 10, 29, 8, 0, tzinfo=UTC)
ATHLETE = UUID("0193b2a0-0000-7000-8000-000000000001")
WEEK = MetricSubject.week(date(2026, 10, 28))


def evidence(number: int, field: str = "distance.meters") -> EvidenceRef:
    return EvidenceRef(EvidenceKind.ACTIVITY, UUID(int=number), field=field)


def contribution(
    number: int,
    value: float | None,
    confidence: Confidence = Confidence.HIGH,
    reasons: frozenset[UncertaintyReason] = frozenset(),
) -> MetricInput:
    return MetricInput(value, evidence(number), confidence, reasons)


def total(definition: MetricDefinition, subject: MetricSubject, *items: MetricInput) -> Any:
    return derive_total(
        definition,
        items,
        value_id=UUID(int=99),
        athlete_id=ATHLETE,
        subject=subject,
        computed_at=NOW,
    )


# ---------------------------------------------------------------- subjects


@pytest.mark.req("FR-MET-004")
def test_subjects_carry_an_optional_dimension() -> None:
    running = MetricSubject.week(date(2026, 10, 28), sport=Sport.RUNNING)
    assert (running.key, running.dimension, running.sport) == ("2026-W44", "RUNNING", Sport.RUNNING)
    assert running.storage_key == "2026-W44/RUNNING"
    assert running != WEEK
    assert WEEK.storage_key == "2026-W44"
    zone = MetricSubject.activity(UUID(int=7), zone=3)
    assert (zone.dimension, zone.zone, zone.sport, zone.dimension_kind) == (
        "Z3",
        3,
        None,
        DimensionKind.ZONE,
    )
    assert dimension_kind("TENNIS") is DimensionKind.SPORT
    for bad in ("Z0", "Z11", "running", "SKIING", "Z1/RUNNING"):
        with pytest.raises(DomainValidationError):
            MetricSubject(SubjectKind.WEEK, "2026-W44", bad)
    with pytest.raises(DomainValidationError):
        zone_code(11)


@pytest.mark.req("FR-MET-004")
def test_period_subjects_know_their_dates() -> None:
    assert WEEK.period() == DateRange(date(2026, 10, 26), date(2026, 11, 1))
    assert MetricSubject.week(date(2027, 1, 1)).key == "2026-W53"
    assert MetricSubject.day(date(2026, 10, 28)).period() == DateRange(
        date(2026, 10, 28), date(2026, 10, 28)
    )
    with pytest.raises(DomainValidationError):
        MetricSubject(SubjectKind.WEEK, "2025-W53")  # 2025 has 52 ISO weeks
    with pytest.raises(DomainValidationError):
        MetricSubject.activity(UUID(int=1)).period()


# ---------------------------------------------------------------- registry rules


def definition(**overrides: Any) -> MetricDefinition:
    values: dict[str, Any] = {
        "metric_id": "any.test_total",
        "name": "Test total",
        "sport": None,
        "unit": "m",
        "method": "sum",
        "required_inputs": (InputSpec("distance_meters", "m", 0, 1_000_000),),
        "minimum_data_quality": Confidence.LOW,
        "calculation_version": "1.0.0",
        "origin": MetricOrigin.APPLICATION_DERIVED,
        "aggregation": Aggregation.SUM,
        "subject_kinds": frozenset({SubjectKind.DAY, SubjectKind.WEEK}),
        "dimension": DimensionKind.SPORT,
    }
    values.update(overrides)
    return MetricDefinition(**values)


@pytest.mark.req("FR-MET-002")
@pytest.mark.parametrize(
    "overrides",
    [
        {"calculate": lambda inputs: 1.0},  # both a calculation and an aggregation
        {"aggregation": None},  # neither
        {"required_inputs": ()},
        {
            "required_inputs": (
                InputSpec("a", "m", 0, 1),
                InputSpec("b", "m", 0, 1),
            )
        },
        {"subject_kinds": frozenset({SubjectKind.ACTIVITY})},
        {"subject_kinds": frozenset()},
        {"dimension": DimensionKind.ZONE, "cross_sport": True},
        {
            "metric_id": "garmin.total",
            "origin": MetricOrigin.PROVIDER_DERIVED,
            "provider": "garmin",
            "provider_metric_name": "Total",
        },
    ],
)
def test_aggregation_rules(overrides: dict[str, Any]) -> None:
    with pytest.raises(DomainValidationError):
        definition(**overrides)


@pytest.mark.req("FR-MET-002", "FR-MET-006")
def test_a_metric_refuses_subjects_it_does_not_describe() -> None:
    sum_by_sport = definition()
    running = MetricSubject.week(date(2026, 10, 28), sport=Sport.RUNNING)
    assert sum_by_sport.check_subject(running) == running
    with pytest.raises(DomainValidationError, match="dimension"):
        sum_by_sport.check_subject(WEEK)  # distance across sports
    with pytest.raises(DomainValidationError):
        sum_by_sport.check_subject(MetricSubject.activity(UUID(int=1)))
    with pytest.raises(DomainValidationError, match="dimension"):
        sum_by_sport.check_subject(WEEK.with_dimension("Z2"))
    cross = definition(cross_sport=True)
    assert cross.check_subject(WEEK) == WEEK


# ---------------------------------------------------------------- totals


@pytest.mark.req("FR-MET-001", "FR-MET-004", "FR-MET-006")
def test_a_total_sums_its_contributions_exactly() -> None:
    running = MetricSubject.week(date(2026, 10, 28), sport=Sport.RUNNING)
    value = total(
        DISTANCE_TOTAL,
        running,
        contribution(1, 0.1),
        contribution(2, 0.2, Confidence.MEDIUM, frozenset({UncertaintyReason.ESTIMATED_VALUE})),
        contribution(3, 0.3),
    )
    assert value.status is DerivedStatus.COMPUTED
    assert value.value == 0.6  # math.fsum: 0.1 + 0.2 + 0.3 would not be exactly 0.6
    assert [record.evidence.ref_id for record in value.inputs] == [UUID(int=n) for n in (1, 2, 3)]
    assert {record.name for record in value.inputs} == {"distance_meters"}
    assert value.input_confidence is Confidence.MEDIUM
    assert value.uncertainty_reasons == frozenset({UncertaintyReason.ESTIMATED_VALUE})
    assert (value.metric_id, value.unit, value.calculation_version) == (
        "any.distance_total",
        "m",
        "1.0.0",
    )


@pytest.mark.req("FR-MET-004")
def test_one_unknown_contribution_makes_the_total_unknown() -> None:
    running = MetricSubject.week(date(2026, 10, 28), sport=Sport.RUNNING)
    value = total(DISTANCE_TOTAL, running, contribution(1, 5000.0), contribution(2, None))
    assert (value.status, value.value, value.missing_inputs) == (
        DerivedStatus.MISSING_INPUT,
        None,
        ("distance_meters",),
    )
    assert [record.value for record in value.inputs] == [5000.0]
    assert UncertaintyReason.MISSING_DATA in value.uncertainty_reasons


@pytest.mark.req("FR-MET-004")
def test_totals_need_contributions_with_distinct_evidence_and_valid_values() -> None:
    running = MetricSubject.week(date(2026, 10, 28), sport=Sport.RUNNING)
    with pytest.raises(DomainValidationError):
        total(DISTANCE_TOTAL, running)
    with pytest.raises(DomainValidationError):
        total(DISTANCE_TOTAL, running, contribution(1, 1.0), contribution(1, 2.0))
    with pytest.raises(DomainValidationError):
        total(DISTANCE_TOTAL, running, contribution(1, -1.0))
    with pytest.raises(DomainValidationError):
        total(DISTANCE_TOTAL, WEEK, contribution(1, 1.0))  # distance needs a sport
    with pytest.raises(DomainValidationError):
        derive(
            DISTANCE_TOTAL,
            {},
            value_id=UUID(int=1),
            athlete_id=ATHLETE,
            subject=running,
            computed_at=NOW,
        )


@pytest.mark.req("FR-MET-006")
def test_cross_sport_totals_cover_time_and_session_rpe_only() -> None:
    cross_sport = {
        item.definition.metric_id for item in PERIOD_TOTALS if item.definition.cross_sport
    }
    sport_only = {
        item.definition.metric_id for item in PERIOD_TOTALS if not item.definition.cross_sport
    }
    assert cross_sport == {"any.duration_elapsed", "any.duration_moving", "any.srpe_load_total"}
    # mechanical work is external load of one sport (P9): never added across sports
    assert sport_only == {"any.distance_total", "any.elevation_gain_total", "cyc.work_total"}
    elapsed = total(DURATION_ELAPSED, WEEK, contribution(1, 3600.0), contribution(2, 1800.0))
    assert elapsed.value == 5400.0


# ---------------------------------------------------------------- catalogue


@pytest.mark.req("FR-MET-002", "FR-MET-003")
def test_the_catalogue_is_registered_with_origins_and_references() -> None:
    expected = {
        "any.srpe_load": MetricOrigin.STANDARD_CALCULATION,
        "any.speed_avg": MetricOrigin.STANDARD_CALCULATION,
        "run.pace_avg": MetricOrigin.STANDARD_CALCULATION,
        "swim.pace_100m_avg": MetricOrigin.STANDARD_CALCULATION,
        "any.hr_zone_time": MetricOrigin.STANDARD_CALCULATION,
        "any.duration_elapsed": MetricOrigin.APPLICATION_DERIVED,
        "any.duration_moving": MetricOrigin.APPLICATION_DERIVED,
        "any.distance_total": MetricOrigin.APPLICATION_DERIVED,
        "any.elevation_gain_total": MetricOrigin.APPLICATION_DERIVED,
        "any.srpe_load_total": MetricOrigin.STANDARD_CALCULATION,
        # P9 (running spec 4): this application's methods, so APPLICATION_DERIVED
        "run.hr_drift": MetricOrigin.APPLICATION_DERIVED,
        "run.pace_variability": MetricOrigin.APPLICATION_DERIVED,
        "run.interval_degradation": MetricOrigin.APPLICATION_DERIVED,
        "run.efficiency_hr": MetricOrigin.APPLICATION_DERIVED,
        # P9 (cycling spec 6): work and power zones are published methods
        "cyc.work_kj": MetricOrigin.STANDARD_CALCULATION,
        "cyc.work_total": MetricOrigin.STANDARD_CALCULATION,
        "cyc.power_zone_time": MetricOrigin.STANDARD_CALCULATION,
        "cyc.power_fade": MetricOrigin.APPLICATION_DERIVED,
        "cyc.interval_power_consistency": MetricOrigin.APPLICATION_DERIVED,
    }
    assert {item.metric_id: item.origin for item in CATALOG} == expected
    for item in CATALOG:
        assert item.is_application_metric
        assert item.provider is None
    assert "Seiler" in (HR_ZONE_TIME.reference or "")
    assert "Foster" in (SRPE_LOAD_TOTAL.reference or "")
    assert "SI" in (SPEED_AVG.reference or "")
    assert (RUN_PACE_AVG.sport, SWIM_PACE_100M_AVG.sport) == (Sport.RUNNING, Sport.SWIMMING)
    assert HR_ZONE_TIME.dimension is DimensionKind.ZONE


@pytest.mark.req("FR-MET-001")
@pytest.mark.parametrize(
    ("metric", "distance", "duration", "expected"),
    [
        (SPEED_AVG, 10_000.0, 3000.0, 10_000 / 3000),
        (RUN_PACE_AVG, 10_000.0, 3000.0, 300.0),
        (SWIM_PACE_100M_AVG, 1500.0, 1800.0, 120.0),
    ],
)
def test_speed_and_pace_are_calculated_in_code(
    metric: MetricDefinition, distance: float, duration: float, expected: float
) -> None:
    value = derive(
        metric,
        {
            "distance_meters": MetricInput(distance, evidence(1), Confidence.HIGH),
            "duration_seconds": MetricInput(
                duration, evidence(1, "timing.movingSeconds"), Confidence.HIGH
            ),
        },
        value_id=UUID(int=1),
        athlete_id=ATHLETE,
        subject=MetricSubject.activity(UUID(int=1)),
        computed_at=NOW,
    )
    assert value.value == pytest.approx(expected)


@pytest.mark.req("FR-MET-001")
def test_bindings_name_their_sports_and_field_alternatives() -> None:
    by_metric = {binding.definition.metric_id: binding for binding in ACTIVITY_BINDINGS}
    speed = by_metric["any.speed_avg"]
    assert dict(speed.inputs)["duration_seconds"] == (
        "timing.movingSeconds",
        "timing.elapsedSeconds",
    )
    assert speed.applies_to(Sport.CYCLING)
    assert not speed.applies_to(Sport.TENNIS)
    assert by_metric["run.pace_avg"].applies_to(Sport.RUNNING)
    assert not by_metric["run.pace_avg"].applies_to(Sport.CYCLING)
    assert by_metric["any.srpe_load"].applies_to(Sport.TENNIS)
    assert dict(by_metric["any.srpe_load"].inputs)["session_duration_seconds"] == (
        "timing.elapsedSeconds",
    )


# ---------------------------------------------------------------- time in zones


BOUNDARIES = (100, 120, 140, 160)  # Z1 [100,120), Z2 [120,140), Z3 [140,160) and above


@pytest.mark.req("FR-MET-001", "FR-MET-005")
def test_time_in_zones_counts_each_interval_toward_its_starting_heart_rate() -> None:
    offsets = (0.0, 10.0, 20.0, 30.0, 40.0, 50.0)
    heart_rates = (95.0, 110.0, 130.0, 150.0, 175.0, 120.0)
    zones = time_in_zones(offsets, heart_rates, BOUNDARIES)
    # 95 is below Z1 (counts nowhere); 175 is above the last boundary (top zone);
    # the last sample starts no interval.
    assert zones.seconds == (10.0, 10.0, 20.0)
    assert (zones.below_seconds, zones.unknown_seconds, zones.gap_seconds) == (10.0, 0.0, 0.0)
    assert zones.coverage == 1.0


@pytest.mark.req("FR-MET-001")
def test_gaps_and_unknown_heart_rates_count_nowhere() -> None:
    offsets = (0.0, 5.0, 5.0 + MAX_SAMPLE_GAP_SECONDS + 1, 70.0, 75.0)
    heart_rates = (130.0, 130.0, None, 130.0, 130.0)
    zones = time_in_zones(offsets, heart_rates, BOUNDARIES)
    assert zones.seconds == (0.0, 10.0, 0.0)  # 0-5 and 70-75
    # 5 to 26 and 26 to 70 are longer than the gap limit: pauses, which do not lower coverage
    assert (zones.gap_seconds, zones.unknown_seconds, zones.coverage) == (65.0, 0.0, 1.0)
    empty = time_in_zones((), (), BOUNDARIES)
    assert (empty.seconds, empty.coverage) == ((0.0, 0.0, 0.0), None)
    with pytest.raises(DomainValidationError):
        time_in_zones((0.0, 1.0), (120.0,), BOUNDARIES)


def test_zone_time_definition_snapshots_the_boundaries() -> None:
    names = [spec.name for spec in HR_ZONE_TIME.required_inputs]
    assert names == ["seconds_in_zone", "zone_lower_bpm", "zone_upper_bpm"]
    value = derive(
        HR_ZONE_TIME,
        {
            "seconds_in_zone": MetricInput(600.0, evidence(1), Confidence.MEDIUM),
            "zone_lower_bpm": MetricInput(
                120.0,
                EvidenceRef(EvidenceKind.HR_ZONE_MODEL, UUID(int=5), field="boundariesBpm[1]"),
                Confidence.HIGH,
            ),
            "zone_upper_bpm": MetricInput(
                140.0,
                EvidenceRef(EvidenceKind.HR_ZONE_MODEL, UUID(int=5), field="boundariesBpm[2]"),
                Confidence.HIGH,
            ),
        },
        value_id=UUID(int=1),
        athlete_id=ATHLETE,
        subject=MetricSubject.activity(UUID(int=1), zone=2),
        computed_at=NOW,
    )
    assert (value.value, value.input_confidence) == (600.0, Confidence.MEDIUM)
    assert replace(value.subject, dimension=None) == MetricSubject.activity(UUID(int=1))


@pytest.mark.req("FR-MET-004")
def test_samples_without_heart_rate_lower_the_coverage() -> None:
    offsets = tuple(float(second) for second in range(0, 101, 10))
    heart_rates = (130.0, None, None, None, None, None, None, None, None, None, 130.0)
    zones = time_in_zones(offsets, heart_rates, BOUNDARIES)
    assert (zones.seconds, zones.unknown_seconds, zones.gap_seconds) == (
        (0.0, 10.0, 0.0),
        90.0,
        0.0,
    )
    assert zones.coverage == pytest.approx(0.1)
