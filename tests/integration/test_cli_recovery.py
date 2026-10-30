"""`coach checkin` and `coach safety` (P11; recovery spec 1, safety spec 2 to 4)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from cli_harness import NOW, Cli
from coach.integrations.garmin.token import TOKEN_PATH
from garmin_samples import hrv_payload
from garmin_world import FakeTime, ScriptedTransport, response
from samples import BUENOS_AIRES


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


@pytest.mark.req("SAFE-MED-002", "SAFE-MED-003")
def test_a_red_flag_is_kept_when_the_other_answers_are_invalid(cli: Cli) -> None:
    assert cli.run("profile", "timezone", "America/Argentina/Buenos_Aires")[0] == 0
    code, output = cli.run(
        "checkin", "add", "--red-flag", "chest_pain", "--red-flag", "chest_pain", "--sleep", "6"
    )
    assert code == 1
    assert output.startswith("warning signs recorded for 2026-10-25; the other answers")
    assert "(dolor o presión en el pecho)" in output
    assert cli.run("safety", "status") == (0, "safety hold: active\n")
    assert "RED FLAGS RF_CHEST_PAIN" in cli.run("checkin", "list")[1]


@pytest.mark.req("FR-REC-005", "INT-GAR-007")
@pytest.mark.acceptance("AT-CON-001")
def test_garmin_sync_brings_consented_recovery_metrics(tmp_path: Path) -> None:
    command = Cli(tmp_path / "data")
    scripted = ScriptedTransport(FakeTime(NOW))
    command.transport = scripted
    assert command.run("init")[0] == 0
    assert command.run("consent", "grant", "activities")[0] == 0
    assert command.run("consent", "grant", "hrv")[0] == 0
    assert command.run("profile", "timezone", "America/Argentina/Buenos_Aires")[0] == 0
    expiry = str(NOW.date() + timedelta(days=180))
    assert command.run("secret", "set", "garmin-api-key", "--expires-on", expiry)[0] == 0
    today = NOW.astimezone(BUENOS_AIRES).date()
    nights = {today - timedelta(days=n): 50 + n for n in range(3)}
    scripted.script(
        f"/hrv-service/hrv/daily/{today - timedelta(days=27)}/{today}",
        response(200, hrv_payload(nights)),
    )
    code, output = command.run("garmin", "sync")
    assert code == 0, output
    line = next(item for item in output.splitlines() if item.startswith("recovery:"))
    assert "HRV_NIGHTLY 3 new" in line
    assert "RESTING_HR needs consent (recovery)" in line
    assert not [item for item in scripted.sent if "dailyHeartRate" in item.path]  # no consent
    assert len([item for item in scripted.sent if item.path == TOKEN_PATH]) == 1  # one per run
    code, output = command.run("recovery", "data", "--days", "3")
    rows = output.splitlines()[1:]
    assert [row.split()[2] for row in rows] == ["52", "51", "50"]
    assert command.run("consent", "revoke", "hrv")[0] == 0
    assert "no recovery data" in command.run("recovery", "data")[1]  # hidden at once


@pytest.mark.req("FR-REC-005", "PRIV-CON-002")
def test_recovery_metrics_sync_without_the_activities_consent(tmp_path: Path) -> None:
    command = Cli(tmp_path / "data")
    scripted = ScriptedTransport(FakeTime(NOW))
    command.transport = scripted
    assert command.run("init")[0] == 0
    assert command.run("profile", "timezone", "America/Argentina/Buenos_Aires")[0] == 0
    expiry = str(NOW.date() + timedelta(days=180))
    assert command.run("secret", "set", "garmin-api-key", "--expires-on", expiry)[0] == 0
    code, output = command.run("garmin", "sync")
    assert (code, "coach consent grant" in output) == (2, True)  # nothing is consented
    assert command.run("consent", "grant", "hrv")[0] == 0
    today = NOW.astimezone(BUENOS_AIRES).date()
    scripted.script(
        f"/hrv-service/hrv/daily/{today - timedelta(days=27)}/{today}",
        response(200, hrv_payload({today: 50})),
    )
    code, output = command.run("garmin", "sync")
    assert code == 0, output
    assert output.startswith("activities: needs consent (activities)\n")
    assert "HRV_NIGHTLY 1 new" in output
    assert not [item for item in scripted.sent if "activitylist" in item.path]


@pytest.mark.req("FR-REC-003", "FR-REC-004", "FR-REC-005")
def test_recovery_status_assesses_the_day_and_blocks_intensity(cli: Cli) -> None:
    assert cli.run("profile", "timezone", "America/Argentina/Buenos_Aires")[0] == 0
    code, output = cli.run("recovery", "status")
    assert code == 0, output
    assert output.startswith("recovery 2026-10-25: INSUFFICIENT_DATA (NO_SIGNAL), confidence LOW")
    assert "RESTING_HR            NO_CONSENT  needs consent: coach consent grant recovery" in output
    command = ("checkin", "add", "--fatigue", "4", "--sleep", "2", "--soreness", "4")
    assert cli.run(*command)[0] == 0
    code, output = cli.run("recovery", "status")
    assert code == 0, output
    lines = output.splitlines()
    assert lines[0] == "recovery 2026-10-25: REST (THREE_GROUPS), confidence MEDIUM"
    assert "  fatigue               DEVIATING   4" in lines
    assert "uncertainty: MISSING_DATA" in lines
    assert (
        "prescription: REST through 2026-10-25 (THREE_GROUPS), re-evaluated on 2026-10-26" in lines
    )
    assert "recovery block: active" in lines
    assert lines[-1] == "training-load guidance, not a medical assessment"


@pytest.mark.req("SAFE-MED-002", "SAFE-MED-003")
def test_a_red_flag_activates_the_hold_without_a_timezone_or_with_a_bad_day(cli: Cli) -> None:
    code, output = cli.run("checkin", "add", "--red-flag", "chest_pain")
    assert code == 2
    assert "SAFETY_HOLD" in output
    assert "coach profile timezone" in output
    assert cli.run("safety", "status") == (0, "safety hold: active\n")
    other = cli.run("safety", "release", "--cleared-by-professional")
    assert other[0] == 0
    code, output = cli.run("checkin", "add", "--day", "2026-13-40", "--notes", "me duele el pecho")
    assert (code, "SAFETY_HOLD" in output) == (2, True)
    assert cli.run("safety", "status") == (0, "safety hold: active\n")
