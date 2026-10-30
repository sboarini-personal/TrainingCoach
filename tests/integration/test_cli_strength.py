"""`coach strength exercises` and `coach strength alias` (P10; strength spec section 3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cli_harness import Cli


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
