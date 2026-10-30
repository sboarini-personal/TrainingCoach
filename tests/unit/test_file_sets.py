"""Strength sets from FIT files: the SETS stream (files spec 8.7, normalisation rules 1.2.0).

Synthetic FIT files from the official SDK encoder. No real athlete data.
"""

from __future__ import annotations

import copy
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from garmin_fit_sdk import Profile

from coach.application.contracts import SETS_CODEC
from coach.application.files import DecodedSession, FileRejectedError
from coach.domain.raw.source import DetectedType
from coach.domain.strength.catalogue import CATALOGUE
from coach.domain.strength.sets import SetType
from coach.integrations.files.fit_exercises import FIT_EXERCISES, map_exercises
from coach.integrations.files.fit_parser import parse_fit
from coach.integrations.files.normalise import normalise
from coach.integrations.files.output import decode_response
from coach.integrations.files.parsing import ParserLimits
from file_samples import START, fit_file, fit_session, fit_set, strength_session
from samples import NOW
from schemas import validation_errors

LIMITS = ParserLimits(records=100_000, activities=500, xml_depth=64, xml_elements=100_000)
SQUAT, ROMANIAN, BACK_SQUAT, GENERIC_SQUAT = "squat", 23, 6, 61


def _output(tmp_path: Path, data: bytes) -> dict[str, Any]:
    path = tmp_path / "quarantined"
    path.write_bytes(data)
    output = parse_fit(path, LIMITS)
    assert validation_errors("parser-output", output) == []
    return output


def _decode(tmp_path: Path, data: bytes) -> list[DecodedSession]:
    response = {"ok": True, "output": _output(tmp_path, data)}
    return normalise(decode_response(response, DetectedType.FIT, max_sessions=500), received_at=NOW)


def _workout() -> list[dict[str, Any]]:
    """Three back squat sets and two Romanian deadlift sets, a rest after each."""
    at = START + timedelta(seconds=60)
    messages = []
    for category, subtype, reps, weight in (
        (SQUAT, BACK_SQUAT, 8, 60.0),
        (SQUAT, BACK_SQUAT, 8, 60.0),
        (SQUAT, BACK_SQUAT, 6, 65.0),
        ("deadlift", ROMANIAN, 10, 50.0),
        ("deadlift", ROMANIAN, 10, 50.0),
    ):
        messages.append(
            fit_set(at, 40, reps=reps, weight=weight, category=category, subtype=subtype)
        )
        at += timedelta(seconds=40)
        messages.append(fit_set(at, 120, set_type="rest"))
        at += timedelta(seconds=120)
    return messages


@pytest.mark.req("INT-FIL-001", "FR-STR-005")
def test_fit_sets_become_the_sets_stream(tmp_path: Path) -> None:
    (session,) = _decode(tmp_path, fit_file([strength_session(_workout())]))
    sets = session.sets
    assert sets is not None
    assert session.normaliser_version == "1.2.0"
    assert sets.start_time_utc == START
    assert len(sets) == 10
    first, rest = sets.sets[0], sets.sets[1]
    assert (first.start_offset_seconds, first.duration_seconds, first.type) == (
        60.0,
        40.0,
        SetType.ACTIVE,
    )
    assert (first.repetitions, first.weight_kg) == (8, 60.0)
    assert (first.exercise_id, first.provider_exercise) == (
        "ex.back_squat",
        "fit:squat/barbell_back_squat",
    )
    assert (rest.type, rest.repetitions, rest.exercise_id, rest.provider_exercise) == (
        SetType.REST,
        None,
        None,
        None,
    )
    assert sets.sets[6].exercise_id == "ex.romanian_deadlift"
    assert SETS_CODEC.load(SETS_CODEC.dump(sets)) == sets
    assert validation_errors("sets", SETS_CODEC.dump(sets)) == []


@pytest.mark.req("FR-STR-003", "FR-STR-005", "FR-PRF-002")
@pytest.mark.parametrize(
    ("category", "subtype", "code"),
    [
        (SQUAT, None, "fit:squat"),  # the category alone
        (SQUAT, GENERIC_SQUAT, "fit:squat/squat"),  # a name without an exact equivalent
        (SQUAT, 9999, "fit:squat/9999"),  # a number the profile does not name
        ([SQUAT, "deadlift"], [BACK_SQUAT, ROMANIAN], "fit:squat/barbell_back_squat"),
    ],
)
def test_an_exercise_without_an_exact_equivalent_stays_unknown(
    tmp_path: Path, category: Any, subtype: Any, code: str
) -> None:
    sets = [fit_set(START + timedelta(seconds=60), 40, reps=12, category=category, subtype=subtype)]
    (session,) = _decode(tmp_path, fit_file([strength_session(sets)]))
    assert session.sets is not None
    (only,) = session.sets.sets
    assert (only.exercise_id, only.provider_exercise, only.repetitions) == (None, code, 12)


@pytest.mark.req("INT-FIL-001", "FR-STR-005")
def test_values_out_of_range_are_unknown_and_sets_outside_the_session_left_out(
    tmp_path: Path,
) -> None:
    sets = [
        fit_set(START + timedelta(seconds=60), 0, reps=2000, weight=2000.0),
        fit_set(START + timedelta(seconds=3600), 30, reps=5),  # after the 1800 s session
    ]
    (session,) = _decode(tmp_path, fit_file([strength_session(sets)]))
    assert session.sets is not None
    (only,) = session.sets.sets
    assert (only.duration_seconds, only.repetitions, only.weight_kg) == (None, None, None)


@pytest.mark.req("INT-FIL-001")
def test_only_strength_sessions_keep_their_sets(tmp_path: Path) -> None:
    records, run = fit_session()
    with_sets = ([*records, fit_set(START + timedelta(seconds=60), 30, reps=5)], run)
    (session,) = _decode(tmp_path, fit_file([with_sets]))
    assert session.sets is None
    (session,) = _decode(tmp_path, fit_file([strength_session([])]))
    assert session.sets is None


@pytest.mark.req("SEC-UPL-004")
@pytest.mark.parametrize(
    "change",
    [
        {"exercises": ["Squat/Back"]},  # not a code
        {"exercises": ["a", "b", "c", "d", "e"]},  # too many
        {"repetitions": "8"},
        {"unexpected": 1},
    ],
)
def test_the_parent_refuses_malformed_sets(tmp_path: Path, change: dict[str, Any]) -> None:
    sets = [fit_set(START + timedelta(seconds=60), 40, reps=8, category=SQUAT, subtype=6)]
    output = _output(tmp_path, fit_file([strength_session(sets)]))
    broken = copy.deepcopy(output)
    broken["sessions"][0]["sets"][0].update(change)
    with pytest.raises(FileRejectedError) as raised:
        decode_response({"ok": True, "output": broken}, DetectedType.FIT, max_sessions=500)
    assert raised.value.detail == "PARSER_OUTPUT"


@pytest.mark.req("FR-STR-003", "INT-FIL-001")
def test_every_mapped_fit_name_exists_in_the_sdk_profile_and_the_catalogue() -> None:
    categories = set(Profile["types"]["exercise_category"].values())
    for code, exercise_id in FIT_EXERCISES.items():
        category, name = code.split("/")
        assert category in categories, code
        assert name in Profile["types"][f"{category}_exercise_name"].values(), code
        assert exercise_id in CATALOGUE, exercise_id
    assert len(set(FIT_EXERCISES.values())) >= 45
    assert map_exercises(()) == (None, None)
    assert map_exercises(("squat/barbell_back_squat", "squat/barbell_back_squat")) == (
        "ex.back_squat",
        "fit:squat/barbell_back_squat",
    )


def _subtype(category: str, name: str) -> int:
    table: dict[int, str] = Profile["types"][f"{category}_exercise_name"]
    return next(number for number, value in table.items() if value == name)


@pytest.mark.req("FR-STR-003", "INT-FIL-001")
def test_every_mapped_fit_exercise_is_recognised_through_the_parser(tmp_path: Path) -> None:
    """Names longer than 40 characters included (review of P10.2)."""
    at = START + timedelta(seconds=60)
    sets, expected = [], []
    for code, exercise_id in FIT_EXERCISES.items():
        category, name = code.split("/")
        sets.append(fit_set(at, 20, reps=5, category=category, subtype=_subtype(category, name)))
        expected.append(exercise_id)
        at += timedelta(seconds=20)
    (session,) = _decode(tmp_path, fit_file([strength_session(sets, seconds=len(sets) * 20 + 120)]))
    assert session.sets is not None
    assert [item.exercise_id for item in session.sets.sets] == expected
    assert FIT_EXERCISES["deadlift/single_leg_romanian_deadlift_with_dumbbell"] == (
        "ex.single_leg_romanian_deadlift"
    )
    assert "hip_raise/barbell_hip_thrust_on_floor" not in FIT_EXERCISES  # a glute bridge


@pytest.mark.req("FR-STR-003", "INT-FIL-001")
def test_categories_the_profile_does_not_name_are_left_out(tmp_path: Path) -> None:
    step_up = _subtype("squat", "barbell_step_up")
    sets = [
        fit_set(
            START + timedelta(seconds=60), 40, reps=8, category=["squat", 48], subtype=[step_up, 3]
        ),
        fit_set(START + timedelta(seconds=200), 40, reps=8, category=48, subtype=3),
    ]
    (session,) = _decode(tmp_path, fit_file([strength_session(sets)]))
    assert session.sets is not None
    first, second = session.sets.sets
    assert (first.exercise_id, first.provider_exercise) == (
        "ex.step_up",
        "fit:squat/barbell_step_up",
    )
    assert (second.exercise_id, second.provider_exercise) == (None, None)
