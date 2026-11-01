"""`coach chat`: the AI coach in the terminal (D-13; agent spec 4 and 8).

`coach chat "question"` asks one question; `coach chat` opens an interactive session that
ends with an empty line, `exit` or end of input. The conversation continues the latest one
unless `--new` is given. Answers are shown once they are validated (FR-AGT-004); meanwhile
each data read is shown as a progress line. Without the AI_PROCESSING consent or the LLM
key the chat explains how to enable it; every other command keeps working without the AI.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable, Mapping
from typing import Final, TextIO

from coach.agent.coach import Coach, CoachReply, MessageTooLongError
from coach.agent.prompts import system_prompt
from coach.agent.services import AgentServices
from coach.agent.tools import coach_tools
from coach.agent.tools.base import Toolbox
from coach.application.consent import ConsentGate, ConsentRequiredError
from coach.application.contracts import AGENT_DECISION_CODEC, CONVERSATION_CODEC
from coach.application.credentials import CredentialKind
from coach.application.errors import NotFoundError
from coach.application.identity import Principal
from coach.application.ids import new_id
from coach.application.llm import LLMFailure, LLMProvider, LLMUnavailableError
from coach.application.ownership import OwnerScope
from coach.application.secrets import SecretValue
from coach.cli.runtime import PRIVACY_POLICY_VERSION, Runtime
from coach.domain.common.errors import DomainValidationError
from coach.domain.consent.ledger import ConsentType
from coach.infrastructure.sqlite.aggregates import SqliteAggregateRepository
from coach.integrations.llm.anthropic_provider import (
    DEFAULT_EFFORT,
    DEFAULT_MODEL,
    AnthropicProvider,
)

type Ask = Callable[[str], str]

PROMPT: Final = "> "
EXIT_WORDS: Final = frozenset({"exit", "quit", "salir", "/exit", "/salir"})
_UNAVAILABLE: Final = {
    LLMFailure.NO_API_KEY: "store the key with `coach secret set llm-api-key`",
    LLMFailure.AUTHENTICATION: "the stored key was refused: store a new one with "
    "`coach secret set llm-api-key`",
    LLMFailure.RATE_LIMITED: "the provider is limiting requests: try again in a few minutes",
    LLMFailure.UNAVAILABLE: "the provider cannot be reached now: try again later",
    LLMFailure.REQUEST_INVALID: "the provider refused the request",
}


def agent_services(runtime: Runtime) -> AgentServices:
    """The use cases the coach's tools may call; nothing else of the runtime."""
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
        generation=runtime.generation,
        executions=runtime.executions,
        adaptation=runtime.adaptation,
        insights=runtime.insights,
        reports=runtime.reports,
        integrations=runtime.integrations,
        sync=runtime.sync,
        recovery_sync=runtime.recovery_sync,
        clock=runtime.clock,
    )


def llm_provider(runtime: Runtime, scope: OwnerScope, variables: Mapping[str, str]) -> LLMProvider:
    """The configured model (D-14): Claude Opus 5.5 at medium effort unless
    ``COACH_LLM_MODEL`` or ``COACH_LLM_EFFORT`` say otherwise. The key is read from the OS
    secret store only when a reply is needed, and only the SDK client receives it."""

    def key() -> SecretValue | None:
        try:
            return runtime.credentials.secret(scope, CredentialKind.LLM_API_KEY)
        except NotFoundError:
            return None

    return AnthropicProvider(
        key,
        model=variables.get("COACH_LLM_MODEL", DEFAULT_MODEL),
        effort=variables.get("COACH_LLM_EFFORT", DEFAULT_EFFORT),
    )


def build_coach(
    runtime: Runtime, llm: LLMProvider, *, on_tool: Callable[[str], None] | None = None
) -> Coach:
    services = agent_services(runtime)
    return Coach(
        llm=llm,
        toolbox=Toolbox(coach_tools(services), events=runtime.events),
        prompt=system_prompt(),
        services=services,
        screen=runtime.screen,
        conversations=SqliteAggregateRepository(runtime.database, CONVERSATION_CODEC),
        decisions=SqliteAggregateRepository(runtime.database, AGENT_DECISION_CODEC),
        events=runtime.events,
        clock=runtime.clock,
        ids=new_id,
        on_tool=on_tool,
    )


def _show(reply: CoachReply, out: TextIO) -> None:
    for notice in reply.notices:
        print(notice, file=out)
    print(reply.text, file=out)


def chat(
    runtime: Runtime,
    scope: OwnerScope,
    args: argparse.Namespace,
    out: TextIO,
    *,
    ask: Ask | None,
    llm: LLMProvider | None = None,
    variables: Mapping[str, str] | None = None,
    principal: Principal | None = None,
) -> int:
    provider = llm or llm_provider(
        runtime, scope, variables if variables is not None else os.environ
    )
    coach = build_coach(
        runtime, provider, on_tool=lambda name: print(f"  · {name}", file=out, flush=True)
    )
    question = " ".join(args.message).strip()
    if not question and ask is None:
        print('error: no message: run `coach chat "your question"` or use a terminal', file=out)
        return 2
    new = bool(args.new)
    conversation = None
    while True:
        if not question:
            try:
                question = ask(PROMPT).strip() if ask is not None else ""
            except EOFError:
                return 0
            if not question or question.casefold() in EXIT_WORDS:
                return 0
        try:
            reply = coach.reply(
                scope, question, conversation_id=conversation, new=new, principal=principal
            )
        except ConsentRequiredError as error:
            if ConsentType.AI_PROCESSING in error.missing:
                print(
                    "error: the AI coach sends your data to the LLM provider only with your "
                    "consent: run `coach consent grant ai-processing`",
                    file=out,
                )
                return 2
            raise
        except LLMUnavailableError as error:
            print(
                f"error: the AI coach is not available now ({error.failure.value}): "
                f"{_UNAVAILABLE[error.failure]}. Every other command keeps working.",
                file=out,
            )
            return 1
        except MessageTooLongError as error:
            print(f"error: {error}", file=out)
            return 2
        except DomainValidationError as error:
            print(f"error: {error}", file=out)
            return 2
        _show(reply, out)
        conversation, new = reply.conversation_id, False
        if args.message:
            return 0
        question = ""
