"""Activity field catalogue: values are validated per field path (FR-ACT-001)."""

import math
from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from coach.domain.activity.fields import (
    CATALOG,
    CORRECTABLE_FIELDS,
    OBSERVABLE_FIELDS,
    REQUIRED_FIELDS,
    SUBTYPES,
    Cadence,
    CadenceUnit,
    HrZones,
    Pain,
    PowerSource,
    PowerSummary,
    ProviderMetric,
    StreamKind,
    StreamRef,
    Swolf,
    Terrain,
    check_subtype,
    check_values,
    field_spec,
    known_number,
)
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.provenance import MetricOrigin
from coach.domain.common.sport import Sport
from coach.domain.common.time import TrainingDayBasis


@pytest.mark.req("FR-ACT-001")
def test_required_and_derived_fields_are_in_the_catalogue() -> None:
    assert set(CATALOG) >= REQUIRED_FIELDS
    computed = {
        "timing.originalLocalTime",
        "timing.trainingDay",
        "timing.trainingDayBasis",
        "performance.averagePaceSecondsPerKm",
        "performance.averagePaceSecondsPer100m",
        "cardiovascular.hrZones",
        "training.load.internal.sessionRpeLoad",
        "strength.strengthSessionId",
    }
    assert not computed & OBSERVABLE_FIELDS
    assert set(CATALOG) - computed == OBSERVABLE_FIELDS


@pytest.mark.req("FR-ACT-006")
def test_correctable_fields_exclude_identity_timing_sources_and_derived_values() -> None:
    for path in (
        "timing.startTimeUtc",
        "timing.trainingDayBasis",
        "performance.averagePaceSecondsPerKm",
        "training.load.internal.sessionRpeLoad",
        "training.aerobicEffect",
        "subjective.comments",
    ):
        assert path not in CORRECTABLE_FIELDS


@pytest.mark.req("FR-ACT-001", "FR-PRF-002")
@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("distance.meters", math.nan),
        ("distance.meters", math.inf),
        ("distance.meters", -1.0),
        ("distance.meters", True),
        ("distance.meters", "10 km"),
        ("cardiovascular.averageHr", 148.5),
        ("cardiovascular.averageHr", 261),
        ("subjective.rpe", 10.5),
        ("subjective.soreness", 0),
        ("environment.poolLengthMeters", 0.0),
        ("environment.temperatureC", 61.0),
        ("classification.sport", "SKIING"),
        ("classification.sport", None),
        ("classification.subtype", "Road"),
        ("timing.startTimeUtc", datetime(2026, 10, 20, 21, 0)),  # noqa: DTZ001 - naive on purpose
        ("timing.originalLocalTime", datetime(2026, 10, 20, 18, 0, tzinfo=UTC)),
        ("timing.trainingDay", datetime(2026, 10, 20, tzinfo=UTC)),
        ("timing.originalTimezone", "Mars/Olympus_Mons"),
        ("timing.utcOffset", "-3"),
        ("performance.power", None),
        ("performance.cadence", 172.0),
        ("strength.strengthSessionId", "0193b2a0-0000-7000-8000-000000000101"),
    ],
)
def test_invalid_values_are_rejected(path: str, value: object) -> None:
    with pytest.raises(DomainValidationError):
        check_values({path: value})


@pytest.mark.req("FR-ACT-001")
def test_unknown_paths_are_rejected() -> None:
    with pytest.raises(DomainValidationError):
        check_values({"distance.miles": 6.2})
    with pytest.raises(DomainValidationError):
        field_spec("isAdmin")


@pytest.mark.req("FR-ACT-001", "SEC-INJ-001")
def test_values_are_returned_in_canonical_form() -> None:
    values = check_values(
        {
            "distance.meters": 10050,
            "classification.sport": "RUNNING",
            "environment.terrain": "ROAD",
            "timing.utcOffset": "-03:00",
            "metadata.name": "  Morning\x00 run  ",
            "metadata.description": "   ",
            "timing.trainingDayBasis": "CONFIGURED",
            "timing.trainingDay": date(2026, 10, 20),
        }
    )
    assert values["distance.meters"] == 10050.0
    assert isinstance(values["distance.meters"], float)
    assert values["classification.sport"] is Sport.RUNNING
    assert values["environment.terrain"] is Terrain.ROAD
    assert values["timing.trainingDayBasis"] is TrainingDayBasis.CONFIGURED
    assert values["metadata.name"] == "Morning run"
    assert values["metadata.description"] is None
    assert list(values) == [path for path in CATALOG if path in values]


@pytest.mark.req("FR-ACT-001", "FR-SWM-001", "FR-TEN-002")
def test_subtype_must_fit_the_sport() -> None:
    check_subtype(Sport.SWIMMING, "OPEN_WATER")
    check_subtype(Sport.TENNIS, None)
    for sport, subtype in ((Sport.SWIMMING, "TRACK"), (Sport.TENNIS, "ROAD"), (Sport.OTHER, "GYM")):
        with pytest.raises(DomainValidationError):
            check_subtype(sport, subtype)
    assert SUBTYPES[Sport.CYCLING] >= {"INDOOR_TRAINER", "GRAVEL"}


@pytest.mark.req("FR-ACT-001", "FR-CYC-002", "FR-MET-007")
def test_composite_values_validate_themselves() -> None:
    with pytest.raises(DomainValidationError):
        Cadence(-1.0, None, CadenceUnit.RPM)
    with pytest.raises(DomainValidationError):
        PowerSummary(PowerSource.MEASURED, math.nan, None)
    with pytest.raises(DomainValidationError):
        HrZones(UUID(int=1), ())
    with pytest.raises(DomainValidationError):
        HrZones(UUID(int=1), (1.0,) * 11)
    with pytest.raises(DomainValidationError):
        Swolf(-3.0, MetricOrigin.RAW_PROVIDER)
    with pytest.raises(DomainValidationError):
        ProviderMetric(3.4, "garmin", "aerobic training effect", MetricOrigin.APPLICATION_DERIVED)
    with pytest.raises(DomainValidationError):
        ProviderMetric(3.4, "Garmin Connect", "aerobic training effect", MetricOrigin.RAW_PROVIDER)
    with pytest.raises(DomainValidationError):
        Pain(flag=True, intensity=11)
    with pytest.raises(DomainValidationError):
        StreamRef(StreamKind.LAPS, "not-a-digest")
    assert Pain(flag=True, intensity=4, location="  left knee\x07 ").location == "left knee"


@pytest.mark.req("FR-ACT-009")
def test_known_number_distinguishes_unknown_from_absent_only_by_none() -> None:
    assert known_number({"distance.meters": 5.0}, "distance.meters") == 5.0
    assert known_number({"distance.meters": None}, "distance.meters") is None
    assert known_number({}, "distance.meters") is None
    with pytest.raises(DomainValidationError):
        known_number({"classification.sport": Sport.RUNNING}, "classification.sport")
