"""Exercise <-> exercise contract (urn:coach:schema:exercise:1; strength spec section 3)."""

from __future__ import annotations

from coach.application.contracts._json import SCHEMA_VERSION, Json
from coach.domain.strength.exercise import Exercise


def exercise_to_contract(exercise: Exercise, catalogue_version: str) -> Json:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "exerciseId": exercise.exercise_id,
        "catalogueVersion": catalogue_version,
        "canonicalName": exercise.canonical_name,
        "movementPattern": exercise.movement_pattern.value,
        "primaryMuscles": [muscle.value for muscle in exercise.primary_muscles],
        "secondaryMuscles": [muscle.value for muscle in exercise.secondary_muscles],
        "equipment": [item.value for item in exercise.equipment],
        "unilateral": exercise.unilateral,
        "technicalDifficulty": exercise.technical_difficulty,
        "contraindicationTags": [tag.value for tag in exercise.contraindication_tags],
        "substitutions": list(exercise.substitutions),
        "aliases": [{"language": alias.language, "text": alias.text} for alias in exercise.aliases],
    }
