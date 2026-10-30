"""Running session metrics in the engine (running spec 4; metrics spec 10). Synthetic data."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from uuid import UUID

import pytest

from activity_world import SCOPE, World
from coach.application.identity import Principal
from coach.domain.activity.fields import StreamKind, StreamRef
from coach.domain.activity.segments import Lap, LapIntensity, Laps
from coach.domain.activity.streams import TimeSeries
from coach.domain.common.evidence import EvidenceKind
from coach.domain.common.quality import UncertaintyReason
from coach.metrics.derived import DerivedMetricValue, DerivedStatus, MetricSubject
from samples import START, USER, sample_observation

PRINCIPAL = Principal(USER)
SERIES_SHA = "a" * 64
LAPS_SHA = "b" * 64


def steady(
    seconds: int = 2400,
    heart_rate: Callable[[int], float | None] = lambda second: 140.0 if second < 1500 else 150.0,
    speed: Callable[[int], float] = lambda _: 3.0,
    altitude: Callable[[int], float] | None = None,
) -> TimeSeries:
    distances, covered = [], 0.0
    for second in range(seconds + 1):
        distances.append(covered)
        covered += speed(second)
    return TimeSeries(
        start_time_utc=START,
        offset_seconds=tuple(float(second) for second in range(seconds + 1)),
        distance_meters=tuple(distances),
        heart_rate_bpm=tuple(heart_rate(second) for second in range(seconds + 1)),
        altitude_meters=(
            None if altitude is None else tuple(altitude(s) for s in range(seconds + 1))
        ),
    )


def intervals() -> Laps:
    plan = [(2000.0, 600.0, LapIntensity.WARMUP)]
    for timer in (200.0, 205.0, 210.0, 220.0):
        plan += [(1000.0, timer, LapIntensity.ACTIVE), (150.0, 90.0, LapIntensity.REST)]
    offset, laps = 0.0, []
    for distance, timer, intensity in plan:
        laps.append(
            Lap(
                start_offset_seconds=offset,
                timer_seconds=timer,
                distance_meters=distance,
                intensity=intensity,
            )
        )
        offset += timer
    return Laps(start_time_utc=START, laps=tuple(laps))


def run(
    world: World,
    series: TimeSeries | None = None,
    laps: Laps | None = None,
    updates: Mapping[str, object] | None = None,
) -> UUID:
    streams = []
    if series is not None:
        world.series[SERIES_SHA] = series
        streams.append(StreamRef(StreamKind.TIME_SERIES, SERIES_SHA))
    if laps is not None:
        world.segments[LAPS_SHA] = laps
        streams.append(StreamRef(StreamKind.LAPS, LAPS_SHA))
    observation = sample_observation(streams=tuple(streams), updates=updates)
    return world.ingest(observation).activity.activity_id


def current(world: World, metric_id: str, activity_id: UUID) -> DerivedMetricValue | None:
    return world.metrics.current(SCOPE, metric_id, MetricSubject.activity(activity_id))


def value_of(world: World, metric_id: str, activity_id: UUID) -> DerivedMetricValue:
    found = current(world, metric_id, activity_id)
    assert found is not None, metric_id
    return found


@pytest.mark.req("FR-RUN-001", "FR-RUN-002", "FR-RUN-003", "FR-RUN-004", "FR-MET-004")
@pytest.mark.acceptance("AT-MET-001")
def test_a_steady_run_gets_drift_variability_and_efficiency() -> None:
    world = World()
    activity_id = run(world, steady())
    drift = value_of(world, "run.hr_drift", activity_id)
    assert drift.status is DerivedStatus.COMPUTED
    assert drift.value == pytest.approx((1 - 140 / 150) * 100)
    assert drift.uncertainty_reasons == frozenset()
    record = drift.inputs[0]
    assert (record.evidence.kind, record.evidence.field, record.evidence.note) == (
        EvidenceKind.RAW_SOURCE,
        "streams.timeSeries",
        f"sha256 {SERIES_SHA}",
    )
    variability = value_of(world, "run.pace_variability", activity_id)
    assert variability.value == pytest.approx(0.0, abs=1e-9)  # an even 3 m/s
    efficiency = value_of(world, "run.efficiency_hr", activity_id)
    assert efficiency.value == pytest.approx(10050.5 / 3650.0 * 60 / 148)
    assert world.series_reads == [SERIES_SHA]  # read once for every analysis
    assert current(world, "run.interval_degradation", activity_id) is None  # no laps


@pytest.mark.req("FR-RUN-003")
def test_an_interval_session_gets_its_degradation_from_the_laps() -> None:
    world = World()
    activity_id = run(world, laps=intervals())
    degradation = value_of(world, "run.interval_degradation", activity_id)
    assert degradation.value == pytest.approx((5.0 - 1000 / 220) / 5.0 * 100)  # k = 1 of 4
    assert {record.evidence.field for record in degradation.inputs} == {"streams.laps"}
    assert {record.evidence.note for record in degradation.inputs} == {f"sha256 {LAPS_SHA}"}


@pytest.mark.req("FR-RUN-001", "FR-MET-004")
def test_a_run_without_its_series_is_unknown_and_an_unsteady_one_has_no_drift() -> None:
    world = World()
    plain = run(world)
    drift = value_of(world, "run.hr_drift", plain)
    assert (drift.status, drift.missing_inputs) == (
        DerivedStatus.MISSING_INPUT,
        (
            "first_half_speed_mps",
            "first_half_hr_bpm",
            "second_half_speed_mps",
            "second_half_hr_bpm",
        ),
    )
    other = World()
    surging = run(other, steady(speed=lambda second: 2.0 if (second // 300) % 2 else 4.0))
    assert current(other, "run.hr_drift", surging) is None  # not steady: no value
    surges = value_of(other, "run.pace_variability", surging).value
    assert surges is not None
    assert surges > 0


@pytest.mark.req("FR-RUN-001", "FR-ACT-010")
def test_stream_and_activity_reasons_are_kept() -> None:
    world = World()
    hilly = run(world, steady(altitude=lambda second: 10.0 if second < 1500 else 60.0))
    reasons = value_of(world, "run.hr_drift", hilly).uncertainty_reasons
    assert UncertaintyReason.ENVIRONMENT in reasons
    sparse = World()
    gaps = run(sparse, steady(heart_rate=lambda second: None if 1500 <= second < 1700 else 145.0))
    assert (
        UncertaintyReason.MISSING_DATA in value_of(sparse, "run.hr_drift", gaps).uncertainty_reasons
    )
    noisy = World()
    implausible = run(noisy, steady(), updates={"cardiovascular.averageHr": 245})  # HR anomaly
    drift = value_of(noisy, "run.hr_drift", implausible)
    assert UncertaintyReason.SENSOR_QUALITY in drift.uncertainty_reasons
    efficiency = value_of(noisy, "run.efficiency_hr", implausible)
    assert (efficiency.status, efficiency.missing_inputs) == (
        DerivedStatus.MISSING_INPUT,
        ("average_hr_bpm",),  # outside 30 to 230 bpm: not credible
    )


@pytest.mark.req("FR-MET-004", "NFR-REL-001")
def test_a_qualifying_run_is_not_read_again_and_other_sports_retire_the_values() -> None:
    world = World()
    activity_id = run(world, steady(), laps=intervals())
    view = world.service.effective(SCOPE, activity_id).activity
    world.engine.recompute(SCOPE, view)
    assert world.series_reads == [SERIES_SHA]  # its values carry the same stream
    world.service.correct(SCOPE, PRINCIPAL, activity_id, "classification.sport", "CYCLING", "Bike")
    for metric_id in ("run.hr_drift", "run.pace_variability", "run.interval_degradation"):
        retired = world.derived.current(SCOPE, metric_id, MetricSubject.activity(activity_id))
        assert retired is not None
        assert retired.status is DerivedStatus.NOT_APPLICABLE
