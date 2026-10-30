"""Strength documents (strength spec sections 3 to 5): Exercise <-> exercise contract,
WatchSets <-> sets contract (the SETS stream), StrengthSession <-> strength-session contract
and StrengthPrescription <-> the strength block of the workout contract."""

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
from coach.domain.strength.prescription import (
    EffortCue,
    ExpectedInterference,
    Load,
    LoadKind,
    PrescribedExercise,
    StrengthPrescription,
)
from coach.domain.strength.session import Relabel, SetEntry, StrengthSession
from coach.domain.strength.sets import SetType, WatchSet, WatchSets
from coach.domain.strength.vocabulary import (
    BodyRegion,
    InterferenceLevel,
    MovementPattern,
    MuscleGroup,
)


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


def _prescribed_to_contract(item: PrescribedExercise) -> Json:
    document: Json = {
        "exerciseId": item.exercise_id,
        "movementPattern": item.movement_pattern.value,
        "primaryMuscles": [muscle.value for muscle in item.primary_muscles],
        "secondaryMuscles": [muscle.value for muscle in item.secondary_muscles],
        "sets": item.sets,
        "reps": {"min": item.reps_min, "max": item.reps_max},
        "load": {"kind": item.load.kind.value, "value": item.load.value},
        "targetRpe": item.target_rpe,
        "targetRir": item.target_rir,
        "restSeconds": item.rest_seconds,
        "tempo": item.tempo,
        "alternatives": list(item.alternatives),
    }
    if item.effort_cue is not None:  # contract 1.1.0
        document["effortCue"] = item.effort_cue.value
    return document


def strength_prescription_to_contract(prescription: StrengthPrescription) -> Json:
    """The ``strength`` block of a planned workout (contract workout, strengthWorkout)."""
    interference = prescription.expected_interference
    return {
        "purpose": prescription.purpose,
        "estimatedDurationSeconds": prescription.estimated_duration_seconds,
        "warmup": list(prescription.warmup),
        "exercises": [_prescribed_to_contract(item) for item in prescription.exercises],
        "progressionRule": prescription.progression_rule,
        "regressionRule": prescription.regression_rule,
        "expectedInterference": {
            "level": interference.level.value,
            "affectedRegions": [region.value for region in interference.affected_regions],
            "notes": interference.notes,
        },
    }


def _whole(value: Any) -> Any:
    """JSON numbers like 3.0 are whole numbers; anything else is left to the domain checks."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _prescribed_from_contract(document: Document) -> PrescribedExercise:
    cue = document.get("effortCue")
    return PrescribedExercise(
        exercise_id=document["exerciseId"],
        movement_pattern=MovementPattern(document["movementPattern"]),
        primary_muscles=tuple(MuscleGroup(item) for item in document["primaryMuscles"]),
        secondary_muscles=tuple(MuscleGroup(item) for item in document["secondaryMuscles"]),
        sets=_whole(document["sets"]),
        reps_min=_whole(document["reps"]["min"]),
        reps_max=_whole(document["reps"]["max"]),
        load=Load(LoadKind(document["load"]["kind"]), _float(document["load"]["value"])),
        target_rpe=_float(document["targetRpe"]),
        target_rir=document["targetRir"],
        effort_cue=None if cue is None else EffortCue(cue),
        rest_seconds=_whole(document["restSeconds"]),
        tempo=document["tempo"],
        alternatives=tuple(document["alternatives"]),
    )


def strength_prescription_from_contract(document: Document) -> StrengthPrescription:
    interference = document["expectedInterference"]
    return StrengthPrescription(
        purpose=document["purpose"],
        estimated_duration_seconds=_whole(document["estimatedDurationSeconds"]),
        warmup=tuple(document["warmup"]),
        exercises=tuple(_prescribed_from_contract(item) for item in document["exercises"]),
        progression_rule=document["progressionRule"],
        regression_rule=document["regressionRule"],
        expected_interference=ExpectedInterference(
            level=InterferenceLevel(interference["level"]),
            affected_regions=tuple(BodyRegion(item) for item in interference["affectedRegions"]),
            notes=interference["notes"],
        ),
    )
