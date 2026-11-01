"""Run the agent evaluation scenarios against the real model (agent spec 6; decision D-16).

Only when the athlete asks for it: without ``--yes`` this prints the plan and a token estimate
and stops. With ``--yes`` every chosen scenario runs in a throw-away synthetic installation
under ``reports/`` (nothing of the athlete's data is sent), with the LLM key read from the OS
secret store of the athlete's installation (``COACH_DATA_DIR``); the key is given only to the
SDK client. The judge of the simulated evaluations names the rules each answer broke; the
results go to ``reports/agent-evals-live.json`` as codes only.

    uv run python tools/agent_evals_live.py                    # plan and estimate
    uv run python tools/agent_evals_live.py --scenario vo2max  # one scenario
    uv run python tools/agent_evals_live.py --yes              # run them all

Prices change: multiply the token estimate by the current price of the model at
https://docs.anthropic.com/en/docs/about-claude/pricing before running.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Final, cast

REPO: Final = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))  # the scenarios and their judge live with the tests

from coach.agent.prompts import system_prompt  # noqa: E402
from coach.agent.services import AgentServices  # noqa: E402
from coach.agent.tools import coach_tools  # noqa: E402
from coach.application.credentials import CredentialKind, credential_handle  # noqa: E402
from coach.application.llm import LLMReply, LLMRequest  # noqa: E402
from coach.application.secrets import SecretValue  # noqa: E402
from coach.infrastructure.datadir import resolve_data_dir  # noqa: E402
from coach.infrastructure.sqlite.database import SqliteDatabase  # noqa: E402
from coach.infrastructure.sqlite.identities import SqliteIdentityDirectory  # noqa: E402
from coach.integrations.llm.anthropic_provider import (  # noqa: E402
    DEFAULT_EFFORT,
    DEFAULT_MODEL,
    AnthropicProvider,
)
from eval_scenarios import BY_NAME, SCENARIOS, Scenario, build, judge, observe  # noqa: E402

REQUESTS_PER_SCENARIO: Final = 4  # a tool round or two, an answer, perhaps a correction
DATA_TOKENS_PER_REQUEST: Final = 4000  # tool results and history, a generous average
OUTPUT_TOKENS_PER_REQUEST: Final = 1500
REPORT: Final = REPO / "reports" / "agent-evals-live.json"


def estimate(scenarios: Sequence[Scenario]) -> tuple[int, int]:
    """Input and output tokens, roughly (four characters a token)."""
    tools = [
        {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
        for tool in coach_tools(cast(AgentServices, object()))
    ]
    prefix = (len(system_prompt().text) + len(json.dumps(tools))) // 4
    per_request = prefix + DATA_TOKENS_PER_REQUEST
    requests = REQUESTS_PER_SCENARIO * len(scenarios)
    return requests * per_request, requests * OUTPUT_TOKENS_PER_REQUEST


def athlete_key() -> SecretValue | None:
    from coach.infrastructure.keyring_credentials import KeyringCredentialProvider

    data_dir = resolve_data_dir(os.environ)
    database = data_dir / "coach.sqlite3"
    if not database.is_file():
        return None
    owner = SqliteIdentityDirectory(SqliteDatabase.open(database)).owner()
    if owner is None:
        return None
    return KeyringCredentialProvider().get(
        credential_handle(CredentialKind.LLM_API_KEY, owner.athlete_id)
    )


class Recording:
    """The real provider, keeping the requests for the judge."""

    def __init__(self, provider: AnthropicProvider) -> None:
        self._provider = provider
        self.requests: list[LLMRequest] = []

    @property
    def model_id(self) -> str:
        return self._provider.model_id

    def reply(self, request: LLMRequest, **_options: object) -> LLMReply:
        self.requests.append(request)
        return self._provider.reply(request)


def run(scenarios: Sequence[Scenario], key: SecretValue) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    model = os.environ.get("COACH_LLM_MODEL", DEFAULT_MODEL)
    effort = os.environ.get("COACH_LLM_EFFORT", DEFAULT_EFFORT)
    REPORT.parent.mkdir(exist_ok=True)
    for scenario in scenarios:
        with tempfile.TemporaryDirectory() as folder:  # never inside the repository (ADR-035)
            world = build(scenario, Path(folder))
            llm = Recording(AnthropicProvider(lambda: key, model=model, effort=effort))
            reply = world.coach(llm).reply(world.scope, scenario.message, principal=world.principal)
            turn = observe(world, reply, llm.requests)
            problems = judge(scenario, turn)
            called = {name for name, _arguments in turn.calls}
            missing = sorted(scenario.expect_tools - called)
            results.append(
                {
                    "scenario": scenario.name,
                    "category": scenario.category,
                    "outcome": reply.outcome.value,
                    "problems": problems,
                    "expectedToolsNotCalled": missing,
                    "requests": len(llm.requests),
                }
            )
            print(f"{scenario.name}: {reply.outcome.value} {problems or 'ok'}")
    return results


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="agent evaluations against the real model")
    parser.add_argument("--scenario", action="append", choices=sorted(BY_NAME))
    parser.add_argument("--yes", action="store_true", help="run them (costs tokens)")
    args = parser.parse_args(argv)
    chosen = [BY_NAME[name] for name in args.scenario] if args.scenario else list(SCENARIOS)
    tokens_in, tokens_out = estimate(chosen)
    print(f"{len(chosen)} scenarios, about {tokens_in:,} input and {tokens_out:,} output tokens")
    print("check the current price per million tokens before running (see this file's notes)")
    if not args.yes:
        print("nothing was sent: run again with --yes to run them")
        return 0
    key = athlete_key()
    if key is None:
        print("no LLM key: store it with `coach secret set llm-api-key`")
        return 2
    results = run(chosen, key)
    REPORT.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8", newline="\n")
    failed = [item for item in results if item["problems"]]
    print(f"{len(results) - len(failed)} of {len(results)} without problems; see {REPORT.name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
