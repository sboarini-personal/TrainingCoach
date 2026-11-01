"""AgentDecision and Conversation documents: lossless round trips and canonical examples
(agent spec 5 and 9; NFR-REL-004). Synthetic data only."""

from __future__ import annotations

from typing import Any

import pytest

from agent_samples import THINKING, conversation, decision, turn
from coach.application.contracts import AGENT_DECISION_CODEC, CONVERSATION_CODEC, DocumentCodec
from coach.domain.agent import Role
from schemas import EXAMPLES_DIR, load_json, validation_errors

CASES: list[tuple[DocumentCodec[Any], Any, str]] = [
    (AGENT_DECISION_CODEC, decision(version=1), "agent-decision"),
    (
        CONVERSATION_CODEC,
        conversation(
            turn(Role.USER, {"type": "text", "text": "¿Cómo vengo?"}),
            turn(
                Role.ASSISTANT,
                THINKING,
                {"type": "tool_use", "id": "toolu_1", "name": "getGoals", "input": {}},
                minutes=1,
            ),
            turn(
                Role.USER,
                {"type": "tool_result", "tool_use_id": "toolu_1", "content": "{}"},
                minutes=1,
            ),
        ).with_version(1),
        "conversation",
    ),
]


@pytest.mark.req("FR-AGT-003", "NFR-REL-004")
@pytest.mark.parametrize(("codec", "entity", "schema"), CASES, ids=[item[2] for item in CASES])
def test_records_round_trip_through_their_contract(
    codec: DocumentCodec[Any], entity: Any, schema: str
) -> None:
    document = codec.dump(entity)
    assert validation_errors(schema, document) == []
    assert codec.load(document) == entity


@pytest.mark.req("FR-AGT-003", "NFR-REL-004")
@pytest.mark.parametrize(
    ("codec", "example"),
    [
        (AGENT_DECISION_CODEC, "agent-decision.example.json"),
        (CONVERSATION_CODEC, "conversation.example.json"),
    ],
    ids=["agent-decision", "conversation"],
)
def test_the_examples_load_and_dump_unchanged(codec: DocumentCodec[Any], example: str) -> None:
    document = load_json(EXAMPLES_DIR / example)
    assert codec.dump(codec.load(document)) == document
