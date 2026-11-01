"""The AI coach's records: AgentDecision and Conversation (agent spec 5 and 9; FR-AGT-003,
decision D-15). Synthetic data only."""

from __future__ import annotations

import pytest

from agent_samples import conversation, decision, turn
from coach.domain.agent import (
    MAX_TURNS,
    PolicyVerdict,
    Role,
    ToolCallRecord,
    ToolOutcome,
)
from coach.domain.common.errors import DomainValidationError


@pytest.mark.req("FR-AGT-003")
def test_a_decision_records_codes_and_the_explanation_never_reasoning() -> None:
    made = decision(explanation="  I proposed\x00 version 3.  ")
    assert made.explanation == "I proposed version 3."  # control characters removed
    for broken in (
        {"proposed_action": "propose plan"},  # a code, not text
        {"policy_verdict": PolicyVerdict.DENIED},  # a denial states its reasons
        {"policy_reasons": ("not a code",)},
        {"prompt_version": "v1"},
        {"explanation": " "},
    ):
        with pytest.raises(DomainValidationError):
            decision(**broken)
    denied = decision(policy_verdict=PolicyVerdict.DENIED, policy_reasons=("SAFETY_HOLD_ACTIVE",))
    assert denied.policy_reasons == ("SAFETY_HOLD_ACTIVE",)
    with pytest.raises(DomainValidationError):
        ToolCallRecord("drop table;", ToolOutcome.OK)


@pytest.mark.req("FR-AGT-003")
def test_a_conversation_is_append_only_and_replays_exactly() -> None:
    thinking = {"type": "thinking", "thinking": "", "signature": "synthetic-signature"}
    first = conversation(turn(Role.USER, {"type": "text", "text": "Hola"}))
    longer = first.appended([turn(Role.ASSISTANT, thinking, {"type": "text", "text": "Hola!"})])
    assert first.turns != longer.turns  # a new value; the earlier turns stay as they were
    assert longer.messages() == [
        {"role": "user", "content": [{"type": "text", "text": "Hola"}]},
        {"role": "assistant", "content": [thinking, {"type": "text", "text": "Hola!"}]},
    ]
    source = {"type": "text", "text": "Hola"}
    stored = turn(Role.USER, source)
    source["text"] = "changed"  # the caller's dict changes later
    assert stored.content[0]["text"] == "Hola"  # the stored turn does not
    assert longer.bound_to("1.0.0", "1.0.0", "claude-opus-5-5")
    assert not longer.bound_to("1.1.0", "1.0.0", "claude-opus-5-5")  # a new prompt: new chat


@pytest.mark.req("FR-AGT-003")
def test_a_conversation_has_limits_and_starts_with_the_athlete() -> None:
    with pytest.raises(DomainValidationError):
        conversation(turn(Role.ASSISTANT, {"type": "text", "text": "Hola"}))
    with pytest.raises(DomainValidationError):
        turn(Role.USER, {"text": "no type"})
    with pytest.raises(DomainValidationError):
        turn(Role.USER, {"type": "text", "text": float("nan")})  # not JSON
    one = turn(Role.USER, {"type": "text", "text": "x"})
    full = conversation(*([one] * MAX_TURNS))
    assert not full.fits([one])  # a longer chat starts a new conversation
    with pytest.raises(DomainValidationError):
        full.appended([one])
    huge = turn(Role.USER, {"type": "text", "text": "x" * (3 * 1024 * 1024)})
    assert not conversation().fits([huge])
