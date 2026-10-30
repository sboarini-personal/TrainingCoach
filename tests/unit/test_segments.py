"""Laps and pool lengths as domain values (files spec 8.6)."""

from __future__ import annotations

from typing import Any

import pytest

from coach.domain.activity.fields import CadenceUnit
from coach.domain.activity.segments import Lap, Laps, Lengths, SwimLength
from coach.domain.common.errors import DomainValidationError
from samples import NOW


@pytest.mark.req("INT-FIL-001")
@pytest.mark.parametrize(
    "fields",
    [
        {"start_offset_seconds": -1.0},
        {"start_offset_seconds": 0.0, "elapsed_seconds": 0.0},
        {"start_offset_seconds": 0.0, "timer_seconds": 86_401.0},
        {"start_offset_seconds": 0.0, "distance_meters": -5.0},
        {"start_offset_seconds": 0.0, "average_power_watts": float("nan")},
    ],
)
def test_a_lap_refuses_impossible_values(fields: dict[str, Any]) -> None:
    with pytest.raises(DomainValidationError):
        Lap(**fields)


@pytest.mark.req("INT-FIL-001")
def test_laps_ascend_and_name_their_cadence_unit() -> None:
    first, second = Lap(start_offset_seconds=0.0), Lap(start_offset_seconds=60.0)
    assert len(Laps(start_time_utc=NOW, laps=(first, second))) == 2
    with pytest.raises(DomainValidationError):
        Laps(start_time_utc=NOW, laps=(second, first))
    with pytest.raises(DomainValidationError):
        Laps(start_time_utc=NOW, laps=())
    with_cadence = Lap(start_offset_seconds=0.0, average_cadence=170.0)
    with pytest.raises(DomainValidationError):
        Laps(start_time_utc=NOW, laps=(with_cadence,))
    laps = Laps(start_time_utc=NOW, laps=(with_cadence,), cadence_unit=CadenceUnit.STEPS_PER_MIN)
    assert laps.cadence_unit is CadenceUnit.STEPS_PER_MIN


@pytest.mark.req("INT-FIL-001", "FR-SWM-002")
def test_lengths_ascend_and_count_whole_strokes() -> None:
    first = SwimLength(start_offset_seconds=0.0, strokes=16)
    second = SwimLength(start_offset_seconds=25.0, strokes=0)
    assert len(Lengths(start_time_utc=NOW, lengths=(first, second))) == 2
    with pytest.raises(DomainValidationError):
        Lengths(start_time_utc=NOW, lengths=(second, first))
    with pytest.raises(DomainValidationError):
        SwimLength(start_offset_seconds=0.0, strokes=-1)
    with pytest.raises(DomainValidationError):
        SwimLength(start_offset_seconds=0.0, elapsed_seconds=0.0)
