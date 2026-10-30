"""``coach strength`` commands: the exercise catalogue and the athlete's aliases (P10;
strength spec section 3)."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from typing import TextIO

from coach.application.errors import NotFoundError
from coach.application.ownership import OwnerScope
from coach.cli.activities import CommandError
from coach.cli.runtime import Runtime
from coach.domain.athlete.profile import AthleteProfile, ProfileUpdate
from coach.domain.common.errors import DomainValidationError
from coach.domain.strength.catalogue import CATALOGUE
from coach.domain.strength.exercise import Exercise, ExerciseAlias, normalise_alias, own_aliases
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
            if exact is not None
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
    if args.exercise_id not in CATALOGUE:
        raise CommandError(f"unknown exercise: {args.exercise_id}")
    owner = CATALOGUE.owner_of(args.text)
    if owner is not None:
        raise CommandError(f"{args.text!r} is already a catalogue name of {owner}")
    try:
        added = ExerciseAlias(args.text, args.exercise_id)
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
