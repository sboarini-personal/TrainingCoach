import pytest

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.quality import (
    Anomaly,
    Confidence,
    DataQuality,
    Reliability,
    UncertaintyReason,
    derive_confidence,
)


@pytest.mark.req("FR-ACT-009")
@pytest.mark.parametrize(
    ("completeness", "reliability", "anomalies", "blocking", "expected"),
    [
        (0.9, Reliability.HIGH, 0, False, Confidence.HIGH),
        (0.8, Reliability.HIGH, 0, False, Confidence.HIGH),
        (0.9, Reliability.HIGH, 1, False, Confidence.MEDIUM),
        (0.9, Reliability.MEDIUM, 0, False, Confidence.MEDIUM),
        (0.9, Reliability.UNKNOWN, 0, False, Confidence.MEDIUM),
        (0.5, Reliability.HIGH, 0, False, Confidence.MEDIUM),
        (0.49, Reliability.HIGH, 0, False, Confidence.LOW),
        (0.9, Reliability.LOW, 0, False, Confidence.LOW),
        (0.9, Reliability.HIGH, 1, True, Confidence.LOW),
    ],
)
def test_confidence_rules(
    completeness: float,
    reliability: Reliability,
    anomalies: int,
    blocking: bool,
    expected: Confidence,
) -> None:
    result = derive_confidence(
        completeness=completeness,
        reliability=reliability,
        anomalies=anomalies,
        blocking_anomaly=blocking,
    )
    assert result is expected


@pytest.mark.req("FR-ACT-009")
@pytest.mark.parametrize("completeness", [-0.01, 1.01])
def test_completeness_is_a_share(completeness: float) -> None:
    with pytest.raises(DomainValidationError):
        DataQuality(
            completeness=completeness,
            reliability=Reliability.HIGH,
            confidence=Confidence.HIGH,
        )


@pytest.mark.req("FR-ACT-009")
def test_anomaly_codes_are_upper_snake_case() -> None:
    assert Anomaly(code="HR_OUT_OF_RANGE", field="cardiovascular.maxHr").code == "HR_OUT_OF_RANGE"
    with pytest.raises(DomainValidationError):
        Anomaly(code="hr out of range", field="cardiovascular.maxHr")


@pytest.mark.req("FR-ACT-010")
def test_uncertainty_reasons_are_exactly_the_specified_set() -> None:
    assert {reason.value for reason in UncertaintyReason} == {
        "MISSING_DATA",
        "SENSOR_QUALITY",
        "NON_COMPARABLE_SESSION",
        "ENVIRONMENT",
        "INSUFFICIENT_HISTORY",
        "ESTIMATED_VALUE",
    }
