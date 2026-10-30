"""Progression rules 1.0.0 from the execution history (strength spec section 6; FR-STR-006).
Synthetic data only."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import UUID

import pytest

from activity_world import SCOPE, World
from coach.application.analytics import AnalyticsService
from coach.application.consent import ConsentRequiredError
from coach.domain.activity.fields import Pain
from coach.domain.activity.observation import SourceKind
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.evidence import EvidenceKind
from coach.domain.common.sport import Sport
from coach.domain.consent.ledger import ConsentType
from coach.domain.strength.catalogue import CATALOGUE
from coach.domain.strength.progression import (
    Decision,
    Exposure,
    Outcome,
    PainReport,
    Reason,
    decide,
    outcome,
)
from coach.domain.strength.regions import region_named, regions_loaded
from coach.domain.strength.session import SetEntry, SetSource, StrengthSetResult
from coach.domain.strength.vocabulary import BodyRegion
from samples import NOW, START, sample_observation

SQUAT = CATALOGUE.get("ex.back_squat")
DAY = date(2026, 10, 20)


def done(
    reps: int | None = 8,
    *,
    target_reps: int | None = 8,
    rpe: float | None = 7.0,
    target_rpe: float | None = 7.5,
    rir: int | None = None,
) -> StrengthSetResult:
    return StrengthSetResult(
        exercise_id="ex.back_squat",
        set_number=1,
        recorded_at=NOW,
        source=SetSource.ATHLETE,
        completed_reps=reps,
        target_reps=target_reps,
        actual_rpe=rpe,
        actual_rir=rir,
        target_rpe=target_rpe,
    )


MET = done()
ABOVE = done(rpe=8.5)  # 1.0 above target
MISSED = done(reps=6)
NO_TARGETS = done(target_reps=None, target_rpe=None)
UNKNOWN = done(rpe=None)
REPS_ONLY = done(target_rpe=None)


@pytest.mark.req("FR-STR-006")
@pytest.mark.parametrize(
    ("item", "expected"),
    [
        (MET, Outcome.MET),
        (ABOVE, Outcome.MET_ABOVE_RPE),
        (done(rpe=9.0), Outcome.MET_ABOVE_RPE),  # exactly 1.5 above: not more than 1.5
        (done(rpe=9.5), Outcome.MISSED),
        (MISSED, Outcome.MISSED),
        (NO_TARGETS, Outcome.NO_TARGETS),
        (UNKNOWN, Outcome.UNKNOWN),
        (done(reps=None), Outcome.UNKNOWN),
        (REPS_ONLY, Outcome.REPS_MET),
        (done(rpe=None, rir=3), Outcome.MET),  # RIR 3 reads as RPE 7
        (done(rpe=None, rir=0), Outcome.MISSED),  # RPE 10: 2.5 above target
    ],
)
def test_an_exposure_is_judged_against_its_own_targets(
    item: StrengthSetResult, expected: Outcome
) -> None:
    assert outcome([item]) is expected


def exposures(*sets: StrengthSetResult) -> list[Exposure]:
    return [
        Exposure(UUID(int=index + 1), DAY + timedelta(days=3 * index), (item,))
        for index, item in enumerate(sets)
    ]


@pytest.mark.req("FR-STR-006")
@pytest.mark.parametrize(
    ("sets", "decision", "reason"),
    [
        ((MET, MET), Decision.PROGRESS, Reason.TWO_EXPOSURES_MET),
        ((MISSED, MISSED, MET, MET), Decision.PROGRESS, Reason.TWO_EXPOSURES_MET),  # last two
        ((MET, ABOVE), Decision.HOLD, Reason.TARGETS_MET_ABOVE_RPE),
        ((ABOVE, MET), Decision.HOLD, Reason.NOT_TWO_EXPOSURES_MET),
        ((MISSED, MET), Decision.HOLD, Reason.ONE_EXPOSURE_MISSED),
        ((MISSED, done(rpe=9.5)), Decision.REGRESS, Reason.TWO_EXPOSURES_MISSED),
        ((MET,), Decision.INSUFFICIENT_DATA, Reason.FEWER_THAN_TWO_EXPOSURES),
        ((NO_TARGETS, MET), Decision.INSUFFICIENT_DATA, Reason.NO_TARGETS),
        ((MET, UNKNOWN), Decision.INSUFFICIENT_DATA, Reason.MISSING_VALUES),
        ((REPS_ONLY, REPS_ONLY), Decision.INSUFFICIENT_DATA, Reason.NO_TARGET_RPE),
    ],
)
def test_the_last_two_exposures_decide(
    sets: tuple[StrengthSetResult, ...], decision: Decision, reason: Reason
) -> None:
    found = decide(SQUAT, exposures(*sets), [])
    assert (found.decision, found.reasons, found.rules_version) == (decision, (reason,), "1.0.0")
    assert len(found.exposures) == min(2, len(sets))
    assert all(ref.kind is EvidenceKind.STRENGTH_SET_RESULT for ref in found.evidence)


def pain(location: str | None, *, flag: bool = True) -> PainReport:
    return PainReport(UUID(int=99), DAY + timedelta(days=4), Pain(flag, 3, location))


@pytest.mark.req("FR-STR-006")
def test_pain_for_a_region_the_exercise_loads_regresses_it() -> None:
    found = decide(SQUAT, exposures(MET, MET), [pain("Rodilla")])
    assert (found.decision, found.reasons) == (Decision.REGRESS, (Reason.PAIN_IN_REGION,))
    assert found.evidence[-1].field == "subjective.pain"
    elsewhere = decide(SQUAT, exposures(MET, MET), [pain("codo")])
    assert elsewhere.decision is Decision.PROGRESS  # squats do not load the elbows
    unnamed = decide(SQUAT, exposures(MET, MET), [pain("me tira algo raro")])
    assert (unnamed.decision, unnamed.reasons) == (Decision.HOLD, (Reason.PAIN_REGION_UNKNOWN,))
    assert decide(SQUAT, exposures(MET, MET), [pain("rodilla", flag=False)]).decision is (
        Decision.PROGRESS
    )
    assert decide(SQUAT, [], [pain("knees")]).decision is Decision.REGRESS


@pytest.mark.req("FR-STR-006", "FR-STR-007", "FR-PRF-002")
def test_regions_come_from_muscles_and_joints_and_names_match_exactly() -> None:
    loaded = regions_loaded(SQUAT)
    assert {BodyRegion.QUADRICEPS, BodyRegion.GLUTES, BodyRegion.KNEES} <= loaded
    assert BodyRegion.SHOULDERS not in loaded
    assert BodyRegion.ACHILLES_REGION in regions_loaded(CATALOGUE.get("ex.box_jump"))
    assert BodyRegion.SHOULDERS in regions_loaded(CATALOGUE.get("ex.overhead_press"))
    assert region_named("  Rodillas ") is BodyRegion.KNEES
    assert region_named("tendón de Aquiles") is BodyRegion.ACHILLES_REGION
    assert region_named("ACHILLES_REGION") is BodyRegion.ACHILLES_REGION
    assert region_named("rodilla izquierda") is None  # not a region name: never guessed
    assert region_named(None) is None


# --------------------------------------------------------------------------- service


def gym(world: World, day: int) -> UUID:
    values = {
        "classification.sport": Sport.STRENGTH,
        "classification.subtype": "GYM",
        "timing.startTimeUtc": START + timedelta(days=day),
        "timing.elapsedSeconds": 3600.0,
    }
    observation = sample_observation(
        values=values, raw_source_id=UUID(int=0x700 + day), sha256=f"{day + 1:x}" * 64
    )
    return world.ingest(observation).activity.activity_id


def entry(exercise: str, number: int, **values: Any) -> SetEntry:
    return SetEntry(exercise_id=exercise, set_number=number, recorded_at=NOW, **values)


def analytics(world: World) -> AnalyticsService:
    return AnalyticsService(
        activities=world.service,
        store=world.activities,
        overrides=world.overrides,
        metrics=world.metrics,
        consents=world.consents_gate,
        strength=world.strength,
    )


TARGETED: dict[str, Any] = {
    "completed_reps": 8,
    "target_reps": 8,
    "actual_rpe": 7.0,
    "target_rpe": 7.5,
}


@pytest.mark.req("FR-STR-006")
def test_decisions_read_the_executed_sets_and_the_pain_of_any_activity() -> None:
    world = World()
    for day in (0, 3):
        activity_id = gym(world, day)
        world.strength.record(SCOPE, activity_id, entry("ex.back_squat", 1, **TARGETED))
        world.strength.record(SCOPE, activity_id, entry("ex.romanian_deadlift", 1, **TARGETED))
    until = DAY + timedelta(days=6)
    found = {item.exercise_id: item for item in analytics(world).progression(SCOPE, until=until)}
    assert found["ex.back_squat"].decision is Decision.PROGRESS
    assert found["ex.romanian_deadlift"].decision is Decision.PROGRESS
    run = sample_observation(
        SourceKind.ATHLETE_ENTRY,
        updates={
            "timing.startTimeUtc": START + timedelta(days=4),
            "subjective.pain": Pain(True, 4, "rodilla"),
        },
    )
    world.ingest(run)
    found = {item.exercise_id: item for item in analytics(world).progression(SCOPE, until=until)}
    assert found["ex.back_squat"].decision is Decision.REGRESS
    assert found["ex.romanian_deadlift"].decision is Decision.PROGRESS  # no knees
    (one,) = analytics(world).progression(SCOPE, until=until, exercise_id="ex.bench_press")
    assert one.decision is Decision.INSUFFICIENT_DATA
    with pytest.raises(DomainValidationError, match="unknown exercise"):
        analytics(world).progression(SCOPE, until=until, exercise_id="ex.made_up")
    world.consents.revoke(SCOPE, ConsentType.ACTIVITIES)
    with pytest.raises(ConsentRequiredError):
        analytics(world).progression(SCOPE, until=until)
