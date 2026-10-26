"""`coach garmin status|sync` end to end: SQLite, sandboxed FIT parsing, scripted HTTP (P7).

The Garmin API is never contacted: a scripted transport answers like the documented
endpoints. The OS secret store is never used.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from cli_harness import NOW, Cli
from coach.integrations.garmin.transport import TransportFailure
from file_samples import fit_file
from garmin_world import FakeTime, ScriptedTransport, response

DETAILS = "/activity-service/activity/{}"


@pytest.fixture
def cli(tmp_path: Path) -> Cli:
    command = Cli(tmp_path / "coach-data")
    command.transport = ScriptedTransport(FakeTime(NOW))
    assert command.run("init")[0] == 0
    return command


def transport(cli: Cli) -> ScriptedTransport:
    assert isinstance(cli.transport, ScriptedTransport)
    return cli.transport


def ready(cli: Cli, *, expires: date | None = None) -> None:
    assert cli.run("consent", "grant", "activities")[0] == 0
    assert cli.run("profile", "timezone", "America/Argentina/Buenos_Aires")[0] == 0
    expiry = expires or NOW.date() + timedelta(days=180)
    assert cli.run("secret", "set", "garmin-api-key", "--expires-on", str(expiry))[0] == 0


@pytest.mark.req("INT-GAR-006", "INT-GAR-008")
def test_status_before_anything_is_configured(cli: Cli) -> None:
    code, output = cli.run("garmin", "status")
    assert code == 0
    assert "key: not stored" in output
    assert "sync: NEVER_SYNCED" in output
    assert transport(cli).sent == []


@pytest.mark.req("INT-GAR-006", "FR-ACT-004")
def test_sync_needs_timezone_and_key_before_any_request(cli: Cli) -> None:
    code, output = cli.run("garmin", "sync")
    assert code == 2
    assert "coach profile timezone" in output
    cli.run("profile", "timezone", "America/Argentina/Buenos_Aires")
    code, output = cli.run("garmin", "sync")
    assert code == 2
    assert "coach secret set garmin-api-key" in output
    assert transport(cli).sent == []


@pytest.mark.req("INT-GAR-005", "INT-GAR-007", "INT-GAR-009")
def test_a_sync_imports_new_activities_and_reports_freshness(cli: Cli) -> None:
    ready(cli)
    transport(cli).activities["2026-10-20"] = [21004587311]
    code, output = cli.run("garmin", "sync")
    assert code == 0, output
    assert "sync: SYNCED" in output
    assert "imported=2" in output
    listing = cli.run("activity", "list")[1]
    assert "RUNNING" in listing
    assert "sources=2" in listing
    code, status = cli.run("garmin", "status")
    assert "sync: SYNCED" in status
    assert "last successful sync: 2026-10-25T10:00:00Z" in status
    assert "fitDownload      SUPPORTED    OBSERVED" in status
    assert "trainingPublish  UNSUPPORTED  CONFIGURED" in status
    again = cli.run("garmin", "sync")[1]
    assert "known=1" in again
    assert "imported=0" in again


@pytest.mark.req("INT-GAR-008", "NFR-REL-002")
@pytest.mark.acceptance("AT-DEG-001")
def test_an_unavailable_provider_degrades_without_touching_local_data(
    cli: Cli, tmp_path: Path
) -> None:
    ready(cli)
    run = tmp_path / "run.fit"
    run.write_bytes(fit_file())
    assert cli.run("import", str(run))[0] == 0
    transport(cli).script(
        "/activitylist-service/activities/search/activities",
        *(response(503, retry_after="2") for _ in range(5)),
    )
    code, output = cli.run("garmin", "sync")
    assert code == 1
    assert "sync: PENDING (PROVIDER_UNAVAILABLE HTTP_5XX)" in output
    assert "last successful sync: never" in output
    assert "local history and file import keep working" in output
    # Exponential backoff with jitter (0.5 to 1 times 1, 2, 4, 8 s), never under Retry-After.
    assert len(cli.sleeps) == 4
    assert cli.sleeps[:2] == [2.0, 2.0]
    assert 2.0 <= cli.sleeps[2] <= 4.0
    assert 4.0 <= cli.sleeps[3] <= 8.0
    status = cli.run("garmin", "status")[1]
    assert "condition: INTEGRATION_ERROR" in status
    assert len(cli.run("activity", "list")[1].splitlines()) == 1
    other = tmp_path / "ride.fit"
    other.write_bytes(fit_file(file_type="activity", utc_offset_hours=-3, manufacturer="garmin"))
    assert cli.run("import", str(other))[0] == 0  # file ingestion keeps working
    code, output = cli.run("garmin", "sync")
    assert code == 0
    assert "condition: INTEGRATION_ERROR" not in cli.run("garmin", "status")[1]


@pytest.mark.req("INT-GAR-010")
def test_a_rate_limit_postpones_the_next_attempt(cli: Cli) -> None:
    ready(cli)
    transport(cli).script(
        "/activitylist-service/activities/search/activities", response(429, retry_after="900")
    )
    code, output = cli.run("garmin", "sync")
    assert code == 1
    assert "PROVIDER_RATE_LIMITED HTTP_429" in output
    assert "next attempt not before: 2026-10-25T10:15:00Z" in output
    sent = len(transport(cli).sent)
    assert cli.run("garmin", "sync")[0] == 1
    assert len(transport(cli).sent) == sent


@pytest.mark.req("INT-GAR-006", "SEC-SEC-004")
def test_a_rejected_key_and_an_expiring_key_are_reported(cli: Cli) -> None:
    ready(cli, expires=NOW.date() + timedelta(days=3))
    transport(cli).script(
        "/personalapitoken-service/personal-api/personal-api-key/token", response(401)
    )
    code, output = cli.run("garmin", "sync")
    assert code == 1
    assert "PROVIDER_AUTH_FAILED KEY_REJECTED" in output
    assert "warning: the Garmin key expires on" in output
    assert "warning: the Garmin key expires on" in cli.run("garmin", "status")[1]


@pytest.mark.req("INT-GAR-008")
def test_a_network_failure_is_typed(cli: Cli) -> None:
    ready(cli)
    transport(cli).script(
        "/personalapitoken-service/personal-api/personal-api-key/token",
        *([TransportFailure.NETWORK] * 5),
    )
    code, output = cli.run("garmin", "sync")
    assert code == 1
    assert "PROVIDER_UNAVAILABLE NETWORK" in output
