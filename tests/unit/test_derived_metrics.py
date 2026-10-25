from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.evidence import EvidenceKind, EvidenceRef
from coach.domain.common.provenance import MetricOrigin
from coach.domain.common.quality import Confidence, UncertaintyReason
from coach.metrics.catalog import SRPE_LOAD
from coach.metrics.derived import (
    DerivedMetricValue,
    DerivedStatus,
    MetricInput,
    MetricSubject,
    SubjectKind,
    derive,
)
from coach.metrics.registry import MetricDefinition

GARMIN_TRAINING_EFFECT = MetricDefinition(
    metric_id="garmin.training_effect_aerobic",
    name="Aerobic training effect",
    sport=None,
    unit="TE",
    method="provider-calculated",
    required_inputs=(),
    minimum_data_quality=Confidence.LOW,
    calculation_version="1.0.0",
    origin=MetricOrigin.PROVIDER_DERIVED,
    provider="garmin",
    provider_metric_name="Aerobic Training Effect",
)

NOW = datetime(2026, 10, 21, 8, 0, 5, tzinfo=UTC)
ATHLETE = UUID("0193b2a0-0000-7000-8000-000000000001")
ACTIVITY = UUID("0193b2a0-0000-7000-8000-000000000101")
VALUE_ID = UUID("0193b2a0-0000-7000-8000-000000000903")
SUBJECT = MetricSubject.activity(ACTIVITY)


def _input(
    value: float | None,
    field: str,
    confidence: Confidence = Confidence.HIGH,
    reasons: frozenset[UncertaintyReason] = frozenset(),
) -> MetricInput:
    return MetricInput(
        value=value,
        evidence=EvidenceRef(EvidenceKind.ACTIVITY, ACTIVITY, field=field),
        confidence=confidence,
        uncertainty_reasons=reasons,
    )


RPE = _input(5, "subjective.rpe")
DURATION = _input(3720, "timing.elapsedSeconds", Confidence.MEDIUM)


@pytest.mark.req("FR-MET-001", "FR-MET-004")
def test_session_rpe_load_is_calculated_in_code_with_provenance() -> None:
    value = derive(
        SRPE_LOAD,
        {"rpe": RPE, "session_duration_seconds": DURATION},
        value_id=VALUE_ID,
        athlete_id=ATHLETE,
        subject=SUBJECT,
        computed_at=NOW,
    )
    assert value.status is DerivedStatus.COMPUTED
    assert value.value == pytest.approx(310.0)
    assert (value.metric_id, value.calculation_version, value.unit) == (
        "any.srpe_load",
        "1.0.0",
        "AU",
    )
    assert {(item.name, item.value) for item in value.inputs} == {
        ("rpe", 5),
        ("session_duration_seconds", 3720),
    }
    assert all(item.evidence.ref_id == ACTIVITY for item in value.inputs)
    assert value.input_confidence is Confidence.MEDIUM
    assert value.computed_at == NOW
    assert value.superseded_by is None


def _srpe(inputs: dict[str, MetricInput], value_id: UUID = VALUE_ID) -> DerivedMetricValue:
    return derive(
        SRPE_LOAD, inputs, value_id=value_id, athlete_id=ATHLETE, subject=SUBJECT, computed_at=NOW
    )


@pytest.mark.req("FR-MET-001")
def test_recomputation_with_same_inputs_is_identical() -> None:
    first = _srpe({"rpe": RPE, "session_duration_seconds": DURATION})
    second = _srpe({"rpe": RPE, "session_duration_seconds": DURATION}, value_id=UUID(int=1))
    assert first.value == second.value
    assert first.same_computation(second)


@pytest.mark.req("FR-MET-004", "FR-ACT-010")
@pytest.mark.parametrize("rpe", [None, "absent"])
def test_missing_or_unknown_input_gives_unknown_value(rpe: str | None) -> None:
    inputs = {"session_duration_seconds": DURATION}
    if rpe is None:
        inputs["rpe"] = _input(None, "subjective.rpe")
    value = _srpe(inputs)
    assert value.status is DerivedStatus.MISSING_INPUT
    assert value.value is None
    assert value.missing_inputs == ("rpe",)
    assert UncertaintyReason.MISSING_DATA in value.uncertainty_reasons
    assert [item.name for item in value.inputs] == ["session_duration_seconds"]


@pytest.mark.req("FR-MET-004")
def test_input_quality_below_minimum_gives_unknown_value() -> None:
    sensor = frozenset({UncertaintyReason.SENSOR_QUALITY})
    weak_duration = _input(3720, "timing.elapsedSeconds", Confidence.LOW, sensor)
    value = _srpe({"rpe": RPE, "session_duration_seconds": weak_duration})
    assert value.status is DerivedStatus.BELOW_MINIMUM_QUALITY
    assert value.value is None
    assert value.input_confidence is Confidence.LOW
    assert value.uncertainty_reasons == {UncertaintyReason.SENSOR_QUALITY}


@pytest.mark.req("FR-MET-004", "FR-ACT-010")
def test_computed_value_keeps_input_uncertainty() -> None:
    estimate = frozenset({UncertaintyReason.ESTIMATED_VALUE})
    estimated = _input(3720, "timing.elapsedSeconds", Confidence.MEDIUM, estimate)
    value = _srpe({"rpe": RPE, "session_duration_seconds": estimated})
    assert value.status is DerivedStatus.COMPUTED
    assert value.uncertainty_reasons == {UncertaintyReason.ESTIMATED_VALUE}


@pytest.mark.req("FR-MET-002")
@pytest.mark.parametrize(("rpe", "duration"), [(11, 3720), (-1, 3720), (5, 0), (5, 86_401)])
def test_out_of_range_inputs_are_rejected(rpe: float, duration: float) -> None:
    inputs = {
        "rpe": _input(rpe, "subjective.rpe"),
        "session_duration_seconds": _input(duration, "t"),
    }
    with pytest.raises(DomainValidationError):
        _srpe(inputs)


@pytest.mark.req("FR-MET-002")
def test_unexpected_input_names_are_rejected() -> None:
    with pytest.raises(DomainValidationError):
        _srpe({"rpe": RPE, "session_duration_seconds": DURATION, "heart_rate": RPE})


@pytest.mark.req("FR-MET-003")
def test_provider_metrics_are_never_derived_in_code() -> None:
    with pytest.raises(DomainValidationError):
        derive(
            GARMIN_TRAINING_EFFECT,
            {},
            value_id=VALUE_ID,
            athlete_id=ATHLETE,
            subject=SUBJECT,
            computed_at=NOW,
        )


@pytest.mark.req("FR-MET-004")
def test_subjects_are_typed() -> None:
    assert MetricSubject.activity(ACTIVITY).key == str(ACTIVITY)
    assert MetricSubject.day(date(2026, 10, 20)).key == "2026-10-20"
    assert MetricSubject.week(date(2026, 10, 28)).key == "2026-W44"
    assert MetricSubject(SubjectKind.EXERCISE, "ex.goblet_squat").kind is SubjectKind.EXERCISE
    for kind, key in [
        (SubjectKind.ACTIVITY, "not-a-uuid"),
        (SubjectKind.DAY, "2026-13-01"),
        (SubjectKind.WEEK, "2026-W54"),
        (SubjectKind.EXERCISE, "Squat"),
    ]:
        with pytest.raises(DomainValidationError):
            MetricSubject(kind, key)
