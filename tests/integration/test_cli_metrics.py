"""`coach load`, `coach metrics`, `coach activity show` and `coach profile hr-zones` (P8).

End to end: SQLite, the real parser sandbox and synthetic FIT files (a 10-minute road run at
3 m/s with heart rate 150 to 154 bpm, recorded every second).
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from cli_harness import Cli
from file_samples import fit_file, fit_session

WEEK = "2026-W43"  # the synthetic run starts on 2026-10-20 (training day in Buenos Aires)


@pytest.fixture
def cli(tmp_path: Path) -> Cli:
    command = Cli(tmp_path / "coach-data")
    assert command.run("init")[0] == 0
    assert command.run("consent", "grant", "activities")[0] == 0
    assert command.run("profile", "timezone", "America/Argentina/Buenos_Aires")[0] == 0
    return command


def imported(cli: Cli, tmp_path: Path, data: bytes, name: str = "run.fit") -> UUID:
    path = tmp_path / name
    path.write_bytes(data)
    code, output = cli.run("import", str(path))
    assert code == 0, output
    listing = cli.run("activity", "list")[1].splitlines()
    return UUID(listing[0].split()[-1])


@pytest.mark.req("FR-MET-001", "FR-MET-005", "FR-MET-006")
def test_the_week_report_of_an_imported_run(cli: Cli, tmp_path: Path) -> None:
    imported(cli, tmp_path, fit_file())
    ride = fit_file(
        [fit_session(sport="cycling", sub_sport="road", distance_per_second=8.0)],
    )
    imported(cli, tmp_path, ride, "ride.fit")
    code, output = cli.run("load", "week", "--week", WEEK)
    assert code == 0, output
    lines = output.splitlines()
    assert lines[0] == f"week {WEEK} (2026-10-19 to 2026-10-25): 2 activities"
    running = next(line for line in lines if line.strip().startswith("RUNNING"))
    assert "elapsed 0:10:00" in running
    assert "distance 1.80 km" in running
    cycling = next(line for line in lines if line.strip().startswith("CYCLING"))
    assert "distance 4.80 km" in cycling
    everything = next(line for line in lines if line.strip().startswith("all"))
    assert "elapsed 0:20:00" in everything
    assert "distance" not in everything  # never summed across sports
    assert "sRPE unknown" in everything  # no RPE in the FIT files


@pytest.mark.req("FR-MET-001", "FR-MET-004", "FR-PRF-001")
def test_zones_speed_and_pace_of_an_activity(cli: Cli, tmp_path: Path) -> None:
    activity_id = imported(cli, tmp_path, fit_file())
    code, output = cli.run("activity", "show", str(activity_id))
    assert code == 0, output
    assert "any.speed_avg" in output
    assert "3.00 m/s (10.8 km/h)" in output
    assert "5:33 /km" in output  # 600 s over 1.8 km
    assert "any.hr_zone_time" not in output  # no zone model yet
    assert cli.run("profile", "hr-zones") == (0, "no heart-rate zone models\n")
    code, output = cli.run("profile", "hr-zones", "100", "140", "160", "--valid-from", "2026-01-01")
    assert code == 0, output
    assert output.splitlines() == [
        "zone model added: 2 zones from 2026-01-01",
        "metrics recomputed for 1 activities",
    ]
    shown = cli.run("activity", "show", str(activity_id))[1]
    assert "any.hr_zone_time Z1" in shown
    zone_two = next(line for line in shown.splitlines() if "any.hr_zone_time Z2" in line)
    assert zone_two.endswith("0:10:00")  # every second at 150-154 bpm
    listing = cli.run("profile", "hr-zones")[1]
    assert listing.startswith("from 2026-01-01  CUSTOM")
    code, output = cli.run("profile", "hr-zones", "100", "130", "150", "--valid-from", "2026-01-01")
    assert output.startswith("zone model replaced: 2 zones")
    assert len(cli.run("profile", "hr-zones")[1].splitlines()) == 1


@pytest.mark.req("FR-MET-004")
def test_recompute_and_the_overview_of_recent_weeks(cli: Cli, tmp_path: Path) -> None:
    imported(cli, tmp_path, fit_file())
    assert cli.run("metrics", "recompute") == (0, "metrics recomputed for 1 activities\n")
    code, output = cli.run("load", "weeks", "--count", "2")
    assert code == 0, output
    lines = output.splitlines()
    assert len(lines) == 2
    assert lines[1].startswith(f"{WEEK}    1 activities")  # the clock is in 2026-W43
    assert "RUNNING 1.80 km" in lines[1]
    assert lines[0].startswith("2026-W42    0 activities")


@pytest.mark.req("PRIV-CON-002")
def test_bad_input_and_missing_consent_are_reported(cli: Cli, tmp_path: Path) -> None:
    code, output = cli.run("load", "week", "--week", "2026-44")
    assert (code, output.startswith("error: invalid ISO week")) == (2, True)
    code, output = cli.run("profile", "hr-zones", "150", "140")
    assert (code, output.startswith("error:")) == (2, True)
    code, output = cli.run("load", "weeks", "--count", "0")
    assert code == 2
    code, output = cli.run("activity", "show", "0193b2a0-0000-7000-8000-000000000999")
    assert output == "error: activity not found\n"
    activity_id = imported(cli, tmp_path, fit_file())
    cli.run("consent", "revoke", "activities")
    code, output = cli.run("load", "week")
    assert code == 2
    assert "coach consent grant activities" in output
    code, output = cli.run("activity", "show", str(activity_id))
    assert (code, "coach consent grant activities" in output) == (2, True)
