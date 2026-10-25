import pytest
from hypothesis import given
from hypothesis import strategies as st

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.units import (
    km_to_m,
    kmh_to_mps,
    m_to_km,
    mps_to_kmh,
    pace_seconds_per_100m,
    pace_seconds_per_km,
    speed_from_pace_seconds_per_km,
)

pytestmark = pytest.mark.req("FR-MET-001")


def test_distance_conversions() -> None:
    assert km_to_m(10.5) == 10_500
    assert m_to_km(42_195) == pytest.approx(42.195)


def test_negative_distance_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        km_to_m(-1)
    with pytest.raises(DomainValidationError):
        m_to_km(-0.1)


def test_speed_conversions() -> None:
    assert kmh_to_mps(36) == pytest.approx(10)
    assert mps_to_kmh(2.5) == pytest.approx(9)


def test_pace_is_derived_from_speed() -> None:
    assert pace_seconds_per_km(1000 / 300) == pytest.approx(300)
    assert pace_seconds_per_100m(1.0) == pytest.approx(100)


@pytest.mark.parametrize("speed", [None, 0.0, -1.0])
def test_pace_is_unknown_without_positive_speed(speed: float | None) -> None:
    assert pace_seconds_per_km(speed) is None
    assert pace_seconds_per_100m(speed) is None


@given(st.floats(min_value=0.2, max_value=30, allow_nan=False))
def test_pace_and_speed_round_trip(speed: float) -> None:
    pace = pace_seconds_per_km(speed)
    assert pace is not None
    assert speed_from_pace_seconds_per_km(pace) == pytest.approx(speed)
