"""AT-SEC-002 (P4 scope): planted secrets never leak into any output or store.

Runs real flows (installation, secret storage, token rotation, failed authentication, chat
screening, audit verification) and scans command output, every log record at every level,
the log file, every byte under the data directory and every exception message.
"""

import io
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from coach.application.authentication import (
    AuthenticationError,
    AuthenticationService,
    FailedAuthLockout,
    LocalTokenAuthenticator,
)
from coach.application.screening import SecretScreen
from coach.application.secrets import SecretValue
from coach.cli.main import main
from coach.infrastructure.events import LoggingEventSink
from coach.infrastructure.memory import InMemoryCredentialProvider
from coach.infrastructure.sqlite.audit import SqliteAuditLog
from coach.infrastructure.sqlite.database import SqliteDatabase
from coach.infrastructure.sqlite.identities import SqliteIdentityDirectory

pytestmark = [
    pytest.mark.req("SEC-SEC-002", "SEC-SEC-003", "NFR-OBS-002", "INT-GAR-006"),
    pytest.mark.acceptance("AT-SEC-002"),
]

NOW = datetime(2026, 10, 25, 10, 0, tzinfo=UTC)
GARMIN_KEY = "planted-garmin-key-Zq81LmN4pX0v"  # pragma: allowlist secret
LLM_KEY = "sk-ant-api03-PlantedLlmKeyMaterial0123"  # pragma: allowlist secret
WRONG_TOKEN = "planted-wrong-token-Hy72Kd0qWx"  # pragma: allowlist secret
CHAT_SECRET = "PlantedChatSecretAbc123Def456Ghi789"  # pragma: allowlist secret


def test_planted_secrets_never_leak(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    data_dir = tmp_path / "coach-data"
    env = {"COACH_DATA_DIR": str(data_dir)}
    credentials = InMemoryCredentialProvider()
    outputs: list[str] = []
    errors: list[str] = []
    inputs = iter([GARMIN_KEY, LLM_KEY])

    def run(*argv: str) -> None:
        out = io.StringIO()
        main(
            list(argv),
            env=env,
            credentials=credentials,
            read_secret=lambda _: next(inputs),
            out=out,
            clock=lambda: NOW,
        )
        outputs.append(out.getvalue())

    with caplog.at_level(logging.DEBUG):
        run("init")
        run("secret", "set", "garmin-api-key", "--expires-on", "2027-01-23")
        run("secret", "set", "llm-api-key")
        run("token", "rotate")
        run("secret", "list")
        run("audit", "verify")

        database = SqliteDatabase.open(data_dir / "coach.sqlite3")
        authentication = AuthenticationService(
            LocalTokenAuthenticator(credentials, SqliteIdentityDirectory(database)),
            FailedAuthLockout(),
            audit=SqliteAuditLog(database),
            events=LoggingEventSink(),
            clock=lambda: NOW,
        )
        try:
            authentication.authenticate("cli", SecretValue(WRONG_TOKEN))
        except AuthenticationError as error:
            errors.append(str(error))
        screened = SecretScreen(LoggingEventSink()).screen(
            f"my key is {CHAT_SECRET}", language="en"
        )
        outputs.append(screened.text)

    owner = SqliteIdentityDirectory(database).owner()
    assert owner is not None
    token = credentials.get(f"local_api_token/{owner.athlete_id}")
    assert token is not None
    planted = [GARMIN_KEY, LLM_KEY, WRONG_TOKEN, CHAT_SECRET, token.reveal()]

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    stored_bytes = b"".join(path.read_bytes() for path in data_dir.rglob("*") if path.is_file())
    haystacks = {
        "command output": "\n".join(outputs),
        "log records": log_text,
        "error messages": "\n".join(errors),
    }
    for secret in planted:
        for name, text in haystacks.items():
            assert secret not in text, f"secret leaked into {name}"
        assert secret.encode() not in stored_bytes, "secret leaked into the data directory"
    assert (data_dir / "logs" / "coach.log").is_file()
    assert "security.authn.failed" in log_text
    assert "security.secret_detected" in log_text
