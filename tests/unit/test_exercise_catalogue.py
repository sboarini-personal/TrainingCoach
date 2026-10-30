"""Strength vocabularies and the exercise catalogue (strength spec 2 and 3; P10)."""

from __future__ import annotations

from typing import Any

import pytest

from coach.application.contracts.strength import exercise_to_contract
from coach.domain.athlete.profile import AthleteProfile, ProfileUpdate
from coach.domain.athlete.resources import Equipment
from coach.domain.common.errors import DomainValidationError
from coach.domain.strength.catalogue import CATALOGUE, CATALOGUE_VERSION
from coach.domain.strength.exercise import (
    Alias,
    Exercise,
    ExerciseAlias,
    ExerciseCatalogue,
    normalise_alias,
    own_aliases,
)
from coach.domain.strength.vocabulary import BodyRegion, MovementPattern, MuscleGroup
from samples import ATHLETE
from schemas import EXAMPLES_DIR, load_json, validation_errors


@pytest.mark.req("FR-STR-002", "FR-STR-003")
def test_the_catalogue_covers_every_pattern_and_conforms_to_its_contract() -> None:
    assert len(CATALOGUE) >= 50
    assert {item.movement_pattern for item in CATALOGUE} == set(MovementPattern)
    for exercise in CATALOGUE:
        document = exercise_to_contract(exercise, CATALOGUE_VERSION)
        assert validation_errors("exercise", document) == [], exercise.exercise_id
        for substitution in exercise.substitutions:
            assert CATALOGUE.get(substitution).movement_pattern is exercise.movement_pattern
        assert {alias.language for alias in exercise.aliases} <= {"en", "es"}
    assert all(any(alias.language == "es" for alias in item.aliases) for item in CATALOGUE)


@pytest.mark.req("FR-STR-003")
def test_the_published_examples_name_catalogue_exercises() -> None:
    workout = load_json(EXAMPLES_DIR / "workout.strength.example.json")
    for item in workout["strength"]["exercises"]:
        assert item["exerciseId"] in CATALOGUE
        assert all(alternative in CATALOGUE for alternative in item["alternatives"])
    example = load_json(EXAMPLES_DIR / "exercise.example.json")
    exercise = CATALOGUE.get(example["exerciseId"])
    assert exercise_to_contract(exercise, CATALOGUE_VERSION) == example


@pytest.mark.req("FR-STR-003")
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ex.back_squat", "ex.back_squat"),
        ("Back squat", "ex.back_squat"),
        ("  sentadilla   CON barra ", "ex.back_squat"),
        ("Sentadilla bulgara", "ex.bulgarian_split_squat"),  # accents do not matter
        ("pull up", "ex.pull_up"),  # nor separators
        ("Dominadas", "ex.pull_up"),
        ("Peso muerto rumano", "ex.romanian_deadlift"),
        ("Farmer's walk", "ex.farmers_carry"),
    ],
)
def test_names_and_aliases_resolve_to_one_identity(text: str, expected: str) -> None:
    found = CATALOGUE.resolve(text)
    assert found is not None
    assert found.exercise_id == expected


@pytest.mark.req("FR-STR-003", "FR-PRF-002")
def test_unknown_text_is_never_guessed() -> None:
    assert CATALOGUE.resolve("sentadilla rara") is None
    assert CATALOGUE.resolve("ex.not_an_exercise") is None
    assert [item.exercise_id for item in CATALOGUE.search("calf")] == [
        "ex.standing_calf_raise",
        "ex.seated_calf_raise",
        "ex.single_leg_calf_raise",
    ]
    assert normalise_alias(" Élévation-de_talón ") == "elevation de talon"


def exercise(**changes: Any) -> Exercise:
    fields: dict[str, Any] = {
        "exercise_id": "ex.test",
        "canonical_name": "Test",
        "movement_pattern": MovementPattern.SQUAT,
        "primary_muscles": (MuscleGroup.QUADRICEPS,),
        "equipment": (Equipment.BODYWEIGHT_ONLY,),
        "technical_difficulty": 1,
    }
    fields.update(changes)
    return Exercise(**fields)


@pytest.mark.req("FR-STR-002", "FR-STR-003")
@pytest.mark.parametrize(
    "changes",
    [
        {"exercise_id": "back squat"},
        {"primary_muscles": ()},
        {"secondary_muscles": (MuscleGroup.QUADRICEPS,)},  # listed twice
        {"equipment": ()},
        {"technical_difficulty": 6},
        {"substitutions": ("ex.test",)},
    ],
)
def test_an_exercise_refuses_inconsistent_data(changes: dict[str, Any]) -> None:
    with pytest.raises(DomainValidationError):
        exercise(**changes)


@pytest.mark.req("FR-STR-003")
def test_a_catalogue_refuses_unknown_substitutions_shared_aliases_and_other_patterns() -> None:
    lunge = exercise(exercise_id="ex.lunge", movement_pattern=MovementPattern.LUNGE)
    with pytest.raises(DomainValidationError, match="unknown substitution"):
        ExerciseCatalogue("9.0.0", [exercise(substitutions=("ex.missing",))])
    with pytest.raises(DomainValidationError, match="pattern"):
        ExerciseCatalogue("9.0.0", [exercise(substitutions=("ex.lunge",)), lunge])
    with pytest.raises(DomainValidationError, match="names two"):
        ExerciseCatalogue(
            "9.0.0",
            [
                exercise(aliases=(Alias("es", "Sentadilla"),)),
                exercise(exercise_id="ex.other", canonical_name="Sentadilla"),
            ],
        )
    with pytest.raises(DomainValidationError, match="duplicate"):
        ExerciseCatalogue("9.0.0", [exercise(), exercise()])


@pytest.mark.req("FR-STR-003", "FR-PRF-001")
def test_the_athletes_own_aliases_map_to_catalogue_ids() -> None:
    mine = (ExerciseAlias("Búlgaras", "ex.bulgarian_split_squat"),)
    profile = AthleteProfile.new(ATHLETE).apply(ProfileUpdate(exercise_aliases=mine))
    found = CATALOGUE.resolve("bulgaras", own_aliases(profile.exercise_aliases))
    assert found is not None
    assert found.exercise_id == "ex.bulgarian_split_squat"
    twice = (*mine, ExerciseAlias("bulgaras", "ex.split_squat"))
    with pytest.raises(DomainValidationError, match="twice"):
        AthleteProfile.new(ATHLETE).apply(ProfileUpdate(exercise_aliases=twice))
    with pytest.raises(DomainValidationError):
        ExerciseAlias("---", "ex.split_squat")


@pytest.mark.req("FR-STR-002", "FR-TEN-003")
def test_body_regions_extend_muscle_groups_with_joint_regions() -> None:
    assert {region.value for region in BodyRegion} == {muscle.value for muscle in MuscleGroup} | {
        "ACHILLES_REGION",
        "KNEES",
        "SHOULDERS",
        "ELBOWS",
    }
    assert BodyRegion.of(MuscleGroup.CALVES) is BodyRegion.CALVES


@pytest.mark.req("FR-STR-003")
@pytest.mark.parametrize(
    ("text", "exercise_id", "message"),
    [
        ("Sentadilla\x07 búlgara", "ex.back_squat", "already a catalogue name"),  # cleaned first
        ("Dominadas", "ex.chin_up", "already a catalogue name of ex.pull_up"),
        ("Mis remos", "ex.not_there", "unknown exercise"),
    ],
)
def test_own_aliases_are_checked_on_the_text_as_stored(
    text: str, exercise_id: str, message: str
) -> None:
    with pytest.raises(DomainValidationError, match=message):
        CATALOGUE.check_own_alias(ExerciseAlias(text, exercise_id))
    assert (
        CATALOGUE.check_own_alias(ExerciseAlias("Mis remos", "ex.barbell_row")).text == "Mis remos"
    )


@pytest.mark.req("FR-STR-003")
@pytest.mark.parametrize("text", ["ex.deadlift", "EX.DEADLIFT", " ex.deadlift_ "])
def test_an_alias_cannot_look_like_an_exercise_id(text: str) -> None:
    with pytest.raises(DomainValidationError, match="exercise ID"):
        ExerciseAlias(text, "ex.back_squat")
    with pytest.raises(DomainValidationError):
        Exercise(**{**_base(), "exercise_id": "ex.back_squat\n"})


def _base() -> dict[str, Any]:
    return {
        "exercise_id": "ex.test",
        "canonical_name": "Test",
        "movement_pattern": MovementPattern.SQUAT,
        "primary_muscles": (MuscleGroup.QUADRICEPS,),
        "equipment": (Equipment.BODYWEIGHT_ONLY,),
        "technical_difficulty": 1,
    }
