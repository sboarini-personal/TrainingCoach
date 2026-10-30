"""Comparable groups and trends (metrics spec 11; AT-SWM-001). Synthetic data only."""

from __future__ import annotations

import math
from datetime import date, timedelta
from uuid import UUID

import pytest

from activity_world import SCOPE, World
from coach.application.analytics import AnalyticsService, TrendReport
from coach.domain.activity.fields import StrokeType
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.quality import Confidence, UncertaintyReason
from coach.domain.common.sport import Sport
from coach.metrics.trends import Point, group_of, trend
from samples import START, sample_activity, sample_observation

UNTIL = date(2026, 10, 25)


def points(values: list[float], *, every: int = 3) -> list[Point]:
    first = date(2026, 9, 1)
    return [
        Point(first + timedelta(days=every * index), value, UUID(int=index + 1))
        for index, value in enumerate(values)
    ]


@pytest.mark.req("FR-RUN-002", "FR-RUN-004", "FR-MET-001")
def test_a_least_squares_trend_with_its_confidence() -> None:
    rising = [1.50 + 0.03 * index + (0.004 if index % 2 else -0.004) for index in range(10)]
    found = trend(points(rising), window_days=56)
    assert found is not None
    assert found.slope_per_day == pytest.approx(0.01, rel=0.05)  # 0.03 per 3 days
    assert found.change == pytest.approx(0.56, rel=0.05)
    assert found.relative_change_percent == pytest.approx(0.56 / 1.635 * 100, rel=0.05)
    assert found.confidence is Confidence.HIGH  # 10 sessions, t far above 2.5
    flat = trend(points([1.5, 1.6, 1.4, 1.6, 1.4, 1.5]), window_days=56)
    assert flat is not None
    assert flat.confidence is Confidence.LOW  # no change to speak of
    assert trend(points([1.5, 1.6, 1.7, 1.8]), window_days=56) is None  # fewer than 5
    same_day = trend(points([1.0, 2.0, 3.0, 4.0, 5.0], every=0), window_days=56)
    assert same_day is not None
    assert (same_day.slope_per_day, same_day.confidence) == (0.0, Confidence.LOW)
    exact = trend(points([1.0, 2.0, 3.0, 4.0, 5.0]), window_days=56)
    assert exact is not None
    assert math.isinf(exact.t_value)


@pytest.mark.req("FR-RUN-002", "FR-SWM-004")
@pytest.mark.parametrize(
    ("updates", "stroke", "expected"),
    [
        ({}, None, "RUNNING ROAD FLAT"),  # 85 m over 10 km
        ({"elevation.gainMeters": 180.0}, None, "RUNNING ROAD ROLLING"),
        ({"elevation.gainMeters": 400.0}, None, "RUNNING ROAD HILLY"),
        ({"classification.subtype": "TREADMILL"}, None, "RUNNING TREADMILL"),
        ({"elevation.gainMeters": None}, None, "RUNNING ROAD UNKNOWN"),
        (
            {
                "classification.sport": Sport.SWIMMING,
                "classification.subtype": "POOL",
                "environment.poolLengthMeters": 25.0,
            },
            StrokeType.FREESTYLE,
            "SWIMMING POOL 25 m FREESTYLE",
        ),
        (
            {"classification.sport": Sport.SWIMMING, "classification.subtype": "OPEN_WATER"},
            None,
            "SWIMMING OPEN_WATER",
        ),
        (
            {"classification.sport": Sport.CYCLING, "classification.subtype": "INDOOR_TRAINER"},
            None,
            "CYCLING INDOOR_TRAINER",
        ),
    ],
)
def test_comparable_groups(
    updates: dict[str, object], stroke: StrokeType | None, expected: str
) -> None:
    activity = sample_activity(sample_observation(updates=updates))
    assert group_of(activity, stroke) == expected


# --------------------------------------------------------------------------- the read model


def analytics(world: World) -> AnalyticsService:
    return AnalyticsService(
        activities=world.service,
        store=world.activities,
        overrides=world.overrides,
        metrics=world.metrics,
        consents=world.consents_gate,
    )


def session(world: World, index: int, **updates: object) -> None:
    observation = sample_observation(
        raw_source_id=UUID(int=1000 + index),
        sha256=f"{index + 1:064x}",
        updates={"timing.startTimeUtc": START - timedelta(days=4 * index), **updates},
    )
    world.ingest(observation)


@pytest.mark.req("FR-RUN-002", "FR-RUN-004")
def test_running_efficiency_trends_per_comparable_group() -> None:
    world = World()
    for index in range(6):  # road runs: heart rate falls at equal speed, efficiency rises
        session(world, index, **{"cardiovascular.averageHr": 140 + 2 * index})
    for index in range(6, 9):
        session(world, index, **{"classification.subtype": "TREADMILL"})
    report = analytics(world).trend(SCOPE, "run.efficiency_hr", until=UNTIL, weeks=8)
    road, treadmill = report.groups
    assert (road.label, len(road.points)) == ("RUNNING ROAD FLAT", 6)
    assert road.trend is not None
    assert road.trend.slope_per_day > 0  # later sessions are more efficient
    assert (treadmill.label, treadmill.trend) == ("RUNNING TREADMILL", None)  # 3: too few
    assert report.across_groups == {UncertaintyReason.NON_COMPARABLE_SESSION}
    cadence = analytics(world).trend(SCOPE, "performance.cadence", until=UNTIL, weeks=8)
    assert cadence.unit == "steps/min"
    assert sum(len(group.points) for group in cadence.groups) == 9


@pytest.mark.req("FR-SWM-001", "FR-SWM-004")
@pytest.mark.acceptance("AT-SWM-001")
def test_pool_and_open_water_are_never_one_series() -> None:
    world = World()
    pool = {
        "classification.sport": Sport.SWIMMING,
        "classification.subtype": "POOL",
        "environment.poolLengthMeters": 25.0,
        "distance.meters": 2000.0,
        "timing.movingSeconds": 2400.0,
    }
    open_water = {
        **pool,
        "classification.subtype": "OPEN_WATER",
        "environment.poolLengthMeters": None,
    }
    for index in range(5):
        session(world, index, **pool)
    for index in range(5, 10):
        session(world, index, **open_water)
    report: TrendReport = analytics(world).trend(SCOPE, "swim.pace_100m_avg", until=UNTIL, weeks=8)
    assert [group.label for group in report.groups] == [
        "SWIMMING OPEN_WATER",
        "SWIMMING POOL 25 m",
    ]
    assert all(len(group.points) == 5 for group in report.groups)
    assert UncertaintyReason.NON_COMPARABLE_SESSION in report.across_groups


@pytest.mark.req("FR-MET-002")
@pytest.mark.parametrize(
    ("metric_id", "stroke", "message"),
    [
        ("run.nothing", None, "unknown metric"),
        ("any.distance_total", None, "not a per-activity metric"),
        ("any.hr_zone_time", None, "per zone"),
        ("swim.swolf_avg", None, "needs a stroke"),
        ("run.efficiency_hr", StrokeType.FREESTYLE, "needs a stroke"),
        ("performance.cadence", StrokeType.FREESTYLE, "no stroke"),
    ],
)
def test_what_has_no_trend_is_refused(
    metric_id: str, stroke: StrokeType | None, message: str
) -> None:
    with pytest.raises(DomainValidationError, match=message):
        analytics(World()).trend(SCOPE, metric_id, until=UNTIL, weeks=8, stroke=stroke)
    with pytest.raises(DomainValidationError, match="weeks"):
        analytics(World()).trend(SCOPE, "run.efficiency_hr", until=UNTIL, weeks=0)
