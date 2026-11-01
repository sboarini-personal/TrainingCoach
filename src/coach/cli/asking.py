"""Asking for what planning is missing (athlete's decision D-12, review of 2026-10-31; ADR-034).

Instead of only refusing, ``coach plan generate`` asks for each missing input it can take as a
short answer, loads it with the very command the athlete would run (same validation, same
audit), and tries once more. Inputs that need a whole command, such as a goal or a health
constraint, are named with that command. Without an interactive terminal nothing is asked and
the refusal names the commands. The policies are unchanged: unknown still never allows, and
health and safety deny until the athlete declares them.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final, TextIO

from coach.application.ownership import OwnerScope
from coach.cli.runtime import Runtime
from coach.domain.common.provenance import FieldValue
from coach.domain.onboarding.readiness import LABELS, ReadinessInput

type Ask = Callable[[str], str]  # one answer; EOFError when nobody answers
type Apply = Callable[[Sequence[str]], str | None]  # runs a command: None, or its error

COMMANDS: Final[dict[ReadinessInput, str]] = {
    ReadinessInput.PROFILE: "coach profile name / timezone / birth-date",
    ReadinessInput.PRIMARY_GOAL: (
        "coach goal add --sport SPORT --date YYYY-MM-DD, then coach goal activate GOAL"
    ),
    ReadinessInput.CURRENT_LEVEL: "coach profile experience SPORT --years N",
    ReadinessInput.WEEKLY_AVAILABILITY: (
        "coach availability set --days N --max-hours H, and coach availability default --minutes M"
    ),
    ReadinessInput.HEALTH_CONSTRAINTS: "coach profile health none, add or confirm",
    ReadinessInput.RESOURCES: "coach profile resources --gym yes|no --equipment LIST",
    ReadinessInput.STRENGTH_EXPERIENCE: (
        "coach profile experience strength --years N --sessions-per-week N"
    ),
    ReadinessInput.TENNIS_SCHEDULE: "coach profile experience tennis --sessions-per-week N",
}


@dataclass(frozen=True, slots=True)
class Question:
    prompts: tuple[str, ...]
    command: Callable[[tuple[str, ...]], list[str] | None]  # None: not an answer it takes


def _unknown(field: FieldValue[Any] | None) -> bool:
    return field is None or field.value is None


def questions(runtime: Runtime, scope: OwnerScope, missing: ReadinessInput) -> list[Question]:
    """The short questions that load one missing input (none when it needs a whole command)."""
    profile = runtime.inputs.profile(scope)
    match missing:
        case ReadinessInput.PROFILE:
            found = []
            if _unknown(profile.display_name):
                found.append(Question(("Your name: ",), lambda a: ["profile", "name", a[0]]))
            if _unknown(profile.timezone):
                found.append(
                    Question(
                        ("Your timezone (for example America/Argentina/Buenos_Aires): ",),
                        lambda a: ["profile", "timezone", a[0]],
                    )
                )
            if _unknown(profile.birth_date):
                found.append(
                    Question(
                        ("Your birth date (YYYY-MM-DD): ",),
                        lambda a: ["profile", "birth-date", a[0]],
                    )
                )
            return found
        case ReadinessInput.CURRENT_LEVEL:
            goal = runtime.inputs.primary_goal(scope)
            if goal is None:
                return []
            sport = goal.sport.value.lower()
            return [
                Question(
                    (f"Years of {sport} experience: ",),
                    lambda a: ["profile", "experience", sport, "--years", a[0]],
                )
            ]
        case ReadinessInput.WEEKLY_AVAILABILITY:
            return [
                Question(
                    ("Days a week you can train (1 to 7): ", "Hours a week at most: "),
                    lambda a: ["availability", "set", "--days", a[0], "--max-hours", a[1]],
                ),
                Question(
                    ("Minutes you can train on a usual day: ",),
                    lambda a: ["availability", "default", "--minutes", a[0]],
                ),
            ]
        case ReadinessInput.HEALTH_CONSTRAINTS:
            if profile.health is not None:  # declared, but older than 30 days
                return [
                    Question(
                        ("Is your health declaration still right? (yes to re-confirm): ",),
                        lambda a: ["profile", "health", "confirm"] if _yes(a[0]) else None,
                    )
                ]
            return [
                Question(
                    ("Any health constraint? (none, or add it with coach profile health add): ",),
                    lambda a: (
                        ["profile", "health", "none"] if a[0].strip().lower() == "none" else None
                    ),
                )
            ]
        case ReadinessInput.RESOURCES:
            return [
                Question(
                    (
                        "Do you have a gym? (yes/no): ",
                        "Equipment you can use, comma-separated (or none): ",
                    ),
                    lambda a: (
                        ["profile", "resources", "--gym", a[0].strip().lower()]
                        + ([] if a[1].strip().lower() == "none" else ["--equipment", a[1].strip()])
                    ),
                )
            ]
        case ReadinessInput.STRENGTH_EXPERIENCE:
            return [
                Question(
                    ("Years of strength training: ", "Strength sessions a week: "),
                    lambda a: [
                        "profile",
                        "experience",
                        "strength",
                        "--years",
                        a[0],
                        "--sessions-per-week",
                        a[1],
                    ],
                )
            ]
        case ReadinessInput.TENNIS_SCHEDULE:
            return [
                Question(
                    ("Tennis sessions a week (0 if none): ",),
                    lambda a: ["profile", "experience", "tennis", "--sessions-per-week", a[0]],
                )
            ]
        case _:  # a goal or the history: a whole command, or time
            return []


def _yes(text: str) -> bool:
    return text.strip().lower() in {"yes", "y", "si", "sí", "s"}


def ask_for(
    runtime: Runtime,
    scope: OwnerScope,
    missing: Sequence[ReadinessInput],
    out: TextIO,
    ask: Ask,
    apply: Apply,
) -> bool:
    """Ask for the missing inputs; True when anything was loaded (worth another try)."""
    loaded = False
    for item in missing:
        for question in questions(runtime, scope, item):  # the refusal names the rest
            try:
                answers = tuple(ask(prompt) for prompt in question.prompts)
            except EOFError:
                return loaded
            argv = question.command(answers)
            if argv is None:
                print(f"not loaded: use {COMMANDS[item]}", file=out)
                continue
            error = apply(argv)
            if error is None:
                loaded = True
            else:
                print(f"not loaded ({error}): use {COMMANDS[item]}", file=out)
    return loaded


def missing_message(missing: Sequence[ReadinessInput]) -> str:
    """The refusal without anybody to answer: what is missing and the command for each."""
    names = "; ".join(
        f"{LABELS[item]} ({COMMANDS.get(item, 'see coach readiness')})" for item in missing
    )
    return f"not ready for a plan; missing: {names}"
