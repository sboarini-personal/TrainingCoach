"""Strength documents (strength spec sections 3 and 5): Exercise <-> exercise contract,
WatchSets <-> sets contract (the SETS stream) and StrengthSession <-> strength-session
contract."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from coach.application.contracts._json import (
    SCHEMA_VERSION,
    Document,
    Json,
    instant,
    parse_instant,
)
from coach.domain.strength.exercise import Exercise
from coach.domain.strength.session import Relabel, SetEntry, StrengthSession
from coach.domain.strength.sets import SetType, WatchSet, WatchSets


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


def _float(value: Any) -> float | None:
    return None if value is None else float(value)


def sets_to_contract(sets: WatchSets) -> Json:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "startTimeUtc": instant(sets.start_time_utc),
        "sets": [
            {
                "startOffsetSeconds": item.start_offset_seconds,
                "durationSeconds": item.duration_seconds,
                "type": None if item.type is None else item.type.value,
                "repetitions": item.repetitions,
                "weightKg": item.weight_kg,
                "exerciseId": item.exercise_id,
                "providerExercise": item.provider_exercise,
            }
            for item in sets.sets
        ],
    }


def sets_from_contract(document: Document) -> WatchSets:
    return WatchSets(
        start_time_utc=parse_instant(document["startTimeUtc"]),
        sets=tuple(
            WatchSet(
                start_offset_seconds=float(item["startOffsetSeconds"]),
                duration_seconds=_float(item["durationSeconds"]),
                type=None if item["type"] is None else SetType(item["type"]),
                repetitions=item["repetitions"],
                weight_kg=_float(item["weightKg"]),
                exercise_id=item["exerciseId"],
                provider_exercise=item["providerExercise"],
            )
            for item in document["sets"]
        ),
    )


def _entry(entry: SetEntry) -> Json:
    return {
        "exerciseId": entry.exercise_id,
        "setNumber": entry.set_number,
        "recordedAt": instant(entry.recorded_at),
        **entry.values(),
    }


def strength_session_to_contract(session: StrengthSession) -> Json:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "sessionId": str(session.session_id),
        "athleteId": str(session.athlete_id),
        "activityId": str(session.activity_id),
        "version": session.version,
        "useWatchSets": session.use_watch_sets,
        "relabels": [
            {"watchExercise": item.watch_exercise, "exerciseId": item.exercise_id}
            for item in session.relabels
        ],
        "entries": [_entry(entry) for entry in session.entries],
    }


def strength_session_from_contract(document: Document) -> StrengthSession:
    return StrengthSession(
        session_id=UUID(document["sessionId"]),
        athlete_id=UUID(document["athleteId"]),
        activity_id=UUID(document["activityId"]),
        version=document["version"],
        use_watch_sets=document["useWatchSets"],
        relabels=tuple(
            Relabel(item["watchExercise"], item["exerciseId"]) for item in document["relabels"]
        ),
        entries=tuple(
            SetEntry(
                exercise_id=item["exerciseId"],
                set_number=item["setNumber"],
                recorded_at=parse_instant(item["recordedAt"]),
                completed_reps=item["completedReps"],
                actual_load_kg=_float(item["actualLoadKg"]),
                actual_rpe=_float(item["actualRpe"]),
                actual_rir=item["actualRir"],
                target_reps=item["targetReps"],
                target_load_kg=_float(item["targetLoadKg"]),
                target_rpe=_float(item["targetRpe"]),
            )
            for item in document["entries"]
        ),
    )
