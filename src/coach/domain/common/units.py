"""Canonical unit conversions. Storage keeps canonical units without rounding."""

from __future__ import annotations

from coach.domain.common.errors import DomainValidationError

METERS_PER_KILOMETER = 1000.0
SECONDS_PER_HOUR = 3600.0


def _non_negative(value: float, name: str) -> float:
    if value < 0:
        raise DomainValidationError(f"{name} must not be negative")
    return value


def km_to_m(kilometers: float) -> float:
    return _non_negative(kilometers, "distance") * METERS_PER_KILOMETER


def m_to_km(meters: float) -> float:
    return _non_negative(meters, "distance") / METERS_PER_KILOMETER


def kmh_to_mps(kilometers_per_hour: float) -> float:
    return kilometers_per_hour * METERS_PER_KILOMETER / SECONDS_PER_HOUR


def mps_to_kmh(meters_per_second: float) -> float:
    return meters_per_second * SECONDS_PER_HOUR / METERS_PER_KILOMETER


def pace_seconds_per_km(speed_mps: float | None) -> float | None:
    """Pace from speed; UNKNOWN (None) without a positive speed."""
    if speed_mps is None or speed_mps <= 0:
        return None
    return METERS_PER_KILOMETER / speed_mps


def pace_seconds_per_100m(speed_mps: float | None) -> float | None:
    if speed_mps is None or speed_mps <= 0:
        return None
    return 100.0 / speed_mps


def speed_from_pace_seconds_per_km(pace: float | None) -> float | None:
    if pace is None or pace <= 0:
        return None
    return METERS_PER_KILOMETER / pace
