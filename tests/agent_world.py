"""The AI coach's tools over a real runtime: an athlete onboarded through the CLI, SQLite in a
temporary data directory, the in-memory secret store. Synthetic data only."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cli_harness import Cli
from coach.agent.services import AgentServices
from coach.agent.tools import read_tools
from coach.agent.tools.base import Toolbox, ToolCallOutput, ToolContext
from coach.application.consent import ConsentGate
from coach.application.errors import TimezoneRequiredError
from coach.application.ownership import OwnerScope
from coach.cli.runtime import PRIVACY_POLICY_VERSION, Runtime, build_runtime
from coach.infrastructure.datadir import ensure_layout
from coach.infrastructure.memory import RecordingEventSink
from coach.infrastructure.sqlite.database import SqliteDatabase
from coach.infrastructure.sqlite.identities import SqliteIdentityDirectory

ONBOARDING = (
    ("profile", "timezone", "America/Argentina/Buenos_Aires"),
    ("profile", "name", "Synthetic Runner"),
    ("profile", "birth-date", "1985-03-02"),
    ("profile", "health", "none"),
    ("profile", "resources", "--gym", "yes", "--equipment", "dumbbells,bench", "--no-tennis"),
    ("profile", "experience", "running", "--years", "3", "--race", "10k=50:00@2026-08-01"),
    ("profile", "experience", "strength", "--years", "1", "--sessions-per-week", "2"),
    ("profile", "experience", "tennis", "--sessions-per-week", "0"),
    ("availability", "set", "--days", "6", "--max-hours", "10"),
    ("availability", "default", "--minutes", "240"),
)


def agent_services(runtime: Runtime) -> AgentServices:
    return AgentServices(
        consents=ConsentGate(runtime.consents.ledgers, policy_version=PRIVACY_POLICY_VERSION),
        conditions=runtime.conditions,
        inputs=runtime.inputs,
        activities=runtime.activities,
        analytics=runtime.analytics,
        strength=runtime.strength,
        recovery=runtime.recovery,
        assessments=runtime.recovery_assessment,
        plans=runtime.plans,
        executions=runtime.executions,
        adaptation=runtime.adaptation,
        insights=runtime.insights,
        reports=runtime.reports,
        integrations=runtime.integrations,
    )


@dataclass
class AgentWorld:
    cli: Cli
    runtime: Runtime
    scope: OwnerScope
    toolbox: Toolbox
    events: RecordingEventSink

    def ok(self, *args: str) -> str:
        code, output = self.cli.run(*args)
        assert code == 0, output
        return output

    def call(self, name: str, arguments: object = None) -> ToolCallOutput:
        try:
            today = self.runtime.recovery.today(self.scope)
        except TimezoneRequiredError:
            today = None
        context = ToolContext(self.scope, today)
        return self.toolbox.run(
            "toolu_synthetic", name, {} if arguments is None else arguments, context
        )

    def data(self, name: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        output = self.call(name, dict(arguments or {}))
        assert output.payload["ok"] is True, output.payload
        found: dict[str, Any] = output.payload["data"]
        return found

    def error(self, name: str, arguments: object = None) -> dict[str, Any]:
        output = self.call(name, arguments)
        assert output.payload["ok"] is False, output.payload
        assert output.block["is_error"] is True
        found: dict[str, Any] = output.payload["error"]
        return found


def agent_world(tmp_path: Path, *, onboard: bool = True) -> AgentWorld:
    cli = Cli(tmp_path / "coach-data")
    assert cli.run("init")[0] == 0
    if onboard:
        for command in ONBOARDING:
            code, output = cli.run(*command)
            assert code == 0, output
    owner = SqliteIdentityDirectory(SqliteDatabase.open(cli.data_dir / "coach.sqlite3")).owner()
    assert owner is not None
    runtime = build_runtime(ensure_layout(cli.data_dir), cli.credentials, lambda: cli.now)
    events = RecordingEventSink()
    toolbox = Toolbox(read_tools(agent_services(runtime)), events=events)
    return AgentWorld(cli, runtime, OwnerScope(owner.athlete_id), toolbox, events)
