"""The Anthropic Claude adapter of the LLMProvider port (ADR-018, ADR-001; decision D-14).

Nothing contacts Anthropic: a fake client records the request and replays a message or an
SDK error. The planted API key must never leave the adapter except into the client factory.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import anthropic
import httpx2
import pytest
from anthropic.types.beta import BetaMessage

from coach.application.llm import (
    LLMFailure,
    LLMRequest,
    LLMUnavailableError,
    ToolSpec,
)
from coach.application.secrets import SecretValue
from coach.integrations.llm.anthropic_provider import (
    DEFAULT_MODEL,
    FALLBACK_BETA,
    AnthropicProvider,
)

API_KEY = "sk-ant-api03-synthetic-key-for-adapter-tests-000000000000"  # pragma: allowlist secret

REQUEST = LLMRequest(
    system="You are the coach.",
    tools=(
        ToolSpec(
            "getGoals",
            "The athlete's goals.",
            {"type": "object", "properties": {}, "additionalProperties": False},
        ),
    ),
    messages=({"role": "user", "content": [{"type": "text", "text": "Hola"}]},),
)

MESSAGE = BetaMessage.model_validate(
    {
        "id": "msg_synthetic",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": [
            {"type": "thinking", "thinking": "", "signature": "synthetic-signature"},
            {"type": "text", "text": "Tu semana "},
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "getGoals",
                "input": {},
            },
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {
            "input_tokens": 120,
            "output_tokens": 40,
            "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 0,
        },
    }
)


class FakeStream:
    def __init__(self, texts: list[str]) -> None:
        self._texts = texts

    def __enter__(self) -> FakeStream:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    @property
    def text_stream(self) -> Iterator[str]:
        return iter(self._texts)

    def get_final_message(self) -> BetaMessage:
        return MESSAGE


class FakeClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error = error
        self.beta = self
        self.messages = self

    def stream(self, **kwargs: Any) -> FakeStream:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return FakeStream(["Tu ", "semana "])


def provider(client: FakeClient, keys: list[str]) -> AnthropicProvider:
    def factory(api_key: str) -> FakeClient:
        keys.append(api_key)
        return client

    return AnthropicProvider(lambda: SecretValue(API_KEY), client_factory=factory)


@pytest.mark.req("FR-AGT-001", "SEC-SEC-002")
def test_a_request_streams_with_caching_effort_and_fallback() -> None:
    client, keys = FakeClient(), list[str]()
    texts: list[str] = []
    reply = provider(client, keys).reply(REQUEST, on_text=texts.append)
    (call,) = client.calls
    assert keys == [API_KEY]  # the key goes to the SDK client only
    assert (call["model"], call["system"], call["output_config"]) == (
        DEFAULT_MODEL,
        "You are the coach.",
        {"effort": "medium"},
    )
    assert call["cache_control"] == {"type": "ephemeral"}  # the stable prefix is cached
    assert (call["betas"], call["fallbacks"]) == ([FALLBACK_BETA], "default")
    assert "thinking" not in call  # the model's default: adaptive, its text omitted
    assert call["tools"] == [
        {
            "name": "getGoals",
            "description": "The athlete's goals.",
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        }
    ]
    assert API_KEY not in repr(call)
    assert texts == ["Tu ", "semana "]
    assert reply.stop_reason == "tool_use"
    assert reply.content[0] == {
        "type": "thinking",
        "thinking": "",
        "signature": "synthetic-signature",
    }
    assert reply.tool_uses() == [
        {"type": "tool_use", "id": "toolu_1", "name": "getGoals", "input": {}}
    ]
    assert (reply.usage.input_tokens, reply.usage.cache_read_input_tokens) == (120, 100)


def _response(status: int) -> httpx2.Response:
    return httpx2.Response(status, request=httpx2.Request("POST", "https://api.anthropic.com"))


ERRORS = [
    (
        anthropic.AuthenticationError("x", response=_response(401), body=None),
        LLMFailure.AUTHENTICATION,
    ),
    (anthropic.RateLimitError("x", response=_response(429), body=None), LLMFailure.RATE_LIMITED),
    (
        anthropic.BadRequestError("x", response=_response(400), body=None),
        LLMFailure.REQUEST_INVALID,
    ),
    (
        anthropic.InternalServerError("x", response=_response(500), body=None),
        LLMFailure.UNAVAILABLE,
    ),
    (
        anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com")),
        LLMFailure.UNAVAILABLE,
    ),
]


@pytest.mark.req("SEC-SEC-002", "NFR-REL-001")
@pytest.mark.parametrize(
    ("error", "failure"), ERRORS, ids=[item[1].value + str(n) for n, item in enumerate(ERRORS)]
)
def test_sdk_errors_become_codes_without_details(error: Exception, failure: LLMFailure) -> None:
    with pytest.raises(LLMUnavailableError) as raised:
        provider(FakeClient(error), []).reply(REQUEST)
    assert raised.value.failure is failure
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__
    assert API_KEY not in str(raised.value)


@pytest.mark.req("SEC-SEC-002", "PRIV-CON-003")
def test_without_a_key_nothing_is_sent() -> None:
    client = FakeClient()
    adapter = AnthropicProvider(lambda: None, client_factory=lambda _key: client)
    with pytest.raises(LLMUnavailableError) as raised:
        adapter.reply(REQUEST)
    assert raised.value.failure is LLMFailure.NO_API_KEY
    assert client.calls == []
    with pytest.raises(ValueError, match="effort is one of"):
        AnthropicProvider(lambda: None, effort="extreme")


@pytest.mark.req("FR-AGT-004")
def test_a_request_without_tools_sends_no_tools_field() -> None:
    client, keys = FakeClient(), list[str]()
    provider(client, keys).reply(replace(REQUEST, tools=()))
    (call,) = client.calls
    assert "tools" not in call  # a narrative is written from the data it was given
