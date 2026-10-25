"""The `coach` command line (SEC-SEC-001, SEC-SEC-004, SEC-AUTH-001, SEC-AUD-001).

The real OS secret store is never touched: an in-memory provider is injected.
"""

from pathlib import Path
from uuid import UUID

import pytest

from cli_harness import GARMIN_KEY, Cli
from coach.application.credentials import CredentialKind, credential_handle
from coach.infrastructure.sqlite.database import SqliteDatabase
from coach.infrastructure.sqlite.identities import SqliteIdentityDirectory


@pytest.fixture
def cli(tmp_path: Path) -> Cli:
    return Cli(tmp_path / "coach-data")


@pytest.mark.req("SEC-AUTH-001", "PRIV-STO-001")
def test_init_creates_the_installation_once(cli: Cli) -> None:
    code, output = cli.run("init")
    assert code == 0
    assert "initialised" in output
    token = cli.local_token()
    assert token is not None
    assert token.reveal() not in output
    assert (cli.data_dir / "coach.sqlite3").is_file()
    again, output = cli.run("init")
    assert again == 0
    assert "already initialised" in output
    assert cli.local_token() == token


@pytest.mark.req("PRIV-STO-001")
def test_init_refuses_a_synced_data_directory(cli: Cli, tmp_path: Path) -> None:
    cli.env["COACH_DATA_DIR"] = str(tmp_path / "OneDrive - Example" / "coach")
    code, output = cli.run("init")
    assert code == 2
    assert "synchronised" in output


@pytest.mark.req("SEC-SEC-001", "SEC-SEC-004")
def test_secret_set_stores_hidden_input_and_lists_expiry(cli: Cli) -> None:
    cli.run("init")
    code, output = cli.run("secret", "set", "garmin-api-key", "--expires-on", "2026-10-30")
    assert code == 0
    assert GARMIN_KEY not in output
    code, listing = cli.run("secret", "list")
    assert code == 0
    assert "GARMIN_PERSONAL_API_KEY" in listing
    assert "EXPIRING" in listing
    assert GARMIN_KEY not in listing


@pytest.mark.req("SEC-SEC-001")
def test_empty_or_past_secret_is_refused(cli: Cli) -> None:
    cli.run("init")
    cli.secret_input = ""
    assert cli.run("secret", "set", "garmin-api-key")[0] == 2
    cli.secret_input = GARMIN_KEY
    assert cli.run("secret", "set", "garmin-api-key", "--expires-on", "2026-10-01")[0] == 2
    assert "GARMIN" not in cli.run("secret", "list")[1]


def test_commands_need_an_initialised_installation(cli: Cli) -> None:
    code, output = cli.run("secret", "list")
    assert code == 2
    assert "coach init" in output


@pytest.mark.req("SEC-AUTH-001", "SEC-SEC-004")
@pytest.mark.acceptance("AT-AUTH-001")
def test_token_rotation_replaces_the_token(cli: Cli) -> None:
    cli.run("init")
    before = cli.local_token()
    code, output = cli.run("token", "rotate")
    assert code == 0
    after = cli.local_token()
    assert after is not None
    assert after != before
    assert after.reveal() not in output


@pytest.mark.req("SEC-AUTH-001")
@pytest.mark.acceptance("AT-AUTH-001")
def test_without_a_token_commands_fail(cli: Cli) -> None:
    """The CLI presents the token it reads from the store; without one it cannot authenticate.

    A wrong token presented by a client is covered in tests/unit/test_authentication.py.
    """
    cli.run("init")
    cli.credentials.delete(credential_handle(CredentialKind.LOCAL_API_TOKEN, _athlete(cli)))
    code, output = cli.run("secret", "list")
    assert code == 2
    assert "authentication failed" in output


@pytest.mark.req("SEC-AUTH-001")
def test_token_rotate_recovers_a_lost_token(cli: Cli) -> None:
    cli.run("init")
    cli.credentials.delete(credential_handle(CredentialKind.LOCAL_API_TOKEN, _athlete(cli)))
    code, _ = cli.run("token", "rotate")
    assert code == 0
    assert cli.local_token() is not None
    assert cli.run("secret", "list")[0] == 0


@pytest.mark.req("SEC-AUD-001")
@pytest.mark.acceptance("AT-AUD-001")
def test_audit_verify_reports_intact_and_broken_chains(cli: Cli) -> None:
    cli.run("init")
    cli.run("secret", "set", "garmin-api-key")
    code, output = cli.run("audit", "verify")
    assert (code, "intact" in output) == (0, True)
    database = SqliteDatabase.open(cli.data_dir / "coach.sqlite3")
    with database.write() as connection:
        connection.execute("DROP TRIGGER audit_log_only_forget")
        connection.execute(
            "UPDATE audit_log SET document = json_set(document, '$.outcome', 'FAILED')"
        )
    code, output = cli.run("audit", "verify")
    assert code == 1
    assert "BROKEN" in output


def test_events_are_logged_to_a_file_in_the_data_directory(cli: Cli) -> None:
    cli.run("init")
    cli.run("secret", "set", "garmin-api-key")
    log = (cli.data_dir / "logs" / "coach.log").read_text(encoding="utf-8")
    assert "credential.stored kind=GARMIN_PERSONAL_API_KEY" in log
    assert GARMIN_KEY not in log


def _athlete(cli: Cli) -> UUID:
    owner = SqliteIdentityDirectory(SqliteDatabase.open(cli.data_dir / "coach.sqlite3")).owner()
    assert owner is not None
    return owner.athlete_id
