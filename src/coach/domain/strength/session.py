"""Strength sessions: the athlete's set entries for one activity, reconciled with the sets
the watch recorded (strength spec section 5; FR-STR-005).

A ``StrengthSession`` holds what the athlete declared about one strength activity: set
entries, the exercises the watch got wrong (relabels) and whether the watch's sets are used
at all. ``reconcile`` merges it with the activity's SETS stream into ``StrengthSetResult``s,
a pure function of the two, field by field:

- sets match on (exercise, set number); the watch numbers its active sets per exercise in
  time order, after the relabels;
- a field the athlete states wins (the entry corrects that set, as a DataOverride corrects an
  activity field); RPE, RIR and targets come only from the athlete; durations and times only
  from the watch;
- a watch set nobody names stays as recorded; an entry no watch set matches is a set of its
  own. Volume is never counted twice.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final, Self
from uuid import UUID, uuid5

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.time import ensure_utc
from coach.domain.strength.exercise import check_exercise_id
from coach.domain.strength.sets import (
    PROVIDER_EXERCISE,
    WatchSets,
    check_load,
    check_repetitions,
    check_rir,
    check_rpe,
)

# Fixed: a session's ID is derived from its activity, one session per activity.
SESSION_NAMESPACE: Final = UUID("0193b2a0-5e55-7000-8000-5e55105e5510")
MAX_SET_NUMBER: Final = 50
MAX_ENTRIES: Final = 300


def session_id_of(activity_id: UUID) -> UUID:
    return uuid5(SESSION_NAMESPACE, str(activity_id))


class SetSource(StrEnum):
    FIT = "FIT"  # recorded by the watch (the SETS stream of a FIT file)
    ATHLETE = "ATHLETE"


@dataclass(frozen=True, slots=True, kw_only=True)
class SetEntry:
    """One set as the athlete declares it; every value is optional but one."""

    exercise_id: str
    set_number: int
    recorded_at: datetime
    completed_reps: int | None = None
    actual_load_kg: float | None = None
    actual_rpe: float | None = None
    actual_rir: int | None = None
    target_reps: int | None = None
    target_load_kg: float | None = None
    target_rpe: float | None = None

    def __post_init__(self) -> None:
        check_exercise_id(self.exercise_id)
        number = self.set_number
        if isinstance(number, bool) or not isinstance(number, int):
            raise DomainValidationError("set numbers are whole numbers")
        if not 1 <= number <= MAX_SET_NUMBER:
            raise DomainValidationError(f"set numbers are 1 to {MAX_SET_NUMBER}")
        object.__setattr__(self, "recorded_at", ensure_utc(self.recorded_at))
        check_repetitions(self.completed_reps, "completed repetitions")
        check_repetitions(self.target_reps, "target repetitions")
        check_load(self.actual_load_kg, "actual load")
        check_load(self.target_load_kg, "target load")
        check_rpe(self.actual_rpe, "actual RPE")
        check_rpe(self.target_rpe, "target RPE")
        check_rir(self.actual_rir)
        if all(value is None for value in self.values().values()):
            raise DomainValidationError("a set entry states at least one value")

    @property
    def key(self) -> tuple[str, int]:
        return self.exercise_id, self.set_number

    def values(self) -> dict[str, float | int | None]:
        return {
            "completedReps": self.completed_reps,
            "actualLoadKg": self.actual_load_kg,
            "actualRpe": self.actual_rpe,
            "actualRir": self.actual_rir,
            "targetReps": self.target_reps,
            "targetLoadKg": self.target_load_kg,
            "targetRpe": self.target_rpe,
        }


@dataclass(frozen=True, slots=True)
class Relabel:
    """The watch named an exercise wrongly or not at all: ``watch_exercise`` (a catalogue ID
    or the file's code) is ``exercise_id`` in this session."""

    watch_exercise: str
    exercise_id: str

    def __post_init__(self) -> None:
        label = self.watch_exercise
        if not (PROVIDER_EXERCISE.fullmatch(label) or label.startswith("ex.")):
            raise DomainValidationError(f"not an exercise the watch names: {label!r}")
        if label.startswith("ex."):
            check_exercise_id(label)
        check_exercise_id(self.exercise_id)
        if label == self.exercise_id:
            raise DomainValidationError("a relabel names another exercise")


@dataclass(frozen=True, slots=True, kw_only=True)
class StrengthSession:
    session_id: UUID
    athlete_id: UUID
    activity_id: UUID
    version: int = 0
    use_watch_sets: bool = True
    relabels: tuple[Relabel, ...] = ()
    entries: tuple[SetEntry, ...] = ()

    def __post_init__(self) -> None:
        if self.session_id != session_id_of(self.activity_id):
            raise DomainValidationError("a strength session's ID derives from its activity")
        keys = [entry.key for entry in self.entries]
        if len(keys) != len(set(keys)):
            raise DomainValidationError("a set is entered twice")
        if len(keys) > MAX_ENTRIES:
            raise DomainValidationError(f"at most {MAX_ENTRIES} set entries per session")
        labels = [relabel.watch_exercise for relabel in self.relabels]
        if len(labels) != len(set(labels)):
            raise DomainValidationError("a watch exercise is relabelled twice")

    @classmethod
    def new(cls, athlete_id: UUID, activity_id: UUID) -> StrengthSession:
        return cls(
            session_id=session_id_of(activity_id), athlete_id=athlete_id, activity_id=activity_id
        )

    @property
    def entity_id(self) -> UUID:
        return self.session_id

    def with_version(self, version: int) -> Self:
        return replace(self, version=version)

    def record(self, entry: SetEntry) -> StrengthSession:
        """Add the entry, replacing an earlier one of the same exercise and set number."""
        kept = tuple(item for item in self.entries if item.key != entry.key)
        return replace(self, entries=(*kept, entry))

    def remove(self, exercise_id: str, set_number: int) -> StrengthSession:
        kept = tuple(item for item in self.entries if item.key != (exercise_id, set_number))
        if len(kept) == len(self.entries):
            raise DomainValidationError(f"no entry for set {set_number} of {exercise_id}")
        return replace(self, entries=kept)

    def relabel(self, watch_exercise: str, exercise_id: str | None) -> StrengthSession:
        """Name the watch's exercise ``exercise_id``; ``None`` forgets the relabel."""
        kept = tuple(item for item in self.relabels if item.watch_exercise != watch_exercise)
        if exercise_id is None:
            if len(kept) == len(self.relabels):
                raise DomainValidationError(f"{watch_exercise} is not relabelled")
            return replace(self, relabels=kept)
        return replace(self, relabels=(*kept, Relabel(watch_exercise, exercise_id)))

    def watch_sets(self, use: bool) -> StrengthSession:
        return replace(self, use_watch_sets=use)


@dataclass(frozen=True, slots=True, kw_only=True)
class StrengthSetResult:
    """One set as executed (strength spec section 5): the reconciled view."""

    exercise_id: str | None  # None: the watch's exercise maps to no catalogue exercise
    set_number: int
    recorded_at: datetime
    source: SetSource  # who recorded the set; ``field_sources`` says who supplied each value
    workout_id: UUID | None = None  # the planned workout, once plans exist (P12)
    completed_reps: int | None = None
    actual_load_kg: float | None = None
    actual_rpe: float | None = None
    actual_rir: int | None = None
    target_reps: int | None = None
    target_load_kg: float | None = None
    target_rpe: float | None = None
    duration_seconds: float | None = None
    start_offset_seconds: float | None = None
    watch_exercise: str | None = None  # what the watch named, when it recorded the set
    field_sources: Mapping[str, SetSource] = field(default_factory=dict)


def _watch_label(exercise_id: str | None, code: str | None) -> str | None:
    return exercise_id if exercise_id is not None else code


def _watch_results(watch: WatchSets, relabels: Mapping[str, str]) -> list[StrengthSetResult]:
    numbers: dict[str | None, int] = {}
    results = []
    for item in watch.sets:
        if not item.active:
            continue
        label = _watch_label(item.exercise_id, item.provider_exercise)
        exercise = relabels.get(label, item.exercise_id) if label is not None else None
        group = exercise if exercise is not None else f"?{label}"
        numbers[group] = numbers.get(group, 0) + 1
        sources = {}
        if item.repetitions is not None:
            sources["completedReps"] = SetSource.FIT
        if item.weight_kg is not None:
            sources["actualLoadKg"] = SetSource.FIT
        results.append(
            StrengthSetResult(
                exercise_id=exercise,
                set_number=numbers[group],
                recorded_at=watch.start_time_utc + timedelta(seconds=item.start_offset_seconds),
                source=SetSource.FIT,
                completed_reps=item.repetitions,
                actual_load_kg=item.weight_kg,
                duration_seconds=item.duration_seconds,
                start_offset_seconds=item.start_offset_seconds,
                watch_exercise=label,
                field_sources=sources,
            )
        )
    return results


_ENTRY_FIELDS: Final = {
    "completedReps": "completed_reps",
    "actualLoadKg": "actual_load_kg",
    "actualRpe": "actual_rpe",
    "actualRir": "actual_rir",
    "targetReps": "target_reps",
    "targetLoadKg": "target_load_kg",
    "targetRpe": "target_rpe",
}


def _merged(result: StrengthSetResult, entry: SetEntry) -> StrengthSetResult:
    changes: dict[str, object] = {}
    sources = dict(result.field_sources)
    for name, value in entry.values().items():
        if value is not None:
            changes[_ENTRY_FIELDS[name]] = value
            sources[name] = SetSource.ATHLETE
    return replace(result, **changes, field_sources=sources)  # type: ignore[arg-type]


def _entry_result(entry: SetEntry) -> StrengthSetResult:
    return _merged(
        StrengthSetResult(
            exercise_id=entry.exercise_id,
            set_number=entry.set_number,
            recorded_at=entry.recorded_at,
            source=SetSource.ATHLETE,
        ),
        entry,
    )


def entry_targets(
    watch: WatchSets | None, session: StrengthSession
) -> dict[tuple[str, int], float | None]:
    """For each entry, the start offset of the watch set it corrects; ``None`` when it is a
    set of its own. A relabel must leave these unchanged (strength spec 5)."""
    matched = {
        (item.exercise_id, item.set_number): item.start_offset_seconds
        for item in reconcile(watch, session)
        if item.source is SetSource.FIT and item.exercise_id is not None
    }
    return {entry.key: matched.get(entry.key) for entry in session.entries}


def reconcile(
    watch: WatchSets | None, session: StrengthSession | None
) -> tuple[StrengthSetResult, ...]:
    """The sets of one activity as executed: watch sets in time order, then the athlete's
    sets no watch set matches, in the order they were first entered per exercise."""
    entries: Iterable[SetEntry] = () if session is None else session.entries
    use_watch = session is None or session.use_watch_sets
    relabels = (
        {} if session is None else {r.watch_exercise: r.exercise_id for r in session.relabels}
    )
    results = _watch_results(watch, relabels) if watch is not None and use_watch else []
    by_key = {entry.key: entry for entry in entries}
    matched = set()
    for index, result in enumerate(results):
        if result.exercise_id is None:
            continue
        entry = by_key.get((result.exercise_id, result.set_number))
        if entry is not None:
            results[index] = _merged(result, entry)
            matched.add(entry.key)
    order: dict[str, int] = {}
    for entry in entries:
        order.setdefault(entry.exercise_id, len(order))
    unmatched = sorted(
        (entry for entry in by_key.values() if entry.key not in matched),
        key=lambda entry: (order[entry.exercise_id], entry.set_number),
    )
    return (*results, *(_entry_result(entry) for entry in unmatched))
