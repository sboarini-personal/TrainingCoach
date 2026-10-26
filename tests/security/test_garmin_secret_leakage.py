"""AT-SEC-002 (P7 extension): the Garmin key and access tokens never leak (INT-GAR-006).

Runs syncs through the CLI with a planted key and planted tokens: success, token renewal
after a 401, server errors with retries, a rate limit, a failing token endpoint and a
rejected key. Then scans command output, every log record at every level, the log file and
every byte under the data directory (database, RAW blobs, quarantine).
"""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

import pytest

from cli_harness import NOW, Cli
from coach.integrations.garmin.token import TOKEN_PATH
from garmin_samples import jwt
from garmin_world import FakeTime, ScriptedTransport, response

pytestmark = [
    pytest.mark.req("SEC-SEC-001", "SEC-SEC-003", "NFR-OBS-002", "INT-GAR-006"),
    pytest.mark.acceptance("AT-SEC-002"),
]

PLANTED_KEY = "planted-garmin-key-Vx93Lq0RtY7"  # pragma: allowlist secret
SEARCH = "/activitylist-service/activities/search/activities"
DETAILS = "/activity-service/activity/21004587311"


def planted_token(name: str) -> str:
    return jwt(expires=NOW + timedelta(hours=24), subject=f"planted-token-{name}")


def test_garmin_key_and_tokens_never_leak(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    data_dir = tmp_path / "coach-data"
    cli = Cli(data_dir)
    transport = ScriptedTransport(FakeTime(NOW))
    transport.activities["2026-10-20"] = [21004587311]
    cli.transport = transport
    cli.secret_input = PLANTED_KEY
    tokens = [planted_token(str(number)) for number in range(4)]
    transport.script(TOKEN_PATH, *(response(200, token.encode()) for token in tokens[:2]))
    transport.script(DETAILS, response(401))  # revoked early: renewed once
    outputs: list[str] = []

    def run(*argv: str) -> None:
        outputs.append(cli.run(*argv)[1])

    with caplog.at_level(logging.DEBUG):
        run("init")
        run("consent", "grant", "activities")
        run("profile", "timezone", "America/Argentina/Buenos_Aires")
        run("secret", "set", "garmin-api-key", "--expires-on", "2027-01-23")
        run("garmin", "sync")
        transport.script(TOKEN_PATH, response(200, tokens[2].encode()))
        transport.script(SEARCH, *(response(500) for _ in range(5)))
        cli.now += timedelta(days=1)
        run("garmin", "sync")
        transport.script(TOKEN_PATH, response(200, tokens[3].encode()))
        transport.script(SEARCH, response(429, retry_after="60"))
        cli.now += timedelta(days=1)
        run("garmin", "sync")
        transport.script(TOKEN_PATH, response(500), response(500), response(500))
        transport.script(TOKEN_PATH, response(500), response(500))
        cli.now += timedelta(days=1)
        run("garmin", "sync")
        transport.script(TOKEN_PATH, response(401))
        cli.now += timedelta(days=1)
        run("garmin", "sync")
        run("garmin", "status")
        run("secret", "list")
        run("issue", "list")
        run("activity", "list")

    sent_authorizations = [item.headers.get("Authorization", "") for item in transport.sent]
    assert f"ApiKey {PLANTED_KEY}" in sent_authorizations  # the secrets were really used
    assert f"Bearer {tokens[0]}" in sent_authorizations
    assert f"Bearer {tokens[1]}" in sent_authorizations
    assert all(
        PLANTED_KEY not in item.url and "planted-token" not in item.url for item in transport.sent
    )

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    stored = b"".join(path.read_bytes() for path in data_dir.rglob("*") if path.is_file())
    haystacks = {"command output": "\n".join(outputs), "log records": log_text}
    for secret in [PLANTED_KEY, *tokens]:
        for name, text in haystacks.items():
            assert secret not in text, f"secret leaked into {name}"
        assert secret.encode() not in stored, "secret leaked into the data directory"
    # The JWT payload part is base64 of the claims; check the decoded subject too.
    assert "planted-token" not in log_text
    assert b"planted-token" not in stored
    assert "integration.sync.failed" in log_text
    assert (data_dir / "logs" / "coach.log").is_file()
