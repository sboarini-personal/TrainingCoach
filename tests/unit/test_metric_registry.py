from collections.abc import Mapping
from typing import Any

import pytest

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.provenance import MetricOrigin
from coach.domain.common.quality import Confidence
from coach.metrics.catalog import CATALOG, SRPE_LOAD
from coach.metrics.registry import InputSpec, MetricDefinition, MetricRegistry


def _sum(values: Mapping[str, float]) -> float:
    return sum(values.values())


def application_metric(**overrides: Any) -> MetricDefinition:
    values: dict[str, Any] = {
        "metric_id": "any.test_total",
        "name": "Test total",
        "sport": None,
        "unit": "AU",
        "method": "sum of inputs",
        "required_inputs": (InputSpec("a", "AU", 0, 10), InputSpec("b", "AU", 0, 10)),
        "minimum_data_quality": Confidence.LOW,
        "calculation_version": "1.0.0",
        "origin": MetricOrigin.APPLICATION_DERIVED,
        "calculate": _sum,
    }
    values.update(overrides)
    return MetricDefinition(**values)


def provider_metric(**overrides: Any) -> MetricDefinition:
    values: dict[str, Any] = {
        "metric_id": "garmin.training_effect_aerobic",
        "name": "Aerobic training effect",
        "sport": None,
        "unit": "TE",
        "method": "provider-calculated",
        "required_inputs": (),
        "minimum_data_quality": Confidence.LOW,
        "calculation_version": "1.0.0",
        "origin": MetricOrigin.PROVIDER_DERIVED,
        "provider": "garmin",
        "provider_metric_name": "Aerobic Training Effect",
    }
    values.update(overrides)
    return MetricDefinition(**values)


# ------------------------------------------------------------------ origin and namespaces


@pytest.mark.req("FR-MET-003")
@pytest.mark.parametrize(
    "origin", [MetricOrigin.APPLICATION_DERIVED, MetricOrigin.STANDARD_CALCULATION]
)
def test_application_metric_cannot_use_a_provider_namespace(origin: MetricOrigin) -> None:
    with pytest.raises(DomainValidationError):
        application_metric(metric_id="garmin.my_load", origin=origin, reference="ref")


@pytest.mark.req("FR-MET-003")
def test_application_metric_has_no_provider_and_has_a_calculation() -> None:
    with pytest.raises(DomainValidationError):
        application_metric(provider="garmin")
    with pytest.raises(DomainValidationError):
        application_metric(calculate=None)


@pytest.mark.req("FR-MET-003")
def test_provider_metric_names_its_provider_and_the_provider_metric() -> None:
    metric = provider_metric()
    assert (metric.provider, metric.provider_metric_name) == ("garmin", "Aerobic Training Effect")
    for broken in ({"provider": None}, {"provider_metric_name": None}, {"provider": "polar"}):
        with pytest.raises(DomainValidationError):
            provider_metric(**broken)


@pytest.mark.req("FR-MET-003")
def test_provider_metric_has_no_calculation_in_code() -> None:
    with pytest.raises(DomainValidationError):
        provider_metric(calculate=_sum)


@pytest.mark.req("FR-MET-003")
def test_provider_name_cannot_be_an_application_namespace() -> None:
    with pytest.raises(DomainValidationError):
        provider_metric(metric_id="run.training_effect", provider="run")


@pytest.mark.req("FR-MET-002")
def test_standard_calculation_must_cite_a_reference() -> None:
    with pytest.raises(DomainValidationError):
        application_metric(origin=MetricOrigin.STANDARD_CALCULATION)
    assert application_metric(origin=MetricOrigin.STANDARD_CALCULATION, reference="Author 2001")


@pytest.mark.req("FR-MET-007")
def test_similar_metrics_of_different_providers_stay_distinct() -> None:
    other = provider_metric(
        metric_id="polar.training_effect_aerobic",
        provider="polar",
        provider_metric_name="Training Benefit",
    )
    registry = MetricRegistry((provider_metric(), other))
    assert registry.get("garmin.training_effect_aerobic").provider == "garmin"
    assert registry.get("polar.training_effect_aerobic").provider == "polar"
    assert len(registry) == 2


# ------------------------------------------------------------------ definition validity


@pytest.mark.req("FR-MET-002")
@pytest.mark.parametrize(
    "overrides",
    [
        {"metric_id": "Any.Total"},
        {"metric_id": "total"},
        {"calculation_version": "1.0"},
        {"required_inputs": (InputSpec("a", "AU", 0, 1), InputSpec("a", "AU", 0, 1))},
        {"unit": ""},
    ],
    ids=["id-case", "id-namespace", "version", "duplicate-input", "unit"],
)
def test_invalid_definitions_are_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(DomainValidationError):
        application_metric(**overrides)


@pytest.mark.req("FR-MET-002")
def test_input_spec_ranges_are_valid() -> None:
    with pytest.raises(DomainValidationError):
        InputSpec("a", "AU", 10, 0)
    with pytest.raises(DomainValidationError):
        InputSpec("A b", "AU", 0, 1)


@pytest.mark.req("FR-MET-002")
def test_registry_ids_are_unique_and_unknown_ids_fail() -> None:
    with pytest.raises(DomainValidationError):
        MetricRegistry((application_metric(), application_metric()))
    with pytest.raises(KeyError):
        MetricRegistry(()).get("any.missing")


# ------------------------------------------------------------------ shipped catalogue


@pytest.mark.req("FR-MET-002", "FR-MET-003")
def test_session_rpe_load_is_a_standard_calculation_with_reference() -> None:
    assert CATALOG.get("any.srpe_load") is SRPE_LOAD
    assert SRPE_LOAD.origin is MetricOrigin.STANDARD_CALCULATION
    assert SRPE_LOAD.reference is not None
    assert "Foster" in SRPE_LOAD.reference
    assert "2001" in SRPE_LOAD.reference
    assert SRPE_LOAD.minimum_data_quality is Confidence.MEDIUM
    assert SRPE_LOAD.calculation_version == "1.0.0"
    assert SRPE_LOAD.unit == "AU"
    assert [spec.name for spec in SRPE_LOAD.required_inputs] == ["rpe", "session_duration_seconds"]
