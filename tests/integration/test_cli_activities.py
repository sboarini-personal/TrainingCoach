"""`coach` commands for consent, timezone, file import, activities and issues (P6).

Runs the real sandboxed parsers against synthetic files; the OS secret store is never used.
"""

import csv
import io
from pathlib import Path
from uuid import UUID

import pytest

from cli_harness import Cli
from file_samples import csv_file, fit_file, gpx_file


@pytest.fixture
def cli(tmp_path: Path) -> Cli:
    command = Cli(tmp_path / "coach-data")
    assert command.run("init")[0] == 0
    return command


def _ready(cli: Cli) -> None:
    assert cli.run("consent", "grant", "activities")[0] == 0
    assert cli.run("profile", "timezone", "America/Argentina/Buenos_Aires")[0] == 0


@pytest.mark.req("PRIV-CON-001", "PRIV-CON-002")
def test_consents_can_be_listed_granted_and_revoked(cli: Cli) -> None:
    code, output = cli.run("consent", "list")
    assert code == 0
    assert "activities" in output
    assert "NOT_GRANTED" in output
    assert cli.run("consent", "grant", "activities") == (0, "activities: granted\n")
    assert "GRANTED" in cli.run("consent", "list")[1].splitlines()[0]
    assert cli.run("consent", "revoke", "activities") == (0, "activities: revoked\n")
    assert "REVOKED" in cli.run("consent", "list")[1].splitlines()[0]
    code, output = cli.run("consent", "revoke", "activities")
    assert code == 2
    assert output.startswith("error:")


@pytest.mark.req("FR-ACT-004")
def test_timezone_is_validated_and_shown(cli: Cli) -> None:
    assert cli.run("profile", "timezone") == (0, "not set\n")
    code, output = cli.run("profile", "timezone", "Mars/Olympus")
    assert (code, output.startswith("error:")) == (2, True)
    assert cli.run("profile", "timezone", "Europe/Madrid")[0] == 0
    assert cli.run("profile", "timezone", "America/Argentina/Buenos_Aires")[0] == 0
    assert cli.run("profile", "timezone") == (0, "America/Argentina/Buenos_Aires\n")


@pytest.mark.req("INT-FIL-001", "PRIV-CON-002", "FR-ACT-004")
def test_import_needs_consent_and_timezone(cli: Cli, tmp_path: Path) -> None:
    path = tmp_path / "run.fit"
    path.write_bytes(fit_file())
    code, output = cli.run("import", str(path))
    assert code == 2
    assert "coach profile timezone" in output
    cli.run("profile", "timezone", "America/Argentina/Buenos_Aires")
    code, output = cli.run("import", str(path))
    assert code == 2
    assert "coach consent grant activities" in output


@pytest.mark.req("INT-FIL-001", "INT-FIL-004", "FR-ACT-007", "FR-ACT-009")
def test_import_list_and_issue_flow(cli: Cli, tmp_path: Path) -> None:
    _ready(cli)
    fit = tmp_path / "run.fit"
    fit.write_bytes(fit_file())
    wrong = tmp_path / "wrong.fit"
    wrong.write_bytes(gpx_file())
    tennis = tmp_path / "tennis.csv"
    tennis.write_bytes(csv_file())
    code, output = cli.run("import", str(fit), str(wrong), str(tennis))
    assert code == 1
    lines = output.splitlines()
    assert lines[0] == "run.fit: IMPORTED: 1 activities (CREATED)"
    assert lines[1] == "wrong.fit: REJECTED: CONTENT_MISMATCH FIT_HEADER"
    assert lines[2] == "tennis.csv: IMPORTED: 1 activities (CREATED)"
    assert cli.run("import", str(fit)) == (0, "run.fit: DUPLICATE: 1 activities (DUPLICATE)\n")
    code, listing = cli.run("activity", "list")
    assert code == 0
    first, second = listing.splitlines()
    assert first.startswith("2026-10-21  TENNIS   MATCH")
    assert "1:30:00" in first
    assert second.startswith("2026-10-20  RUNNING  ROAD")
    assert "1.80 km" in second
    code, issues = cli.run("issue", "list")
    (issue,) = issues.splitlines()
    assert "IMPORT_FAILED" in issue
    assert "CONTENT_MISMATCH" in issue
    issue_id = issue.split()[-1]
    assert cli.run("issue", "ack", issue_id) == (0, "issue acknowledged\n")
    assert cli.run("issue", "list") == (0, "no open issues\n")
    code, output = cli.run("issue", "ack", str(UUID(int=7)))
    assert (code, output) == (2, "error: issue not found\n")


@pytest.mark.req("SEC-INJ-004")
@pytest.mark.acceptance("AT-CSV-001")
def test_export_writes_the_activity_template(cli: Cli, tmp_path: Path) -> None:
    _ready(cli)
    source = tmp_path / "tennis.csv"
    source.write_bytes(csv_file())
    cli.run("import", str(source))
    target = tmp_path / "export.csv"
    assert cli.run("activity", "export", str(target)) == (0, "1 activities exported\n")
    (row,) = list(csv.DictReader(io.StringIO(target.read_text(encoding="utf-8"))))
    assert (row["sport"], row["subtype"], row["rpe"], row["utc_offset"]) == (
        "TENNIS",
        "MATCH",
        "7.0",
        "'-03:00",
    )


@pytest.mark.req("INT-FIL-002")
def test_unreadable_paths_are_reported(cli: Cli, tmp_path: Path) -> None:
    _ready(cli)
    code, output = cli.run("import", str(tmp_path / "missing.fit"))
    assert (code, output) == (1, "missing.fit: cannot be read\n")


@pytest.mark.req("FR-ACT-002")
def test_reprocess_reports_what_it_did(cli: Cli, tmp_path: Path) -> None:
    _ready(cli)
    path = tmp_path / "run.fit"
    path.write_bytes(fit_file())
    assert cli.run("import", str(path))[0] == 0
    assert cli.run("activity", "reprocess") == (
        0,
        "reprocessed 0 sources (0 activities rebuilt), 1 already up to date\n",
    )
    cli.run("consent", "revoke", "activities")
    code, output = cli.run("activity", "reprocess")
    assert (code, "coach consent grant activities" in output) == (2, True)
