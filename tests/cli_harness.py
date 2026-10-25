"""The `coach` command line driven in-process, with an in-memory secret store."""

import io
from datetime import UTC, datetime
from pathlib import Path

from coach.application.credentials import CredentialKind, credential_handle
from coach.application.secrets import SecretValue
from coach.cli.main import main
from coach.infrastructure.memory import InMemoryCredentialProvider
from coach.infrastructure.sqlite.database import SqliteDatabase
from coach.infrastructure.sqlite.identities import SqliteIdentityDirectory

NOW = datetime(2026, 10, 25, 10, 0, tzinfo=UTC)
GARMIN_KEY = "synthetic-garmin-key-for-cli-tests-0001"  # pragma: allowlist secret


class Cli:
    def __init__(self, data_dir: Path) -> None:
        self.env = {"COACH_DATA_DIR": str(data_dir)}
        self.data_dir = data_dir
        self.credentials = InMemoryCredentialProvider()
        self.secret_input = GARMIN_KEY

    def run(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        code = main(
            list(argv),
            env=self.env,
            credentials=self.credentials,
            read_secret=lambda _prompt: self.secret_input,
            out=out,
            clock=lambda: NOW,
        )
        return code, out.getvalue()

    def local_token(self) -> SecretValue | None:
        owner = SqliteIdentityDirectory(
            SqliteDatabase.open(self.data_dir / "coach.sqlite3")
        ).owner()
        assert owner is not None
        return self.credentials.get(
            credential_handle(CredentialKind.LOCAL_API_TOKEN, owner.athlete_id)
        )
