"""`coach checkin` and `coach safety` (P11; recovery spec 1, safety spec 2 to 4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cli_harness import Cli


@pytest.fixture
def cli(tmp_path: Path) -> Cli:
    command = Cli(tmp_path / "coach-data")
    assert command.run("init")[0] == 0
    return command


@pytest.mark.req("FR-REC-001")
def test_checkins_are_recorded_and_listed(cli: Cli) -> None:
    code, output = cli.run("checkin", "add", "--fatigue", "4")
    assert (code, "coach profile timezone" in output) == (2, True)
    assert cli.run("profile", "timezone", "America/Argentina/Buenos_Aires")[0] == 0
    code, output = cli.run("checkin", "add", "--sleep", "2", "--fatigue", "4", "--soreness", "3")
    assert (code, output) == (0, "check-in recorded for 2026-10-25\n")
    code, output = cli.run("checkin", "add", "--day", "2026-10-24", "--motivation", "5")
    assert code == 0, output
    code, output = cli.run("checkin", "list")
    lines = output.splitlines()
    assert lines[0].startswith("2026-10-24  sleepQuality -")
    assert "motivation 5" in lines[0]
    assert lines[1].startswith("2026-10-25  sleepQuality 2  fatigue 4  soreness 3")
    code, output = cli.run("checkin", "add", "--fatigue", "9")
    assert (code, output.startswith("error:")) == (2, True)


@pytest.mark.req("SAFE-MED-002", "SAFE-MED-004", "SAFE-MED-005")
def test_a_red_flag_pauses_intensive_training_until_the_athlete_releases_it(cli: Cli) -> None:
    assert cli.run("profile", "timezone", "America/Argentina/Buenos_Aires")[0] == 0
    assert cli.run("safety", "status") == (0, "safety hold: none\n")
    code, output = cli.run("checkin", "add", "--red-flag", "palpitations")
    assert code == 0, output
    assert "SAFETY_HOLD" in output
    assert "coach safety release --cleared-by-professional" in output
    assert cli.run("safety", "status") == (0, "safety hold: active\n")
    code, output = cli.run("safety", "release")
    assert code == 2  # the confirmation is required
    code, output = cli.run("safety", "release", "--cleared-by-professional")
    assert code == 0, output
    assert cli.run("safety", "status") == (0, "safety hold: none\n")
    code, output = cli.run("safety", "release", "--cleared-by-professional")
    assert (code, "no safety hold" in output) == (2, True)
