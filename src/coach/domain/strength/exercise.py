"""Exercises and the versioned exercise catalogue (strength spec section 3; FR-STR-003).

Identity is the ``exerciseId``; aliases in any language map normalised text to it. Unknown
text resolves to nothing: an exercise is never guessed.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Final

from coach.domain.athlete.resources import Equipment
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.text import clean_text
from coach.domain.strength.vocabulary import ContraindicationTag, MovementPattern, MuscleGroup

EXERCISE_ID: Final = re.compile(r"^ex\.[a-z0-9_]+$")
_LANGUAGE: Final = re.compile(r"^[a-z]{2,3}$")
_SEPARATORS: Final = re.compile(r"[\s\-_/]+")
MAX_NAME_LENGTH: Final = 100


def normalise_alias(text: str) -> str:
    """Lower case, accents removed, spaces, hyphens, underscores and slashes collapsed to one
    space: the form aliases are matched in."""
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _SEPARATORS.sub(" ", plain.casefold()).strip()


def check_exercise_id(exercise_id: str) -> str:
    if not EXERCISE_ID.fullmatch(exercise_id):
        raise DomainValidationError(f"invalid exercise id: {exercise_id!r}")
    return exercise_id


@dataclass(frozen=True, slots=True)
class Alias:
    language: str
    text: str

    def __post_init__(self) -> None:
        if not _LANGUAGE.fullmatch(self.language):
            raise DomainValidationError(f"invalid alias language: {self.language!r}")
        object.__setattr__(
            self, "text", clean_text(self.text, max_length=MAX_NAME_LENGTH, field="alias")
        )


@dataclass(frozen=True, slots=True)
class ExerciseAlias:
    """An athlete's own name for a catalogue exercise (strength spec section 3)."""

    text: str
    exercise_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "text", clean_text(self.text, max_length=MAX_NAME_LENGTH, field="alias")
        )
        check_exercise_id(self.exercise_id)
        if not normalise_alias(self.text):
            raise DomainValidationError("an alias needs letters or digits")
        if normalise_alias(self.text).startswith("ex."):
            raise DomainValidationError("an alias cannot look like an exercise ID")

    @property
    def key(self) -> str:
        return normalise_alias(self.text)


def own_aliases(aliases: Iterable[ExerciseAlias]) -> dict[str, str]:
    """Normalised text -> exercise ID, for ``ExerciseCatalogue.resolve``."""
    return {alias.key: alias.exercise_id for alias in aliases}


@dataclass(frozen=True, slots=True, kw_only=True)
class Exercise:
    exercise_id: str
    canonical_name: str
    movement_pattern: MovementPattern
    primary_muscles: tuple[MuscleGroup, ...]
    secondary_muscles: tuple[MuscleGroup, ...] = ()
    equipment: tuple[Equipment, ...]
    unilateral: bool = False
    technical_difficulty: int
    contraindication_tags: tuple[ContraindicationTag, ...] = ()
    substitutions: tuple[str, ...] = ()
    aliases: tuple[Alias, ...] = ()

    def __post_init__(self) -> None:
        check_exercise_id(self.exercise_id)
        object.__setattr__(
            self,
            "canonical_name",
            clean_text(self.canonical_name, max_length=MAX_NAME_LENGTH, field="exercise name"),
        )
        if not self.primary_muscles:
            raise DomainValidationError(f"{self.exercise_id} needs a primary muscle")
        muscles = (*self.primary_muscles, *self.secondary_muscles)
        if len(set(muscles)) != len(muscles):
            raise DomainValidationError(f"{self.exercise_id}: a muscle is listed twice")
        if not self.equipment or len(set(self.equipment)) != len(self.equipment):
            raise DomainValidationError(f"{self.exercise_id}: equipment once each, at least one")
        if not 1 <= self.technical_difficulty <= 5:
            raise DomainValidationError("technical difficulty is 1 to 5")
        for substitution in self.substitutions:
            check_exercise_id(substitution)
        if self.exercise_id in self.substitutions:
            raise DomainValidationError(f"{self.exercise_id} cannot substitute itself")

    @property
    def muscles(self) -> tuple[MuscleGroup, ...]:
        return (*self.primary_muscles, *self.secondary_muscles)

    def names(self) -> Iterator[str]:
        """The canonical name and every alias, normalised."""
        yield normalise_alias(self.canonical_name)
        for alias in self.aliases:
            yield normalise_alias(alias.text)


class ExerciseCatalogue:
    """Versioned, immutable reference data (strength spec section 3)."""

    def __init__(self, version: str, exercises: Iterable[Exercise]) -> None:
        self.version = version
        by_id: dict[str, Exercise] = {}
        for exercise in exercises:
            if exercise.exercise_id in by_id:
                raise DomainValidationError(f"duplicate exercise id: {exercise.exercise_id}")
            by_id[exercise.exercise_id] = exercise
        names: dict[str, str] = {}
        for exercise in by_id.values():
            for substitution in exercise.substitutions:
                other = by_id.get(substitution)
                if other is None:
                    raise DomainValidationError(f"unknown substitution: {substitution}")
                if other.movement_pattern is not exercise.movement_pattern:
                    raise DomainValidationError(
                        f"{substitution} does not share the pattern of {exercise.exercise_id}"
                    )
            for name in exercise.names():
                owner = names.setdefault(name, exercise.exercise_id)
                if owner != exercise.exercise_id:
                    raise DomainValidationError(f"alias {name!r} names two exercises")
        self._by_id: Final = by_id
        self._names: Final = names

    def __iter__(self) -> Iterator[Exercise]:
        return iter(self._by_id.values())

    def __len__(self) -> int:
        return len(self._by_id)

    def __contains__(self, exercise_id: object) -> bool:
        return exercise_id in self._by_id

    def get(self, exercise_id: str) -> Exercise:
        found = self._by_id.get(exercise_id)
        if found is None:
            raise DomainValidationError(f"unknown exercise: {exercise_id}")
        return found

    def owner_of(self, text: str) -> str | None:
        """The exercise a catalogue name or alias belongs to, if any."""
        return self._names.get(normalise_alias(text))

    def check_own_alias(self, alias: ExerciseAlias) -> ExerciseAlias:
        """An athlete's alias names a catalogue exercise and never a catalogue name (strength
        spec 3): it can never rename an exercise. Checked on the text as stored."""
        if alias.exercise_id not in self._by_id:
            raise DomainValidationError(f"unknown exercise: {alias.exercise_id}")
        owner = self._names.get(alias.key)
        if owner is not None:
            raise DomainValidationError(f"{alias.text!r} is already a catalogue name of {owner}")
        return alias

    def resolve(self, text: str, own: Mapping[str, str] | None = None) -> Exercise | None:
        """An exercise ID, a catalogue name or alias, or one of the athlete's own aliases
        (normalised text -> exercise ID); ``None`` when nothing matches."""
        stripped = text.strip()
        if stripped in self._by_id:
            return self._by_id[stripped]
        normalised = normalise_alias(stripped)
        exercise_id = self._names.get(normalised) or (own or {}).get(normalised)
        return None if exercise_id is None else self._by_id.get(exercise_id)

    def search(self, text: str) -> list[Exercise]:
        """Exercises whose ID, name or an alias contains the text (normalised)."""
        wanted = normalise_alias(text)
        return [
            exercise
            for exercise in self._by_id.values()
            if wanted in exercise.exercise_id or any(wanted in name for name in exercise.names())
        ]
