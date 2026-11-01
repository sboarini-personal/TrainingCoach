"""A scripted stand-in for the LLM provider: it answers with the replies it was given, in
order, and keeps every request, so tests can see exactly what would be sent. Nothing leaves
the process (decision D-16: agent tests are simulated)."""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable
from typing import Any, Final

from coach.application.llm import LLMReply, LLMRequest, LLMUsage

MODEL: Final = "claude-opus-5-5"
_IDS = itertools.count(1)

type Step = LLMReply | Exception | Callable[[LLMRequest], LLMReply]


def thinking() -> dict[str, Any]:
    """A reasoning block as the provider returns it: its text omitted, a signature kept."""
    return {"type": "thinking", "thinking": "", "signature": "synthetic-signature"}


def answer(text: str, *, stop_reason: str = "end_turn") -> LLMReply:
    return LLMReply(
        content=(thinking(), {"type": "text", "text": text}),
        stop_reason=stop_reason,
        model=MODEL,
        usage=LLMUsage(input_tokens=10, output_tokens=5),
    )


def tools(*calls: tuple[str, dict[str, Any]]) -> LLMReply:
    uses = tuple(
        {"type": "tool_use", "id": f"toolu_{next(_IDS):04d}", "name": name, "input": arguments}
        for name, arguments in calls
    )
    return LLMReply(content=(thinking(), *uses), stop_reason="tool_use", model=MODEL)


class ScriptedLLM:
    def __init__(self, *steps: Step, model_id: str = MODEL) -> None:
        self._steps = list(steps)
        self._model_id = model_id
        self.requests: list[LLMRequest] = []

    @property
    def model_id(self) -> str:
        return self._model_id

    def reply(
        self, request: LLMRequest, *, on_text: Callable[[str], None] | None = None
    ) -> LLMReply:
        self.requests.append(request)
        if not self._steps:
            raise AssertionError("the model was asked more often than scripted")
        step = self._steps.pop(0)
        if isinstance(step, Exception):
            raise step
        found = step(request) if callable(step) else step
        if on_text is not None:
            on_text(found.text())
        return found

    def sent(self) -> str:
        """Everything the requests carried, as one text (system, tools and messages)."""
        return "\n".join(
            json.dumps(
                {
                    "system": request.system,
                    "tools": [tool.name for tool in request.tools],
                    "messages": list(request.messages),
                },
                ensure_ascii=False,
            )
            for request in self.requests
        )
