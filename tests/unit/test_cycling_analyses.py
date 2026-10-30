"""Cycling power in the engine: attribution, metrics, zones and the work total (cycling spec
2, 3, 6; AT-CYC-001). Synthetic data only."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from uuid import UUID

import pytest

from activity_world import SCOPE, World
from coach.application.analytics import AnalyticsService
from coach.application.identity import Principal
from coach.domain.activity.fields import (
    PowerSensor,
    PowerSource,
    PowerSummary,
    StreamKind,
    StreamRef,
)
from coach.domain.activity.segments import Lap, LapIntensity, Laps
from coach.domain.activity.streams import TimeSeries
from coach.domain.athlete.experience import FtpValue
from coach.domain.common.evidence import EvidenceKind
from coach.domain.common.quality import UncertaintyReason
from coach.domain.common.sport import Sport
from coach.metrics.derived import DerivedMetricValue, DerivedStatus, MetricSubject
from samples import START, USER, sample_observation

PRINCIPAL = Principal(USER)
SERIES_SHA = "c" * 64
LAPS_SHA = "d" * 64
WEEK = "2026-W43"
RIDE_DAY = date(2026, 10, 20)
POWER_METRICS = ("cyc.work_kj", "cyc.power_fade", "cyc.interval_power_consistency")


def power_series(seconds: int = 2400) -> TimeSeries:
    """250 W for the first half, 200 W for the second."""
    return TimeSeries(
        start_time_utc=START,
        offset_seconds=tuple(float(second) for second in range(seconds + 1)),
        power_watts=tuple(
            250.0 if second < seconds // 2 else 200.0 for second in range(seconds + 1)
        ),
    )


def power_laps() -> Laps:
    offset, built = 0.0, []
    for timer, watts, intensity in (
        (300.0, 300.0, LapIntensity.INTERVAL),
        (120.0, 120.0, LapIntensity.RECOVERY),
        (300.0, 290.0, LapIntensity.INTERVAL),
        (120.0, 110.0, LapIntensity.RECOVERY),
        (300.0, 280.0, LapIntensity.INTERVAL),
    ):
        built.append(
            Lap(
                start_offset_seconds=offset,
                timer_seconds=timer,
                average_power_watts=watts,
                intensity=intensity,
            )
        )
        offset += timer
    return Laps(start_time_utc=START, laps=tuple(built))


def ride(
    world: World,
    sensor: PowerSensor | None = PowerSensor.POWER_METER,
    *,
    start_shift: timedelta = timedelta(),
    raw: tuple[UUID, str] | None = None,
    extra: Mapping[str, object] | None = None,
) -> UUID:
    world.series[SERIES_SHA] = power_series()
    world.segments[LAPS_SHA] = power_laps()
    values: dict[str, object] = {
        "classification.sport": Sport.CYCLING,
        "timing.startTimeUtc": START + start_shift,
        "performance.power": PowerSummary(PowerSource.UNKNOWN, 225.0, 260.0),
    }
    if sensor is not None:
        values["performance.powerSensor"] = sensor
    values.update(extra or {})
    streams = (
        StreamRef(StreamKind.TIME_SERIES, SERIES_SHA),
        StreamRef(StreamKind.LAPS, LAPS_SHA),
    )
    if raw is None:
        observation = sample_observation(streams=streams, updates=values)
    else:
        observation = sample_observation(
            streams=streams, updates=values, raw_source_id=raw[0], sha256=raw[1]
        )
    return world.ingest(observation).activity.activity_id


def current(
    world: World, metric_id: str, activity_id: UUID, zone: int | None = None
) -> DerivedMetricValue | None:
    return world.metrics.current(SCOPE, metric_id, MetricSubject.activity(activity_id, zone=zone))


def power_source(world: World, activity_id: UUID) -> PowerSource:
    power = world.service.effective(SCOPE, activity_id).activity.values["performance.power"]
    assert isinstance(power, PowerSummary)
    return power.source


@pytest.mark.req("FR-CYC-002", "FR-CYC-003", "FR-CYC-004")
@pytest.mark.acceptance("AT-CYC-001")
@pytest.mark.parametrize(
    ("declared", "named"),
    [
        (set(), PowerSensor.POWER_METER),  # no reliable meter declared
        ({PowerSensor.POWER_METER}, None),  # power of unknown origin in the file
        ({PowerSensor.POWER_METER}, PowerSensor.SMART_TRAINER),  # another device
    ],
)
def test_no_power_analysis_without_a_reliable_declared_meter(
    declared: set[PowerSensor], named: PowerSensor | None
) -> None:
    world = World()
    world.reliable_power = declared
    world.ftp = FtpValue(250, RIDE_DAY, measured=True)
    activity_id = ride(world, named)
    assert power_source(world, activity_id) is PowerSource.UNKNOWN  # never MEASURED
    for metric_id in POWER_METRICS:
        assert current(world, metric_id, activity_id) is None
    assert current(world, "cyc.power_zone_time", activity_id, zone=1) is None  # no zones


@pytest.mark.req("FR-CYC-002", "FR-CYC-004", "FR-MET-004")
def test_measured_power_gets_work_fade_and_consistency() -> None:
    world = World()
    world.reliable_power = {PowerSensor.POWER_METER}
    activity_id = ride(world)
    assert power_source(world, activity_id) is PowerSource.MEASURED
    work = current(world, "cyc.work_kj", activity_id)
    assert work is not None
    assert work.value == pytest.approx((1200 * 250 + 1200 * 200) / 1000)
    assert work.inputs[0].evidence.field == "streams.timeSeries"
    fade = current(world, "cyc.power_fade", activity_id)
    assert fade is not None
    assert fade.value == pytest.approx(20.0)  # 250 W to 200 W
    consistency = current(world, "cyc.interval_power_consistency", activity_id)
    assert consistency is not None
    assert consistency.value == pytest.approx((8.16496580927726 / 290) * 100)
    assert current(world, "cyc.power_zone_time", activity_id, zone=1) is None  # no FTP


@pytest.mark.req("FR-CYC-003", "FR-CYC-004")
def test_power_zones_need_an_ftp_of_the_season() -> None:
    world = World()
    world.reliable_power = {PowerSensor.SMART_TRAINER}
    world.ftp = FtpValue(250, RIDE_DAY - timedelta(days=100), measured=False)
    activity_id = ride(world, PowerSensor.SMART_TRAINER)
    zones = [current(world, "cyc.power_zone_time", activity_id, zone) for zone in range(1, 8)]
    assert all(value is not None for value in zones)
    seconds = [value.value for value in zones if value is not None]
    assert seconds == pytest.approx([0, 0, 1200, 1200, 0, 0, 0])  # 0.8 (Z3) and 1.0 (Z4) x FTP
    four = zones[3]
    assert four is not None
    assert UncertaintyReason.ESTIMATED_VALUE in four.uncertainty_reasons  # FTP not measured
    ftp_record = next(record for record in four.inputs if record.name == "ftp_watts")
    assert (ftp_record.evidence.kind, ftp_record.evidence.field) == (
        EvidenceKind.ATHLETE_PROFILE,
        "experience.cycling.ftp",
    )
    world.ftp = FtpValue(250, RIDE_DAY - timedelta(days=200), measured=True)  # too old now
    world.engine.recompute(SCOPE, world.service.effective(SCOPE, activity_id).activity)
    retired = world.derived.current(
        SCOPE, "cyc.power_zone_time", MetricSubject.activity(activity_id, zone=4)
    )
    assert retired is not None
    assert retired.status is DerivedStatus.NOT_APPLICABLE


@pytest.mark.req("FR-CYC-002", "FR-ACT-006")
def test_a_corrected_power_sensor_makes_power_measured() -> None:
    world = World()
    world.reliable_power = {PowerSensor.POWER_METER}
    activity_id = ride(world, None)
    assert current(world, "cyc.work_kj", activity_id) is None
    world.service.correct(
        SCOPE, PRINCIPAL, activity_id, "performance.powerSensor", "POWER_METER", "Stages meter"
    )
    assert power_source(world, activity_id) is PowerSource.MEASURED
    assert current(world, "cyc.work_kj", activity_id) is not None


@pytest.mark.req("FR-MET-005", "FR-MET-006", "FR-CYC-004")
def test_the_week_adds_the_work_of_rides_with_measured_power() -> None:
    world = World()
    world.reliable_power = {PowerSensor.POWER_METER}
    ride(world)
    ride(
        world,
        None,
        start_shift=timedelta(days=1),
        raw=(UUID("0193b2a0-0000-7000-8000-000000000777"), "e" * 64),
    )
    analytics = AnalyticsService(
        activities=world.service,
        store=world.activities,
        overrides=world.overrides,
        metrics=world.metrics,
        consents=world.consents_gate,
    )
    report = analytics.week(SCOPE, WEEK)
    (cycling,) = report.sports
    work = cycling.totals["cyc.work_total"]
    assert work.value.value == pytest.approx(540.0)
    assert work.left_out == 1  # the second ride has no measured power
    assert "cyc.work_total" not in report.cross_sport  # never added across sports
