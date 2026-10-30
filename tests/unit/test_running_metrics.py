"""Running session analyses (running spec 4; version 1.0.0). Synthetic series only."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from coach.domain.activity.segments import Lap, LapIntensity, Laps
from coach.domain.activity.streams import TimeSeries
from coach.metrics.running import (
    first_and_last,
    kilometre_splits,
    moving_intervals,
    repetition_speeds,
    steady_halves,
)
from samples import NOW


def series(
    seconds: int,
    speed: Callable[[int], float],
    heart_rate: Callable[[int], float | None] = lambda _: 150.0,
    altitude: Callable[[int], float | None] | None = None,
) -> TimeSeries:
    """One sample per second; the distance accumulates the speed of each second."""
    distances, covered = [], 0.0
    for second in range(seconds + 1):
        distances.append(covered)
        covered += speed(second)
    return TimeSeries(
        start_time_utc=NOW,
        offset_seconds=tuple(float(second) for second in range(seconds + 1)),
        distance_meters=tuple(distances),
        heart_rate_bpm=tuple(heart_rate(second) for second in range(seconds + 1)),
        altitude_meters=(
            None if altitude is None else tuple(altitude(second) for second in range(seconds + 1))
        ),
    )


# --------------------------------------------------------------------------- moving intervals


@pytest.mark.req("FR-RUN-001", "FR-MET-001")
def test_pauses_and_standing_still_are_not_moving_time() -> None:
    sample = TimeSeries(
        start_time_utc=NOW,
        offset_seconds=(0.0, 10.0, 20.0, 60.0, 70.0),
        distance_meters=(0.0, 30.0, 35.0, 40.0, 70.0),
    )
    intervals = moving_intervals(sample)
    # 0-10 s at 3 m/s; 10-20 s at 0.5 m/s (standing); 20-60 s a 40 s gap; 60-70 s at 3 m/s
    assert [(item.offset, item.meters) for item in intervals] == [(0.0, 30.0), (60.0, 30.0)]
    by_speed = TimeSeries(start_time_utc=NOW, offset_seconds=(0.0, 5.0), speed_mps=(2.5, None))
    assert [item.meters for item in moving_intervals(by_speed)] == [12.5]


# --------------------------------------------------------------------------- heart-rate drift


@pytest.mark.req("FR-RUN-002", "FR-MET-001")
def test_a_steady_run_drifts_when_heart_rate_rises_at_equal_speed() -> None:
    # 40 min at 3 m/s; after the 10-min warm-up the window is 30 min, halves of 15 min
    halves = steady_halves(
        series(2400, lambda _: 3.0, lambda second: 140.0 if second < 1500 else 150.0)
    )
    assert halves is not None
    assert (halves.first_speed, halves.second_speed) == pytest.approx((3.0, 3.0))
    assert (halves.first_heart_rate, halves.second_heart_rate) == pytest.approx((140.0, 150.0))
    assert (halves.first_coverage, halves.second_coverage) == pytest.approx((1.0, 1.0))
    assert halves.altitude_difference is None


@pytest.mark.req("FR-RUN-002")
def test_short_or_unsteady_runs_do_not_qualify() -> None:
    assert steady_halves(series(1500, lambda _: 3.0)) is None  # a 15-min window
    alternating = series(2400, lambda second: 2.0 if (second // 300) % 2 else 4.0)
    assert steady_halves(alternating) is None  # block speeds vary by a third


@pytest.mark.req("FR-RUN-002", "FR-ACT-010")
def test_coverage_and_altitude_of_the_halves() -> None:
    halves = steady_halves(
        series(
            2400,
            lambda _: 3.0,
            lambda second: None if 1500 <= second < 1680 else 145.0,  # 180 s of 900 unknown
            lambda second: 10.0 if second < 1500 else 45.0,
        )
    )
    assert halves is not None
    assert halves.second_coverage == pytest.approx(0.8)
    assert halves.second_heart_rate == pytest.approx(145.0)
    assert halves.altitude_difference == pytest.approx(35.0)


# --------------------------------------------------------------------------- splits


@pytest.mark.req("FR-RUN-003", "FR-MET-001")
def test_kilometre_splits_count_moving_time_only() -> None:
    assert kilometre_splits(series(800, lambda _: 4.0)) == pytest.approx([250.0, 250.0, 250.0])
    # standing for a minute inside the second kilometre does not lengthen it
    stop = series(860, lambda second: 0.0 if 300 <= second < 360 else 4.0)
    assert kilometre_splits(stop) == pytest.approx([250.0, 250.0, 250.0])
    slower = series(900, lambda second: 4.0 if second < 250 else 2.5)
    first, second, *_ = kilometre_splits(slower)
    assert (first, second) == pytest.approx((250.0, 400.0))


# --------------------------------------------------------------------------- intervals


def laps(*items: tuple[float, float | None, LapIntensity]) -> Laps:
    offset, built = 0.0, []
    for distance, timer, intensity in items:
        built.append(
            Lap(
                start_offset_seconds=offset,
                timer_seconds=timer,
                distance_meters=distance,
                intensity=intensity,
            )
        )
        offset += timer or 60.0
    return Laps(start_time_utc=NOW, laps=tuple(built))


WORK, REST = LapIntensity.ACTIVE, LapIntensity.REST


@pytest.mark.req("FR-RUN-003")
def test_repetitions_of_a_structured_interval_session() -> None:
    session = laps(
        (2000, 600, LapIntensity.WARMUP),
        (1000, 200, WORK),
        (150, 90, REST),
        (1000, 205, WORK),
        (150, 90, REST),
        (990, 210, LapIntensity.INTERVAL),
        (150, 90, LapIntensity.RECOVERY),
        (1000, 220, WORK),
        (1500, 500, LapIntensity.COOLDOWN),
    )
    speeds = repetition_speeds(session)
    assert speeds is not None
    assert speeds == pytest.approx([5.0, 1000 / 205, 990 / 210, 1000 / 220])
    assert first_and_last(speeds) == pytest.approx((5.0, 1000 / 220))  # k = 1 for 4 reps
    assert first_and_last([6.0, 5.0, 4.0, 3.0, 2.0, 1.0]) == (5.5, 1.5)  # k = 2 for 6 reps


@pytest.mark.req("FR-RUN-003")
@pytest.mark.parametrize(
    "session",
    [
        laps((1000, 240, WORK), (1000, 240, WORK), (1000, 240, WORK)),  # auto-laps: no rest
        laps((1000, 200, WORK), (100, 60, REST), (1000, 200, WORK)),  # two repetitions
        laps(
            (1000, 200, WORK), (100, 60, REST), (400, 80, WORK), (100, 60, REST), (1000, 200, WORK)
        ),
        laps(
            (1000, None, WORK),
            (100, 60, REST),
            (1000, 200, WORK),
            (100, 60, REST),
            (1000, 200, WORK),
        ),
    ],
)
def test_other_sessions_have_no_comparable_repetitions(session: Laps) -> None:
    assert repetition_speeds(session) is None
