"""`coach insights` and `coach report` (P11; insights and reporting specs), end to end with
SQLite."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli_harness import Cli
from schemas import validation_errors


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
    assert "PATTERN.ADHERENCE (NO_CONSENT)" in skipped  # executions need the activities
    assert "PATTERN.PLATEAU (NO_CONSENT)" in skipped
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


@pytest.mark.req("FR-RPT-002", "FR-RPT-003", "FR-AGT-004")
def test_reports_print_their_data_part_and_their_document(cli: Cli) -> None:
    assert cli.run("checkin", "add", "--fatigue", "4", "--sleep", "2", "--soreness", "4")[0] == 0
    assert cli.run("recovery", "status")[0] == 0  # REST: a recovery block today
    code, output = cli.run("report", "week")
    assert code == 0, output
    lines = output.splitlines()
    assert lines[0] == "weekly report 2026-W43 (2026-10-19 to 2026-10-25)"
    assert "Adherence: not available (NO_CONSENT)" in lines  # executions read activities
    assert "Volume by sport: not available (NO_CONSENT)" in lines
    assert "  recovery 2026-10-25: REST (THREE_GROUPS)" in lines
    assert "  condition: RECOVERY_BLOCK" in lines
    assert lines[-2].startswith("Confidence: LOW")
    assert lines[-1].startswith(
        "Missing data: Adherence (NO_CONSENT), Volume by sport (NO_CONSENT)"
    )
    code, output = cli.run("report", "month", "--json")
    assert code == 0, output
    document = json.loads(output)
    assert validation_errors("report-data", document) == []
    assert (document["kind"], document["subject"]) == ("MONTHLY", "2026-10")
    assert cli.run("report", "month", "--month", "2026-13")[0] == 2
    code, output = cli.run("report", "activity", "0193b2a0-0000-7000-8000-000000000999")
    assert (code, "coach consent grant activities" in output) == (2, True)
