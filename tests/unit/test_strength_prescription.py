"""Strength workout prescription rules 1.0.0 (strength spec section 4; FR-STR-004)."""

from __future__ import annotations

import copy
from dataclasses import replace
from typing import Any

import pytest

from coach.application.contracts.strength import (
    strength_prescription_from_contract,
    strength_prescription_to_contract,
)
from coach.domain.athlete.experience import StrengthExperience
from coach.domain.common.errors import DomainValidationError
from coach.domain.strength.catalogue import CATALOGUE
from coach.domain.strength.prescription import (
    EffortCue,
    ExpectedInterference,
    Load,
    LoadKind,
    PrescribedExercise,
    StrengthPrescription,
    check_against,
)
from coach.domain.strength.vocabulary import BodyRegion, InterferenceLevel
from schemas import EXAMPLES_DIR, load_json, validation_errors

FAMILIAR = StrengthExperience(rpe_familiar=True, rir_familiar=True)
NEW_TO_SCALES = StrengthExperience(rpe_familiar=False, rir_familiar=False)


def example() -> dict[str, Any]:
    document: dict[str, Any] = load_json(EXAMPLES_DIR / "workout.strength.example.json")
    return document


@pytest.mark.req("FR-STR-004", "NFR-REL-004")
def test_the_published_strength_block_round_trips_and_follows_the_rules() -> None:
    block = example()["strength"]
    prescription = strength_prescription_from_contract(block)
    assert check_against(prescription, CATALOGUE, FAMILIAR) is prescription
    assert strength_prescription_to_contract(prescription) == block
    assert validation_errors("workout", example()) == []


def goblet(**changes: Any) -> PrescribedExercise:
    fields: dict[str, Any] = {
        "sets": 3,
        "reps_min": 8,
        "reps_max": 10,
        "load": Load(LoadKind.ABSOLUTE_KG, 16.0),
        "effort_cue": EffortCue.MODERATE,
        "rest_seconds": 90,
        "alternatives": ("ex.leg_press",),
    }
    fields.update(changes)
    return PrescribedExercise.of(CATALOGUE.get("ex.goblet_squat"), **fields)


def workout(*exercises: PrescribedExercise) -> StrengthPrescription:
    return StrengthPrescription(
        purpose="Maintain lower-body strength",
        estimated_duration_seconds=1800,
        warmup=("5 minutes easy cycling",),
        exercises=exercises or (goblet(),),
        progression_rule="Add a repetition per set up to the top of the range",
        regression_rule="Reduce the load when repetitions are missed twice",
        expected_interference=ExpectedInterference(
            level=InterferenceLevel.LOW,
            affected_regions=(BodyRegion.QUADRICEPS, BodyRegion.KNEES),
            notes="48 hours before intervals",
        ),
    )


@pytest.mark.req("FR-STR-004", "FR-PRF-001")
def test_an_athlete_new_to_rpe_and_rir_gets_conservative_effort_cues() -> None:
    plain = workout()
    assert check_against(plain, CATALOGUE, NEW_TO_SCALES) is plain
    assert check_against(plain, CATALOGUE, None) is plain  # unknown familiarity
    document = {**example(), "strength": strength_prescription_to_contract(plain)}
    assert validation_errors("workout", document) == []  # effortCue and KNEES: contract 1.1.0
    for scaled in (goblet(target_rpe=7.0), goblet(target_rir=3)):
        with pytest.raises(DomainValidationError, match="familiar"):
            check_against(workout(scaled), CATALOGUE, NEW_TO_SCALES)
        assert check_against(workout(scaled), CATALOGUE, FAMILIAR) is not None
    with pytest.raises(DomainValidationError, match="familiar"):
        check_against(workout(goblet(target_rpe=7.0)), CATALOGUE, StrengthExperience())
    with pytest.raises(DomainValidationError, match="EASY or MODERATE"):
        check_against(workout(goblet(effort_cue=EffortCue.HARD)), CATALOGUE, NEW_TO_SCALES)


@pytest.mark.req("FR-STR-004", "FR-STR-003")
def test_exercises_and_alternatives_are_the_catalogues() -> None:
    restated = replace(goblet(), secondary_muscles=())
    with pytest.raises(DomainValidationError, match="catalogue's"):
        check_against(workout(restated), CATALOGUE, FAMILIAR)
    with pytest.raises(DomainValidationError, match="is not a SQUAT"):
        check_against(workout(goblet(alternatives=("ex.deadlift",))), CATALOGUE, FAMILIAR)
    with pytest.raises(DomainValidationError, match="unknown alternative"):
        check_against(workout(goblet(alternatives=("ex.made_up",))), CATALOGUE, FAMILIAR)
    unknown = replace(goblet(), exercise_id="ex.made_up")
    with pytest.raises(DomainValidationError, match="unknown exercise"):
        check_against(workout(unknown), CATALOGUE, FAMILIAR)


@pytest.mark.req("FR-STR-004")
@pytest.mark.parametrize(
    "changes",
    [
        {"sets": 0},
        {"reps_min": 10, "reps_max": 8},
        {"rest_seconds": 901},
        {"tempo": "3-1-1"},
        {"effort_cue": None},  # no RPE, no RIR, no cue
        {"load": Load(LoadKind.RPE_BASED)},  # RPE-based without a target
        {"alternatives": ("ex.goblet_squat",)},
        {"target_rpe": 7.3},
    ],
)
def test_a_prescribed_exercise_refuses_incomplete_or_inconsistent_values(
    changes: dict[str, Any],
) -> None:
    with pytest.raises(DomainValidationError):
        goblet(**changes)


@pytest.mark.req("FR-STR-004")
def test_loads_and_workouts_state_what_they_need() -> None:
    for kind, value in (
        (LoadKind.ABSOLUTE_KG, None),
        (LoadKind.PERCENT_1RM, 120.0),
        (LoadKind.BODYWEIGHT, 10.0),
        (LoadKind.RPE_BASED, 50.0),
    ):
        with pytest.raises(DomainValidationError):
            Load(kind, value)
    with pytest.raises(DomainValidationError):
        replace(workout(), exercises=())
    with pytest.raises(DomainValidationError):
        replace(workout(), estimated_duration_seconds=30)
    broken = copy.deepcopy(example())
    broken["strength"]["exercises"][0]["effortCue"] = "VERY_HARD"
    assert validation_errors("workout", broken) != []
