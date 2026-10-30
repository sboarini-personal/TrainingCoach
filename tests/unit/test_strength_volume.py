"""Strength volume: volume load and hard sets per muscle group (strength spec 8; metrics spec
9.6). Synthetic data only."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest

from activity_world import SCOPE, World
from coach.application.analytics import AnalyticsService
from coach.application.contracts import DERIVED_VALUE_CODEC
from coach.domain.activity.fields import StreamKind, StreamRef
from coach.domain.common.evidence import EvidenceKind
from coach.domain.common.quality import UncertaintyReason
from coach.domain.common.sport import Sport
from coach.domain.strength.catalogue import CATALOGUE
from coach.domain.strength.session import SetEntry, SetSource, StrengthSetResult
from coach.domain.strength.sets import SetType, WatchSet, WatchSets
from coach.domain.strength.vocabulary import MuscleGroup
from coach.metrics.derived import DerivedStatus, MetricSubject
from coach.metrics.strength import is_hard, muscle_shares, volume_load
from samples import NOW, START, sample_observation
from schemas import validation_errors

WEEK = "2026-W43"  # START is Tuesday 2026-10-20
LATER = NOW + timedelta(hours=1)


def result(
    exercise: str | None = "ex.back_squat",
    *,
    reps: int | None = 8,
    load: float | None = 60.0,
    rpe: float | None = None,
    rir: int | None = None,
) -> StrengthSetResult:
    return StrengthSetResult(
        exercise_id=exercise,
        set_number=1,
        recorded_at=START,
        source=SetSource.FIT,
        completed_reps=reps,
        actual_load_kg=load,
        actual_rpe=rpe,
        actual_rir=rir,
    )


# --------------------------------------------------------------------------- pure rules


@pytest.mark.req("FR-STR-008", "FR-MET-001")
def test_volume_load_counts_known_sets_and_leaves_the_others_out() -> None:
    sets = [result(), result(reps=6, load=70.0), result("ex.push_up", load=0.0), result(load=None)]
    found = volume_load(sets)
    assert (found.kilograms, found.counted, found.left_out) == (8 * 60 + 6 * 70, 3, 1)
    assert volume_load([]).counted == 0


@pytest.mark.req("FR-STR-008")
@pytest.mark.parametrize(
    ("rpe", "rir", "hard"),
    [
        (None, 4, True),
        (None, 5, False),
        (6.0, None, True),
        (5.5, None, False),
        (9.0, 6, False),  # RIR is read first
        (None, None, None),  # unknown effort: never assumed
    ],
)
def test_a_hard_set_is_judged_from_the_athletes_effort(
    rpe: float | None, rir: int | None, hard: bool | None
) -> None:
    assert is_hard(result(rpe=rpe, rir=rir)) is hard


@pytest.mark.req("FR-STR-008", "FR-STR-002")
def test_primary_muscles_count_one_and_secondary_half() -> None:
    shares = muscle_shares(result("ex.romanian_deadlift"), CATALOGUE)
    assert shares == {
        MuscleGroup.HAMSTRINGS: 1.0,
        MuscleGroup.GLUTES: 1.0,
        MuscleGroup.ERECTOR_SPINAE: 0.5,
        MuscleGroup.FOREARMS: 0.5,
    }
    assert muscle_shares(result(None), CATALOGUE) == {}


# --------------------------------------------------------------------------- engine

SETS_SHA = "6" * 64


def watch(*, loads: tuple[float | None, ...] = (60.0, 60.0, 60.0)) -> WatchSets:
    sets = [
        WatchSet(
            start_offset_seconds=60.0 + index * 160,
            duration_seconds=40.0,
            type=SetType.ACTIVE,
            repetitions=8,
            weight_kg=load,
            exercise_id="ex.back_squat",
            provider_exercise="fit:squat/barbell_back_squat",
        )
        for index, load in enumerate(loads)
    ]
    return WatchSets(start_time_utc=START, sets=tuple(sets))


def gym(world: World, sets: WatchSets | None, *, day: int = 0, sha: str = SETS_SHA) -> UUID:
    streams: tuple[StreamRef, ...] = ()
    if sets is not None:
        world.segments[sha] = sets
        streams = (StreamRef(StreamKind.SETS, sha),)
    values = {
        "classification.sport": Sport.STRENGTH,
        "classification.subtype": "GYM",
        "timing.startTimeUtc": START + timedelta(days=day),
        "timing.elapsedSeconds": 3600.0,
    }
    observation = sample_observation(
        values=values,
        streams=streams,
        raw_source_id=UUID(int=0x600 + day),
        sha256=f"{day:x}" * 64,
    )
    return world.ingest(observation).activity.activity_id


def volume(world: World, activity_id: UUID) -> Any:
    return world.metrics.current(SCOPE, "str.volume_load", MetricSubject.activity(activity_id))


def entry(exercise: str = "ex.back_squat", number: int = 1, **values: Any) -> SetEntry:
    return SetEntry(exercise_id=exercise, set_number=number, recorded_at=LATER, **values)


@pytest.mark.req("FR-STR-008", "FR-MET-004")
def test_the_volume_load_of_a_session_follows_its_executed_sets() -> None:
    world = World()
    activity_id = gym(world, watch())
    first = volume(world, activity_id)
    assert (first.status, first.value) == (DerivedStatus.COMPUTED, 3 * 8 * 60.0)
    assert first.inputs[0].evidence.kind is EvidenceKind.RAW_SOURCE  # the SETS stream
    assert first.inputs[0].evidence.note == f"sha256 {SETS_SHA}"
    assert first.inputs[1].evidence.kind is EvidenceKind.STRENGTH_SET_RESULT
    world.strength.record(SCOPE, activity_id, entry(number=3, completed_reps=5, actual_rpe=9.5))
    corrected = volume(world, activity_id)
    assert corrected.value == (8 + 8 + 5) * 60.0  # the athlete's correction is recomputed
    world.strength.record(
        SCOPE, activity_id, entry("ex.romanian_deadlift", completed_reps=10, actual_load_kg=50.0)
    )
    assert volume(world, activity_id).value == (8 + 8 + 5) * 60.0 + 500.0
    assert UncertaintyReason.MISSING_DATA not in volume(world, activity_id).uncertainty_reasons
    world.strength.use_watch_sets(SCOPE, activity_id, False)
    only_mine = volume(world, activity_id)
    # without the watch, set 3 (repetitions and RPE only) has no load: left out, and said so
    assert (only_mine.value, only_mine.status) == (500.0, DerivedStatus.COMPUTED)
    assert UncertaintyReason.MISSING_DATA in only_mine.uncertainty_reasons
    assert only_mine.inputs[2].value == 1.0  # sets_left_out


@pytest.mark.req("FR-STR-008", "FR-PRF-002")
def test_without_known_sets_there_is_no_volume_load_and_unread_sets_are_unknown() -> None:
    world = World()
    no_load = gym(world, watch(loads=(None, None, None)))
    assert volume(world, no_load) is None  # it does not qualify: never estimated
    no_sets = gym(world, None, day=1)
    assert volume(world, no_sets) is None
    unreadable = gym(world, watch(), day=2, sha="7" * 64)
    del world.segments["7" * 64]
    world.service.refresh(SCOPE, unreadable)
    damaged = volume(world, unreadable)
    assert damaged.status is DerivedStatus.MISSING_INPUT
    assert UncertaintyReason.MISSING_DATA in damaged.uncertainty_reasons
    run = world.ingest(sample_observation()).activity.activity_id
    assert volume(world, run) is None  # not a strength activity
    huge = gym(world, watch(loads=(600.0, 600.0)), day=3, sha="a" * 64)
    world.strength.record(SCOPE, huge, entry(number=1, completed_reps=1000))
    world.strength.record(SCOPE, huge, entry(number=2, completed_reps=1000))
    implausible = volume(world, huge)  # 1,200,000 kg: beyond the range, never an error
    assert (implausible.status, implausible.value) == (DerivedStatus.MISSING_INPUT, None)
    assert UncertaintyReason.SENSOR_QUALITY in implausible.uncertainty_reasons


# --------------------------------------------------------------------------- week


def analytics(world: World) -> AnalyticsService:
    return AnalyticsService(
        activities=world.service,
        store=world.activities,
        overrides=world.overrides,
        metrics=world.metrics,
        consents=world.consents_gate,
        strength=world.strength,
    )


@pytest.mark.req("FR-STR-008", "FR-MET-005")
def test_the_week_counts_hard_sets_per_muscle_and_adds_up_volume_load() -> None:
    world = World()
    monday = gym(world, watch())
    thursday = gym(world, None, day=2)
    for number, rpe in ((1, 8.0), (2, 8.5), (3, 5.0)):  # the third is not hard
        world.strength.record(SCOPE, monday, entry(number=number, actual_rpe=rpe))
    for number in (1, 2):
        world.strength.record(
            SCOPE,
            thursday,
            entry(
                "ex.romanian_deadlift", number, completed_reps=10, actual_load_kg=50.0, actual_rir=2
            ),
        )
    report = analytics(world).strength_week(SCOPE, WEEK)
    assert (report.activities, report.sets, report.unattributed) == (2, 5, 0)
    by_muscle = {item.muscle: item.hard_sets for item in report.muscles}
    assert by_muscle[MuscleGroup.QUADRICEPS].value.value == 2.0  # squat, primary
    assert by_muscle[MuscleGroup.GLUTES].value.value == 4.0  # both exercises, primary
    assert by_muscle[MuscleGroup.HAMSTRINGS].value.value == 2 * 0.5 + 2 * 1.0
    assert by_muscle[MuscleGroup.FOREARMS].value.value == 1.0
    assert MuscleGroup.PECTORALS not in by_muscle
    stored = by_muscle[MuscleGroup.QUADRICEPS].value
    assert stored.subject == MetricSubject(
        MetricSubject.week(START.date()).kind, WEEK, "QUADRICEPS"
    )
    assert validation_errors("derived-metric-value", DERIVED_VALUE_CODEC.dump(stored)) == []
    assert report.volume_load is not None
    assert report.volume_load.value.value == 3 * 8 * 60.0 + 2 * 500.0
    week = analytics(world).week(SCOPE, WEEK)
    (strength,) = week.sports
    assert strength.totals["str.volume_load_total"].value.value == 3 * 8 * 60.0 + 2 * 500.0


@pytest.mark.req("FR-STR-008", "FR-PRF-002")
def test_unknown_effort_and_unknown_exercises_are_reported_not_assumed() -> None:
    world = World()
    named = WatchSets(
        start_time_utc=START,
        sets=(
            *watch().sets,
            WatchSet(start_offset_seconds=600.0, repetitions=12, provider_exercise="fit:squat"),
        ),
    )
    activity_id = gym(world, named)
    world.strength.record(SCOPE, activity_id, entry(number=1, actual_rpe=8.0))
    report = analytics(world).strength_week(SCOPE, WEEK)
    assert (report.sets, report.unattributed) == (4, 1)
    quads = next(item.hard_sets for item in report.muscles if item.muscle is MuscleGroup.QUADRICEPS)
    # two sets of unknown effort, and the unnamed squat that may have worked them
    assert quads.value.status is DerivedStatus.MISSING_INPUT
    assert (quads.known_total, quads.unknown) == (1.0, 3)
    world.strength.use_watch_sets(SCOPE, activity_id, False)
    world.strength.remove(SCOPE, activity_id, "ex.back_squat", 1)
    empty = analytics(world).strength_week(SCOPE, WEEK)
    assert (empty.sets, empty.muscles) == (0, ())
    subject = MetricSubject(MetricSubject.week(START.date()).kind, WEEK, "QUADRICEPS")
    assert world.metrics.current(SCOPE, "str.weekly_sets_by_muscle", subject) is None  # retired


@pytest.mark.req("FR-STR-008", "FR-MET-005", "FR-PRF-002")
def test_the_weeks_volume_leaves_out_sessions_without_loads_and_flags_unread_sets() -> None:
    world = World()
    loaded = gym(world, watch())
    unloaded = gym(world, watch(loads=(None, None, None)), day=1, sha="b" * 64)
    report = analytics(world).strength_week(SCOPE, WEEK)
    assert report.volume_load is not None
    assert (report.volume_load.value.value, report.volume_load.left_out) == (3 * 8 * 60.0, 1)
    for number in (1, 2, 3):
        world.strength.record(SCOPE, loaded, entry(number=number, actual_rpe=8.0))
        world.strength.record(SCOPE, unloaded, entry(number=number, actual_rpe=8.0))
    unread = gym(world, watch(), day=2, sha="c" * 64)
    del world.segments["c" * 64]
    world.service.refresh(SCOPE, unread)
    report = analytics(world).strength_week(SCOPE, WEEK)
    assert report.unread == 1
    quads = next(item.hard_sets for item in report.muscles if item.muscle is MuscleGroup.QUADRICEPS)
    assert (quads.value.status, quads.known_total, quads.unknown) == (
        DerivedStatus.MISSING_INPUT,
        6.0,
        1,
    )
    assert report.volume_load is not None
    assert report.volume_load.value.status is DerivedStatus.MISSING_INPUT  # unread: unknown
    empty = analytics(world).strength_week(SCOPE, "2026-W45")
    assert (empty.activities, empty.volume_load) == (0, None)
