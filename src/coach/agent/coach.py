"""The AI coach's message pipeline (agent spec 4; FR-AGT-001 to FR-AGT-004, PRIV-CON-003,
SEC-SEC-005, SAFE-MED-002, SEC-INJ-001).

For each athlete message, in order, all deterministic around one model exchange:

1. AI_PROCESSING consent, else nothing is stored or sent.
2. Secret redaction: a disclosed credential is replaced before anything is stored or sent,
   and the athlete is told to rotate it.
3. Red-flag phrases: a hit activates SAFETY_HOLD, the fixed safety notice is the answer, the
   message is not sent to the model, and a SAFETY_RESPONSE decision is recorded.
4. Context: a block written by the application (today, language, coaching conditions) goes
   with the athlete's text; data reaches the model only through tool results.
5. The model answers, calling READ tools as it needs (at most ``MAX_TOOL_ROUNDS`` rounds).
6. Output validation: every number of the answer traces to this turn's sources; otherwise the
   model is asked once to correct it, and numbers still untraced are named to the athlete.

The conversation is append-only and bound to the prompt, the tool set and the model; the
exchange is stored only when it is complete (a failure stores nothing).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, Final
from uuid import UUID

from coach import SPEC_VERSION
from coach.agent.prompts import Prompt
from coach.agent.services import AgentServices
from coach.agent.tools.base import TOOLS_VERSION, Toolbox, ToolContext
from coach.agent.validation import untraced_numbers
from coach.application.errors import TimezoneRequiredError
from coach.application.events import EventName
from coach.application.llm import LLMProvider, LLMReply, LLMRequest
from coach.application.ownership import OwnerScope
from coach.application.ports import Clock, EventSink, IdFactory, OwnedRepository
from coach.application.safety import hold_notice
from coach.application.screening import SecretScreen
from coach.domain.agent import (
    AgentDecision,
    Conversation,
    ConversationTurn,
    DecisionKind,
    PolicyVerdict,
    Role,
    ToolCallRecord,
)
from coach.domain.coaching.state import Condition
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.evidence import EvidenceRef
from coach.domain.consent.ledger import ConsentType
from coach.domain.recovery.checkin import RedFlag
from coach.domain.recovery.warning_phrases import detect

MAX_TOOL_ROUNDS: Final = 8
MAX_MESSAGE_LENGTH: Final = 8000
# Room an exchange may need: the athlete's turn, tool rounds, a correction and the answers.
EXCHANGE_TURNS: Final = 2 * (MAX_TOOL_ROUNDS + 1) + 4
EXCHANGE_BYTES: Final = 768 * 1024
_EXHAUSTED: Final = json.dumps(
    {"ok": False, "error": {"code": "VALIDATION_ERROR", "reason": "TOOL_BUDGET_EXHAUSTED"}}
)
_TEXTS: Final = {
    "es": {
        "unverified": "Aviso: no pude verificar estos números con tus datos: {numbers}. "
        "Tomalos con cautela.",
        "refused": "No puedo ayudarte con eso.",
        "incomplete": "La respuesta quedó incompleta.",
        "exhausted": "No pude completar la consulta de tus datos. Probá con una pregunta más "
        "acotada.",
    },
    "en": {
        "unverified": "Note: I could not verify these numbers against your data: {numbers}. "
        "Treat them with caution.",
        "refused": "I can't help with that.",
        "incomplete": "The answer is incomplete.",
        "exhausted": "I could not finish reading your data. Try a narrower question.",
    },
}
CORRECTION: Final = (
    "[Application check] Your answer contains numbers that are not in this turn's tool "
    "results, the context block or the athlete's message: {numbers}. Rewrite the whole "
    "answer using only numbers from those sources (call a tool if you need a value), or "
    "leave the numbers out. Do not mention this check."
)


class ReplyOutcome(StrEnum):
    ANSWERED = "ANSWERED"
    NOT_VERIFIED = "NOT_VERIFIED"  # numbers left untraced after the correction
    SAFETY_HOLD = "SAFETY_HOLD"
    REFUSED = "REFUSED"
    INCOMPLETE = "INCOMPLETE"


@dataclass(frozen=True, slots=True)
class CoachReply:
    conversation_id: UUID
    text: str  # what the athlete is shown, notices excluded
    outcome: ReplyOutcome
    notices: tuple[str, ...] = ()  # fixed texts written in code, shown first
    untraced: tuple[str, ...] = ()
    tool_calls: tuple[ToolCallRecord, ...] = ()
    decision_id: UUID | None = None
    model: str | None = None


class MessageTooLongError(DomainValidationError):
    def __init__(self) -> None:
        super().__init__(f"a message has at most {MAX_MESSAGE_LENGTH} characters")


def _texts(language: str) -> dict[str, str]:
    return _TEXTS.get(language.split("-", 1)[0].lower(), _TEXTS["es"])


@dataclass
class _Exchange:
    """One athlete message and everything it led to, before it is stored."""

    turns: list[ConversationTurn]
    sources: list[str]
    records: list[ToolCallRecord]
    evidence: list[EvidenceRef]
    context: ToolContext


class Coach:
    def __init__(
        self,
        *,
        llm: LLMProvider,
        toolbox: Toolbox,
        prompt: Prompt,
        services: AgentServices,
        screen: SecretScreen,
        conversations: OwnedRepository[Conversation],
        decisions: OwnedRepository[AgentDecision],
        events: EventSink,
        clock: Clock,
        ids: IdFactory,
        on_tool: Callable[[str], None] | None = None,
    ) -> None:
        self._llm = llm
        self._toolbox = toolbox
        self._specs = toolbox.specs()
        self._prompt = prompt
        self._services = services
        self._screen = screen
        self._conversations = conversations
        self._decisions = decisions
        self._events = events
        self._clock = clock
        self._ids = ids
        self._on_tool = on_tool

    # ------------------------------------------------------------------ conversations

    def latest(self, scope: OwnerScope) -> Conversation | None:
        """The most recent conversation that a new message may continue, if any."""
        found = [
            item
            for item in self._conversations.list_all(scope)
            if item.bound_to(self._prompt.version, TOOLS_VERSION, self._llm.model_id)
        ]
        return max(found, key=lambda item: item.started_at, default=None)

    def _continued(
        self, scope: OwnerScope, conversation_id: UUID | None, new: bool
    ) -> Conversation | None:
        if new:
            return None
        if conversation_id is None:
            found = self.latest(scope)
        else:
            found = self._conversations.get(scope, conversation_id)
            if not found.bound_to(self._prompt.version, TOOLS_VERSION, self._llm.model_id):
                found = None
        if found is None or not found.has_room(turns=EXCHANGE_TURNS, size=EXCHANGE_BYTES):
            return None
        return found

    def _store(
        self,
        scope: OwnerScope,
        conversation: Conversation | None,
        turns: Sequence[ConversationTurn],
    ) -> Conversation:
        if conversation is not None:
            return self._conversations.update(
                scope, conversation.appended(turns), expected_version=conversation.version
            )
        started = Conversation(
            conversation_id=self._ids(),
            athlete_id=scope.athlete_id,
            started_at=turns[0].at,
            prompt_version=self._prompt.version,
            tools_version=TOOLS_VERSION,
            model_id=self._llm.model_id,
            turns=tuple(turns),
        )
        return self._conversations.add(scope, started)

    # ------------------------------------------------------------------ the pipeline

    def reply(
        self,
        scope: OwnerScope,
        message: str,
        *,
        conversation_id: UUID | None = None,
        new: bool = False,
    ) -> CoachReply:
        services = self._services
        services.consents.require(scope, ConsentType.AI_PROCESSING)
        if len(message) > MAX_MESSAGE_LENGTH:
            raise MessageTooLongError
        language = services.inputs.profile(scope).effective_language
        screened = self._screen.screen(message, language=language, source="chat")
        notices = (screened.notice,) if screened.notice is not None else ()
        text = screened.text.strip()
        if not text:
            raise DomainValidationError("an empty message")
        conversation = self._continued(scope, conversation_id, new)
        flags = detect(text)
        if flags:
            return self._safety(scope, conversation, text, flags, language, notices)
        today = self._today(scope)
        context = self._context(scope, language, today)
        user = ConversationTurn(
            Role.USER,
            ({"type": "text", "text": context}, {"type": "text", "text": text}),
            self._clock(),
        )
        exchange = _Exchange([user], [context, text], [], [], ToolContext(scope, today))
        history = [] if conversation is None else conversation.messages()
        final = self._answer(history, exchange)
        texts = _texts(language)
        answer, outcome = self._read(final, texts)
        untraced = untraced_numbers(answer, exchange.sources)
        if untraced and outcome is ReplyOutcome.ANSWERED:
            note = CORRECTION.format(numbers=", ".join(untraced))
            exchange.turns.append(
                ConversationTurn(Role.USER, ({"type": "text", "text": note},), self._clock())
            )
            final = self._answer(history, exchange)
            answer, outcome = self._read(final, texts)
            untraced = untraced_numbers(answer, exchange.sources)
            if untraced and outcome is ReplyOutcome.ANSWERED:
                outcome = ReplyOutcome.NOT_VERIFIED
        if outcome is ReplyOutcome.NOT_VERIFIED:
            answer = f"{answer}\n\n{texts['unverified'].format(numbers=', '.join(untraced))}"
        stored = self._store(scope, conversation, exchange.turns)
        self._events.emit(
            EventName.AGENT_REPLIED,
            {
                "outcome": outcome.value,
                "tool_calls": str(len(exchange.records)),
                "untraced": str(len(untraced)),
            },
        )
        return CoachReply(
            conversation_id=stored.conversation_id,
            text=answer,
            outcome=outcome,
            notices=notices,
            untraced=untraced if outcome is ReplyOutcome.NOT_VERIFIED else (),
            tool_calls=tuple(exchange.records),
            model=final.model,
        )

    def _today(self, scope: OwnerScope) -> date | None:
        try:
            return self._services.recovery.today(scope)
        except TimezoneRequiredError:
            return None

    def _context(self, scope: OwnerScope, language: str, today: date | None) -> str:
        """Written by the application; volatile, so it travels with the message and the
        system prompt stays the same (and cached) for the whole conversation."""
        state = self._services.conditions.current(scope)
        data = {
            "today": None if today is None else today.isoformat(),
            "timezoneSet": today is not None,
            "language": language,
            "lifecycle": state.lifecycle.value,
            "conditions": sorted(item.value for item in state.conditions),
        }
        return "<context>" + json.dumps(data, ensure_ascii=False) + "</context>"

    def _answer(self, history: list[dict[str, Any]], exchange: _Exchange) -> LLMReply:
        """Ask the model until it answers without tools; every tool use gets its result,
        so the stored conversation is always well formed."""
        for round_number in range(MAX_TOOL_ROUNDS + 2):
            messages = (*history, *(_message(turn) for turn in exchange.turns))
            request = LLMRequest(self._prompt.text, self._specs, tuple(messages))
            reply = self._llm.reply(request)
            exchange.turns.append(ConversationTurn(Role.ASSISTANT, reply.content, self._clock()))
            uses = reply.tool_uses()
            if reply.stop_reason != "tool_use" or not uses:
                return reply
            exhausted = round_number >= MAX_TOOL_ROUNDS
            results = [self._result(block, exchange, exhausted=exhausted) for block in uses]
            exchange.turns.append(ConversationTurn(Role.USER, tuple(results), self._clock()))
        # The model kept asking for tools after its budget: close the exchange in code.
        closing = {"type": "text", "text": "TOOL_BUDGET_EXHAUSTED"}
        exchange.turns.append(ConversationTurn(Role.ASSISTANT, (closing,), self._clock()))
        return LLMReply(content=(closing,), stop_reason="tool_budget", model=self._llm.model_id)

    def _result(
        self, block: Mapping[str, Any], exchange: _Exchange, *, exhausted: bool
    ) -> dict[str, Any]:
        tool_use_id = str(block.get("id", ""))
        if exhausted:
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": _EXHAUSTED,
                "is_error": True,
            }
        name = str(block.get("name", ""))
        if self._on_tool is not None:
            self._on_tool(name)
        output = self._toolbox.run(tool_use_id, name, block.get("input"), exchange.context)
        exchange.records.append(output.record)
        exchange.evidence.extend(output.evidence)
        exchange.sources.append(str(output.block["content"]))
        return dict(output.block)

    @staticmethod
    def _read(reply: LLMReply, texts: dict[str, str]) -> tuple[str, ReplyOutcome]:
        answer = reply.text().strip()
        match reply.stop_reason:
            case "refusal":
                return texts["refused"], ReplyOutcome.REFUSED
            case "tool_budget":
                return texts["exhausted"], ReplyOutcome.INCOMPLETE
            case "max_tokens":
                return f"{answer}\n\n{texts['incomplete']}".strip(), ReplyOutcome.INCOMPLETE
        return answer, ReplyOutcome.ANSWERED

    # ------------------------------------------------------------------ safety

    def _safety(
        self,
        scope: OwnerScope,
        conversation: Conversation | None,
        text: str,
        flags: Sequence[RedFlag],
        language: str,
        notices: tuple[str, ...],
    ) -> CoachReply:
        """Red flags in the message: SAFETY_HOLD first, the fixed notice as the answer, and
        no model call (SAFE-MED-002: the coach neither diagnoses nor speculates)."""
        self._services.conditions.activate(scope, Condition.SAFETY_HOLD, reason=flags[0].value)
        notice = hold_notice(flags, language)
        now = self._clock()
        turns = [
            ConversationTurn(Role.USER, ({"type": "text", "text": text},), now),
            ConversationTurn(Role.ASSISTANT, ({"type": "text", "text": notice},), now),
        ]
        stored = self._store(scope, conversation, turns)
        decision = self._decisions.add(
            scope,
            AgentDecision(
                decision_id=self._ids(),
                athlete_id=scope.athlete_id,
                decided_at=now,
                kind=DecisionKind.SAFETY_RESPONSE,
                prompt_version=self._prompt.version,
                spec_version=SPEC_VERSION,
                model_id=None,  # no model took part
                tool_calls=(),
                evidence=(),
                proposed_action="ACTIVATE_SAFETY_HOLD",
                policy_verdict=PolicyVerdict.NOT_APPLICABLE,
                explanation=notice,
                policy_reasons=tuple(flag.value for flag in flags),
                conversation_id=stored.conversation_id,
            ),
        )
        self._events.emit(EventName.AGENT_DECISION_RECORDED, {"kind": decision.kind.value})
        self._events.emit(
            EventName.AGENT_REPLIED,
            {"outcome": ReplyOutcome.SAFETY_HOLD.value, "tool_calls": "0", "untraced": "0"},
        )
        return CoachReply(
            conversation_id=stored.conversation_id,
            text=notice,
            outcome=ReplyOutcome.SAFETY_HOLD,
            notices=notices,
            decision_id=decision.decision_id,
        )


def _message(turn: ConversationTurn) -> dict[str, Any]:
    return {"role": turn.role.value, "content": list(turn.content)}
