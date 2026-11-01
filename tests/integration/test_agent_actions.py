"""The coach's tools that change something (P13.4; tools contract; FR-AGT-001, FR-AGT-003,
FR-POL-002, SEC-AUTHZ-002, SEC-AUTHZ-003, SAFE-MED-002). The coach asks and the code decides.
Nothing reaches a real model or provider. Synthetic data only."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from agent_llm import ScriptedLLM, answer, tools
from agent_world import AgentWorld, agent_world
from coach.agent.coach import UNEXPLAINED, ReplyOutcome
from coach.agent.tools.base import ToolKind
from coach.application.contracts import AGENT_DECISION_CODEC, CONVERSATION_CODEC
from coach.application.llm import LLMFailure, LLMUnavailableError
from coach.application.safety import hold_notice
from coach.domain.agent import AgentDecision, DecisionKind, PolicyVerdict
from coach.domain.coaching.state import Condition
from coach.domain.plans.plan import PlanStatus
from coach.domain.recovery.checkin import RedFlag
from coach.infrastructure.sqlite.aggregates import SqliteAggregateRepository
from file_samples import fit_file


def with_goal(tmp_path: Path) -> tuple[AgentWorld, str]:
    world = agent_world(tmp_path)
    world.ok("consent", "grant", "activities")
    world.ok("consent", "grant", "ai-processing")
    path = tmp_path / "run.fit"
    path.write_bytes(fit_file())
    world.ok("import", str(path))
    added = world.ok(
        "goal", "add", "--sport", "running", "--date", "2027-03-14", "--distance-km", "10"
    )
    goal_id = added.strip().rsplit(" ", 1)[-1]
    world.ok("goal", "activate", goal_id)
    return world, goal_id


def decisions(world: AgentWorld) -> list[AgentDecision]:
    return SqliteAggregateRepository(world.runtime.database, AGENT_DECISION_CODEC).list_all(
        world.scope
    )


def held(world: AgentWorld) -> bool:
    return Condition.SAFETY_HOLD in world.runtime.conditions.current(world.scope).conditions


@pytest.mark.req("FR-REC-001", "SAFE-MED-002", "FR-AGT-001")
def test_feedback_records_a_check_in_and_an_activity_rpe_and_red_flags_hold(tmp_path: Path) -> None:
    world, _goal = with_goal(tmp_path)
    run = world.data("getActivities")["activities"][0]["activityId"]
    found = world.data(
        "recordSubjectiveFeedback",
        {
            "fatigue": 4,
            "sleepQuality": 2,
            "notes": "Dormí poco",
            "activityId": run,
            "activityRpe": 6,
        },
    )
    assert found["checkin"]["redFlags"] == []
    assert found["checkin"]["safetyHold"] is False
    assert found["activity"]["rpe"]["value"] == 6
    (checkin,) = world.data("getDailyCheckIn")["checkins"]
    assert (checkin["fatigue"]["value"], checkin["sleepQuality"]["value"]) == (4, 2)
    detail = world.data("getActivityDetail", {"activityId": run})
    assert detail["fields"]["subjective.rpe"]["value"] == 6
    assert "subjective.rpe" in detail["correctedFields"]
    assert not held(world)
    flagged = world.data("recordSubjectiveFeedback", {"notes": "Casi me desmayo en la cuesta"})
    assert flagged["checkin"]["redFlags"] == ["RF_FAINTING"]
    assert held(world)
    assert [item.kind for item in world.decisions] == [DecisionKind.SAFETY_RESPONSE]
    alone = {"code": "VALIDATION_ERROR", "reason": "ACTIVITY_ID_AND_RPE_TOGETHER"}
    assert world.error("recordSubjectiveFeedback", {"activityRpe": 5}) == alone
    assert world.error("recordSubjectiveFeedback", {})["reason"] == "INVALID_INPUT"


@pytest.mark.req("SAFE-MED-002", "SAFE-MED-003")
@pytest.mark.acceptance("AT-SAFE-001")
def test_the_model_can_flag_a_red_flag_and_only_the_athlete_releases_it(tmp_path: Path) -> None:
    world, _goal = with_goal(tmp_path)
    flagged = world.data("flagSafetyConcern", {"redFlags": ["RF_PALPITATIONS"]})
    assert flagged == {"safetyHold": True, "redFlags": ["RF_PALPITATIONS"]}
    assert held(world)
    assert not any(tool.name.casefold().startswith("release") for tool in world.toolbox.tools)
    assert (
        world.error("flagSafetyConcern", {"redFlags": ["NOT_A_FLAG"]})["reason"] == "INVALID_INPUT"
    )


@pytest.mark.req("SEC-AUTHZ-003", "FR-PRF-001")
def test_profile_changes_are_allow_listed_and_versioned(tmp_path: Path) -> None:
    world, _goal = with_goal(tmp_path)
    version = world.data("getAthleteProfile", {"sections": ["physiology"]})["version"]
    changed = world.data("updateAthleteProfile", {"expectedVersion": version, "restingHr": 52})
    assert changed == {"version": version + 1, "changed": ["restingHr"]}
    assert world.data("getAthleteProfile", {"sections": ["physiology"]})["restingHr"]["value"] == 52
    stale = world.error("updateAthleteProfile", {"expectedVersion": version, "maxHr": 190})
    assert stale == {"code": "CONCURRENCY_CONFLICT", "currentVersion": version + 1}
    for forbidden in (
        {"weightKg": 70},
        {"displayName": "x"},
        {"birthDate": "1990-01-01"},
        {"health": "none"},
    ):
        attempt = {"expectedVersion": version + 1, **forbidden}
        assert world.error("updateAthleteProfile", attempt)["reason"] == "INVALID_INPUT"
    assert (
        world.error("updateAthleteProfile", {"expectedVersion": version + 1})["reason"]
        == "INVALID_INPUT"
    )


@pytest.mark.req("FR-GOL-001", "FR-GOL-002")
def test_a_goal_is_saved_as_a_draft_for_the_athlete_to_activate(tmp_path: Path) -> None:
    world, _goal = with_goal(tmp_path)
    saved = world.data(
        "saveGoal",
        {"sport": "CYCLING", "targetDate": "2027-05-01", "priority": "B", "distanceMeters": 100000},
    )
    goal = saved["goal"]
    assert (goal["status"], goal["sport"], goal["priority"]) == ("DRAFT", "CYCLING", "B")
    assert saved["activateWith"] == f"coach goal activate {goal['goalId']}"
    assert goal["goalId"] in {item["goalId"] for item in world.data("getGoals")["goals"]}


@pytest.mark.req("FR-POL-002", "FR-PLN-002", "FR-AGT-003")
def test_plans_are_requested_from_the_rules_and_never_activated_by_the_coach(
    tmp_path: Path,
) -> None:
    world, goal_id = with_goal(tmp_path)
    assert world.error("proposeTrainingPlan", {"mode": "ADAPT"}) == {
        "code": "VALIDATION_ERROR",
        "reason": "NO_ACTIVE_PLAN",
    }
    proposed = world.data("proposeTrainingPlan", {"mode": "GENERATE"})
    assert (proposed["number"], proposed["status"]) == (1, PlanStatus.PROPOSED.value)
    assert proposed["activateWith"].startswith(
        f"coach plan activate {proposed['planId']} 1 --revision "
    )
    (pending,) = world.decisions
    assert (pending.kind, pending.verdict, pending.action) == (
        DecisionKind.PLAN_PROPOSAL,
        PolicyVerdict.ALLOWED,
        "PROPOSE_PLAN_VERSION",
    )
    assert world.runtime.plans.active(world.scope) is None  # proposed, not active
    world.ok(*proposed["activateWith"].split()[1:])  # the athlete activates it
    adapted = world.data("proposeTrainingPlan", {"mode": "ADAPT"})
    assert adapted["decision"] in {
        "MAINTAIN",
        "PROGRESS",
        "REDUCE",
        "RESCHEDULE",
        "REPLACE",
        "REST",
    }
    assert world.decisions[-1].kind in {DecisionKind.ADAPTATION, DecisionKind.LOAD_REDUCTION}
    assert UUID(goal_id)


@pytest.mark.req("FR-ONB-003")
def test_a_plan_is_not_proposed_before_the_athlete_is_ready(tmp_path: Path) -> None:
    world = agent_world(tmp_path, onboard=False)
    world.ok("profile", "timezone", "America/Argentina/Buenos_Aires")
    refused = world.error("proposeTrainingPlan", {"mode": "GENERATE"})
    assert refused["code"] == "VALIDATION_ERROR"
    assert refused["reason"] == "NOT_READY"
    assert "PRIMARY_GOAL" in refused["missing"]


@pytest.mark.req("SEC-AUTHZ-002", "INT-GAR-002", "INT-GAR-003")
@pytest.mark.acceptance("AT-GAR-001")
def test_external_actions_stop_before_any_effect(tmp_path: Path) -> None:
    world, _goal = with_goal(tmp_path)
    externals = {
        tool.name for tool in world.toolbox.tools if tool.kind is ToolKind.EXECUTE_EXTERNAL
    }
    assert externals == {"publishGarminWorkout", "requestDataDeletion"}
    publish = world.error(
        "publishGarminWorkout", {"proposalId": "01a19829-4981-7257-9222-0ae779a94e1f"}
    )
    assert publish == {"code": "CAPABILITY_UNAVAILABLE", "capability": "trainingPublish"}
    deletion = world.error("requestDataDeletion", {"scope": "ALL"})
    assert deletion == {
        "code": "CONFIRMATION_REQUIRED",
        "scope": "ALL",
        "confirmedBy": "ATHLETE_ONLY",
        "deletionWorkflow": "NOT_AVAILABLE_YET",
    }
    assert world.data("getActivities")["count"] == 1  # nothing was deleted
    proposed = world.data("proposeTrainingPlan", {"mode": "GENERATE"})
    world.ok(*proposed["activateWith"].split()[1:])
    plan = world.data("getTrainingPlan")["version"]
    workout = plan["weeks"][0]["workouts"][0]["workoutId"]
    local = world.data("proposeGarminWorkout", {"plannedWorkoutId": workout})
    assert (local["publishable"], local["capability"]["value"], local["stays"]) == (
        False,
        "UNKNOWN",
        "LOCAL",
    )


@pytest.mark.req("INT-GAR-001", "INT-GAR-003")
def test_a_sync_without_a_connected_provider_says_so(tmp_path: Path) -> None:
    world, _goal = with_goal(tmp_path)
    assert world.error("requestProviderSync") == {
        "code": "PROVIDER_UNAVAILABLE",
        "reason": "NOT_CONNECTED",
    }


@pytest.mark.req("FR-AGT-003", "SAFE-MED-003")
def test_the_pipeline_records_decisions_with_what_the_athlete_saw(tmp_path: Path) -> None:
    world, _goal = with_goal(tmp_path)
    llm = ScriptedLLM(
        tools(("proposeTrainingPlan", {"mode": "GENERATE"})),
        answer("Te propuse una versión nueva del plan; revisala y activala si te parece bien."),
    )
    reply = world.coach(llm).reply(world.scope, "Armame un plan", principal=world.principal)
    assert reply.outcome is ReplyOutcome.ANSWERED
    (decision,) = decisions(world)
    assert decision.decision_id == reply.decision_id
    assert decision.kind is DecisionKind.PLAN_PROPOSAL
    assert decision.policy_verdict is PolicyVerdict.ALLOWED
    assert decision.explanation == reply.text
    assert decision.conversation_id == reply.conversation_id
    assert decision.model_id == "claude-opus-5-5"
    assert [call.name for call in decision.tool_calls] == ["proposeTrainingPlan"]
    assert decision.proposal_id is not None
    stored = SqliteAggregateRepository(world.runtime.database, CONVERSATION_CODEC).list_all(
        world.scope
    )
    assert [item.conversation_id for item in stored] == [reply.conversation_id]
    safety = ScriptedLLM(
        tools(("flagSafetyConcern", {"redFlags": ["RF_NEURO"]})),
        answer("Pará de entrenar y consultá a un médico ahora."),
    )
    flagged = world.coach(safety).reply(
        world.scope, "Se me nubló la vista de un lado", principal=world.principal
    )
    assert flagged.notices == (hold_notice([RedFlag.RF_NEURO], "es"),)
    assert decisions(world)[-1].kind is DecisionKind.SAFETY_RESPONSE


@pytest.mark.req("FR-AGT-003")
def test_a_decision_stays_audited_when_the_model_fails_after_a_tool_acted(tmp_path: Path) -> None:
    world, _goal = with_goal(tmp_path)
    llm = ScriptedLLM(
        tools(("proposeTrainingPlan", {"mode": "GENERATE"})),
        LLMUnavailableError(LLMFailure.UNAVAILABLE),
    )
    with pytest.raises(LLMUnavailableError):
        world.coach(llm).reply(world.scope, "Armame un plan", principal=world.principal)
    (decision,) = decisions(world)
    assert (decision.explanation, decision.conversation_id) == (UNEXPLAINED, None)
    assert decision.kind is DecisionKind.PLAN_PROPOSAL
