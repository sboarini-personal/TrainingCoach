"""Pool swimming analyses (swimming spec 5; version 1.0.0). Synthetic lengths only."""

from __future__ import annotations

import pytest

from coach.domain.activity.fields import StrokeType
from coach.domain.activity.segments import Lengths, LengthType, SwimLength
from coach.metrics.subjects import DimensionKind, MetricSubject, dimension_kind
from coach.metrics.swimming import by_stroke, pace_degradation, sets, technical_fatigue
from samples import ACTIVITY, NOW

FREE, BACK = StrokeType.FREESTYLE, StrokeType.BACKSTROKE


def pool(
    *items: tuple[float | None, int | None, StrokeType | None, LengthType],
) -> Lengths:
    offset, built = 0.0, []
    for seconds, strokes, stroke, kind in items:
        built.append(
            SwimLength(
                start_offset_seconds=offset,
                timer_seconds=seconds,
                type=kind,
                strokes=strokes,
                stroke_type=stroke,
            )
        )
        offset += seconds or 20.0
    return Lengths(start_time_utc=NOW, lengths=tuple(built))


ACTIVE, IDLE = LengthType.ACTIVE, LengthType.IDLE


@pytest.mark.req("FR-SWM-002")
def test_only_valid_lengths_count_per_stroke() -> None:
    lengths = pool(
        (25.0, 16, FREE, ACTIVE),
        (26.0, 17, FREE, ACTIVE),
        (24.0, 15, FREE, ACTIVE),
        (25.0, 16, FREE, ACTIVE),
        (30.0, None, FREE, ACTIVE),  # no stroke count
        (40.0, 10, StrokeType.DRILL, ACTIVE),  # drill
        (20.0, None, None, IDLE),  # a rest
        (31.0, 18, BACK, ACTIVE),  # three backstroke lengths are too few
        (32.0, 18, BACK, ACTIVE),
        (31.0, 19, BACK, ACTIVE),
    )
    stats = by_stroke(lengths)
    assert set(stats) == {FREE}
    free = stats[FREE]
    assert (free.lengths, free.strokes) == (4, 64)
    assert (free.mean_seconds, free.mean_strokes) == pytest.approx((25.0, 16.0))
    assert free.stdev_seconds == pytest.approx(0.7071067811865476)


def steady_set(
    seconds: list[float], strokes: list[int], stroke: StrokeType = FREE
) -> list[tuple[float | None, int | None, StrokeType | None, LengthType]]:
    return [(time, count, stroke, ACTIVE) for time, count in zip(seconds, strokes, strict=True)]


@pytest.mark.req("FR-SWM-003")
def test_sets_end_at_a_rest_or_a_change_of_stroke() -> None:
    lengths = pool(
        *steady_set([25.0] * 6, [16] * 6),
        (20.0, None, None, IDLE),
        *steady_set([31.0] * 3, [18] * 3, BACK),
        *steady_set([26.0] * 7, [17] * 7),
    )
    assert [len(group) for group in sets(lengths)] == [6, 7]  # 3 backstroke lengths: too few


@pytest.mark.req("FR-SWM-003")
def test_pace_degradation_and_technical_fatigue_within_sets() -> None:
    slowing = pool(*steady_set([24, 24, 25, 25, 27, 27], [16, 16, 16, 16, 18, 18]))
    degradation = pace_degradation(slowing)
    assert degradation is not None
    assert (degradation.first, degradation.last) == (24.0, 27.0)  # k = 2
    assert degradation.change_percent == pytest.approx(12.5)
    assert technical_fatigue(slowing) is None  # the pace changed: not equal pace
    tiring = pool(*steady_set([25] * 9, [15, 15, 15, 16, 16, 16, 17, 17, 18]))
    fatigue = technical_fatigue(tiring)
    assert fatigue is not None
    assert (fatigue.first, fatigue.last) == pytest.approx((15.0, 52 / 3))
    short = pool(*steady_set([25] * 5, [16] * 5))
    assert (pace_degradation(short), technical_fatigue(short)) == (None, None)


@pytest.mark.req("FR-SWM-002", "FR-MET-004")
def test_a_stroke_is_a_subject_dimension() -> None:
    subject = MetricSubject.activity(ACTIVITY, stroke=FREE)
    assert (subject.dimension, subject.stroke, subject.zone) == ("FREESTYLE", FREE, None)
    assert subject.storage_key == f"{ACTIVITY}/FREESTYLE"
    assert dimension_kind("BUTTERFLY") is DimensionKind.STROKE
    with pytest.raises(ValueError, match="dimension"):
        dimension_kind("MIXED")
    with pytest.raises(ValueError, match="one dimension"):
        MetricSubject.activity(ACTIVITY, zone=1, stroke=FREE)
