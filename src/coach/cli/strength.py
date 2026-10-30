"""``coach strength`` commands: the exercise catalogue, the athlete's aliases and the sets of
strength activities (P10; strength spec sections 3 and 5)."""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import TextIO

from coach.application.consent import ConsentRequiredError
from coach.application.errors import InvalidOperationError, NotFoundError
from coach.application.ownership import OwnerScope
from coach.application.strength import SessionSets
from coach.cli.activities import CONSENT_HINT, CommandError
from coach.cli.analytics import athlete_today, total_text, week_argument
from coach.cli.runtime import Runtime
from coach.domain.athlete.profile import AthleteProfile, ProfileUpdate
from coach.domain.common.errors import DomainValidationError
from coach.domain.strength.catalogue import CATALOGUE
from coach.domain.strength.exercise import Exercise, ExerciseAlias, normalise_alias, own_aliases
from coach.domain.strength.progression import PROGRESSION_RULES_VERSION, Reason
from coach.domain.strength.session import SetEntry, SetSource, StrengthSetResult
from coach.domain.strength.vocabulary import MovementPattern


def _profile(runtime: Runtime, scope: OwnerScope) -> tuple[AthleteProfile, AthleteProfile | None]:
    try:
        stored: AthleteProfile | None = runtime.profiles.get(scope, scope.athlete_id)
    except NotFoundError:
        stored = None
    return stored or AthleteProfile.new(scope.athlete_id), stored


def _line(exercise: Exercise) -> str:
    muscles = ", ".join(muscle.value for muscle in exercise.primary_muscles)
    return (
        f"{exercise.exercise_id:<34} {exercise.canonical_name:<30} "
        f"{exercise.movement_pattern.value:<18} {muscles}"
    )


def exercises(runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO) -> int:
    """List or search the catalogue; with one exact match, show the exercise in full."""
    profile, _ = _profile(runtime, scope)
    found = list(CATALOGUE)
    if args.pattern is not None:
        found = [item for item in found if item.movement_pattern is MovementPattern(args.pattern)]
    if args.text is not None:
        exact = CATALOGUE.resolve(args.text, own_aliases(profile.exercise_aliases))
        found = (
            [exact]
            if exact is not None and exact in found
            else [item for item in CATALOGUE.search(args.text) if item in found]
        )
    if len(found) == 1 and args.text is not None:
        _details(found[0], out)
        return 0
    for exercise in found:
        print(_line(exercise), file=out)
    if not found:
        print("no exercise matches", file=out)
    print(f"catalogue {CATALOGUE.version}: {len(found)} of {len(CATALOGUE)} exercises", file=out)
    return 0


def _names(items: Iterable[str]) -> str:
    """Codes (enum members are strings) or IDs, comma-separated; ``-`` for none."""
    return ", ".join(str(item) for item in items) or "-"


def _details(exercise: Exercise, out: TextIO) -> None:
    names = _names
    print(f"{exercise.exercise_id}  {exercise.canonical_name}", file=out)
    print(f"  pattern       {exercise.movement_pattern.value}", file=out)
    print(f"  primary       {names(exercise.primary_muscles)}", file=out)
    print(f"  secondary     {names(exercise.secondary_muscles)}", file=out)
    print(f"  equipment     {names(exercise.equipment)}", file=out)
    print(f"  difficulty    {exercise.technical_difficulty} of 5", file=out)
    print(f"  unilateral    {'yes' if exercise.unilateral else 'no'}", file=out)
    print(f"  tags          {names(exercise.contraindication_tags)}", file=out)
    print(f"  substitutions {names(exercise.substitutions)}", file=out)
    aliases = ", ".join(f"{alias.text} ({alias.language})" for alias in exercise.aliases)
    print(f"  aliases       {aliases or '-'}", file=out)


def alias(runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO) -> int:
    """Show, add or remove the athlete's own exercise names."""
    profile, stored = _profile(runtime, scope)
    if args.text is None:
        for item in profile.exercise_aliases:
            print(f"{item.text:<34} {item.exercise_id}", file=out)
        if not profile.exercise_aliases:
            print("no aliases of your own", file=out)
        return 0
    key = normalise_alias(args.text)
    kept = tuple(item for item in profile.exercise_aliases if item.key != key)
    if args.remove:
        if len(kept) == len(profile.exercise_aliases):
            raise CommandError(f"no alias {args.text!r}")
        _save(runtime, scope, profile, stored, kept)
        print(f"alias {args.text!r} removed", file=out)
        return 0
    if args.exercise_id is None:
        raise CommandError("name the exercise, for example ex.bulgarian_split_squat")
    screened = runtime.screen.screen(args.text, source="exercise_alias")
    if screened.notice is not None:
        raise CommandError(screened.notice)  # never stored (SEC-SEC-005)
    try:
        added = CATALOGUE.check_own_alias(ExerciseAlias(args.text, args.exercise_id))
    except DomainValidationError as error:
        raise CommandError(str(error)) from None
    _save(runtime, scope, profile, stored, (*kept, added))
    print(f"alias {added.text!r} -> {added.exercise_id}", file=out)
    return 0


def _save(
    runtime: Runtime,
    scope: OwnerScope,
    profile: AthleteProfile,
    stored: AthleteProfile | None,
    aliases: tuple[ExerciseAlias, ...],
) -> None:
    updated = profile.apply(ProfileUpdate(exercise_aliases=aliases))
    if stored is None:
        runtime.profiles.add(scope, updated)
    else:
        runtime.profiles.update(scope, updated, expected_version=stored.version)


# --------------------------------------------------------------------------- sets


@contextmanager
def _translated() -> Iterator[None]:
    try:
        yield
    except NotFoundError:
        raise CommandError("activity not found") from None
    except ConsentRequiredError:
        raise CommandError(CONSENT_HINT) from None
    except (DomainValidationError, InvalidOperationError) as error:
        raise CommandError(str(error)) from None


def _exercise(runtime: Runtime, scope: OwnerScope, text: str) -> str:
    """A catalogue exercise named by ID, catalogue name or alias; never guessed."""
    profile, _ = _profile(runtime, scope)
    found = CATALOGUE.resolve(text, own_aliases(profile.exercise_aliases))
    if found is None:
        raise CommandError(f"unknown exercise {text!r}: see coach strength exercises")
    return found.exercise_id


def _number(value: float | None) -> str:
    return "-" if value is None else f"{value:g}"


def _source(item: StrengthSetResult) -> str:
    sources = {item.source, *item.field_sources.values()}
    return "+".join(source.value for source in SetSource if source in sources)


def _target(item: StrengthSetResult) -> str:
    parts = []
    if item.target_reps is not None:
        parts.append(f"{item.target_reps} reps")
    if item.target_load_kg is not None:
        parts.append(f"{item.target_load_kg:g} kg")
    if item.target_rpe is not None:
        parts.append(f"RPE {item.target_rpe:g}")
    return ", ".join(parts) or "-"


def _show(found: SessionSets, out: TextIO) -> None:
    view = found.activity
    watch = "none" if found.watch is None else "used"
    if found.session is not None and not found.session.use_watch_sets:
        watch = "ignored"
    print(f"activity {view.activity_id}  {view.training_day}  watch sets: {watch}", file=out)
    if not found.sets:
        print("no sets: enter them with coach strength log", file=out)
        return
    print(
        f"{'exercise':<34} {'set':>3} {'reps':>5} {'load kg':>8} {'RPE':>5} {'RIR':>4}  "
        f"{'target':<22} source",
        file=out,
    )
    unknown: list[str] = []
    for item in found.sets:
        exercise = item.exercise_id
        if exercise is None:
            exercise = f"? {item.watch_exercise or 'unnamed'}"
            if item.watch_exercise is not None and item.watch_exercise not in unknown:
                unknown.append(item.watch_exercise)
        print(
            f"{exercise:<34} {item.set_number:>3} {_number(item.completed_reps):>5} "
            f"{_number(item.actual_load_kg):>8} {_number(item.actual_rpe):>5} "
            f"{_number(item.actual_rir):>4}  {_target(item):<22} {_source(item)}",
            file=out,
        )
    named = {item.exercise_id for item in found.sets if item.exercise_id is not None}
    print(f"{len(found.sets)} sets, {len(named)} named exercises", file=out)
    if unknown:
        print(
            "the watch did not name: " + ", ".join(unknown) + " (coach strength relabel)",
            file=out,
        )


def sets(runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO) -> int:
    with _translated():
        found = runtime.strength.sets(scope, args.activity_id)
    _show(found, out)
    return 0


def log(runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO) -> int:
    exercise_id = _exercise(runtime, scope, args.exercise)
    with _translated():
        if args.remove:
            if args.set_number is None:
                raise CommandError("name the set to forget with --set")
            found = runtime.strength.remove(scope, args.activity_id, exercise_id, args.set_number)
        else:
            number = args.set_number
            if number is None and args.reps is None:
                raise CommandError(
                    "without --reps an entry describes a set already done: name it with --set"
                )
            if number is None:
                current = runtime.strength.sets(scope, args.activity_id)
                done = [item.set_number for item in current.sets if item.exercise_id == exercise_id]
                number = max(done, default=0) + 1
            entry = SetEntry(
                exercise_id=exercise_id,
                set_number=number,
                recorded_at=runtime.clock(),
                completed_reps=args.reps,
                actual_load_kg=args.load,
                actual_rpe=args.rpe,
                actual_rir=args.rir,
                target_reps=args.target_reps,
                target_load_kg=args.target_load,
                target_rpe=args.target_rpe,
            )
            found = runtime.strength.record(scope, args.activity_id, entry)
    _show(found, out)
    return 0


def relabel(runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO) -> int:
    if args.remove == (args.exercise is not None):
        raise CommandError("name the exercise, or forget the relabel with --remove")
    exercise_id = None if args.remove else _exercise(runtime, scope, args.exercise)
    with _translated():
        found = runtime.strength.relabel(scope, args.activity_id, args.watch_exercise, exercise_id)
    _show(found, out)
    return 0


def watch_sets(runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO) -> int:
    with _translated():
        found = runtime.strength.use_watch_sets(scope, args.activity_id, args.choice == "use")
    _show(found, out)
    return 0


def week(runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO) -> int:
    """Hard sets per muscle group and volume load of an ISO week (strength spec 8)."""
    key = week_argument(args.week, runtime, scope)
    with _translated():
        report = runtime.analytics.strength_week(scope, key)
    period = report.period
    print(
        f"week {key} ({period.start} to {period.end}): {report.activities} strength "
        f"sessions, {report.sets} sets",
        file=out,
    )
    if report.volume_load is not None:
        print(f"volume load {total_text(report.volume_load)}", file=out)
    if report.muscles:
        print(
            "hard sets per muscle group (primary 1, secondary 0.5; hard: RIR 4 or less, "
            "else RPE 6 or more):",
            file=out,
        )
    for item in report.muscles:
        text = total_text(item.hard_sets).replace(" unknown)", " of unknown effort)")
        print(f"  {item.muscle.value:<16} {text}", file=out)
    if report.unattributed:
        print(
            f"{report.unattributed} sets of an exercise the watch did not name "
            "(coach strength relabel)",
            file=out,
        )
    return 0


_REASONS = {
    Reason.TWO_EXPOSURES_MET: "the last two sessions met their target reps at or below target RPE",
    Reason.TARGETS_MET_ABOVE_RPE: "targets met, but above the target RPE",
    Reason.NOT_TWO_EXPOSURES_MET: "not two sessions in a row met the targets at or below RPE",
    Reason.ONE_EXPOSURE_MISSED: "one of the last two sessions missed its targets",
    Reason.TWO_EXPOSURES_MISSED: "reps missed or RPE over target + 1.5 in the last two sessions",
    Reason.PAIN_IN_REGION: "pain reported for a region this exercise loads",
    Reason.PAIN_REGION_UNKNOWN: "pain reported without a region it names: no progression",
    Reason.FEWER_THAN_TWO_EXPOSURES: "fewer than two sessions with this exercise",
    Reason.NO_TARGETS: "a session has no targets (coach strength log --target-reps --target-rpe)",
    Reason.NO_TARGET_RPE: "no target RPE to judge the effort by",
    Reason.MISSING_VALUES: "a value the targets need (reps or RPE) is missing",
}


def progress(runtime: Runtime, scope: OwnerScope, args: argparse.Namespace, out: TextIO) -> int:
    """Progression decisions from the execution history (strength spec 6)."""
    exercise_id = None if args.exercise is None else _exercise(runtime, scope, args.exercise)
    until = athlete_today(runtime, scope)
    with _translated():
        decisions = runtime.analytics.progression(
            scope, until=until, weeks=args.weeks, exercise_id=exercise_id
        )
    print(
        f"progression rules {PROGRESSION_RULES_VERSION}, the {args.weeks} weeks to {until}",
        file=out,
    )
    if not decisions:
        print("no strength sets in these weeks", file=out)
    for decision in decisions:
        reasons = "; ".join(_REASONS[reason] for reason in decision.reasons)
        sessions = ", ".join(
            f"{exposure.training_day} {found.value}" for exposure, found in decision.exposures
        )
        print(f"{decision.exercise_id:<34} {decision.decision.value:<18} {reasons}", file=out)
        if sessions:
            print(f"{'':<34} {'':<18} sessions: {sessions}", file=out)
    return 0
