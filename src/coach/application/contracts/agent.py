"""AgentDecision and Conversation <-> their contract documents (agent spec 5 and 9)."""

from __future__ import annotations

from uuid import UUID

from coach.application.contracts._json import (
    SCHEMA_VERSION,
    Document,
    Json,
    evidence,
    instant,
    parse_evidence,
    parse_instant,
    parse_optional_uuid,
)
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


def agent_decision_to_contract(decision: AgentDecision) -> Json:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "decisionId": str(decision.decision_id),
        "athleteId": str(decision.athlete_id),
        "decidedAt": instant(decision.decided_at),
        "kind": decision.kind.value,
        "promptVersion": decision.prompt_version,
        "specVersion": decision.spec_version,
        "modelId": decision.model_id,
        "toolCalls": [
            {"name": call.name, "outcome": call.outcome.value} for call in decision.tool_calls
        ],
        "evidence": [evidence(item) for item in decision.evidence],
        "proposedAction": decision.proposed_action,
        "proposalId": None if decision.proposal_id is None else str(decision.proposal_id),
        "policyDecision": decision.policy_verdict.value,
        "policyReasons": list(decision.policy_reasons),
        "userFacingExplanation": decision.explanation,
        "conversationId": None
        if decision.conversation_id is None
        else str(decision.conversation_id),
        "version": decision.version,
    }


def agent_decision_from_contract(document: Document) -> AgentDecision:
    return AgentDecision(
        decision_id=UUID(document["decisionId"]),
        athlete_id=UUID(document["athleteId"]),
        decided_at=parse_instant(document["decidedAt"]),
        kind=DecisionKind(document["kind"]),
        prompt_version=document["promptVersion"],
        spec_version=document["specVersion"],
        model_id=document["modelId"],
        tool_calls=tuple(
            ToolCallRecord(item["name"], ToolOutcome(item["outcome"]))
            for item in document["toolCalls"]
        ),
        evidence=tuple(parse_evidence(item) for item in document["evidence"]),
        proposed_action=document["proposedAction"],
        proposal_id=parse_optional_uuid(document["proposalId"]),
        policy_verdict=PolicyVerdict(document["policyDecision"]),
        policy_reasons=tuple(document["policyReasons"]),
        explanation=document["userFacingExplanation"],
        conversation_id=parse_optional_uuid(document["conversationId"]),
        version=document["version"],
    )


def conversation_to_contract(conversation: Conversation) -> Json:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "conversationId": str(conversation.conversation_id),
        "athleteId": str(conversation.athlete_id),
        "startedAt": instant(conversation.started_at),
        "promptVersion": conversation.prompt_version,
        "toolsVersion": conversation.tools_version,
        "modelId": conversation.model_id,
        "turns": [
            {"role": turn.role.value, "at": instant(turn.at), "content": list(turn.content)}
            for turn in conversation.turns
        ],
        "version": conversation.version,
    }


def conversation_from_contract(document: Document) -> Conversation:
    return Conversation(
        conversation_id=UUID(document["conversationId"]),
        athlete_id=UUID(document["athleteId"]),
        started_at=parse_instant(document["startedAt"]),
        prompt_version=document["promptVersion"],
        tools_version=document["toolsVersion"],
        model_id=document["modelId"],
        turns=tuple(
            ConversationTurn(
                role=Role(item["role"]),
                content=tuple(item["content"]),
                at=parse_instant(item["at"]),
            )
            for item in document["turns"]
        ),
        version=document["version"],
    )
