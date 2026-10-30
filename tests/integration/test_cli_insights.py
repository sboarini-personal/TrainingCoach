"""`coach insights` (P11; insights spec): detection end to end with SQLite."""

from __future__ import annotations

from pathlib import Path

import pytest

from cli_harness import Cli


@pytest.fixture
def cli(tmp_path: Path) -> Cli:
    command = Cli(tmp_path / "coach-data")
    assert command.run("init")[0] == 0
    assert command.run("profile", "timezone", "America/Argentina/Buenos_Aires")[0] == 0
    return command


@pytest.mark.req("FR-INS-001", "FR-INS-002", "PRIV-CON-002")
def test_detection_finds_fatigue_and_says_what_it_could_not_evaluate(cli: Cli) -> None:
    for day, fatigue in (("2026-10-22", "3"), ("2026-10-23", "4"), ("2026-10-24", "4")):
        assert cli.run("checkin", "add", "--day", day, "--fatigue", fatigue)[0] == 0
    assert cli.run("checkin", "add", "--fatigue", "5")[0] == 0  # today, 2026-10-25
    code, output = cli.run("insights", "detect")
    assert code == 0, output
    lines = output.splitlines()
    assert lines[0] == "insights to 2026-10-25: 1 found (1 new)"
    assert lines[1] == ("PATTERN.ACCUMULATED_FATIGUE  2026-10-19 to 2026-10-25  confidence MEDIUM")
    assert lines[2] == (
        "  Fatigue was rated 4 or more of 5 on 3 of the last 4 check-ins to 2026-10-25 "
        "(2026-10-22 3, 2026-10-23 4, 2026-10-24 4, 2026-10-25 5)."
    )
    skipped = next(line for line in lines if line.startswith("not evaluated:"))
    assert "PATTERN.ADHERENCE (NEEDS_PLANS)" in skipped
    assert "PATTERN.PLATEAU (NEEDS_GOAL_METRICS)" in skipped
    assert "PATTERN.MONOTONY (NO_CONSENT)" in skipped  # no activities consent
    assert "PATTERN.SLEEP_PERFORMANCE (NO_CONSENT)" in skipped
    assert lines[-1] == "interpretation and recommendations arrive with the AI coach"
    again = cli.run("insights", "detect")[1]
    assert again.startswith("insights to 2026-10-25: 1 found (0 new)")  # not stored twice
    code, listing = cli.run("insights", "list")
    assert code == 0
    assert listing.startswith("PATTERN.ACCUMULATED_FATIGUE  2026-10-19 to 2026-10-25")


@pytest.mark.req("FR-INS-002")
def test_with_the_activities_consent_and_no_data_nothing_is_found(cli: Cli) -> None:
    assert cli.run("consent", "grant", "activities")[0] == 0
    code, output = cli.run("insights", "detect")
    assert code == 0, output
    assert output.startswith("insights to 2026-10-25: 0 found (0 new)")
    assert "PATTERN.MONOTONY" not in output
    assert cli.run("insights", "list") == (0, "no insights in these days\n")
