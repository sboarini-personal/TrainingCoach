"""`coach strength` (P10; strength spec sections 3 and 5): the catalogue, aliases and the
sets of imported strength activities, end to end through the parser sandbox and SQLite."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from cli_harness import Cli
from file_samples import START, fit_file, fit_set, strength_session


@pytest.fixture
def cli(tmp_path: Path) -> Cli:
    command = Cli(tmp_path / "coach-data")
    assert command.run("init")[0] == 0
    return command


@pytest.mark.req("FR-STR-003")
def test_the_catalogue_is_listed_searched_and_shown(cli: Cli) -> None:
    code, output = cli.run("strength", "exercises")
    assert code == 0
    assert output.splitlines()[-1] == "catalogue 1.0.0: 54 of 54 exercises"
    code, output = cli.run("strength", "exercises", "--pattern", "CALF_RAISE")
    assert code == 0
    assert "ex.seated_calf_raise" in output
    assert "ex.back_squat" not in output
    code, output = cli.run("strength", "exercises", "sentadilla bulgara")
    assert code == 0
    assert output.splitlines()[0] == "ex.bulgarian_split_squat  Bulgarian split squat"
    assert "substitutions ex.split_squat, ex.reverse_lunge" in output
    code, output = cli.run("strength", "exercises", "zzz")
    assert (code, "no exercise matches" in output) == (0, True)


@pytest.mark.req("FR-STR-003", "FR-PRF-001")
def test_own_aliases_are_added_used_and_removed(cli: Cli) -> None:
    assert cli.run("strength", "alias") == (0, "no aliases of your own\n")
    code, output = cli.run("strength", "alias", "Búlgaras", "ex.bulgarian_split_squat")
    assert (code, output) == (0, "alias 'Búlgaras' -> ex.bulgarian_split_squat\n")
    code, output = cli.run("strength", "exercises", "bulgaras")
    assert output.startswith("ex.bulgarian_split_squat")
    code, output = cli.run("strength", "alias", "bulgaras", "ex.split_squat")  # replaces it
    assert code == 0
    assert cli.run("strength", "alias")[1].split() == ["bulgaras", "ex.split_squat"]
    assert cli.run("strength", "alias", "Bulgaras", "--remove")[0] == 0
    assert cli.run("strength", "alias") == (0, "no aliases of your own\n")


@pytest.mark.req("FR-STR-003", "FR-PRF-002")
@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (("Dominadas", "ex.chin_up"), "already a catalogue name of ex.pull_up"),
        (("Mis sentadillas", "ex.unknown"), "unknown exercise"),
        (("Mis sentadillas",), "name the exercise"),
        (("Mis sentadillas", "--remove"), "no alias"),
    ],
)
def test_aliases_never_shadow_the_catalogue_or_invent_exercises(
    cli: Cli, argv: tuple[str, ...], message: str
) -> None:
    code, output = cli.run("strength", "alias", *argv)
    assert code == 2
    assert output.startswith("error:")
    assert message in output


# --------------------------------------------------------------------------- sets


def _imported_workout(cli: Cli, tmp_path: Path) -> str:
    """Two back squat sets and one squat the mapping does not name, through the sandbox."""
    assert cli.run("consent", "grant", "activities")[0] == 0
    assert cli.run("profile", "timezone", "America/Argentina/Buenos_Aires")[0] == 0
    at = START + timedelta(seconds=60)
    sets = [
        fit_set(at, 40, reps=8, weight=60.0, category="squat", subtype=6),
        fit_set(at + timedelta(seconds=40), 120, set_type="rest"),
        fit_set(at + timedelta(seconds=160), 40, reps=8, weight=62.5, category="squat", subtype=6),
        fit_set(at + timedelta(seconds=400), 40, reps=12, weight=20.0, category="squat"),
    ]
    path = tmp_path / "gym.fit"
    path.write_bytes(fit_file([strength_session(sets)]))
    code, output = cli.run("import", str(path))
    assert code == 0, output
    return cli.run("activity", "list")[1].splitlines()[0].split()[-1]


def _rows(output: str) -> list[list[str]]:
    lines = output.splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith("exercise"))
    return [line.split() for line in lines[start + 1 :] if line.startswith(("ex.", "?"))]


@pytest.mark.req("FR-STR-005", "INT-FIL-001")
def test_imported_sets_are_shown_corrected_and_completed(cli: Cli, tmp_path: Path) -> None:
    activity = _imported_workout(cli, tmp_path)
    code, output = cli.run("strength", "sets", activity)
    assert code == 0, output
    assert "watch sets: used" in output
    assert _rows(output) == [
        ["ex.back_squat", "1", "8", "60", "-", "-", "-", "FIT"],
        ["ex.back_squat", "2", "8", "62.5", "-", "-", "-", "FIT"],
        ["?", "fit:squat", "1", "12", "20", "-", "-", "-", "FIT"],
    ]
    assert "the watch did not name: fit:squat (coach strength relabel)" in output
    code, output = cli.run(
        "strength", "log", activity, "sentadilla con barra", "--set", "2", "--reps", "7",
        "--rpe", "9", "--target-reps", "8",
    )  # fmt: skip
    assert code == 0, output
    assert _rows(output)[1] == [
        "ex.back_squat", "2", "7", "62.5", "9", "-", "8", "reps", "FIT+ATHLETE",
    ]  # fmt: skip
    code, output = cli.run("strength", "relabel", activity, "fit:squat", "goblet squat")
    assert code == 0, output
    assert _rows(output)[2][:2] == ["ex.goblet_squat", "1"]
    code, output = cli.run(
        "strength", "log", activity, "ex.romanian_deadlift", "--reps", "10", "--load", "50"
    )
    assert _rows(output)[3] == ["ex.romanian_deadlift", "1", "10", "50", "-", "-", "-", "ATHLETE"]
    code, output = cli.run("strength", "watch-sets", activity, "ignore")
    assert "watch sets: ignored" in output
    assert [row[0] for row in _rows(output)] == ["ex.back_squat", "ex.romanian_deadlift"]
    code, output = cli.run("activity", "show", activity)
    assert code == 0, output


@pytest.mark.req("FR-STR-005", "FR-PRF-002")
def test_sets_need_a_known_exercise_and_a_strength_activity(cli: Cli, tmp_path: Path) -> None:
    activity = _imported_workout(cli, tmp_path)
    code, output = cli.run("strength", "log", activity, "sentadilla rara", "--reps", "8")
    assert (code, "unknown exercise 'sentadilla rara'" in output) == (2, True)
    code, output = cli.run("strength", "log", activity, "ex.back_squat")
    assert (code, "at least one value" in output) == (2, True)
    code, output = cli.run("strength", "log", activity, "ex.back_squat", "--remove")
    assert (code, "--set" in output) == (2, True)
    code, output = cli.run("strength", "relabel", activity, "fit:lunge", "ex.reverse_lunge")
    assert (code, "recorded no fit:lunge" in output) == (2, True)
    missing = "0193b2a0-0000-7000-8000-00000000ffff"
    assert cli.run("strength", "sets", missing) == (2, "error: activity not found\n")
