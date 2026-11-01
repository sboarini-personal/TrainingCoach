"""Synthetic AI coach records for the agent tests (agent spec 5 and 9)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from coach.domain.agent import (
    AgentDecision,
    Conversation,
    ConversationTurn,
    DecisionKind,
    PolicyVerdict,
    Role,
    ToolCallRecord,
    ToolOutcome,
)
from coach.domain.common.evidence import EvidenceKind, EvidenceRef
from samples import ATHLETE

AT = datetime(2026, 11, 1, 18, 0, tzinfo=UTC)
THINKING: Mapping[str, Any] = {"type": "thinking", "thinking": "", "signature": "synthetic-sig"}


def decision(**changes: Any) -> AgentDecision:
    made = AgentDecision(
        decision_id=UUID(int=0xC01),
        athlete_id=ATHLETE,
        decided_at=AT,
        kind=DecisionKind.PLAN_PROPOSAL,
        prompt_version="1.0.0",
        spec_version="1.1.0",
        model_id="claude-opus-5-5",
        tool_calls=(ToolCallRecord("proposeTrainingPlan", ToolOutcome.OK),),
        evidence=(EvidenceRef(EvidenceKind.PLAN_VERSION, UUID(int=0x502)),),
        proposed_action="PROPOSE_PLAN_VERSION",
        policy_verdict=PolicyVerdict.ALLOWED,
        explanation="I proposed version 3 of your plan.",
    )
    return replace(made, **changes)


def turn(role: Role, *blocks: Mapping[str, Any], minutes: int = 0) -> ConversationTurn:
    return ConversationTurn(role, tuple(blocks), AT + timedelta(minutes=minutes))


def conversation(*turns: ConversationTurn) -> Conversation:
    return Conversation(
        conversation_id=UUID(int=0xD01),
        athlete_id=ATHLETE,
        started_at=AT,
        prompt_version="1.0.0",
        tools_version="1.0.0",
        model_id="claude-opus-5-5",
        turns=turns,
    )
