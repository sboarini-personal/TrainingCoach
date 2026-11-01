"""The LLM provider port (ADR-018; agent spec 7).

The coach talks to the model through this port only. Messages and content blocks are plain
JSON in the Messages API shape, so a conversation can be stored and sent again exactly as the
model returned it (D-15: append-only history; the model's own reasoning blocks stay valid
only when replayed unchanged). The default adapter (``coach.integrations.llm``) targets
Anthropic Claude; the model ID is configuration and is recorded on every decision. The
adapter reads the API key from the secret store itself: no key ever passes through this port,
the agent or a prompt (ADR-001, SEC-SEC-002).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from coach.application.errors import ApplicationError

type Block = Mapping[str, Any]
type Message = Mapping[str, Any]  # {"role": "user" | "assistant", "content": [Block, ...]}


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A tool as the model sees it: name, description and JSON Schema of its input."""

    name: str
    description: str
    input_schema: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class LLMRequest:
    system: str
    tools: tuple[ToolSpec, ...]
    messages: tuple[Message, ...]


@dataclass(frozen=True, slots=True)
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass(frozen=True, slots=True)
class LLMReply:
    """The model's turn: its content blocks unchanged, why it stopped and who answered."""

    content: tuple[Block, ...]
    stop_reason: str  # end_turn, tool_use, max_tokens, refusal, pause_turn, ...
    model: str
    usage: LLMUsage = field(default_factory=LLMUsage)

    def text(self) -> str:
        return "".join(
            str(block.get("text", "")) for block in self.content if block.get("type") == "text"
        )

    def tool_uses(self) -> list[Block]:
        return [block for block in self.content if block.get("type") == "tool_use"]


class LLMFailure(StrEnum):
    NO_API_KEY = "NO_API_KEY"  # pragma: allowlist secret
    AUTHENTICATION = "AUTHENTICATION"
    RATE_LIMITED = "RATE_LIMITED"
    UNAVAILABLE = "UNAVAILABLE"
    REQUEST_INVALID = "REQUEST_INVALID"


class LLMUnavailableError(ApplicationError):
    """The model could not answer; the deterministic product keeps working (agent spec 7)."""

    def __init__(self, failure: LLMFailure) -> None:
        super().__init__(f"the AI coach is not available now ({failure.value})")
        self.failure = failure


class LLMProvider(Protocol):
    @property
    def model_id(self) -> str: ...

    def reply(
        self, request: LLMRequest, *, on_text: Callable[[str], None] | None = None
    ) -> LLMReply:
        """The model's next turn; ``on_text`` receives its text as it arrives.

        Raises ``LLMUnavailableError``; never returns or logs the API key."""
        ...


def tool_results(results: Sequence[Block]) -> Message:
    """Every tool result of one model turn in a single user message (parallel tool use)."""
    return {"role": "user", "content": list(results)}
