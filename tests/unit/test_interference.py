"""Interference engine rules 1.0.0 (strength spec section 7; FR-STR-007, AT-STR-001).

Synthetic sessions only."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from activity_world import SCOPE, World
from coach.application.analytics import AnalyticsService
from coach.application.contracts.strength import strength_prescription_from_contract
from coach.application.identity import Principal
from coach.domain.activity.fields import StreamKind, StreamRef
from coach.domain.activity.segments import Lap, LapIntensity, Laps
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.evidence import EvidenceKind, EvidenceRef
from coach.domain.common.quality import UncertaintyReason
from coach.domain.common.sport import Sport
from coach.domain.strength.catalogue import CATALOGUE
from coach.domain.strength.prescription import EffortCue, Load, LoadKind, PrescribedExercise
from coach.domain.strength.session import SetEntry, SetSource, StrengthSetResult
from coach.domain.strength.vocabulary import BodyRegion, InterferenceLevel
from coach.planning.interference import (
    Demand,
    Modifier,
    Rule,
    SessionLoad,
    assess,
    prescription_demands,
    run_demands,
    strength_demands,
    swim_demands,
    tennis_demands,
    worst,
)
from samples import NOW, START, USER, sample_observation
from schemas import EXAMPLES_DIR, load_json

MONDAY = datetime(2026, 10, 26, 18, 0, tzinfo=UTC)


def done(exercise: str, *, rpe: float | None = 8.0, count: int = 3) -> list[StrengthSetResult]:
    return [
        StrengthSetResult(
            exercise_id=exercise,
            set_number=number,
            recorded_at=MONDAY,
            source=SetSource.FIT,
            actual_rpe=rpe,
        )
        for number in range(1, count + 1)
    ]


def session(
    number: int,
    hours: float,
    sport: Sport,
    demands: frozenset[Demand] = frozenset(),
    possible: frozenset[Demand] = frozenset(),
    regions: frozenset[BodyRegion] = frozenset(),
) -> SessionLoad:
    identifier = UUID(int=number)
    return SessionLoad(
        identifier,
        MONDAY + timedelta(hours=hours),
        sport,
        planned=True,
        demands=demands,
        possible=possible,
        regions=regions,
        evidence=(EvidenceRef(EvidenceKind.ACTIVITY, identifier),),
    )


def legs(number: int = 1, hours: float = 0) -> SessionLoad:
    demands, possible, regions = strength_demands(done("ex.back_squat"), CATALOGUE)
    return session(number, hours, Sport.STRENGTH, demands, possible, regions)


def intervals(number: int = 2, hours: float = 20, rpe: float | None = 8.0) -> SessionLoad:
    demands, possible = run_demands(rpe=rpe, elapsed_seconds=3000.0, structured_intervals=True)
    return session(number, hours, Sport.RUNNING, demands, possible)


# --------------------------------------------------------------------------- AT-STR-001


@pytest.mark.req("FR-STR-007")
@pytest.mark.acceptance("AT-STR-001")
def test_heavy_legs_the_day_before_hard_intervals_is_high() -> None:
    block = load_json(EXAMPLES_DIR / "workout.strength.example.json")["strength"]
    demands, possible, regions = prescription_demands(
        strength_prescription_from_contract(block), CATALOGUE
    )
    assert Demand.HEAVY_LOWER_BODY in demands  # 9 sets at RPE 7
    today = session(1, 0, Sport.STRENGTH, demands, possible, regions)
    (found,) = assess([today, intervals()])
    assert (found.rule, found.level, found.base_level) == (
        Rule.HEAVY_LEGS_BEFORE_KEY_RUN,
        InterferenceLevel.HIGH,
        InterferenceLevel.HIGH,
    )
    assert found.sessions == (UUID(int=1), UUID(int=2))
    assert {BodyRegion.QUADRICEPS, BodyRegion.KNEES, BodyRegion.CALVES} <= set(
        found.affected_regions
    )
    assert found.reasons == frozenset()
    assert found.rules_version == "1.0.0"
    assert worst([found]) is InterferenceLevel.HIGH


@pytest.mark.req("FR-STR-007")
@pytest.mark.acceptance("AT-STR-001")
def test_only_stated_evidence_moves_a_level_and_it_is_recorded() -> None:
    history = EvidenceRef(EvidenceKind.ATHLETE_PROFILE, UUID(int=9), field="experience.strength")
    lowered = Modifier("LONG_STRENGTH_HISTORY", -1, (history,))
    (found,) = assess([legs(), intervals()], [lowered])
    assert (found.level, found.base_level) == (InterferenceLevel.MODERATE, InterferenceLevel.HIGH)
    assert found.modifiers == (lowered,)
    assert history in found.evidence
    with pytest.raises(DomainValidationError, match="evidence"):
        Modifier("GOOD_RECOVERY", -1, ())
    with pytest.raises(DomainValidationError, match="one step"):
        Modifier("GOOD_RECOVERY", -2, (history,))


# --------------------------------------------------------------------------- baseline rules


@pytest.mark.req("FR-STR-007")
@pytest.mark.parametrize(
    ("sessions", "expected"),
    [
        ((legs(), intervals(hours=36)), [(Rule.HEAVY_LEGS_BEFORE_INTERVALS, "MODERATE")]),
        ((legs(), intervals(hours=60)), []),  # beyond 48 h
        ((intervals(hours=0), legs(1, 20)), []),  # the run first: not a baseline rule
        ((legs(), intervals(rpe=5.0)), []),  # an easy run is not hard intervals
        (
            (
                legs(),
                session(
                    2,
                    12,
                    Sport.RUNNING,
                    run_demands(rpe=3.0, elapsed_seconds=6000.0, structured_intervals=False)[0],
                ),
            ),
            [(Rule.HEAVY_LEGS_BEFORE_KEY_RUN, "HIGH")],  # a long run
        ),
    ],
)
def test_strength_before_running(
    sessions: tuple[SessionLoad, ...], expected: list[tuple[Rule, str]]
) -> None:
    assert [(item.rule, item.level.value) for item in assess(sessions)] == expected


@pytest.mark.req("FR-STR-007")
def test_heavy_upper_body_before_a_hard_swim_is_moderate() -> None:
    demands, possible, regions = strength_demands(done("ex.overhead_press"), CATALOGUE)
    press = session(1, 0, Sport.STRENGTH, demands, possible, regions)
    swim = session(2, 10, Sport.SWIMMING, swim_demands(rpe=8.0)[0])
    (found,) = assess([press, swim])
    assert (found.rule, found.level) == (
        Rule.HEAVY_UPPER_BEFORE_HARD_SWIM,
        InterferenceLevel.MODERATE,
    )
    assert BodyRegion.SHOULDERS in found.affected_regions
    assert BodyRegion.KNEES not in found.affected_regions


@pytest.mark.req("FR-STR-007", "FR-TEN-003")
def test_demanding_tennis_intervals_and_heavy_legs_within_48_hours_is_very_high() -> None:
    tennis = session(3, 30, Sport.TENNIS, tennis_demands(high_load=True)[0])
    found = assess([legs(), intervals(hours=44), tennis])
    assert found[0].rule is Rule.TENNIS_INTERVALS_HEAVY_LEGS
    assert found[0].level is InterferenceLevel.VERY_HIGH
    assert found[0].sessions == (UUID(int=1), UUID(int=3), UUID(int=2))
    assert BodyRegion.ELBOWS in found[0].affected_regions  # tennis regions
    easy = session(3, 30, Sport.TENNIS, tennis_demands(high_load=False)[0])
    assert all(
        item.rule is not Rule.TENNIS_INTERVALS_HEAVY_LEGS
        for item in assess([legs(), intervals(hours=44), easy])
    )


@pytest.mark.req("FR-STR-007", "FR-TEN-003")
@pytest.mark.parametrize("calf_first", [False, True])
def test_tennis_or_plyometrics_with_calf_work_within_24_hours_is_high(calf_first: bool) -> None:
    demands, _, regions = strength_demands(done("ex.standing_calf_raise"), CATALOGUE)
    assert Demand.CALF_ACHILLES_INTENSIVE in demands
    calves = session(1, 0 if calf_first else 6, Sport.STRENGTH, demands, frozenset(), regions)
    tennis = session(2, 6 if calf_first else 0, Sport.TENNIS, tennis_demands(high_load=False)[0])
    (found,) = assess([calves, tennis])
    assert (found.rule, found.level) == (Rule.CALF_ACHILLES_LOAD, InterferenceLevel.HIGH)
    assert found.affected_regions == (
        BodyRegion.CALVES,
        BodyRegion.SOLEUS,
        BodyRegion.ACHILLES_REGION,
    )
    jumps, _, jump_regions = strength_demands(done("ex.box_jump", count=1), CATALOGUE)
    assert Demand.PLYOMETRIC in jumps
    plyo = session(3, 3, Sport.STRENGTH, jumps, frozenset(), jump_regions)
    assert [item.rule for item in assess([calves, plyo])] == [Rule.CALF_ACHILLES_LOAD]


@pytest.mark.req("FR-STR-007", "FR-PRF-002")
def test_a_combination_resting_on_unknown_effort_keeps_its_level_and_says_so() -> None:
    demands, possible, regions = strength_demands(done("ex.back_squat", rpe=None), CATALOGUE)
    assert (demands, possible) == (frozenset(), frozenset({Demand.HEAVY_LOWER_BODY}))
    unknown = session(1, 0, Sport.STRENGTH, demands, possible, regions)
    (found,) = assess([unknown, intervals(rpe=None)])
    assert found.level is InterferenceLevel.HIGH
    assert found.reasons == frozenset({UncertaintyReason.MISSING_DATA})
    easy = done("ex.back_squat", rpe=5.0)
    assert strength_demands(easy, CATALOGUE)[:2] == (frozenset(), frozenset())


@pytest.mark.req("FR-STR-007", "FR-STR-004")
def test_planned_effort_cues_easy_and_moderate_are_not_heavy() -> None:
    block = load_json(EXAMPLES_DIR / "workout.strength.example.json")["strength"]
    prescription = strength_prescription_from_contract(block)
    plain = tuple(
        PrescribedExercise.of(
            CATALOGUE.get(item.exercise_id),
            sets=item.sets,
            reps_min=item.reps_min,
            reps_max=item.reps_max,
            load=Load(LoadKind.BODYWEIGHT),
            effort_cue=EffortCue.MODERATE,
            rest_seconds=item.rest_seconds,
        )
        for item in prescription.exercises
    )
    demands, possible, _ = prescription_demands(replace(prescription, exercises=plain), CATALOGUE)
    assert Demand.HEAVY_LOWER_BODY not in demands | possible
    assert worst([]) is InterferenceLevel.NONE


# --------------------------------------------------------------------------- completed sessions

LAPS_SHA = "8" * 64


def interval_laps(start: datetime) -> Laps:
    laps, offset = [], 0.0
    for seconds, intensity in (
        [(600, LapIntensity.WARMUP)]
        + [
            (180, LapIntensity.INTERVAL) if index % 2 == 0 else (90, LapIntensity.REST)
            for index in range(8)
        ]
        + [(300, LapIntensity.COOLDOWN)]
    ):
        laps.append(
            Lap(
                start_offset_seconds=offset,
                timer_seconds=float(seconds),
                distance_meters=seconds * 3.5,
                intensity=intensity,
            )
        )
        offset += seconds
    return Laps(start_time_utc=start, laps=tuple(laps))


@pytest.mark.req("FR-STR-007", "FR-ACT-011")
def test_completed_sessions_are_assessed_from_their_own_data() -> None:
    world = World()
    gym_start = START + timedelta(hours=-3)  # 2026-10-20 18:00 UTC
    gym = world.ingest(
        sample_observation(
            values={
                "classification.sport": Sport.STRENGTH,
                "classification.subtype": "GYM",
                "timing.startTimeUtc": gym_start,
                "timing.elapsedSeconds": 3600.0,
            },
            raw_source_id=UUID(int=0x801),
            sha256="9" * 64,
        )
    ).activity.activity_id
    for number in (1, 2, 3):
        world.strength.record(
            SCOPE,
            gym,
            SetEntry(
                exercise_id="ex.back_squat",
                set_number=number,
                recorded_at=NOW,
                completed_reps=5,
                actual_rpe=8.0,
            ),
        )
    run_start = gym_start + timedelta(hours=20)
    world.segments[LAPS_SHA] = interval_laps(run_start)
    run = world.ingest(
        sample_observation(
            updates={"timing.startTimeUtc": run_start},
            streams=(StreamRef(StreamKind.LAPS, LAPS_SHA),),
        )
    ).activity.activity_id
    service = AnalyticsService(
        activities=world.service,
        store=world.activities,
        overrides=world.overrides,
        metrics=world.metrics,
        consents=world.consents_gate,
        strength=world.strength,
        streams=world.read_stream,
    )
    until = run_start.date()
    possible = service.interference(SCOPE, until=until)
    (found,) = possible.assessments
    assert found.reasons == frozenset({UncertaintyReason.MISSING_DATA})  # the run's RPE is unknown
    world.service.correct(SCOPE, Principal(USER), run, "subjective.rpe", 8.0, "hard reps")
    (found,) = service.interference(SCOPE, until=until).assessments
    assert (found.rule, found.level, found.reasons) == (
        Rule.HEAVY_LEGS_BEFORE_KEY_RUN,
        InterferenceLevel.HIGH,
        frozenset(),
    )
    assert found.sessions == (gym, run)
    assert service.interference(SCOPE, until=until + timedelta(days=5), days=1).assessments == ()
