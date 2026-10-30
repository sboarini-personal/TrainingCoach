"""Cycling session analyses (cycling spec 6; version 1.0.0). Synthetic series only."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from coach.domain.activity.segments import Lap, LapIntensity, Laps
from coach.domain.activity.streams import TimeSeries
from coach.metrics.cycling import (
    POWER_ZONES,
    power_halves,
    power_zone_times,
    repetition_powers,
    work_kilojoules,
)
from samples import NOW


def ride(seconds: int, watts: Callable[[int], float | None]) -> TimeSeries:
    return TimeSeries(
        start_time_utc=NOW,
        offset_seconds=tuple(float(second) for second in range(seconds + 1)),
        power_watts=tuple(watts(second) for second in range(seconds + 1)),
    )


@pytest.mark.req("FR-CYC-004", "FR-MET-001")
def test_work_adds_power_times_duration() -> None:
    assert work_kilojoules(ride(3600, lambda _: 200.0)) == pytest.approx(720.0)
    with_unknown = ride(3600, lambda second: None if second < 600 else 200.0)
    assert work_kilojoules(with_unknown) == pytest.approx(600.0)  # unknown power adds nothing
    gap = TimeSeries(
        start_time_utc=NOW, offset_seconds=(0.0, 10.0, 70.0), power_watts=(200.0, 200.0, 200.0)
    )
    assert work_kilojoules(gap) == pytest.approx(2.0)  # the 60 s gap is a pause
    no_power = TimeSeries(start_time_utc=NOW, offset_seconds=(0.0, 1.0))
    assert work_kilojoules(no_power) is None


@pytest.mark.req("FR-CYC-004")
def test_power_halves_of_a_long_enough_ride() -> None:
    halves = power_halves(ride(2400, lambda second: 250.0 if second < 1200 else 200.0))
    assert halves == pytest.approx((250.0, 200.0))
    assert power_halves(ride(1000, lambda _: 200.0)) is None  # under 1,200 s of power


@pytest.mark.req("FR-CYC-004", "FR-MET-001")
def test_power_zones_relative_to_ftp() -> None:
    # 100 s at each level for an FTP of 250 W: 0.4, 0.6, 0.8, 1.0, 1.12, 1.28 and 1.6 x FTP
    levels = (100.0, 150.0, 200.0, 250.0, 280.0, 320.0, 400.0)
    times = power_zone_times(ride(700, lambda second: levels[min(second // 100, 6)]), 250.0)
    assert times is not None
    assert len(times.seconds) == POWER_ZONES
    assert times.seconds == pytest.approx((100.0,) * 7)
    edges = power_zone_times(ride(200, lambda second: 137.5 if second < 100 else 375.0), 250.0)
    assert edges is not None
    assert (edges.seconds[1], edges.seconds[6]) == pytest.approx((100.0, 100.0))  # lower bounds
    # whole watts exactly on a bound: 55 % of 200 W is 110 W, in Z2 (not 110.00000000000001)
    exact = power_zone_times(ride(10, lambda _: 110.0), 200.0)
    assert exact is not None
    assert exact.seconds[1] == pytest.approx(10.0)


def laps(*items: tuple[float | None, float | None, LapIntensity]) -> Laps:
    offset, built = 0.0, []
    for timer, watts, intensity in items:
        built.append(
            Lap(
                start_offset_seconds=offset,
                timer_seconds=timer,
                average_power_watts=watts,
                intensity=intensity,
            )
        )
        offset += timer or 60.0
    return Laps(start_time_utc=NOW, laps=tuple(built))


WORK, REST = LapIntensity.INTERVAL, LapIntensity.RECOVERY


@pytest.mark.req("FR-CYC-004")
def test_repetition_powers_of_structured_sessions_only() -> None:
    session = laps(
        (300, 300.0, WORK),
        (120, 120.0, REST),
        (290, 290.0, WORK),
        (120, 110.0, REST),
        (310, 280.0, WORK),
    )
    assert repetition_powers(session) == [300.0, 290.0, 280.0]
    assert (
        repetition_powers(laps((300, 300.0, WORK), (300, 290.0, WORK), (300, 280.0, WORK))) is None
    )
    uneven = laps(
        (300, 300.0, WORK),
        (120, 120.0, REST),
        (60, 400.0, WORK),
        (120, 110.0, REST),
        (300, 280.0, WORK),
    )
    assert repetition_powers(uneven) is None
    without = laps(
        (300, 300.0, WORK),
        (120, 120.0, REST),
        (300, None, WORK),
        (120, 110.0, REST),
        (300, 280.0, WORK),
    )
    assert repetition_powers(without) is None
