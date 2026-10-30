"""Pool swimming metrics in the engine (swimming spec 5; metrics spec 10). Synthetic data."""

from __future__ import annotations

from uuid import UUID

import pytest

from activity_world import SCOPE, World
from coach.application.identity import Principal
from coach.domain.activity.fields import StreamKind, StreamRef, StrokeType
from coach.domain.activity.segments import Lengths, LengthType, SwimLength
from coach.domain.common.evidence import EvidenceKind
from coach.domain.common.quality import UncertaintyReason
from coach.domain.common.sport import Sport
from coach.metrics.derived import DerivedMetricValue, DerivedStatus, MetricSubject
from samples import START, USER, sample_observation

PRINCIPAL = Principal(USER)
LENGTHS_SHA = "9" * 64
FREE = StrokeType.FREESTYLE


def lengths() -> Lengths:
    """Two freestyle sets of 6 lengths with a rest between: the second set slows down."""
    times = [24.0, 24.0, 25.0, 25.0, 24.0, 24.0, None, 25.0, 25.0, 26.0, 26.0, 28.0, 28.0]
    strokes = [16, 16, 16, 16, 16, 16, None, 16, 16, 17, 17, 18, 18]
    offset, built = 0.0, []
    for seconds, count in zip(times, strokes, strict=True):
        rest = seconds is None
        built.append(
            SwimLength(
                start_offset_seconds=offset,
                timer_seconds=30.0 if rest else seconds,
                type=LengthType.IDLE if rest else LengthType.ACTIVE,
                strokes=count,
                stroke_type=None if rest else FREE,
            )
        )
        offset += 30.0 if seconds is None else seconds
    return Lengths(start_time_utc=START, lengths=tuple(built))


def swim(world: World, *, pool: float | None = 25.0, subtype: str = "POOL") -> UUID:
    world.segments[LENGTHS_SHA] = lengths()
    updates: dict[str, object] = {
        "classification.sport": Sport.SWIMMING,
        "classification.subtype": subtype,
    }
    if pool is not None:
        updates["environment.poolLengthMeters"] = pool
    observation = sample_observation(
        streams=(StreamRef(StreamKind.LENGTHS, LENGTHS_SHA),),
        updates=updates,
        without=("performance.cadence",),
    )
    return world.ingest(observation).activity.activity_id


def value(
    world: World, metric_id: str, activity_id: UUID, stroke: StrokeType | None = None
) -> DerivedMetricValue | None:
    subject = MetricSubject.activity(activity_id, stroke=stroke)
    return world.metrics.current(SCOPE, metric_id, subject)


@pytest.mark.req("FR-SWM-002", "FR-MET-004")
def test_a_pool_swim_gets_per_stroke_swolf_distance_per_stroke_and_variability() -> None:
    world = World()
    activity_id = swim(world)
    swolf = value(world, "swim.swolf_avg", activity_id, FREE)
    assert swolf is not None
    mean_time = (24 * 4 + 25 * 4 + 26 * 2 + 28 * 2) / 12
    mean_strokes = (16 * 8 + 17 * 2 + 18 * 2) / 12
    assert swolf.value == pytest.approx(mean_time + mean_strokes)
    pool = next(record for record in swolf.inputs if record.name == "pool_length_meters")
    assert (pool.evidence.kind, pool.evidence.field) == (
        EvidenceKind.ACTIVITY,
        "environment.poolLengthMeters",
    )
    per_stroke = value(world, "swim.distance_per_stroke", activity_id, FREE)
    assert per_stroke is not None
    assert per_stroke.value == pytest.approx(25 * 12 / (16 * 8 + 17 * 2 + 18 * 2))
    variability = value(world, "swim.pace_variability", activity_id, FREE)
    assert variability is not None
    assert variability.value is not None
    assert variability.value > 0
    assert value(world, "swim.swolf_avg", activity_id, StrokeType.BACKSTROKE) is None


@pytest.mark.req("FR-SWM-003")
def test_sets_give_pace_degradation_and_technical_fatigue() -> None:
    world = World()
    activity_id = swim(world)
    degradation = value(world, "swim.pace_degradation", activity_id)
    assert degradation is not None
    # first two of each set: 24, 24, 25, 25; last two: 24, 24, 28, 28
    assert degradation.value == pytest.approx((26.0 - 24.5) / 24.5 * 100)
    fatigue = value(world, "swim.technical_fatigue", activity_id)
    assert fatigue is not None
    assert fatigue.value == pytest.approx(0.0)  # only the first set kept an equal pace


@pytest.mark.req("FR-SWM-001", "FR-SWM-002")
def test_without_a_pool_length_or_in_open_water_there_are_no_length_metrics() -> None:
    world = World()
    unknown_pool = swim(world, pool=None)
    assert value(world, "swim.swolf_avg", unknown_pool, FREE) is None
    open_water = World()
    outside = swim(open_water, pool=None, subtype="OPEN_WATER")
    assert value(open_water, "swim.pace_degradation", outside) is None
    usual = World()
    usual.pool_length = 25.0  # the athlete's resources: an estimate
    estimated = swim(usual, pool=None)
    swolf = value(usual, "swim.swolf_avg", estimated, FREE)
    assert swolf is not None
    assert UncertaintyReason.ESTIMATED_VALUE in swolf.uncertainty_reasons


@pytest.mark.req("FR-SWM-002", "FR-ACT-006")
def test_a_corrected_pool_length_recomputes_and_another_sport_retires() -> None:
    world = World()
    activity_id = swim(world)
    world.service.correct(
        SCOPE, PRINCIPAL, activity_id, "environment.poolLengthMeters", 50.0, "Long course"
    )
    per_stroke = value(world, "swim.distance_per_stroke", activity_id, FREE)
    assert per_stroke is not None
    assert per_stroke.value == pytest.approx(50 * 12 / (16 * 8 + 17 * 2 + 18 * 2))
    world.service.correct(SCOPE, PRINCIPAL, activity_id, "classification.sport", "RUNNING", "No")
    retired = world.derived.current(
        SCOPE, "swim.swolf_avg", MetricSubject.activity(activity_id, stroke=FREE)
    )
    assert retired is not None
    assert retired.status is DerivedStatus.NOT_APPLICABLE


def odd(*rows: tuple[float, int]) -> Lengths:
    offset, built = 0.0, []
    for seconds, strokes in rows:
        built.append(
            SwimLength(
                start_offset_seconds=offset,
                timer_seconds=seconds,
                type=LengthType.ACTIVE,
                strokes=strokes,
                stroke_type=FREE,
            )
        )
        offset += seconds
    return Lengths(start_time_utc=START, lengths=tuple(built))


@pytest.mark.req("FR-SWM-002", "NFR-REL-001")
@pytest.mark.parametrize(
    ("rows", "computed"),
    [
        (((25.0, 0),) * 4, False),  # a kick set the watch labels freestyle: no stroke lengths
        (((25.0, 16), (25.0, 16), (25.0, 16), (2600.0, 16)), True),  # mean above 600 s
        (((25.0, 16), (25.0, 16), (25.0, 16), (25.0, 900)), True),  # mean above 200 strokes
    ],
)
def test_odd_lengths_never_break_the_import(
    rows: tuple[tuple[float, int], ...], computed: bool
) -> None:
    world = World()
    world.segments[LENGTHS_SHA] = odd(*rows)
    observation = sample_observation(
        streams=(StreamRef(StreamKind.LENGTHS, LENGTHS_SHA),),
        updates={
            "classification.sport": Sport.SWIMMING,
            "classification.subtype": "POOL",
            "environment.poolLengthMeters": 25.0,
        },
        without=("performance.cadence",),
    )
    activity_id = world.ingest(observation).activity.activity_id  # no exception
    swolf = value(world, "swim.swolf_avg", activity_id, FREE)
    if not computed:
        assert swolf is None
        return
    assert swolf is not None
    assert swolf.status is DerivedStatus.MISSING_INPUT
    assert UncertaintyReason.SENSOR_QUALITY in swolf.uncertainty_reasons
