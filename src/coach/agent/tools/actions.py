"""Tools that change something: the athlete's feedback and declarations, a safety flag, plan
requests, provider sync, and the external actions the coach can never complete (P13.4; tools
contract section 3; SEC-AUTHZ-002, SEC-AUTHZ-003, FR-POL-002).

The coach asks and the code decides. A plan version is never written by the model: it asks
the deterministic generator or the adaptive loop, and every proposal goes through the full
policy set; only the athlete activates a version. WRITE_INTERNAL tools set allow-listed
fields only, with the values the athlete gave. EXECUTE_EXTERNAL tools always stop at
CAPABILITY_UNAVAILABLE or CONFIRMATION_REQUIRED: the confirmation is a human action the agent
cannot take. Significant decisions are handed to the pipeline as pending decisions
(FR-AGT-003).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import date
from functools import partial
from typing import Any, Final
from uuid import UUID

from coach.agent.services import AgentServices
from coach.agent.tools.athlete import goal_json
from coach.agent.tools.base import (
    DATE,
    ID,
    PendingDecision,
    Tool,
    ToolContext,
    ToolError,
    ToolKind,
    code,
    day,
    quantity,
    schema,
)
from coach.application.consent import ConsentRequiredError
from coach.application.policy_gate import PolicyDeniedError
from coach.application.recovery import CheckInAnswers
from coach.application.sync import SyncOutcome
from coach.domain.agent import DecisionKind, PolicyVerdict, ToolOutcome
from coach.domain.athlete.profile import ProfileUpdate
from coach.domain.coaching.state import Condition
from coach.domain.common.evidence import EvidenceKind, EvidenceRef
from coach.domain.common.provenance import FieldValue
from coach.domain.common.sport import Sport
from coach.domain.goals.goal import GoalPriority
from coach.domain.integration.capabilities import Capability
from coach.domain.plans.adaptation import AdaptationReason, AdaptationTrigger, Decision
from coach.domain.recovery.checkin import PainOnset, RedFlag

SCALE: Final = {"type": "integer", "minimum": 1, "maximum": 5}
CHECKIN_SCALES: Final = ("sleepQuality", "fatigue", "soreness", "stress", "motivation")
PROFILE_FIELDS: Final = ("heightCm", "restingHr", "maxHr", "metricUnits", "language")
LANGUAGES: Final = ("es", "en")
UNKNOWABLE: Final = ("heightCm", "restingHr", "maxHr")
REDUCTIONS: Final = frozenset({Decision.REDUCE, Decision.REST})
DELETION_SCOPES: Final = ("ALL", "ACTIVITIES", "RECOVERY", "CHECKINS", "PLANS", "CONVERSATIONS")
_STATED: Final = (
    " Only values the athlete stated explicitly, on the documented scale; ask when unsure. "
    "Never infer a value from tone or context."
)


def _safety(context: ToolContext, flags: tuple[RedFlag, ...]) -> None:
    context.decisions.append(
        PendingDecision(
            kind=DecisionKind.SAFETY_RESPONSE,
            action="ACTIVATE_SAFETY_HOLD",
            verdict=PolicyVerdict.NOT_APPLICABLE,
            reasons=tuple(flag.value for flag in flags),
        )
    )


def _feedback(
    services: AgentServices, context: ToolContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    found: dict[str, Any] = {}
    if ("activityId" in arguments) != ("activityRpe" in arguments):
        raise ToolError(ToolOutcome.VALIDATION_ERROR, reason="ACTIVITY_ID_AND_RPE_TOGETHER")
    if "activityId" in arguments:
        if context.principal is None:
            raise ToolError(ToolOutcome.VALIDATION_ERROR, reason="NO_SESSION_PRINCIPAL")
        activity_id = UUID(arguments["activityId"])
        services.activities.correct(
            context.scope,
            context.principal,
            activity_id,
            "subjective.rpe",
            float(arguments["activityRpe"]),
            "Told the AI coach",
        )
        ref = EvidenceRef(EvidenceKind.ACTIVITY, activity_id, field="subjective.rpe")
        found["activity"] = {
            "activityId": str(activity_id),
            "rpe": quantity(float(arguments["activityRpe"]), "RPE 0-10", ref),
        }
    checkin = {
        key: value for key, value in arguments.items() if key not in {"activityId", "activityRpe"}
    }
    if checkin:
        answers = CheckInAnswers(
            training_day=date.fromisoformat(checkin["trainingDay"])
            if "trainingDay" in checkin
            else None,
            pain_intensity=checkin.get("pain"),
            pain_location=checkin.get("painLocation"),
            pain_onset=PainOnset(checkin["painOnset"]) if "painOnset" in checkin else None,
            notes=checkin.get("notes"),
            sleep_quality=checkin.get("sleepQuality"),
            fatigue=checkin.get("fatigue"),
            soreness=checkin.get("soreness"),
            stress=checkin.get("stress"),
            motivation=checkin.get("motivation"),
        )
        result = services.recovery.record_checkin(context.scope, answers)
        if result.red_flags:
            _safety(context, result.red_flags)
        found["checkin"] = {
            "checkinId": str(result.checkin.checkin_id),
            "trainingDay": day(result.checkin.training_day),
            "redFlags": [code(flag) for flag in result.red_flags],
            "safetyHold": bool(result.red_flags),
            "secretRemoved": result.secret_notice is not None,
            "notRecorded": None if result.not_recorded is None else "INVALID_ANSWERS",
        }
    return found


def _flag(
    services: AgentServices, context: ToolContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    flags = tuple(RedFlag(item) for item in arguments["redFlags"])
    # The application activates the hold on the model's flag (safety spec 2, source 3); the
    # agent never changes conditions itself, and only the athlete releases a hold.
    services.conditions.activate(context.scope, Condition.SAFETY_HOLD, reason=flags[0].value)
    _safety(context, flags)
    return {"safetyHold": True, "redFlags": [flag.value for flag in flags]}


def _profile(
    services: AgentServices, context: ToolContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    current = services.inputs.profile(context.scope)
    if current.version != arguments["expectedVersion"]:
        raise ToolError(ToolOutcome.CONCURRENCY_CONFLICT, currentVersion=current.version)
    now = services.clock()
    declared = partial(FieldValue.declared, at=now)
    unknown = set(arguments.get("unknown", ()))
    if unknown & set(arguments):
        raise ToolError(ToolOutcome.VALIDATION_ERROR, reason="VALUE_AND_UNKNOWN")
    changes = {key: arguments[key] for key in PROFILE_FIELDS if key in arguments}
    update = ProfileUpdate(
        height_cm=declared(float(changes["heightCm"])) if "heightCm" in changes else None,
        resting_hr=declared(int(changes["restingHr"])) if "restingHr" in changes else None,
        max_hr=declared(int(changes["maxHr"])) if "maxHr" in changes else None,
        metric_units=declared(bool(changes["metricUnits"])) if "metricUnits" in changes else None,
        language=declared(str(changes["language"])) if "language" in changes else None,
    )
    if unknown:  # "I don't know": UNKNOWN, not asked again within its window (FR-ONB-002)
        update = replace(
            update,
            height_cm=FieldValue.unknown(at=now) if "heightCm" in unknown else update.height_cm,
            resting_hr=FieldValue.unknown(at=now) if "restingHr" in unknown else update.resting_hr,
            max_hr=FieldValue.unknown(at=now) if "maxHr" in unknown else update.max_hr,
        )
    saved = services.inputs.update_profile(context.scope, update)
    return {"version": saved.version, "changed": sorted(changes), "unknown": sorted(unknown)}


def _goal(
    services: AgentServices, context: ToolContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    goal = services.inputs.add_goal(
        context.scope,
        sport=Sport(arguments["sport"]),
        target_date=date.fromisoformat(arguments["targetDate"]),
        priority=GoalPriority(arguments["priority"]),
        distance_meters=arguments.get("distanceMeters"),
        target_time_seconds=arguments.get("targetTimeSeconds"),
        event_id=UUID(arguments["eventId"]) if "eventId" in arguments else None,
    )
    return {
        "goal": goal_json(goal),
        "activateWith": f"coach goal activate {goal.goal_id}",  # the athlete's action
    }


def _propose(
    services: AgentServices, context: ToolContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    scope = context.scope
    if arguments["mode"] == "ADAPT":
        return _adapt(services, context)
    goal_id = UUID(arguments["goalId"]) if "goalId" in arguments else None
    try:
        version = services.generation.generate(scope, goal_id)
    except PolicyDeniedError as denied:
        reasons = tuple(dict.fromkeys(item.code for item in denied.decision.reasons))
        context.decisions.append(
            PendingDecision(
                kind=DecisionKind.PLAN_PROPOSAL,
                action="PROPOSE_PLAN_VERSION",
                verdict=PolicyVerdict.DENIED,
                reasons=reasons,
            )
        )
        raise ToolError(ToolOutcome.POLICY_DENIED, reasons=list(reasons)) from None
    ref = EvidenceRef(EvidenceKind.PLAN_VERSION, version.entity_id)
    context.decisions.append(
        PendingDecision(
            kind=DecisionKind.PLAN_PROPOSAL,
            action="PROPOSE_PLAN_VERSION",
            verdict=PolicyVerdict.ALLOWED,
            proposal_id=version.entity_id,
            evidence=(ref,),
        )
    )
    revision = services.plans.plan(scope, version.plan_id).version
    return {
        "planId": str(version.plan_id),
        "number": version.number,
        "status": code(version.status),
        "provisional": version.content.provisional,
        "start": day(version.content.start),
        "end": day(version.content.end),
        "reviewWith": f"coach plan show {version.plan_id} {version.number}",
        "activateWith": f"coach plan activate {version.plan_id} {version.number} "
        f"--revision {revision}",  # only the athlete activates
    }


def _adapt(services: AgentServices, context: ToolContext) -> Mapping[str, Any]:
    if services.plans.active(context.scope) is None:
        raise ToolError(ToolOutcome.VALIDATION_ERROR, reason="NO_ACTIVE_PLAN")
    result = services.adaptation.run(context.scope, AdaptationTrigger.ATHLETE_REQUEST)
    decision = result.decision
    proposed = result.proposed
    if proposed is not None:
        verdict = PolicyVerdict.ALLOWED
    elif AdaptationReason.PROPOSAL_REFUSED in decision.reasons:
        verdict = PolicyVerdict.DENIED
    else:
        verdict = PolicyVerdict.NOT_APPLICABLE
    context.decisions.append(
        PendingDecision(
            kind=DecisionKind.LOAD_REDUCTION
            if decision.decision in REDUCTIONS
            else DecisionKind.ADAPTATION,
            action="REQUEST_ADAPTATION",
            verdict=verdict,
            reasons=tuple(reason.value for reason in decision.reasons),
            proposal_id=None if proposed is None else proposed.entity_id,
            evidence=decision.evidence,
        )
    )
    found: dict[str, Any] = {
        "decisionId": str(decision.decision_id),
        "decision": code(decision.decision),
        "reasons": [code(reason) for reason in decision.reasons],
        "interpretation": decision.interpretation,  # written in code by the rules
        "confidence": code(decision.confidence),
        "proposedVersion": None if proposed is None else proposed.number,
    }
    if proposed is not None:
        revision = services.plans.plan(context.scope, proposed.plan_id).version
        found["activateWith"] = (
            f"coach plan activate {proposed.plan_id} {proposed.number} --revision {revision}"
        )
    return found


def _sync(
    services: AgentServices, context: ToolContext, _arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    scope = context.scope
    activities: dict[str, Any]
    try:
        report = services.sync.sync(scope)
    except ConsentRequiredError:
        activities = {"outcome": "NO_CONSENT"}
    else:
        if report.outcome is SyncOutcome.NOT_CONFIGURED:
            raise ToolError(ToolOutcome.PROVIDER_UNAVAILABLE, reason="NOT_CONNECTED")
        counts = report.counts
        activities = {
            "outcome": code(report.outcome),
            "imported": counts.imported,
            "duplicates": counts.duplicates,
            "rejected": counts.rejected,
        }
    recovery = services.recovery_sync.sync(scope)
    return {
        "activities": activities,
        "recovery": {
            "outcome": code(recovery.outcome),
            "metrics": [
                {"metric": code(item.metric), "outcome": code(item.outcome), "stored": item.stored}
                for item in recovery.metrics
            ],
        },
    }


def _workout_proposal(
    services: AgentServices, context: ToolContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    workout_id = UUID(arguments["plannedWorkoutId"])
    active = services.plans.active(context.scope)
    workout = next(
        (
            item
            for item in (active.content.workouts if active else ())
            if item.workout_id == workout_id
        ),
        None,
    )
    if workout is None:
        raise ToolError(ToolOutcome.NOT_FOUND)
    state = None
    for status in services.integrations.list_all(context.scope):
        state = status.capabilities.states.get(Capability.TRAINING_PUBLISH)
    value = "UNKNOWN" if state is None else state.value.value
    return {
        "plannedWorkoutId": str(workout_id),
        "trainingDay": day(workout.training_day),
        "type": code(workout.type),
        "publishable": value == "SUPPORTED",
        "capability": {"name": Capability.TRAINING_PUBLISH.value, "value": value},
        "stays": "LOCAL",  # V1 never sends workouts to a provider
    }


def _publish(
    _services: AgentServices, _context: ToolContext, _arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    raise ToolError(
        ToolOutcome.CAPABILITY_UNAVAILABLE, capability=Capability.TRAINING_PUBLISH.value
    )


def _deletion(
    _services: AgentServices, _context: ToolContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    raise ToolError(
        ToolOutcome.CONFIRMATION_REQUIRED,
        scope=arguments["scope"],
        confirmedBy="ATHLETE_ONLY",
        deletionWorkflow="NOT_AVAILABLE_YET",
    )


def action_tools(services: AgentServices) -> list[Tool]:
    return [
        Tool(
            "recordSubjectiveFeedback",
            ToolKind.WRITE_INTERNAL,
            "Record what the athlete reported: a daily check-in (sleep quality, fatigue, "
            "soreness, stress and motivation 1-5, pain 0-10 with location and onset, notes) "
            "and/or the RPE 0-10 of one activity. Red flags activate SAFETY_HOLD." + _STATED,
            {
                **schema(
                    {
                        "trainingDay": DATE,
                        **dict.fromkeys(CHECKIN_SCALES, SCALE),
                        "pain": {"type": "integer", "minimum": 0, "maximum": 10},
                        "painLocation": {"type": "string", "minLength": 1, "maxLength": 100},
                        "painOnset": {"enum": [item.value for item in PainOnset]},
                        "notes": {"type": "string", "minLength": 1, "maxLength": 1000},
                        "activityId": ID,
                        "activityRpe": {"type": "number", "minimum": 0, "maximum": 10},
                    }
                ),
                "minProperties": 1,
            },
            partial(_feedback, services),
        ),
        Tool(
            "flagSafetyConcern",
            ToolKind.WRITE_INTERNAL,
            "Activate SAFETY_HOLD when the athlete describes a red flag the application did "
            "not catch: chest pain or pressure, fainting, unusual breathing difficulty, sudden "
            "neurological symptoms, sudden loss of strength, severe sudden pain, palpitations "
            "with dizziness. Fail-safe: when in doubt, flag. Only the athlete can release it.",
            schema(
                {
                    "redFlags": {
                        "type": "array",
                        "items": {"enum": [item.value for item in RedFlag]},
                        "minItems": 1,
                        "uniqueItems": True,
                    }
                },
                "redFlags",
            ),
            partial(_flag, services),
        ),
        Tool(
            "updateAthleteProfile",
            ToolKind.WRITE_INTERNAL,
            "Change allow-listed profile fields (height cm, resting and max heart rate bpm, "
            "metric units, language) with the profile version read by getAthleteProfile." + _STATED,
            {
                **schema(
                    {
                        "expectedVersion": {"type": "integer", "minimum": 1},
                        "heightCm": {"type": "number", "minimum": 100, "maximum": 250},
                        "restingHr": {"type": "integer", "minimum": 25, "maximum": 150},
                        "maxHr": {"type": "integer", "minimum": 100, "maximum": 230},
                        "metricUnits": {"type": "boolean"},
                        "language": {"enum": list(LANGUAGES)},
                        "unknown": {
                            "type": "array",
                            "items": {"enum": list(UNKNOWABLE)},
                            "minItems": 1,
                            "uniqueItems": True,
                            "description": "Fields the athlete said they do not know.",
                        },
                    },
                    "expectedVersion",
                ),
                "minProperties": 2,
            },
            partial(_profile, services),
        ),
        Tool(
            "saveGoal",
            ToolKind.WRITE_INTERNAL,
            "Create a DRAFT goal the athlete described (sport, date, priority, distance in "
            "meters, target time in seconds, optional event). The athlete activates it with "
            "`coach goal activate`." + _STATED,
            schema(
                {
                    "sport": {"enum": [item.value for item in Sport if item is not Sport.OTHER]},
                    "targetDate": DATE,
                    "priority": {"enum": [item.value for item in GoalPriority]},
                    "distanceMeters": {"type": "number", "exclusiveMinimum": 0, "maximum": 1000000},
                    "targetTimeSeconds": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                        "maximum": 1000000,
                    },
                    "eventId": ID,
                },
                "sport",
                "targetDate",
                "priority",
            ),
            partial(_goal, services),
        ),
        Tool(
            "proposeTrainingPlan",
            ToolKind.PROPOSE,
            "Ask the coaching rules for a new PROPOSED plan version: GENERATE runs the plan "
            "generator for a goal (default: the primary goal); ADAPT runs the adaptive loop "
            "over the ACTIVE plan. Every proposal passes the policy engine "
            "(POLICY_DENIED with reasons otherwise). Only the athlete activates a version.",
            schema({"mode": {"enum": ["GENERATE", "ADAPT"]}, "goalId": ID}, "mode"),
            partial(_propose, services),
        ),
        Tool(
            "requestProviderSync",
            ToolKind.WRITE_INTERNAL,
            "Pull new activities and daily recovery metrics from the connected provider "
            "(Garmin), each only with its consent. Slow: use only when the athlete asks.",
            schema(),
            partial(_sync, services),
        ),
        Tool(
            "proposeGarminWorkout",
            ToolKind.PROPOSE,
            "Check whether a planned workout of the ACTIVE plan could be sent to the "
            "provider. In V1 workouts stay local: the answer says why.",
            schema({"plannedWorkoutId": ID}, "plannedWorkoutId"),
            partial(_workout_proposal, services),
        ),
        Tool(
            "publishGarminWorkout",
            ToolKind.EXECUTE_EXTERNAL,
            "Send a workout to Garmin. Not available in V1 (CAPABILITY_UNAVAILABLE); an "
            "external write always needs the athlete's own confirmation.",
            schema({"proposalId": ID}, "proposalId"),
            partial(_publish, services),
        ),
        Tool(
            "requestDataDeletion",
            ToolKind.EXECUTE_EXTERNAL,
            "Request the deletion of the athlete's data. Never done by the coach: it answers "
            "CONFIRMATION_REQUIRED, and only the athlete can confirm a deletion.",
            schema({"scope": {"enum": list(DELETION_SCOPES)}}, "scope"),
            partial(_deletion, services),
        ),
    ]
