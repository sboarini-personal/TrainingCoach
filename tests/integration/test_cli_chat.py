"""`coach chat` (D-13): one question or an interactive session, with a scripted model; the
LLM key stays in the secret store (D-16: nothing is sent to a real model). Synthetic data
only."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from agent_llm import ScriptedLLM, answer, tools
from cli_harness import NOW, Cli
from coach.application.credentials import CredentialKind, credential_handle
from coach.cli.main import main
from coach.infrastructure.sqlite.database import SqliteDatabase
from coach.infrastructure.sqlite.identities import SqliteIdentityDirectory

LLM_KEY = "synthetic-llm-key-for-cli-tests-000000000001"  # pragma: allowlist secret


def run(
    cli: Cli, llm: ScriptedLLM | None, *argv: str, answers: list[str] | None = None
) -> tuple[int, str]:
    out = io.StringIO()
    pending = list(answers or [])

    def ask(prompt: str) -> str:
        cli.prompts.append(prompt)
        if not pending:
            raise EOFError
        return pending.pop(0)

    code = main(
        list(argv),
        env=cli.env,
        credentials=cli.credentials,
        read_secret=lambda _prompt: cli.secret_input,
        out=out,
        clock=lambda: NOW,
        read_answer=ask,
        llm=llm,
    )
    return code, out.getvalue()


@pytest.fixture
def cli(tmp_path: Path) -> Cli:
    command = Cli(tmp_path / "coach-data")
    assert command.run("init")[0] == 0
    assert command.run("profile", "timezone", "America/Argentina/Buenos_Aires")[0] == 0
    return command


@pytest.mark.req("PRIV-CON-003")
def test_the_chat_asks_for_the_ai_consent_first(cli: Cli) -> None:
    llm = ScriptedLLM()
    code, output = run(cli, llm, "chat", "Hola")
    assert code == 2
    assert "coach consent grant ai-processing" in output
    assert llm.requests == []


@pytest.mark.req("FR-AGT-001")
def test_one_question_shows_the_reads_and_the_answer(cli: Cli) -> None:
    assert cli.run("consent", "grant", "ai-processing")[0] == 0
    llm = ScriptedLLM(tools(("getGoals", {})), answer("Todavía no tenés objetivos cargados."))
    code, output = run(cli, llm, "chat", "¿Cuáles", "son", "mis", "objetivos?")
    assert code == 0, output
    assert output.splitlines() == ["  · getGoals", "Todavía no tenés objetivos cargados."]
    question = llm.requests[0].messages[0]["content"][1]["text"]
    assert question == "¿Cuáles son mis objetivos?"


@pytest.mark.req("FR-AGT-003")
def test_an_interactive_session_continues_one_conversation(cli: Cli) -> None:
    assert cli.run("consent", "grant", "ai-processing")[0] == 0
    llm = ScriptedLLM(answer("Hola."), answer("Bien."))
    code, output = run(cli, llm, "chat", answers=["Hola", "¿Cómo estás?", "salir"])
    assert code == 0, output
    assert output.splitlines() == ["Hola.", "Bien."]
    assert cli.prompts == ["> ", "> ", "> "]
    assert len(llm.requests[1].messages) == 3  # the first exchange, then the new message


@pytest.mark.req("SEC-SEC-002", "SEC-SEC-001")
@pytest.mark.acceptance("AT-SEC-002")
def test_without_a_key_the_chat_says_how_to_store_it_and_the_key_never_travels(cli: Cli) -> None:
    assert cli.run("consent", "grant", "ai-processing")[0] == 0
    code, output = run(cli, None, "chat", "Hola")  # the real adapter, no key stored
    assert code == 1
    assert "NO_API_KEY" in output
    assert "coach secret set llm-api-key" in output
    cli.secret_input = LLM_KEY
    assert cli.run("secret", "set", "llm-api-key")[0] == 0
    owner = SqliteIdentityDirectory(SqliteDatabase.open(cli.data_dir / "coach.sqlite3")).owner()
    assert owner is not None
    stored = cli.credentials.get(credential_handle(CredentialKind.LLM_API_KEY, owner.athlete_id))
    assert stored is not None
    llm = ScriptedLLM(answer("Hola."))
    code, output = run(cli, llm, "chat", "Hola")
    assert code == 0
    assert LLM_KEY not in llm.sent()
    assert LLM_KEY not in output
    assert LLM_KEY.encode() not in (cli.data_dir / "coach.sqlite3").read_bytes()


def test_a_non_interactive_chat_needs_a_question(cli: Cli) -> None:
    out = io.StringIO()
    code = main(["chat"], env=cli.env, credentials=cli.credentials, out=out, llm=ScriptedLLM())
    assert code == 2
    assert "no message" in out.getvalue()
