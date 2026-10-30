"""Per-activity metrics from the EFFECTIVE view (metrics spec 8, 9.1, 9.2; AT-MET-001)."""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

import pytest

from activity_world import SCOPE, World
from coach.application.identity import Principal
from coach.domain.activity.fields import HrZones, StreamKind, StreamRef
from coach.domain.activity.observation import SourceKind
from coach.domain.activity.streams import TimeSeries
from coach.domain.athlete.physiology import HeartRateZoneModel, ZoneMethod, ZoneSource
from coach.domain.common.evidence import EvidenceKind
from coach.domain.common.quality import Confidence, UncertaintyReason
from coach.domain.common.sport import Sport
from coach.metrics.derived import DerivedMetricValue, DerivedStatus, MetricSubject
from samples import START, USER, age_predicted_zones, sample_observation, tennis_values

PRINCIPAL = Principal(USER)
SERIES_SHA = "e" * 64
MODEL = HeartRateZoneModel(
    zone_model_id=UUID("0193b2a0-0000-7000-8000-000000000601"),
    method=ZoneMethod.CUSTOM,
    source=ZoneSource.ATHLETE_DECLARED,
    boundaries_bpm=(100, 130, 150, 170),
    valid_from=date(2026, 1, 1),
)


def current(
    world: World, metric_id: str, activity_id: UUID, zone: int | None = None
) -> DerivedMetricValue:
    value = world.metrics.current(SCOPE, metric_id, MetricSubject.activity(activity_id, zone=zone))
    assert value is not None, metric_id
    return value


def series() -> TimeSeries:
    # 10 s at 120 bpm (Z1), 20 s at 140 (Z2), 10 s at 180 (Z3, above the last boundary)
    return TimeSeries(
        start_time_utc=START,
        offset_seconds=(0.0, 10.0, 20.0, 30.0, 40.0),
        heart_rate_bpm=(120.0, 140.0, 140.0, 180.0, 180.0),
    )


def run_with_series(world: World) -> UUID:
    world.series[SERIES_SHA] = series()
    observation = sample_observation(streams=(StreamRef(StreamKind.TIME_SERIES, SERIES_SHA),))
    return world.ingest(observation).activity.activity_id


# ---------------------------------------------------------------- speed and pace


@pytest.mark.req("FR-MET-001", "FR-MET-004")
@pytest.mark.acceptance("AT-MET-001")
def test_a_run_gets_speed_and_pace_from_distance_and_moving_time() -> None:
    world = World()
    activity_id = world.ingest(sample_observation()).activity.activity_id
    speed = current(world, "any.speed_avg", activity_id)
    pace = current(world, "run.pace_avg", activity_id)
    assert speed.value == pytest.approx(10050.5 / 3650.0)
    assert pace.value == pytest.approx(3650.0 / 10.0505)
    assert [(record.name, record.evidence.field) for record in pace.inputs] == [
        ("distance_meters", "distance.meters"),
        ("duration_seconds", "timing.movingSeconds"),
    ]
    assert all(record.evidence.ref_id == activity_id for record in pace.inputs)
    assert speed.uncertainty_reasons == frozenset()
    swim = world.metrics.current(SCOPE, "swim.pace_100m_avg", MetricSubject.activity(activity_id))
    assert swim is None  # not a swim
    srpe = current(world, "any.srpe_load", activity_id)
    assert (srpe.status, srpe.missing_inputs) == (DerivedStatus.MISSING_INPUT, ("rpe",))


@pytest.mark.req("FR-MET-004")
def test_elapsed_time_stands_in_for_an_unknown_moving_time_and_says_so() -> None:
    world = World()
    observation = sample_observation(without=("timing.movingSeconds",))
    activity_id = world.ingest(observation).activity.activity_id
    speed = current(world, "any.speed_avg", activity_id)
    assert speed.value == pytest.approx(10050.5 / 3720.0)
    assert speed.inputs[1].evidence.field == "timing.elapsedSeconds"
    assert UncertaintyReason.ESTIMATED_VALUE in speed.uncertainty_reasons


@pytest.mark.req("FR-MET-004")
def test_a_correction_recomputes_and_supersedes_and_keeps_the_old_value() -> None:
    world = World()
    activity_id = world.ingest(sample_observation()).activity.activity_id
    before = current(world, "any.speed_avg", activity_id)
    world.service.correct(
        SCOPE, PRINCIPAL, activity_id, "distance.meters", 10000.0, "Measured course"
    )
    after = current(world, "any.speed_avg", activity_id)
    assert after.value == pytest.approx(10000.0 / 3650.0)
    assert world.metrics.current(SCOPE, "any.speed_avg", after.subject) == after
    history = world.derived.history(SCOPE, "any.speed_avg", after.subject)
    assert [item.value_id for item in history] == [before.value_id, after.value_id]


@pytest.mark.req("FR-MET-004", "FR-ACT-006")
def test_a_metric_that_no_longer_applies_is_no_longer_reported() -> None:
    world = World()
    activity_id = world.ingest(sample_observation()).activity.activity_id
    view = world.service.effective(SCOPE, activity_id).activity
    assert {value.metric_id for value in world.engine.current(SCOPE, view)} >= {
        "any.speed_avg",
        "run.pace_avg",
    }
    world.service.correct(
        SCOPE, PRINCIPAL, activity_id, "classification.sport", Sport.TENNIS, "It was tennis"
    )
    view = world.service.effective(SCOPE, activity_id).activity
    reported = {value.metric_id for value in world.engine.current(SCOPE, view)}
    assert "run.pace_avg" not in reported
    assert "any.speed_avg" not in reported
    # Retired, never deleted: the stored current value says NOT_APPLICABLE (metrics spec 4).
    subject = MetricSubject.activity(activity_id)
    assert world.metrics.current(SCOPE, "run.pace_avg", subject) is None
    retired = world.derived.current(SCOPE, "run.pace_avg", subject)
    assert retired is not None
    assert (retired.status, retired.value, retired.inputs) == (
        DerivedStatus.NOT_APPLICABLE,
        None,
        (),
    )
    history = world.derived.history(SCOPE, "run.pace_avg", subject)
    assert [item.status for item in history] == [
        DerivedStatus.COMPUTED,
        DerivedStatus.NOT_APPLICABLE,
    ]
    world.service.correct(
        SCOPE, PRINCIPAL, activity_id, "classification.sport", Sport.RUNNING, "It was a run"
    )
    assert world.metrics.current(SCOPE, "run.pace_avg", subject) is not None  # applies again


@pytest.mark.req("FR-MET-004")
def test_a_low_confidence_activity_has_no_speed() -> None:
    world = World()
    # Moving time longer than elapsed time is a blocking anomaly: confidence LOW.
    observation = sample_observation(updates={"timing.movingSeconds": 4000.0})
    activity_id = world.ingest(observation).activity.activity_id
    speed = current(world, "any.speed_avg", activity_id)
    assert speed.status is DerivedStatus.BELOW_MINIMUM_QUALITY
    assert speed.input_confidence is Confidence.LOW
    assert UncertaintyReason.SENSOR_QUALITY in speed.uncertainty_reasons


@pytest.mark.req("FR-MET-001")
def test_tennis_gets_session_rpe_load_but_no_speed() -> None:
    world = World()
    entry = sample_observation(
        SourceKind.ATHLETE_ENTRY, values=tennis_values(**{"subjective.rpe": 7.0})
    )
    activity_id = world.ingest(entry).activity.activity_id
    assert current(world, "any.srpe_load", activity_id).value == 7 * 90.0
    assert (
        world.metrics.current(SCOPE, "any.speed_avg", MetricSubject.activity(activity_id)) is None
    )
    view = world.service.effective(SCOPE, activity_id).activity
    assert view.values["training.load.internal.sessionRpeLoad"] == 630.0


# ---------------------------------------------------------------- time in zones


@pytest.mark.req("FR-MET-001", "FR-MET-004", "FR-MET-005")
def test_time_in_zones_from_the_heart_rate_series_and_the_zone_model() -> None:
    world = World()
    world.zone_models.append(MODEL)
    activity_id = run_with_series(world)
    seconds = [current(world, "any.hr_zone_time", activity_id, zone).value for zone in (1, 2, 3)]
    assert seconds == [10.0, 20.0, 10.0]
    zone_two = current(world, "any.hr_zone_time", activity_id, 2)
    by_name = {record.name: record for record in zone_two.inputs}
    assert (by_name["zone_lower_bpm"].value, by_name["zone_upper_bpm"].value) == (130.0, 150.0)
    assert by_name["zone_lower_bpm"].evidence.kind is EvidenceKind.HR_ZONE_MODEL
    assert by_name["zone_lower_bpm"].evidence.ref_id == MODEL.zone_model_id
    series_evidence = by_name["seconds_in_zone"].evidence
    assert (series_evidence.kind, series_evidence.field, series_evidence.note) == (
        EvidenceKind.RAW_SOURCE,
        "streams.timeSeries",
        f"sha256 {SERIES_SHA}",
    )
    view = world.service.effective(SCOPE, activity_id).activity
    assert view.values["cardiovascular.hrZones"] == HrZones(MODEL.zone_model_id, (10.0, 20.0, 10.0))


@pytest.mark.req("FR-MET-004")
def test_without_a_series_every_zone_is_unknown() -> None:
    world = World()
    world.zone_models.append(MODEL)
    activity_id = world.ingest(sample_observation()).activity.activity_id
    for zone in (1, 2, 3):
        value = current(world, "any.hr_zone_time", activity_id, zone)
        assert (value.status, value.missing_inputs) == (
            DerivedStatus.MISSING_INPUT,
            ("seconds_in_zone",),
        )
    view = world.service.effective(SCOPE, activity_id).activity
    assert view.values["cardiovascular.hrZones"] is None


@pytest.mark.req("FR-PRF-001", "FR-MET-004")
def test_zones_need_a_model_valid_on_the_training_day() -> None:
    world = World()
    later = HeartRateZoneModel(
        zone_model_id=UUID(int=8),
        method=ZoneMethod.CUSTOM,
        source=ZoneSource.ATHLETE_DECLARED,
        boundaries_bpm=(110, 140, 160),
        valid_from=START.date() + timedelta(days=1),
    )
    world.zone_models.append(later)
    activity_id = run_with_series(world)
    assert (
        world.metrics.current(
            SCOPE, "any.hr_zone_time", MetricSubject.activity(activity_id, zone=1)
        )
        is None
    )
    view = world.service.effective(SCOPE, activity_id).activity
    assert "cardiovascular.hrZones" not in view.values


@pytest.mark.req("FR-MET-004")
def test_a_new_zone_model_applies_after_recomputation_and_old_values_are_ignored() -> None:
    world = World()
    world.zone_models.append(MODEL)
    activity_id = run_with_series(world)
    newer = HeartRateZoneModel(
        zone_model_id=UUID(int=9),
        method=ZoneMethod.CUSTOM,
        source=ZoneSource.ATHLETE_DECLARED,
        boundaries_bpm=(100, 145, 200),
        valid_from=date(2026, 6, 1),
    )
    world.zone_models.append(newer)
    view = world.service.effective(SCOPE, activity_id).activity
    assert view.values["cardiovascular.hrZones"] is None  # stored values are of the old model
    world.engine.recompute(SCOPE, view)
    view = world.service.effective(SCOPE, activity_id).activity
    assert view.values["cardiovascular.hrZones"] == HrZones(newer.zone_model_id, (30.0, 10.0))
    zone_ids = [
        value.subject.zone
        for value in world.engine.current(SCOPE, view)
        if value.metric_id == "any.hr_zone_time"
    ]
    assert zone_ids == [1, 2]  # the third zone of the old model is not reported


# ---------------------------------------------------------------- review findings (P8)


def run_with(world: World, series_value: TimeSeries | None, digest: str = SERIES_SHA) -> UUID:
    if series_value is not None:
        world.series[digest] = series_value
    observation = sample_observation(streams=(StreamRef(StreamKind.TIME_SERIES, digest),))
    return world.ingest(observation).activity.activity_id


@pytest.mark.req("FR-MET-004", "NFR-REL-001")
def test_more_than_a_day_in_one_zone_is_unknown_not_a_crash() -> None:
    world = World()
    world.zone_models.append(MODEL)
    long_series = TimeSeries(
        start_time_utc=START,
        offset_seconds=tuple(float(second) for second in range(0, 90_001, 10)),
        heart_rate_bpm=tuple(140.0 for _ in range(0, 90_001, 10)),
    )
    activity_id = run_with(world, long_series)
    zone_two = current(world, "any.hr_zone_time", activity_id, 2)
    assert (zone_two.status, zone_two.missing_inputs) == (
        DerivedStatus.MISSING_INPUT,
        ("seconds_in_zone",),
    )
    assert UncertaintyReason.SENSOR_QUALITY in zone_two.uncertainty_reasons
    assert current(world, "any.hr_zone_time", activity_id, 1).value == 0.0


@pytest.mark.req("FR-MET-004", "NFR-REL-001")
def test_a_series_that_cannot_be_read_gives_unknown_zones() -> None:
    world = World()
    world.zone_models.append(MODEL)
    activity_id = run_with(world, None, digest="f" * 64)  # no blob for that stream
    value = current(world, "any.hr_zone_time", activity_id, 1)
    assert value.status is DerivedStatus.MISSING_INPUT


@pytest.mark.req("FR-MET-004")
def test_sparse_heart_rate_marks_zone_times_as_missing_data() -> None:
    world = World()
    world.zone_models.append(MODEL)
    sparse = TimeSeries(
        start_time_utc=START,
        offset_seconds=tuple(float(second) for second in range(0, 101, 10)),
        heart_rate_bpm=(140.0, *([None] * 9), 140.0),
    )
    activity_id = run_with(world, sparse)
    zone_two = current(world, "any.hr_zone_time", activity_id, 2)
    assert (zone_two.status, zone_two.value) == (DerivedStatus.COMPUTED, 10.0)
    assert UncertaintyReason.MISSING_DATA in zone_two.uncertainty_reasons


@pytest.mark.req("FR-MET-004")
def test_an_unchanged_series_is_not_read_again() -> None:
    world = World()
    world.zone_models.append(MODEL)
    # a ride: no running analysis reads the series (a run that does not qualify for one is
    # read again, since nothing is stored for it; metrics spec 10)
    ride = sample_observation(
        streams=(StreamRef(StreamKind.TIME_SERIES, SERIES_SHA),),
        updates={"classification.sport": Sport.CYCLING},
    )
    world.series[SERIES_SHA] = series()
    activity_id = world.ingest(ride).activity.activity_id
    assert world.series_reads == [SERIES_SHA]
    view = world.service.effective(SCOPE, activity_id).activity
    world.engine.recompute(SCOPE, view)
    assert world.series_reads == [SERIES_SHA]  # reused from the stored snapshot
    world.zone_models.append(
        HeartRateZoneModel(
            zone_model_id=UUID(int=10),
            method=ZoneMethod.CUSTOM,
            source=ZoneSource.ATHLETE_DECLARED,
            boundaries_bpm=(100, 145, 200),
            valid_from=date(2026, 6, 1),
        )
    )
    world.engine.recompute(SCOPE, world.service.effective(SCOPE, activity_id).activity)
    assert world.series_reads == [SERIES_SHA, SERIES_SHA]  # a new model needs the series
    third = current(world, "any.hr_zone_time", activity_id, 2)
    assert third.value == 10.0
    retired = world.derived.current(
        SCOPE, "any.hr_zone_time", MetricSubject.activity(activity_id, zone=3)
    )
    assert retired is not None
    assert retired.status is DerivedStatus.NOT_APPLICABLE  # the new model has two zones


@pytest.mark.req("NFR-TRC-001", "FR-ACT-006")
def test_a_corrected_input_names_its_correction() -> None:
    world = World()
    activity_id = world.ingest(sample_observation()).activity.activity_id
    override = world.service.correct(
        SCOPE, PRINCIPAL, activity_id, "distance.meters", 10000.0, "Measured course"
    ).override
    speed = current(world, "any.speed_avg", activity_id)
    distance = next(record for record in speed.inputs if record.name == "distance_meters")
    assert distance.evidence.note == f"override {override.override_id}"


# ---------------------------------------------------------------- age-predicted zones (ADR-028)

BORN = date(1971, 3, 10)  # 55 on the sample's training day (2026-10-20): 220 - 55 = 165 bpm


def estimated_world() -> tuple[World, UUID]:
    world = World()
    world.birth_date = BORN
    world.zone_models.append(age_predicted_zones(valid_from=date(2026, 1, 1)))
    return world, run_with_series(world)


@pytest.mark.req("FR-PRF-002", "FR-MET-004")
@pytest.mark.acceptance("AT-MET-003")
def test_age_predicted_zones_are_estimates_naming_the_age() -> None:
    world, activity_id = estimated_world()
    # boundaries 85 98 118 134 149 165: 120 bpm is Z3, 140 is Z4, 180 is Z5
    seconds = [current(world, "any.hr_zone_time", activity_id, zone).value for zone in range(1, 6)]
    assert seconds == [0.0, 0.0, 10.0, 20.0, 10.0]
    zone_four = current(world, "any.hr_zone_time", activity_id, 4)
    assert zone_four.status is DerivedStatus.COMPUTED
    assert UncertaintyReason.ESTIMATED_VALUE in zone_four.uncertainty_reasons
    assert zone_four.input_confidence is Confidence.MEDIUM
    by_name = {record.name: record for record in zone_four.inputs}
    lower, upper = by_name["zone_lower_bpm"], by_name["zone_upper_bpm"]
    assert (lower.value, lower.confidence) == (134.0, Confidence.MEDIUM)
    assert (lower.evidence.kind, lower.evidence.field, lower.evidence.note) == (
        EvidenceKind.HR_ZONE_MODEL,
        "boundariesPercentMaxHr[3]",
        "81% of 220 - age 55 = 165 bpm",
    )
    assert (upper.value, upper.evidence.field) == (149.0, "boundariesPercentMaxHr[4]")
    view = world.service.effective(SCOPE, activity_id).activity
    model_id = world.zone_models[0].zone_model_id
    assert view.values["cardiovascular.hrZones"] == HrZones(model_id, (0.0, 0.0, 10.0, 20.0, 10.0))


@pytest.mark.req("FR-PRF-002", "FR-MET-004")
@pytest.mark.acceptance("AT-MET-003")
def test_a_birth_date_change_moves_the_age_predicted_zones() -> None:
    world, activity_id = estimated_world()
    world.birth_date = date(1956, 6, 15)  # 70 years: 150 bpm, boundaries 77 89 107 122 135 150
    view = world.service.effective(SCOPE, activity_id).activity
    assert view.values["cardiovascular.hrZones"] is None  # stored values used other boundaries
    world.engine.recompute(SCOPE, view)
    seconds = [current(world, "any.hr_zone_time", activity_id, zone).value for zone in range(1, 6)]
    assert seconds == [0.0, 0.0, 10.0, 0.0, 30.0]
    world.birth_date = None  # no age: the model gives no zones and the values are retired
    world.engine.recompute(SCOPE, world.service.effective(SCOPE, activity_id).activity)
    for zone in range(1, 6):
        subject = MetricSubject.activity(activity_id, zone=zone)
        retired = world.derived.current(SCOPE, "any.hr_zone_time", subject)
        assert retired is not None
        assert retired.status is DerivedStatus.NOT_APPLICABLE
