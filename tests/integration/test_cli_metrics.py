"""`coach load`, `coach metrics`, `coach activity show` and `coach profile hr-zones` (P8).

End to end: SQLite, the real parser sandbox and synthetic FIT files (a 10-minute road run at
3 m/s with heart rate 150 to 154 bpm, recorded every second).
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest

from cli_harness import Cli
from file_samples import fit_device, fit_file, fit_length, fit_session

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


PERCENTAGES = ("51", "59", "71", "81", "90", "100")


@pytest.mark.req("FR-PRF-001", "FR-PRF-002", "FR-MET-004")
@pytest.mark.acceptance("AT-MET-003")
def test_age_predicted_zones_follow_the_birth_date(cli: Cli, tmp_path: Path) -> None:
    activity_id = imported(cli, tmp_path, fit_file())  # 150 to 154 bpm for 10 minutes
    zones = ("profile", "hr-zones", *PERCENTAGES, "--age-predicted", "--valid-from", "2000-01-01")
    code, output = cli.run(*zones)
    assert (code, "coach profile birth-date" in output) == (2, True)
    assert cli.run("profile", "birth-date") == (0, "not set\n")
    assert cli.run("profile", "birth-date", "1971-03-10") == (0, "birth date set to 1971-03-10\n")
    code, output = cli.run(*zones)
    assert code == 0, output
    assert output.splitlines() == [
        "zone model added: 5 zones from 2000-01-01, an estimate (% of 220 - age)",
        "today: 220 - age 55 = 165 bpm, boundaries 85 98 118 134 149 165",
        "metrics recomputed for 1 activities",
    ]
    assert cli.run("profile", "hr-zones")[1] == (
        "from 2000-01-01  PERCENT_MAX_HR       51 59 71 81 90 100 % of 220 - age (estimate)\n"
    )
    shown = cli.run("activity", "show", str(activity_id))[1]
    zone_five = next(line for line in shown.splitlines() if "any.hr_zone_time Z5" in line)
    assert zone_five.endswith("0:10:00 (estimated)")  # Z5 starts at 149 bpm at 55
    code, output = cli.run("profile", "birth-date", "1986-03-10")  # 40: 180 bpm
    assert output.splitlines() == [
        "birth date set to 1986-03-10",
        "metrics recomputed for 1 activities",
    ]
    shown = cli.run("activity", "show", str(activity_id))[1]
    zone_four = next(line for line in shown.splitlines() if "any.hr_zone_time Z4" in line)
    assert zone_four.endswith("0:10:00 (estimated)")  # Z4 is 146 to 161 bpm at 40


@pytest.mark.req("FR-PRF-001")
def test_bad_birth_dates_and_methods_are_refused(cli: Cli) -> None:
    for text in ("2026-10-26", "2010-01-01", "1920-01-01"):  # future, under 18, over 100
        code, output = cli.run("profile", "birth-date", text)
        assert (code, output.startswith("error:")) == (2, True), text
    assert cli.run("profile", "birth-date", "1971-03-10")[0] == 0
    code, output = cli.run(
        "profile", "hr-zones", *PERCENTAGES, "--age-predicted", "--method", "heart-rate-reserve"
    )
    assert (code, "percent-max-hr" in output) == (2, True)
    code, output = cli.run("profile", "hr-zones", "51", "120", "--age-predicted")
    assert (code, output.startswith("error:")) == (2, True)  # percentages above 100


@pytest.mark.req("FR-RUN-002", "FR-RUN-004")
def test_running_metrics_of_an_imported_steady_run(cli: Cli, tmp_path: Path) -> None:
    records, summary = fit_session(seconds=2400)  # 40 min at 3 m/s
    for record in records:  # heart rate 140 until the middle of the window, then 150
        elapsed = (record["timestamp"] - summary["start_time"]).total_seconds()
        record["heart_rate"] = 140 if elapsed < 1500 else 150
    activity_id = imported(cli, tmp_path, fit_file([(records, summary)]))
    shown = cli.run("activity", "show", str(activity_id))[1]
    drift = next(line for line in shown.splitlines() if "run.hr_drift" in line)
    assert drift.endswith("6.7 %")
    assert any(line.strip().startswith("run.pace_variability") for line in shown.splitlines())
    assert any(line.strip().startswith("run.efficiency_hr") for line in shown.splitlines())


@pytest.mark.req("FR-CYC-002", "FR-CYC-003", "FR-CYC-004", "FR-PRF-001")
@pytest.mark.acceptance("AT-CYC-001")
def test_power_metrics_need_a_declared_reliable_meter(cli: Cli, tmp_path: Path) -> None:
    records, summary = fit_session(
        seconds=2400, sport="cycling", distance_per_second=8.0, extra={"avg_power": 225}
    )
    for record in records:  # 250 W for 20 minutes, then 200 W
        elapsed = (record["timestamp"] - summary["start_time"]).total_seconds()
        record["power"] = 250 if elapsed < 1200 else 200
    activity_id = imported(cli, tmp_path, fit_file([([*records, fit_device(11)], summary)]))
    shown = cli.run("activity", "show", str(activity_id))[1]
    assert "cyc.work_kj" not in shown  # no meter declared yet: power is not MEASURED
    assert cli.run("profile", "sensor") == (0, "no sensors declared\n")
    code, output = cli.run("profile", "sensor", "CYCLING_POWER_METER", "--reliable")
    assert output.splitlines() == [
        "sensor CYCLING_POWER_METER reliable",
        "metrics recomputed for 1 activities",
    ]
    assert cli.run("profile", "ftp") == (0, "not set\n")
    code, output = cli.run("profile", "ftp", "250", "--on", "2026-10-01", "--measured")
    assert (code, output.splitlines()[0]) == (0, "FTP set to 250 W on 2026-10-01")
    shown = cli.run("activity", "show", str(activity_id))[1]
    work = next(line for line in shown.splitlines() if "cyc.work_kj" in line)
    assert work.endswith("540 kJ")
    fade = next(line for line in shown.splitlines() if "cyc.power_fade" in line)
    assert fade.endswith("20.0 %")
    zone_four = next(line for line in shown.splitlines() if "cyc.power_zone_time Z4" in line)
    assert zone_four.endswith("0:20:00")  # 250 W is 1.0 x FTP
    week = cli.run("load", "week", "--week", WEEK)[1]
    cycling = next(line for line in week.splitlines() if line.strip().startswith("CYCLING"))
    assert "work 540 kJ" in cycling
    assert cli.run("profile", "sensor", "CYCLING_POWER_METER", "--remove")[0] == 0
    assert "cyc.work_kj" not in cli.run("activity", "show", str(activity_id))[1]


@pytest.mark.req("FR-SWM-002", "FR-SWM-003")
def test_pool_swim_metrics_of_an_imported_file(cli: Cli, tmp_path: Path) -> None:
    records, summary = fit_session(
        sport="swimming",
        sub_sport="lap_swimming",
        seconds=150,
        distance_per_second=1.0,
        extra={"pool_length": 25.0, "num_active_lengths": 6},
    )
    start = summary["start_time"]
    lengths = [
        fit_length(start + timedelta(seconds=25 * index), 25, 16 + index // 3) for index in range(6)
    ]
    activity_id = imported(cli, tmp_path, fit_file([([*records, *lengths], summary)]), "swim.fit")
    shown = cli.run("activity", "show", str(activity_id))[1]
    swolf = next(line for line in shown.splitlines() if "swim.swolf_avg FREESTYLE" in line)
    assert swolf.endswith("41.5")  # 25 s + 16.5 strokes per length
    per_stroke = next(line for line in shown.splitlines() if "swim.distance_per_stroke" in line)
    assert per_stroke.endswith(f"{150 / 99:.2f} m/stroke")


@pytest.mark.req("FR-ACT-006", "FR-TEN-002", "FR-TEN-003")
@pytest.mark.acceptance("AT-TEN-001")
def test_rpe_of_an_imported_tennis_session_is_recorded_with_a_correction(
    cli: Cli, tmp_path: Path
) -> None:
    records, summary = fit_session(seconds=7200, sport="tennis", sub_sport="match")
    activity_id = imported(cli, tmp_path, fit_file([(records, summary)]), "tennis.fit")
    shown = cli.run("activity", "show", str(activity_id))[1]
    load = next(line for line in shown.splitlines() if "ten.neuromuscular_load" in line)
    assert "unknown (missing rpe)" in load
    code, output = cli.run(
        "activity", "correct", str(activity_id), "subjective.rpe", "8", "--reason", "Long match"
    )
    assert (code, output) == (0, "subjective.rpe corrected\n")
    shown = cli.run("activity", "show", str(activity_id))[1]
    assert next(line for line in shown.splitlines() if "ten.neuromuscular_load" in line).endswith(
        "HIGH"
    )
    assert next(line for line in shown.splitlines() if "any.srpe_load" in line).endswith("960 AU")
    week = cli.run("load", "week", "--week", WEEK)[1]
    assert "sRPE 960 AU" in next(line for line in week.splitlines() if "TENNIS" in line)


@pytest.mark.req("FR-ACT-006")
def test_corrections_are_validated(cli: Cli, tmp_path: Path) -> None:
    activity_id = imported(cli, tmp_path, fit_file())
    for field, value, expected in (
        ("timing.startTimeUtc", "2026-10-20", "cannot be corrected"),
        ("subjective.rpe", "hard", "needs a number value"),
        ("subjective.rpe", "11", "error:"),
    ):
        code, output = cli.run(
            "activity", "correct", str(activity_id), field, value, "--reason", "Test"
        )
        assert (code, expected in output) == (2, True), output
    code, output = cli.run(
        "activity",
        "correct",
        "0193b2a0-0000-7000-8000-000000000999",
        "subjective.rpe",
        "5",
        "--reason",
        "Test",
    )
    assert output == "error: activity not found\n"
    code, _ = cli.run(
        "activity", "correct", str(activity_id), "environment.surface", "unknown", "--reason", "x"
    )
    assert code == 0  # unknown marks a field UNKNOWN


@pytest.mark.req("FR-RUN-004", "FR-SWM-004")
def test_the_trend_command_lists_comparable_groups(cli: Cli, tmp_path: Path) -> None:
    imported(cli, tmp_path, fit_file())
    code, output = cli.run("analysis", "trend", "run.efficiency_hr", "--weeks", "4")
    assert code == 0, output
    lines = output.splitlines()
    assert (
        lines[0]
        == "run.efficiency_hr from 2026-09-28 to 2026-10-25: 1 sessions in 1 comparable groups"
    )
    assert lines[1].startswith("  RUNNING ROAD ROLLING: 1 sessions, mean ")  # 40 m over 1.8 km
    assert lines[1].endswith("too few for a trend")
    code, output = cli.run("analysis", "trend", "run.nothing")
    assert (code, output) == (2, "error: unknown metric: run.nothing\n")
