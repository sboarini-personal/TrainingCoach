"""Anthropic Claude behind the ``LLMProvider`` port (ADR-018; decision D-14).

The default model is Claude Opus 5.5 (``claude-opus-5-5``); model and effort are
configuration (``COACH_LLM_MODEL``, ``COACH_LLM_EFFORT``). The request:

- streams the answer (long turns cannot hit an HTTP timeout) and hands its text to the caller
  as it arrives;
- leaves thinking at the model's default (adaptive, its text omitted: no reasoning is ever
  shown or stored, agent spec 5) and sets the effort explicitly;
- marks the stable prefix (system prompt and tools) for prompt caching;
- enables Anthropic's server-side fallback for a declined request (``fallbacks: "default"``):
  the answering model is reported and recorded;
- returns the content blocks unchanged, as JSON, so the conversation can be replayed exactly.

The API key is read from the OS secret store when a reply is needed and given only to the SDK
client; it never reaches the agent, a prompt, a log or an error (ADR-001, SEC-SEC-002).
Errors are mapped to codes without their details.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

import anthropic

from coach.application.llm import (
    LLMFailure,
    LLMReply,
    LLMRequest,
    LLMUnavailableError,
    LLMUsage,
)
from coach.application.secrets import SecretValue

DEFAULT_MODEL: Final = "claude-opus-5-5"
DEFAULT_EFFORT: Final = "medium"
EFFORTS: Final = frozenset({"low", "medium", "high", "xhigh", "max"})
MAX_TOKENS: Final = 32000
FALLBACK_BETA: Final = "server-side-fallback-2026-07-01"

type ClientFactory = Callable[[str], Any]


def _client(api_key: str) -> Any:
    return anthropic.Anthropic(api_key=api_key, max_retries=2)


class AnthropicProvider:
    def __init__(
        self,
        api_key: Callable[[], SecretValue | None],
        *,
        model: str = DEFAULT_MODEL,
        effort: str = DEFAULT_EFFORT,
        max_tokens: int = MAX_TOKENS,
        client_factory: ClientFactory = _client,
    ) -> None:
        if effort not in EFFORTS:
            raise ValueError(f"effort is one of {sorted(EFFORTS)}")
        self._api_key = api_key
        self._model = model
        self._effort = effort
        self._max_tokens = max_tokens
        self._client_factory = client_factory

    @property
    def model_id(self) -> str:
        return self._model

    def reply(
        self, request: LLMRequest, *, on_text: Callable[[str], None] | None = None
    ) -> LLMReply:
        key = self._api_key()
        if key is None:
            raise LLMUnavailableError(LLMFailure.NO_API_KEY)
        client = self._client_factory(key.reveal())
        try:
            with client.beta.messages.stream(
                model=self._model,
                max_tokens=self._max_tokens,
                system=request.system,
                tools=[
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "input_schema": dict(tool.input_schema),
                    }
                    for tool in request.tools
                ],
                messages=[dict(message) for message in request.messages],
                output_config={"effort": self._effort},
                cache_control={"type": "ephemeral"},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            ) as stream:
                if on_text is not None:
                    for text in stream.text_stream:
                        on_text(text)
                final = stream.get_final_message()
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
            raise LLMUnavailableError(LLMFailure.AUTHENTICATION) from None
        except anthropic.RateLimitError:
            raise LLMUnavailableError(LLMFailure.RATE_LIMITED) from None
        except (
            anthropic.BadRequestError,
            anthropic.NotFoundError,
            anthropic.UnprocessableEntityError,
            anthropic.RequestTooLargeError,
        ):
            raise LLMUnavailableError(LLMFailure.REQUEST_INVALID) from None
        except (anthropic.APIStatusError, anthropic.APIConnectionError):
            raise LLMUnavailableError(LLMFailure.UNAVAILABLE) from None
        usage = final.usage
        return LLMReply(
            content=tuple(block.to_dict(mode="json") for block in final.content),
            stop_reason=final.stop_reason or "end_turn",
            model=final.model,
            usage=LLMUsage(
                input_tokens=usage.input_tokens or 0,
                output_tokens=usage.output_tokens or 0,
                cache_read_input_tokens=usage.cache_read_input_tokens or 0,
                cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
            ),
        )
