"""The AI coach under failure: safety before anything can fail, consent revocation, broken
model output, tool failures and budgets (review of P13.1-P13.4; FR-AGT-001, FR-AGT-003,
FR-AGT-004, PRIV-CON-002, SAFE-MED-002). A scripted model; synthetic data only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_llm import MODEL, ScriptedLLM, answer, thinking, tools
from agent_world import AgentWorld, agent_world
from coach.agent.coach import MAX_TOOL_ROUNDS, NO_ANSWER, ReplyOutcome
from coach.agent.tools.base import Tool, Toolbox, ToolKind, schema
from coach.application.contracts import AGENT_DECISION_CODEC, CONVERSATION_CODEC
from coach.application.ids import new_id
from coach.application.llm import LLMFailure, LLMReply, LLMUnavailableError
from coach.application.safety import hold_notice
from coach.domain.agent import Conversation, DecisionKind
from coach.domain.coaching.state import Condition
from coach.domain.recovery.checkin import RedFlag
from coach.infrastructure.sqlite.aggregates import SqliteAggregateRepository
from file_samples import fit_file


def ready(tmp_path: Path) -> AgentWorld:
    world = agent_world(tmp_path)
    world.ok("consent", "grant", "ai-processing")
    world.ok("consent", "grant", "activities")
    path = tmp_path / "run.fit"
    path.write_bytes(fit_file())
    world.ok("import", str(path))
    return world


def held(world: AgentWorld) -> bool:
    return Condition.SAFETY_HOLD in world.runtime.conditions.current(world.scope).conditions


def conversations(world: AgentWorld) -> list[Conversation]:
    return SqliteAggregateRepository(world.runtime.database, CONVERSATION_CODEC).list_all(
        world.scope
    )


def well_formed(conversation: Conversation) -> None:
    uses = [
        block["id"]
        for turn in conversation.turns
        for block in turn.content
        if block["type"] == "tool_use"
    ]
    results = [
        block["tool_use_id"]
        for turn in conversation.turns
        for block in turn.content
        if block["type"] == "tool_result"
    ]
    assert uses == results
    assert conversation.turns[-1].role.value == "assistant"


@pytest.mark.req("SAFE-MED-002", "FR-AGT-003")
def test_red_flags_in_feedback_hold_training_even_when_the_rest_fails(tmp_path: Path) -> None:
    world = ready(tmp_path)
    failed = world.error(
        "recordSubjectiveFeedback",
        {"activityId": str(new_id()), "activityRpe": 6, "pain": 8, "painOnset": "SUDDEN"},
    )
    assert failed == {"code": "NOT_FOUND"}
    assert held(world)  # the check-in went first
    assert [item.kind for item in world.decisions] == [DecisionKind.SAFETY_RESPONSE]


@pytest.mark.req("SAFE-MED-002")
def test_red_flags_in_feedback_hold_training_without_a_timezone(tmp_path: Path) -> None:
    world = agent_world(tmp_path, onboard=False)
    refused = world.error("recordSubjectiveFeedback", {"notes": "me desmayé corriendo"})
    assert refused == {
        "code": "VALIDATION_ERROR",
        "reason": "CHECKIN_NOT_RECORDED",
        "redFlags": ["RF_FAINTING"],
        "safetyHold": True,
    }
    assert held(world)
    assert [item.kind for item in world.decisions] == [DecisionKind.SAFETY_RESPONSE]


@pytest.mark.req("SAFE-MED-002", "PRIV-CON-003")
def test_red_flags_hold_training_even_without_the_ai_consent(tmp_path: Path) -> None:
    world = agent_world(tmp_path)
    llm = ScriptedLLM()
    reply = world.coach(llm).reply(world.scope, "Tengo dolor en el pecho desde ayer")
    assert reply.outcome is ReplyOutcome.SAFETY_HOLD
    assert reply.text == hold_notice([RedFlag.RF_CHEST_PAIN], "es")
    assert reply.conversation_id is None  # nothing stored or sent without the consent
    assert llm.requests == []
    assert held(world)
    assert conversations(world) == []
    (decision,) = SqliteAggregateRepository(world.runtime.database, AGENT_DECISION_CODEC).list_all(
        world.scope
    )
    assert decision.kind is DecisionKind.SAFETY_RESPONSE


@pytest.mark.req("PRIV-CON-002", "PRIV-CON-003", "FR-AGT-003")
def test_a_revoked_consent_starts_a_new_conversation(tmp_path: Path) -> None:
    world = ready(tmp_path)
    first = world.coach(ScriptedLLM(tools(("getActivities", {})), answer("Listo."))).reply(
        world.scope, "¿Qué hice esta semana?"
    )
    world.ok("consent", "revoke", "activities")
    llm = ScriptedLLM(answer("Hola."))
    second = world.coach(llm).reply(world.scope, "Hola de nuevo")
    assert second.conversation_id != first.conversation_id
    (request,) = llm.requests
    assert len(request.messages) == 1  # the earlier activity data is not sent again
    assert "getActivities" not in json.dumps(list(request.messages))


@pytest.mark.req("FR-AGT-001", "FR-AGT-003")
def test_a_failing_tool_does_not_break_the_turn(tmp_path: Path) -> None:
    world = ready(tmp_path)

    def broken(*_args: Any) -> dict[str, Any]:
        raise RuntimeError("a synthetic bug with private detail 1234")

    toolbox = Toolbox(
        [Tool("getBroken", ToolKind.READ, "A broken tool.", schema(), broken)], events=world.events
    )
    output = toolbox.run("toolu_1", "getBroken", {}, _context(world))
    assert output.payload == {
        "ok": False,
        "error": {"code": "VALIDATION_ERROR", "reason": "TOOL_FAILED"},
    }
    assert "1234" not in output.block["content"]


def _context(world: AgentWorld) -> Any:
    from coach.agent.tools.base import ToolContext

    return ToolContext(world.scope, None)


@pytest.mark.req("SAFE-MED-002", "FR-AGT-003")
def test_a_model_failure_after_a_red_flag_keeps_the_notice_and_the_decision(tmp_path: Path) -> None:
    world = ready(tmp_path)
    llm = ScriptedLLM(
        tools(("flagSafetyConcern", {"redFlags": ["RF_BREATHING"]})),
        LLMUnavailableError(LLMFailure.UNAVAILABLE),
    )
    with pytest.raises(LLMUnavailableError) as raised:
        world.coach(llm).reply(
            world.scope, "Después de entrenar me sentí raro", principal=world.principal
        )
    assert raised.value.notices == (hold_notice([RedFlag.RF_BREATHING], "es"),)
    decisions = SqliteAggregateRepository(world.runtime.database, AGENT_DECISION_CODEC).list_all(
        world.scope
    )
    assert [item.kind for item in decisions] == [DecisionKind.SAFETY_RESPONSE]


@pytest.mark.req("FR-AGT-001")
def test_cut_off_tool_calls_and_empty_refusals_keep_the_history_valid(tmp_path: Path) -> None:
    world = ready(tmp_path)
    cut = LLMReply(
        content=(
            thinking(),
            {"type": "text", "text": "Voy a"},
            {"type": "tool_use", "id": "toolu_cut", "name": "getGoals", "input": {}},
        ),
        stop_reason="max_tokens",
        model=MODEL,
    )
    coach = world.coach(ScriptedLLM(cut, answer("Hola.")))
    reply = coach.reply(world.scope, "Contame mis objetivos")
    assert reply.outcome is ReplyOutcome.INCOMPLETE
    (stored,) = conversations(world)
    well_formed(stored)
    coach.reply(world.scope, "Hola")
    (longer,) = conversations(world)
    well_formed(longer)
    empty = LLMReply(content=(), stop_reason="refusal", model=MODEL)
    refused = world.coach(ScriptedLLM(empty)).reply(world.scope, "Otra cosa", new=True)
    assert refused.outcome is ReplyOutcome.REFUSED
    assert refused.text == "No puedo ayudarte con eso."
    last = next(
        item for item in conversations(world) if item.conversation_id == refused.conversation_id
    )
    assert last.turns[-1].content == ({"type": "text", "text": NO_ANSWER},)


@pytest.mark.req("FR-AGT-004")
def test_identifiers_and_instants_never_pass_as_data(tmp_path: Path) -> None:
    world = ready(tmp_path)
    run = world.data("getActivities")["activities"][0]["activityId"]
    digits = [
        token for token in run.replace("-", " ").split() if token.isdigit() and len(token) >= 2
    ]
    invented = digits[0] if digits else "8877"
    llm = ScriptedLLM(
        tools(("getActivityDetail", {"activityId": run})),
        answer(f"Tu cadencia fue {invented} pasos."),
        answer(f"Tu cadencia fue {invented} pasos."),
    )
    reply = world.coach(llm).reply(world.scope, "¿Cuál fue mi cadencia?")
    assert reply.outcome is ReplyOutcome.NOT_VERIFIED
    assert invented in reply.untraced


@pytest.mark.req("FR-AGT-004")
def test_an_incomplete_answer_is_checked_too(tmp_path: Path) -> None:
    world = ready(tmp_path)
    llm = ScriptedLLM(answer("Corriste 37 km y", stop_reason="max_tokens"))
    reply = world.coach(llm).reply(world.scope, "¿Cuánto corrí?")
    assert reply.outcome is ReplyOutcome.INCOMPLETE
    assert reply.untraced == ("37",)
    assert "no pude verificar estos números con tus datos: 37" in reply.text


@pytest.mark.req("FR-AGT-001")
def test_the_tool_budget_covers_the_whole_message(tmp_path: Path) -> None:
    world = ready(tmp_path)
    steps = [tools(("getGoals", {})) for _ in range(MAX_TOOL_ROUNDS)]
    steps.append(answer("Tu VO2max es 55."))  # untraced: one correction pass
    steps += [tools(("getGoals", {})) for _ in range(4)]
    reply = world.coach(ScriptedLLM(*steps)).reply(world.scope, "Revisá todo")
    assert len(reply.tool_calls) == MAX_TOOL_ROUNDS  # not 8 more in the correction pass
    (stored,) = conversations(world)
    well_formed(stored)


@pytest.mark.req("SEC-AUTHZ-003", "FR-AGT-003")
def test_the_coachs_writes_are_audited_as_the_agents(tmp_path: Path) -> None:
    world = ready(tmp_path)
    version = world.data("getAthleteProfile", {"sections": ["basics"]})["version"]
    world.data("updateAthleteProfile", {"expectedVersion": version, "heightCm": 178})
    world.data("saveGoal", {"sport": "RUNNING", "targetDate": "2027-04-01", "priority": "B"})
    actions = [
        (record.document["actor"], record.document["action"])
        for record in world.runtime.audit.events_for(world.scope)
    ]
    assert ("AGENT", "agent.profile_updated") in actions
    assert ("AGENT", "agent.goal_saved") in actions


@pytest.mark.req("FR-PRF-005")
def test_a_future_period_can_start_from_its_first_day(tmp_path: Path) -> None:
    world = ready(tmp_path)
    days = world.data("getAvailability", {"from": "2026-11-02"})["days"]
    assert days[0]["day"] == "2026-11-02"
    assert len(days) == 14
