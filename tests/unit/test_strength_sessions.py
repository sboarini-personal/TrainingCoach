"""Strength sessions: the athlete's set entries reconciled with the watch's sets
(strength spec section 5; FR-STR-005). Synthetic data only."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest

from activity_world import SCOPE, World
from coach.application.consent import ConsentRequiredError
from coach.application.contracts import STRENGTH_SESSION_CODEC
from coach.application.errors import InvalidOperationError, NotFoundError
from coach.application.events import EventName
from coach.application.ownership import OwnerScope
from coach.domain.activity.fields import StreamKind, StreamRef
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.sport import Sport
from coach.domain.consent.ledger import ConsentType
from coach.domain.strength.session import (
    SetEntry,
    SetSource,
    StrengthSession,
    StrengthSetResult,
    entry_targets,
    reconcile,
    session_id_of,
)
from coach.domain.strength.sets import SetType, WatchSet, WatchSets
from samples import ACTIVITY, ATHLETE, NOW, OTHER_ATHLETE, SHA_C, START, sample_observation
from schemas import validation_errors

SETS_SHA = "5" * 64
LATER = NOW + timedelta(hours=1)


def watch_set(
    offset: float,
    exercise: str | None,
    *,
    reps: int | None = 8,
    weight: float | None = 60.0,
    code: str | None = None,
    rest: bool = False,
) -> WatchSet:
    return WatchSet(
        start_offset_seconds=offset,
        duration_seconds=40.0,
        type=SetType.REST if rest else SetType.ACTIVE,
        repetitions=None if rest else reps,
        weight_kg=None if rest else weight,
        exercise_id=exercise,
        provider_exercise=code,
    )


def watch() -> WatchSets:
    """Back squat x3, an unrecognised squat x2 (the file's code only), a rest between."""
    return WatchSets(
        start_time_utc=START,
        sets=(
            watch_set(60, "ex.back_squat", code="fit:squat/barbell_back_squat"),
            watch_set(100, None, rest=True),
            watch_set(220, "ex.back_squat", code="fit:squat/barbell_back_squat"),
            watch_set(380, "ex.back_squat", reps=6, code="fit:squat/barbell_back_squat"),
            watch_set(540, None, reps=12, weight=20.0, code="fit:squat"),
            watch_set(700, None, reps=12, weight=20.0, code="fit:squat"),
        ),
    )


def entry(exercise: str = "ex.back_squat", number: int = 1, **values: Any) -> SetEntry:
    return SetEntry(exercise_id=exercise, set_number=number, recorded_at=LATER, **values)


def session(*entries: SetEntry, **changes: Any) -> StrengthSession:
    base = StrengthSession.new(ATHLETE, ACTIVITY)
    for item in entries:
        base = base.record(item)
    if "relabel" in changes:
        base = base.relabel(*changes["relabel"])
    if changes.get("watch") is False:
        base = base.watch_sets(False)
    return base


def keys(results: tuple[StrengthSetResult, ...]) -> list[tuple[str | None, int]]:
    return [(item.exercise_id, item.set_number) for item in results]


# --------------------------------------------------------------------------- domain


@pytest.mark.req("FR-STR-005")
def test_the_watch_numbers_its_active_sets_per_exercise_in_time_order() -> None:
    results = reconcile(watch(), None)
    assert keys(results) == [
        ("ex.back_squat", 1),
        ("ex.back_squat", 2),
        ("ex.back_squat", 3),
        (None, 1),  # the exercise stays UNKNOWN: never guessed
        (None, 2),
    ]
    first, unknown = results[0], results[3]
    assert first.recorded_at == START + timedelta(seconds=60)
    assert (first.source, first.completed_reps, first.actual_load_kg) == (SetSource.FIT, 8, 60.0)
    assert first.field_sources == {"completedReps": SetSource.FIT, "actualLoadKg": SetSource.FIT}
    assert (unknown.watch_exercise, unknown.actual_rpe) == ("fit:squat", None)


@pytest.mark.req("FR-STR-005", "FR-ACT-005")
def test_the_athletes_entry_wins_field_by_field_and_rpe_comes_only_from_the_athlete() -> None:
    corrected = entry(number=3, completed_reps=7, actual_rpe=8.5, actual_rir=1, target_reps=8)
    results = reconcile(watch(), session(corrected))
    third = results[2]
    assert (third.source, third.completed_reps, third.actual_load_kg) == (SetSource.FIT, 7, 60.0)
    assert (third.actual_rpe, third.actual_rir, third.target_reps) == (8.5, 1, 8)
    assert third.field_sources == {
        "completedReps": SetSource.ATHLETE,
        "actualLoadKg": SetSource.FIT,
        "actualRpe": SetSource.ATHLETE,
        "actualRir": SetSource.ATHLETE,
        "targetReps": SetSource.ATHLETE,
    }
    assert third.recorded_at == START + timedelta(seconds=380)  # when it was done
    assert len(results) == 5  # matched, not added: volume is counted once


@pytest.mark.req("FR-STR-005")
def test_entries_no_watch_set_matches_are_sets_of_their_own() -> None:
    extra = (
        entry("ex.romanian_deadlift", 2, completed_reps=10, actual_load_kg=50.0),
        entry("ex.back_squat", 4, completed_reps=5, actual_load_kg=70.0),
        entry("ex.romanian_deadlift", 1, completed_reps=10, actual_load_kg=50.0),
    )
    results = reconcile(watch(), session(*extra))
    assert keys(results)[5:] == [
        ("ex.romanian_deadlift", 1),
        ("ex.romanian_deadlift", 2),
        ("ex.back_squat", 4),
    ]
    own = results[5]
    assert (own.source, own.recorded_at, own.duration_seconds) == (SetSource.ATHLETE, LATER, None)
    assert reconcile(None, session(*extra)) == tuple(results[5:])


@pytest.mark.req("FR-STR-005", "FR-STR-003")
def test_a_relabel_names_what_the_watch_did_not_recognise() -> None:
    named = session(
        entry("ex.goblet_squat", 2, actual_rpe=7.0), relabel=("fit:squat", "ex.goblet_squat")
    )
    results = reconcile(watch(), named)
    assert keys(results)[3:] == [("ex.goblet_squat", 1), ("ex.goblet_squat", 2)]
    assert results[4].actual_rpe == 7.0
    assert results[4].watch_exercise == "fit:squat"
    wrong = session(relabel=("ex.back_squat", "ex.front_squat"))
    assert keys(reconcile(watch(), wrong))[:3] == [
        ("ex.front_squat", 1),
        ("ex.front_squat", 2),
        ("ex.front_squat", 3),
    ]


@pytest.mark.req("FR-STR-005")
def test_ignoring_the_watch_leaves_only_the_athletes_sets() -> None:
    mine = session(entry(completed_reps=8, actual_load_kg=60.0), watch=False)
    results = reconcile(watch(), mine)
    assert keys(results) == [("ex.back_squat", 1)]
    assert results[0].source is SetSource.ATHLETE


@pytest.mark.req("FR-STR-005", "FR-PRF-002")
@pytest.mark.parametrize(
    "values",
    [
        {},  # states nothing
        {"actual_rpe": 7.3},  # RPE steps of 0.5
        {"actual_rpe": 11.0},
        {"actual_rir": 11},
        {"completed_reps": -1},
        {"actual_load_kg": 1001.0},
        {"set_number": 0, "completed_reps": 8},
    ],
)
def test_a_set_entry_refuses_values_out_of_range(values: dict[str, Any]) -> None:
    number = values.pop("set_number", 1)
    with pytest.raises(DomainValidationError):
        entry(number=number, **values)


@pytest.mark.req("FR-STR-005")
def test_a_session_keeps_one_entry_per_set_and_derives_its_id() -> None:
    first = session(entry(completed_reps=8), entry(completed_reps=9))
    assert [item.completed_reps for item in first.entries] == [9]  # replaced
    assert first.session_id == session_id_of(ACTIVITY)
    with pytest.raises(DomainValidationError):
        StrengthSession(session_id=UUID(int=1), athlete_id=ATHLETE, activity_id=ACTIVITY)
    with pytest.raises(DomainValidationError):
        first.remove("ex.back_squat", 2)
    with pytest.raises(DomainValidationError):
        first.relabel("fit:squat", None)  # nothing to forget
    with pytest.raises(DomainValidationError):
        first.relabel("Squat!", "ex.goblet_squat")


# --------------------------------------------------------------------------- service


def strength_activity(world: World, *, sets: WatchSets | None = None) -> UUID:
    streams: tuple[StreamRef, ...] = ()
    if sets is not None:
        world.segments[SETS_SHA] = sets
        streams = (StreamRef(StreamKind.SETS, SETS_SHA),)
    values = {
        "classification.sport": Sport.STRENGTH,
        "classification.subtype": "GYM",
        "timing.startTimeUtc": START,
        "timing.elapsedSeconds": 3600.0,
    }
    observation = sample_observation(
        values=values, streams=streams, raw_source_id=UUID(int=0x5E7), sha256=SHA_C
    )
    return world.ingest(observation).activity.activity_id


@pytest.mark.req("FR-STR-005", "SEC-AUD-001")
def test_recording_a_set_creates_the_session_and_links_it_to_the_activity() -> None:
    world = World()
    activity_id = strength_activity(world, sets=watch())
    view = world.service.effective(SCOPE, activity_id).activity
    assert "strength.strengthSessionId" not in view.values
    recorded = world.strength.record(SCOPE, activity_id, entry(number=2, actual_rpe=8.0))
    assert recorded.session is not None
    assert recorded.session.version == 1
    assert recorded.sets[1].actual_rpe == 8.0
    view = world.service.effective(SCOPE, activity_id).activity
    assert view.values["strength.strengthSessionId"] == session_id_of(activity_id)
    world.strength.record(SCOPE, activity_id, entry(number=2, actual_rpe=8.5))
    again = world.strength.sets(SCOPE, activity_id)
    assert again.session is not None
    assert (again.session.version, again.sets[1].actual_rpe) == (2, 8.5)
    assert again.watch_exercises() == ["ex.back_squat", "fit:squat"]
    documents = [record.document for record in world.audit.all_records()]
    actions = [(item["action"], item["target"]["type"]) for item in documents if item["target"]]
    assert actions.count(("strength.session_changed", "STRENGTH_SESSION")) == 2
    assert (EventName.STRENGTH_SESSION_CHANGED, {"change": "RECORDED"}) in world.events.events


@pytest.mark.req("FR-STR-005", "FR-STR-003")
def test_relabels_need_a_name_the_watch_used_and_a_catalogue_exercise() -> None:
    world = World()
    activity_id = strength_activity(world, sets=watch())
    with pytest.raises(InvalidOperationError, match="recorded no"):
        world.strength.relabel(SCOPE, activity_id, "fit:lunge", "ex.reverse_lunge")
    with pytest.raises(InvalidOperationError, match="unknown exercise"):
        world.strength.relabel(SCOPE, activity_id, "fit:squat", "ex.made_up")
    named = world.strength.relabel(SCOPE, activity_id, "fit:squat", "ex.goblet_squat")
    assert keys(named.sets)[3:] == [("ex.goblet_squat", 1), ("ex.goblet_squat", 2)]
    forgotten = world.strength.relabel(SCOPE, activity_id, "fit:squat", None)
    assert keys(forgotten.sets)[3] == (None, 1)
    ignored = world.strength.use_watch_sets(SCOPE, activity_id, False)
    assert ignored.sets == ()


@pytest.mark.req("FR-STR-005")
def test_sets_are_recorded_on_active_strength_activities_with_catalogue_exercises() -> None:
    world = World()
    run = world.ingest(sample_observation()).activity.activity_id
    with pytest.raises(InvalidOperationError, match="strength activities"):
        world.strength.record(SCOPE, run, entry(completed_reps=8))
    activity_id = strength_activity(world)
    with pytest.raises(InvalidOperationError, match="unknown exercise"):
        world.strength.record(SCOPE, activity_id, entry("ex.made_up", completed_reps=8))
    only_mine = world.strength.record(SCOPE, activity_id, entry(completed_reps=8))
    assert (only_mine.watch, keys(only_mine.sets)) == (None, [("ex.back_squat", 1)])


@pytest.mark.req("FR-STR-005")
def test_an_unreadable_sets_stream_leaves_only_the_athletes_sets() -> None:
    world = World()
    activity_id = strength_activity(world, sets=watch())
    del world.segments[SETS_SHA]  # missing or damaged blob
    found = world.strength.sets(SCOPE, activity_id)
    assert (found.watch, found.sets) == (None, ())


@pytest.mark.req("FR-STR-005", "SEC-AUTHZ-001", "PRIV-CON-002")
def test_sessions_are_owner_scoped_and_need_the_activities_consent() -> None:
    world = World()
    activity_id = strength_activity(world, sets=watch())
    world.strength.record(SCOPE, activity_id, entry(number=1, actual_rpe=7.0))
    world.grant(OTHER_ATHLETE)
    with pytest.raises(NotFoundError):
        world.strength.sets(OwnerScope(OTHER_ATHLETE), activity_id)
    world.consents.revoke(SCOPE, ConsentType.ACTIVITIES)
    with pytest.raises(ConsentRequiredError):
        world.strength.sets(SCOPE, activity_id)


def watch_before_squats() -> WatchSets:
    """An unrecognised squat (60 kg) before two back squat sets (100 kg)."""
    return WatchSets(
        start_time_utc=START,
        sets=(
            watch_set(0, None, weight=60.0, code="fit:squat/9999"),
            watch_set(100, "ex.back_squat", weight=100.0, code="fit:squat/barbell_back_squat"),
            watch_set(200, "ex.back_squat", weight=100.0, code="fit:squat/barbell_back_squat"),
        ),
    )


@pytest.mark.req("FR-STR-005")
def test_entry_targets_name_the_watch_set_each_entry_corrects() -> None:
    mine = session(entry(number=1, actual_rpe=9.0), entry("ex.goblet_squat", 1, completed_reps=8))
    assert entry_targets(watch_before_squats(), mine) == {
        ("ex.back_squat", 1): 100.0,
        ("ex.goblet_squat", 1): None,  # a set of its own
    }
    moved = mine.relabel("fit:squat/9999", "ex.back_squat")
    assert entry_targets(watch_before_squats(), moved)[("ex.back_squat", 1)] == 0.0


@pytest.mark.req("FR-STR-005")
@pytest.mark.parametrize(
    ("label", "exercise", "number"),
    [
        ("fit:squat/9999", "ex.back_squat", 1),  # the RPE would move to the 60 kg set
        ("ex.back_squat", "ex.front_squat", 2),  # the entry would become a fourth set
    ],
)
def test_a_relabel_that_would_move_entries_is_refused(
    label: str, exercise: str, number: int
) -> None:
    world = World()
    activity_id = strength_activity(world, sets=watch_before_squats())
    world.strength.record(SCOPE, activity_id, entry(number=number, actual_rpe=9.0))
    with pytest.raises(InvalidOperationError, match="remove those entries first"):
        world.strength.relabel(SCOPE, activity_id, label, exercise)
    found = world.strength.sets(SCOPE, activity_id)
    assert len(found.sets) == 3
    assert found.session is not None
    assert found.session.relabels == ()
    world.strength.remove(SCOPE, activity_id, "ex.back_squat", number)
    assert len(world.strength.relabel(SCOPE, activity_id, label, exercise).sets) == 3


@pytest.mark.req("FR-STR-005", "NFR-REL-004")
def test_the_contract_refuses_an_entry_that_states_nothing() -> None:
    document = STRENGTH_SESSION_CODEC.dump(session(entry(completed_reps=8)).with_version(1))
    assert validation_errors("strength-session", document) == []
    for key in ("completedReps",):
        document["entries"][0][key] = None
    assert validation_errors("strength-session", document) != []
