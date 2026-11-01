"""The AI coach's records (agent spec 4, 5 and 7; FR-AGT-003, decisions D-14 and D-15).

``AgentDecision`` records a significant decision: a plan proposal, an adaptation, a load
reduction or a safety response, with the prompt and spec versions, the model, the tool calls
(name and outcome, never their inputs), the evidence, the action proposed, what the policy
engine decided and the explanation shown to the athlete. Private chain-of-thought is never
stored.

``Conversation`` keeps a chat as the provider exchanged it: each turn's content blocks exactly
as sent or received, appended only (an earlier turn is never edited, so the model's own
reasoning blocks stay valid when the history is sent again). User text is screened for
secrets before it becomes a turn. A conversation is bound to the prompt and tool versions it
started with; a change of either starts a new one.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any, Final, Self
from uuid import UUID

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.evidence import EvidenceRef
from coach.domain.common.text import clean_optional_text, clean_text
from coach.domain.common.time import ensure_utc

MAX_EXPLANATION: Final = 4000
MAX_TURNS: Final = 400  # a longer chat starts a new conversation
MAX_CONVERSATION_BYTES: Final = 2 * 1024 * 1024
_CODE: Final = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class DecisionKind(StrEnum):
    PLAN_PROPOSAL = "PLAN_PROPOSAL"
    ADAPTATION = "ADAPTATION"
    LOAD_REDUCTION = "LOAD_REDUCTION"
    SAFETY_RESPONSE = "SAFETY_RESPONSE"


class ToolOutcome(StrEnum):
    """A tool call's result: OK or the typed error of the tools contract (section 2)."""

    OK = "OK"
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    CONSENT_REQUIRED = "CONSENT_REQUIRED"
    POLICY_DENIED = "POLICY_DENIED"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    CONCURRENCY_CONFLICT = "CONCURRENCY_CONFLICT"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"


class PolicyVerdict(StrEnum):
    ALLOWED = "ALLOWED"
    DENIED = "DENIED"
    NOT_APPLICABLE = "NOT_APPLICABLE"  # no proposal reached the policy engine


def _check_version(value: str, name: str) -> None:
    parts = value.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise DomainValidationError(f"{name} is a semantic version")


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    name: str
    outcome: ToolOutcome

    def __post_init__(self) -> None:
        if not self.name or len(self.name) > 64 or not self.name.replace("_", "").isalnum():
            raise DomainValidationError("a tool name is a short identifier")


@dataclass(frozen=True, slots=True)
class AgentDecision:
    decision_id: UUID
    athlete_id: UUID
    decided_at: datetime
    kind: DecisionKind
    prompt_version: str
    spec_version: str
    model_id: str | None
    tool_calls: tuple[ToolCallRecord, ...]
    evidence: tuple[EvidenceRef, ...]
    proposed_action: str  # a code, for example PROPOSE_PLAN_VERSION or ACTIVATE_SAFETY_HOLD
    policy_verdict: PolicyVerdict
    explanation: str  # what the athlete was shown
    policy_reasons: tuple[str, ...] = ()
    proposal_id: UUID | None = None  # the plan version or proposal it created
    conversation_id: UUID | None = None
    version: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "decided_at", ensure_utc(self.decided_at))
        _check_version(self.prompt_version, "promptVersion")
        _check_version(self.spec_version, "specVersion")
        object.__setattr__(
            self, "model_id", clean_optional_text(self.model_id, max_length=100, field="model")
        )
        if not _CODE.match(self.proposed_action):
            raise DomainValidationError("a proposed action is a code")
        if not all(_CODE.match(code) for code in self.policy_reasons):
            raise DomainValidationError("policy reasons are codes")
        if self.policy_verdict is PolicyVerdict.DENIED and not self.policy_reasons:
            raise DomainValidationError("a denial states its reasons")
        object.__setattr__(
            self,
            "explanation",
            clean_text(self.explanation, max_length=MAX_EXPLANATION, field="explanation"),
        )

    @property
    def entity_id(self) -> UUID:
        return self.decision_id

    def with_version(self, version: int) -> Self:
        return replace(self, version=version)


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


def _json_block(block: Mapping[str, Any]) -> dict[str, Any]:
    """A content block as plain JSON data (a copy: the stored turn cannot change later)."""
    try:
        copied = json.loads(json.dumps(block, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError):
        raise DomainValidationError("a content block is JSON data") from None
    if not isinstance(copied, dict) or not isinstance(copied.get("type"), str):
        raise DomainValidationError("a content block is an object with a type")
    return copied


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    role: Role
    content: tuple[Mapping[str, Any], ...]
    at: datetime

    def __post_init__(self) -> None:
        if not self.content:
            raise DomainValidationError("a turn has content")
        object.__setattr__(self, "content", tuple(_json_block(item) for item in self.content))
        object.__setattr__(self, "at", ensure_utc(self.at))

    def size(self) -> int:
        return len(json.dumps(list(self.content), ensure_ascii=False).encode("utf-8"))


@dataclass(frozen=True, slots=True)
class Conversation:
    conversation_id: UUID
    athlete_id: UUID
    started_at: datetime
    prompt_version: str
    tools_version: str
    model_id: str
    turns: tuple[ConversationTurn, ...] = field(default_factory=tuple)
    version: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "started_at", ensure_utc(self.started_at))
        _check_version(self.prompt_version, "promptVersion")
        _check_version(self.tools_version, "toolsVersion")
        object.__setattr__(
            self, "model_id", clean_text(self.model_id, max_length=100, field="model")
        )
        if len(self.turns) > MAX_TURNS:
            raise DomainValidationError(f"a conversation has at most {MAX_TURNS} turns")
        if self.turns and self.turns[0].role is not Role.USER:
            raise DomainValidationError("a conversation starts with the athlete")
        if sum(turn.size() for turn in self.turns) > MAX_CONVERSATION_BYTES:
            raise DomainValidationError("the conversation is too long: start a new one")

    @property
    def entity_id(self) -> UUID:
        return self.conversation_id

    def with_version(self, version: int) -> Self:
        return replace(self, version=version)

    def appended(self, turns: Sequence[ConversationTurn]) -> Conversation:
        """The conversation with more turns at its end: earlier turns never change."""
        return replace(self, turns=(*self.turns, *turns))

    def fits(self, turns: Sequence[ConversationTurn]) -> bool:
        """Whether these turns can still be appended (else a new conversation starts)."""
        return (
            len(self.turns) + len(turns) <= MAX_TURNS
            and sum(turn.size() for turn in (*self.turns, *turns)) <= MAX_CONVERSATION_BYTES
        )

    def messages(self) -> list[dict[str, Any]]:
        """The history as the provider receives it, exactly as stored."""
        return [{"role": turn.role.value, "content": list(turn.content)} for turn in self.turns]

    def bound_to(self, prompt_version: str, tools_version: str, model_id: str) -> bool:
        """Whether a new turn may continue it: same prompt, tools and model (D-15)."""
        return (self.prompt_version, self.tools_version, self.model_id) == (
            prompt_version,
            tools_version,
            model_id,
        )
