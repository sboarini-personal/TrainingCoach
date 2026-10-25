"""Activity data quality rules 1.0.0 (FR-ACT-009, FR-ACT-010)."""

from datetime import timedelta

import pytest

from coach.domain.activity.fields import CATALOG, HrSensor, PowerSource, PowerSummary
from coach.domain.common.quality import Anomaly, Confidence, Reliability, UncertaintyReason
from coach.domain.common.sport import Sport
from coach.domain.common.time import TrainingDayBasis
from coach.metrics.quality import (
    EXPECTED_FIELDS,
    AnomalyCode,
    assess,
    completeness,
    detect_anomalies,
    detect_gps_jumps,
    is_blocking,
    reliability,
)
from samples import NOW, START, sample_activity, tennis_values

BASE = dict(sample_activity().values)


def _codes(values: dict[str, object]) -> list[tuple[str, str]]:
    return [(anomaly.code, anomaly.field) for anomaly in detect_anomalies(values, imported_at=NOW)]


@pytest.mark.req("FR-ACT-009")
def test_expected_fields_exist_for_every_sport() -> None:
    assert set(EXPECTED_FIELDS) == set(Sport)
    for fields in EXPECTED_FIELDS.values():
        assert fields <= set(CATALOG)


@pytest.mark.req("FR-ACT-009")
@pytest.mark.parametrize(
    ("path", "value", "flagged"),
    [
        ("cardiovascular.maxHr", 230, False),
        ("cardiovascular.maxHr", 231, True),
        ("cardiovascular.averageHr", 25, False),
        ("cardiovascular.averageHr", 24, True),
    ],
)
def test_heart_rate_range(path: str, value: int, flagged: bool) -> None:
    found = (AnomalyCode.HR_OUT_OF_RANGE, path) in _codes({**BASE, path: value})
    assert found is flagged


@pytest.mark.req("FR-ACT-009")
@pytest.mark.parametrize(
    ("sport", "speed", "flagged"),
    [
        (Sport.RUNNING, 12.0, False),
        (Sport.RUNNING, 12.1, True),
        (Sport.CYCLING, 30.1, True),
        (Sport.CYCLING, 29.0, False),
        (Sport.SWIMMING, 3.1, True),
        (Sport.TENNIS, 50.0, False),
    ],
)
def test_speed_limits_per_sport(sport: Sport, speed: float, flagged: bool) -> None:
    values = {
        **BASE,
        "classification.sport": sport,
        "classification.subtype": None,
        "performance.maxSpeedMps": speed,
    }
    found = (AnomalyCode.SPEED_IMPLAUSIBLE, "performance.maxSpeedMps") in _codes(values)
    assert found is flagged


@pytest.mark.req("FR-ACT-009")
def test_distance_too_far_for_the_time_is_an_implausible_speed() -> None:
    values = {**BASE, "distance.meters": 100_000.0}  # 100 km in about an hour of running
    assert (AnomalyCode.SPEED_IMPLAUSIBLE, "distance.meters") in _codes(values)
    no_moving = {**values, "timing.movingSeconds": None, "timing.elapsedSeconds": 36_000.0}
    assert (AnomalyCode.SPEED_IMPLAUSIBLE, "distance.meters") not in _codes(no_moving)


@pytest.mark.req("FR-ACT-009")
def test_duration_power_and_future_start() -> None:
    assert (AnomalyCode.DURATION_INCONSISTENT, "timing.movingSeconds") in _codes(
        {**BASE, "timing.movingSeconds": 3721.0}
    )
    power = {**BASE, "performance.power": PowerSummary(PowerSource.MEASURED, 250.0, 2500.0)}
    assert not _codes(power)
    surge = {**BASE, "performance.power": PowerSummary(PowerSource.MEASURED, 250.0, 2501.0)}
    assert (AnomalyCode.POWER_OUT_OF_RANGE, "performance.power") in _codes(surge)
    future = detect_anomalies(BASE, imported_at=START - timedelta(seconds=1))
    assert [anomaly.code for anomaly in future] == [AnomalyCode.TIME_IN_FUTURE]
    assert not detect_anomalies(BASE, imported_at=START)


@pytest.mark.req("FR-ACT-009")
def test_blocking_anomalies() -> None:
    assert is_blocking(Anomaly(AnomalyCode.TIME_IN_FUTURE, "timing.startTimeUtc"))
    assert is_blocking(Anomaly(AnomalyCode.SPEED_IMPLAUSIBLE, "performance.averageSpeedMps"))
    assert is_blocking(Anomaly(AnomalyCode.DURATION_INCONSISTENT, "timing.movingSeconds"))
    assert not is_blocking(Anomaly(AnomalyCode.SPEED_IMPLAUSIBLE, "performance.maxSpeedMps"))
    assert not is_blocking(Anomaly(AnomalyCode.HR_OUT_OF_RANGE, "cardiovascular.maxHr"))


@pytest.mark.req("FR-ACT-009")
def test_gps_jumps_come_from_distance_samples() -> None:
    steady = [(float(second), second * 3.0) for second in range(10)]
    assert detect_gps_jumps(steady, Sport.RUNNING) == ()
    jumped = [*steady, (10.0, 27.0 + 400.0), (10.0, 430.0)]
    (anomaly,) = detect_gps_jumps(jumped, Sport.RUNNING)
    assert (anomaly.code, anomaly.field, anomaly.detail) == (
        AnomalyCode.DISTANCE_GPS_JUMP,
        "distance.meters",
        "2 segments above 12 m/s",
    )
    assert detect_gps_jumps(jumped, Sport.TENNIS) == ()
    backwards = [(0.0, 100.0), (1.0, 50.0)]
    assert detect_gps_jumps(backwards, Sport.RUNNING) == ()


@pytest.mark.req("FR-ACT-009")
def test_completeness_is_the_share_of_known_expected_fields() -> None:
    assert completeness(BASE) == 1.0
    assert completeness({**BASE, "performance.cadence": None}) == 0.875
    tennis = BASE | tennis_values() | {"cardiovascular.averageHr": None}
    assert completeness(tennis) == 0.5  # elapsed and max HR known; average HR and RPE not


@pytest.mark.req("FR-ACT-009")
def test_reliability_from_sensors_and_anomaly_count() -> None:
    strap = {**BASE, "cardiovascular.hrSensor": HrSensor.CHEST_STRAP}
    assert reliability(strap, anomaly_count=0) is Reliability.HIGH
    assert reliability(BASE, anomaly_count=0) is Reliability.MEDIUM
    estimated = {**strap, "performance.power": PowerSummary(PowerSource.ESTIMATED, 200.0, None)}
    assert reliability(estimated, anomaly_count=0) is Reliability.MEDIUM
    unknown_power = {**strap, "performance.power": PowerSummary(PowerSource.UNKNOWN, 200.0, None)}
    assert reliability(unknown_power, anomaly_count=0) is Reliability.HIGH
    no_sensor = {**BASE, "cardiovascular.hrSensor": None}
    assert reliability(no_sensor, anomaly_count=0) is Reliability.UNKNOWN
    assert reliability(strap, anomaly_count=1) is Reliability.MEDIUM
    assert reliability(BASE, anomaly_count=1) is Reliability.LOW
    assert reliability(no_sensor, anomaly_count=1) is Reliability.LOW
    assert reliability(strap, anomaly_count=2) is Reliability.LOW


@pytest.mark.req("FR-ACT-009", "FR-ACT-010")
def test_assessment_combines_confidence_and_reasons() -> None:
    strap = {**BASE, "cardiovascular.hrSensor": HrSensor.CHEST_STRAP}
    best = assess(strap, imported_at=NOW)
    assert (best.confidence, best.uncertainty_reasons) == (Confidence.HIGH, frozenset())
    nearly = assess({**strap, "performance.cadence": None}, imported_at=NOW)
    assert (nearly.completeness, nearly.confidence) == (0.875, Confidence.HIGH)
    missing = assess(
        {**strap, "performance.cadence": None, "elevation.gainMeters": None}, imported_at=NOW
    )
    assert (missing.completeness, missing.confidence) == (0.75, Confidence.MEDIUM)
    assert missing.uncertainty_reasons == {UncertaintyReason.MISSING_DATA}
    glitch = assess({**strap, "cardiovascular.maxHr": 245}, imported_at=NOW)
    assert glitch.confidence is Confidence.MEDIUM
    assert UncertaintyReason.SENSOR_QUALITY in glitch.uncertainty_reasons
    blocked = assess({**strap, "timing.movingSeconds": 4000.0}, imported_at=NOW)
    assert blocked.confidence is Confidence.LOW
    estimated = assess(
        {**strap, "timing.trainingDayBasis": TrainingDayBasis.ATHLETE_TIMEZONE_ESTIMATED},
        imported_at=NOW,
    )
    assert estimated.uncertainty_reasons == {UncertaintyReason.ESTIMATED_VALUE}


@pytest.mark.req("FR-ACT-009")
def test_source_anomalies_join_the_assessment() -> None:
    jump = Anomaly(AnomalyCode.DISTANCE_GPS_JUMP, "distance.meters", "1 segments above 12 m/s")
    quality = assess(BASE, imported_at=NOW, source_anomalies=(jump, jump))
    assert quality.anomalies == (jump,)
    assert quality.reliability is Reliability.LOW
