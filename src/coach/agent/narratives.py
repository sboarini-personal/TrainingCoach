"""Texts the AI coach writes from data computed in code: the narrative part of a report and
the reading of a detected pattern (reporting spec; insights spec 2; FR-AGT-004; P13.5).

The model gets the data part (or the insight) as tool-shaped JSON, with external text marked
untrusted, and no tools: it can only use what it was given. Output validation rules 1.0.0
apply with that data and the context block as the sources; untraced numbers go back once,
then a report narrative is shown with a warning and an insight reading is not stored. A
recommendation is never stored while SAFETY_HOLD or RECOVERY_BLOCK is active. Reports stay
read models: the narrative is written when asked, never stored.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from coach.agent.coach import CORRECTION, ReplyOutcome, texts_for
from coach.agent.prompts import Prompt
from coach.agent.services import AgentServices
from coach.agent.tools.plans import report as report_json
from coach.agent.tools.recovery import insight_json
from coach.agent.validation import json_sources, untraced_numbers
from coach.application.events import EventName
from coach.application.llm import LLMProvider, LLMReply, LLMRequest
from coach.application.ownership import OwnerScope
from coach.application.ports import EventSink
from coach.domain.coaching.state import Condition
from coach.domain.consent.ledger import ConsentType
from coach.domain.insights import AgentVersion, Insight
from coach.domain.reports import ReportData, Unavailable

QUIET: Final = frozenset({Condition.SAFETY_HOLD, Condition.RECOVERY_BLOCK})
_FENCE: Final = re.compile(r"^```(?:json)?\s*|\s*```$")
SUMMARY: Final = "Summary"


@dataclass(frozen=True, slots=True)
class Narrative:
    text: str
    outcome: ReplyOutcome
    untraced: tuple[str, ...] = ()
    model: str | None = None


@dataclass(frozen=True, slots=True)
class Reading:
    interpretation: str
    hypothesis: str | None
    recommendation: str | None


class Narrator:
    def __init__(
        self,
        *,
        llm: LLMProvider,
        prompt: Prompt,
        services: AgentServices,
        events: EventSink,
    ) -> None:
        self._llm = llm
        self._prompt = prompt
        self._services = services
        self._events = events

    def _context(self, scope: OwnerScope) -> tuple[str, str, frozenset[Condition]]:
        services = self._services
        services.consents.require(scope, ConsentType.AI_PROCESSING)
        language = services.inputs.profile(scope).effective_language
        conditions = services.conditions.current(scope).conditions
        data = {"language": language, "conditions": sorted(item.value for item in conditions)}
        return "<context>" + json.dumps(data) + "</context>", language, conditions

    def _ask(self, text: str, sources: list[str]) -> tuple[LLMReply, str, tuple[str, ...]]:
        """One request, and one correction when numbers do not trace."""
        messages: list[Mapping[str, Any]] = [
            {"role": "user", "content": [{"type": "text", "text": text}]}
        ]
        reply = self._llm.reply(LLMRequest(self._prompt.text, (), tuple(messages)))
        answer = reply.text().strip()
        untraced = untraced_numbers(answer, sources)
        if untraced and reply.stop_reason == "end_turn":
            note = CORRECTION.format(numbers=", ".join(untraced))
            messages += [
                {"role": "assistant", "content": list(reply.content)},
                {"role": "user", "content": [{"type": "text", "text": note}]},
            ]
            reply = self._llm.reply(LLMRequest(self._prompt.text, (), tuple(messages)))
            answer = reply.text().strip()
            untraced = untraced_numbers(answer, sources)
        return reply, answer, untraced

    # ------------------------------------------------------------------ reports

    def report(self, scope: OwnerScope, data: ReportData) -> Narrative:
        context, language, _conditions = self._context(scope)
        document = report_json(data)
        payload = json.dumps(document, ensure_ascii=False, sort_keys=True)
        sections = [
            section.name
            for section in data.sections
            if section.unavailable is Unavailable.NARRATIVE
        ] or [SUMMARY]
        request = (
            f"{context}\n<report>{payload}</report>\n"
            "Write these sections of the narrative, each as a line '## <section name>' followed "
            "by at most five short sentences, and nothing else: " + "; ".join(sections) + "."
        )
        reply, answer, untraced = self._ask(request, [*json_sources(document), context])
        texts = texts_for(language)
        outcome = ReplyOutcome.ANSWERED
        if reply.stop_reason == "refusal":
            answer, outcome = texts["refused"], ReplyOutcome.REFUSED
        elif reply.stop_reason == "max_tokens":
            answer, outcome = f"{answer}\n\n{texts['incomplete']}", ReplyOutcome.INCOMPLETE
        elif untraced:
            answer = f"{answer}\n\n{texts['unverified'].format(numbers=', '.join(untraced))}"
            outcome = ReplyOutcome.NOT_VERIFIED
        self._events.emit(
            EventName.AGENT_REPLIED,
            {"outcome": outcome.value, "tool_calls": "0", "untraced": str(len(untraced))},
        )
        return Narrative(answer, outcome, untraced if untraced else (), reply.model)

    # ------------------------------------------------------------------ insights

    def interpret(self, scope: OwnerScope, insight: Insight) -> Insight | None:
        """The reading of one stored insight, stored with the prompt and model that wrote
        it; ``None`` when the model refused or its numbers did not trace."""
        context, _language, conditions = self._context(scope)
        document = insight_json(insight)
        payload = json.dumps(document, ensure_ascii=False, sort_keys=True)
        request = (
            f"{context}\n<insight>{payload}</insight>\n"
            'Answer only with a JSON object with three keys: "interpretation" (what the '
            'pattern probably means for training, at most two sentences), "hypothesis" (a '
            'possible explanation, explicitly tentative, or null) and "recommendation" (a '
            "training action to consider, or null)."
        )
        reply, answer, untraced = self._ask(request, [*json_sources(document), context])
        reading = _reading(answer) if reply.stop_reason == "end_turn" else None
        if reading is None or untraced:
            outcome = ReplyOutcome.NOT_VERIFIED if untraced else ReplyOutcome.REFUSED
            self._events.emit(
                EventName.AGENT_REPLIED,
                {"outcome": outcome.value, "tool_calls": "0", "untraced": str(len(untraced))},
            )
            return None
        recommendation = None if conditions & QUIET else reading.recommendation
        stored = self._services.insights.interpret(
            scope,
            insight.insight_id,
            interpretation=reading.interpretation,
            hypothesis=reading.hypothesis,
            recommendation=recommendation,
            agent=AgentVersion(self._prompt.version, reply.model),
        )
        self._events.emit(
            EventName.AGENT_REPLIED,
            {"outcome": ReplyOutcome.ANSWERED.value, "tool_calls": "0", "untraced": "0"},
        )
        return stored


def _reading(answer: str) -> Reading | None:
    try:
        found = json.loads(_FENCE.sub("", answer.strip()))
    except ValueError:
        return None
    if not isinstance(found, dict) or set(found) != {
        "interpretation",
        "hypothesis",
        "recommendation",
    }:
        return None
    interpretation = found["interpretation"]
    hypothesis = found["hypothesis"]
    recommendation = found["recommendation"]
    if not isinstance(interpretation, str) or not interpretation.strip():
        return None
    if not all(item is None or isinstance(item, str) for item in (hypothesis, recommendation)):
        return None
    return Reading(
        interpretation.strip(),
        (hypothesis or "").strip() or None,
        (recommendation or "").strip() or None,
    )
