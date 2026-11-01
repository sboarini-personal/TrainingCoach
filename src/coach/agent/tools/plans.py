"""Read tools over plans and their results: a plan version, executions, the adaptive loop's
decisions, report data and the provider capabilities (P13.2; tools contract section 3).

Plan text (objectives, descriptions, assumptions) is written in code by the generator or the
adaptation rules, so it is sent as it is; report values that are not codes or dates are
untrusted text. Executions and adaptation decisions read activities, so they need the
ACTIVITIES consent, as in the CLI. Capabilities are observed or documented, never inferred:
UNKNOWN stays UNKNOWN (POL-CAPABILITY).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, timedelta
from functools import partial
from typing import Any, Final
from uuid import UUID

from coach.agent.services import AgentServices
from coach.agent.tools.base import (
    ID,
    RANGE_PROPERTIES,
    Tool,
    ToolContext,
    ToolError,
    ToolKind,
    code,
    codes,
    day,
    evidence,
    instant,
    period,
    quantity,
    schema,
    to_json,
    untrusted,
)
from coach.domain.agent import ToolOutcome
from coach.domain.common.evidence import EvidenceKind, EvidenceRef
from coach.domain.common.time import DateRange
from coach.domain.plans.execution import WorkoutExecution
from coach.domain.plans.plan import PlanVersion, TrainingWeek
from coach.domain.plans.workout import PlannedWorkout
from coach.domain.reports import ReportData, ReportItem
from coach.metrics.subjects import week_key

PLAN_DAYS_BEFORE: Final = 7
PLAN_DAYS_AFTER: Final = 21
EXECUTION_DAYS: Final = 14
MAX_DECISIONS: Final = 50
_CODE_OR_DATE: Final = re.compile(r"^([A-Z][A-Z0-9_]{0,63}|[0-9]{4}-[0-9]{2}-[0-9]{2})$")
WEEK: Final = {"type": "string", "pattern": "^[0-9]{4}-W[0-9]{2}$"}
MONTH: Final = {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}$"}


def workout(item: PlannedWorkout, ref: EvidenceRef, *, details: bool) -> dict[str, Any]:
    found: dict[str, Any] = {
        "workoutId": str(item.workout_id),
        "trainingDay": day(item.training_day),
        "order": item.order,
        "type": code(item.type),
        "sport": code(item.sport),
        "isKey": item.is_key,
        "objective": item.objective,
        "what": item.what,
        "why": item.why,
        "plannedDuration": quantity(item.planned_seconds, "s", ref, digits=0),
        "maxRpe": quantity(item.max_rpe, "RPE 0-10", ref),
    }
    if details:
        for name in ("endurance", "strength", "tennis", "recovery", "evaluation"):
            part = getattr(item, name)
            if part is not None:
                found[name] = to_json(part, ref, trusted=True)
    return found


def _week(
    week: TrainingWeek, ref: EvidenceRef, start: date, end: date, *, details: bool
) -> dict[str, Any]:
    load = week.estimated_load
    return {
        "weekIndex": week.week_index,
        "start": day(week.start),
        "end": day(week.end),
        "phase": code(week.phase),
        "primaryGoal": week.primary_goal,
        "estimatedLoad": {
            "sessionRpeLoad": quantity(load.session_rpe_load, "AU (RPE x min)", ref, digits=0),
            "bySport": to_json(load.by_sport, ref, trusted=True),
        },
        "workouts": [
            workout(item, ref, details=details)
            for item in week.workouts
            if start <= item.training_day <= end
        ],
    }


def plan_version(found: PlanVersion, start: date, end: date, *, details: bool) -> dict[str, Any]:
    ref = EvidenceRef(EvidenceKind.PLAN_VERSION, found.entity_id)
    content = found.content
    generator = found.generator
    return {
        "planId": str(found.plan_id),
        "number": found.number,
        "parentNumber": found.parent_number,
        "status": code(found.status),
        "goalId": str(found.goal_id),
        "generatedAt": instant(found.generated_at),
        "generator": {
            "kind": code(generator.kind),
            "engineVersion": generator.engine_version,
            "promptVersion": generator.prompt_version,
            "modelId": generator.model_id,
        },
        "start": day(content.start),
        "end": day(content.end),
        "provisional": content.provisional,
        "assumptions": list(content.assumptions),
        "phases": [
            {
                "phase": code(item.phase),
                "start": day(item.start),
                "end": day(item.end),
                "focus": item.focus,
            }
            for item in content.phases
        ],
        "weeksShown": {"from": day(start), "to": day(end)},
        "weeks": [
            _week(item, ref, start, end, details=details)
            for item in content.weeks
            if item.end >= start and item.start <= end
        ],
        "evidence": evidence(ref),
    }


def _plan(
    services: AgentServices, context: ToolContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    scope = context.scope
    if "from" in arguments or "to" in arguments:
        start, end = period(arguments, context, default_days=PLAN_DAYS_BEFORE)
    else:
        today = context.day()
        start = today - timedelta(days=PLAN_DAYS_BEFORE)
        end = today + timedelta(days=PLAN_DAYS_AFTER)
    if "planId" in arguments or "number" in arguments:
        if "planId" in arguments:
            plan_id = UUID(arguments["planId"])
        else:
            latest = services.plans.latest_plan(scope)
            if latest is None:
                raise ToolError(ToolOutcome.NOT_FOUND)
            plan_id = latest.plan_id
        versions = services.plans.versions(scope, plan_id)
        number = arguments.get("number", max((item.number for item in versions), default=0))
        found = services.plans.version(scope, plan_id, number)
    else:
        active = services.plans.active(scope)
        if active is None:
            return {"version": None, "reason": "NO_ACTIVE_PLAN"}
        found, versions = active, services.plans.versions(scope, active.plan_id)
    return {
        "version": plan_version(found, start, end, details=bool(arguments.get("details"))),
        "versions": [
            {"number": item.number, "status": code(item.status), "parent": item.parent_number}
            for item in versions
        ],
    }


def execution(item: WorkoutExecution) -> dict[str, Any]:
    refs: list[EvidenceRef] = [
        EvidenceRef(EvidenceKind.ACTIVITY, activity) for activity in item.completed_activity_ids
    ]
    if item.planned_workout_id is not None:
        refs.insert(0, EvidenceRef(EvidenceKind.WORKOUT_EXECUTION, item.planned_workout_id))
    compliance = item.compliance
    return {
        "trainingDay": day(item.training_day),
        "plannedWorkoutId": None
        if item.planned_workout_id is None
        else str(item.planned_workout_id),
        "completedActivityIds": [str(activity) for activity in item.completed_activity_ids],
        "status": code(item.status),
        "manualLink": item.manual,
        "sessionRpe": quantity(item.session_rpe, "RPE 0-10", *refs),
        "compliance": {
            "durationRatio": quantity(compliance.duration_ratio, "ratio", *refs, digits=2),
            "distanceRatio": quantity(compliance.distance_ratio, "ratio", *refs, digits=2),
            "intensityShare": quantity(compliance.intensity_share, "share 0-1", *refs, digits=2),
            "structureShare": quantity(compliance.structure_share, "share 0-1", *refs, digits=2),
            "withinBand": compliance.within_band,
        },
        "rulesVersion": item.rules_version,
    }


def _executions(
    services: AgentServices, context: ToolContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    start, end = period(arguments, context, default_days=EXECUTION_DAYS)
    found = services.executions.executions(context.scope, DateRange(start, end))
    return {"from": day(start), "to": day(end), "executions": [execution(item) for item in found]}


def _decisions(
    services: AgentServices, context: ToolContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    found = services.adaptation.decisions(context.scope)
    limit = int(arguments.get("limit", 10))
    latest = list(reversed(found))[:limit]
    return {
        "count": len(found),
        "decisions": [
            {
                "decisionId": str(item.decision_id),
                "decidedAt": instant(item.decided_at),
                "trainingDay": day(item.training_day),
                "trigger": code(item.trigger),
                "decision": code(item.decision),
                "reasons": [code(reason) for reason in item.reasons],
                "interpretation": item.interpretation,  # written in code by the rules
                "confidence": code(item.confidence),
                "uncertaintyReasons": codes(item.uncertainty_reasons),
                "affectedWorkouts": [str(workout_id) for workout_id in item.affected_workouts],
                "planId": None if item.plan_id is None else str(item.plan_id),
                "fromVersion": item.from_version,
                "proposedVersion": item.proposed_version,
                "evidence": evidence(*item.evidence),
                "rulesVersion": item.rules_version,
            }
            for item in latest
        ],
    }


def _item(item: ReportItem) -> dict[str, Any]:
    value: Any = item.value
    if isinstance(value, float):
        value = quantity(value, item.unit or "1", *item.evidence, digits=2)
    elif isinstance(value, str) and not _CODE_OR_DATE.match(value):
        value = untrusted(value)
    return {
        "label": item.label,
        "value": value,
        "confidence": code(item.confidence),
        "uncertaintyReasons": codes(item.reasons),
        "evidence": evidence(*item.evidence),
    }


def report(found: ReportData) -> dict[str, Any]:
    return {
        "kind": code(found.kind),
        "from": day(found.period.start),
        "to": day(found.period.end),
        "subject": found.subject,
        "confidence": code(found.confidence),
        "uncertaintyReasons": codes(found.uncertainty_reasons),
        "missingData": [
            {"section": item.section, "reason": code(item.reason), "item": item.item}
            for item in found.missing_data
        ],
        "sections": [
            {
                "name": section.name,
                "notAvailable": code(section.unavailable),
                "items": [_item(item) for item in section.items],
            }
            for section in found.sections
        ],
        "generatedAt": instant(found.generated_at),
        "version": found.version,
    }


def _report(
    services: AgentServices, context: ToolContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    scope = context.scope
    match arguments["kind"]:
        case "POST_ACTIVITY":
            if "activityId" not in arguments:
                raise ToolError(ToolOutcome.VALIDATION_ERROR, reason="ACTIVITY_ID_REQUIRED")
            found = services.reports.post_activity(scope, UUID(arguments["activityId"]))
        case "WEEKLY":
            week = arguments.get("week") or week_key(context.day() - timedelta(days=7))
            found = services.reports.weekly(scope, week)
        case _:
            month = arguments.get("month")
            if month is None:
                first = context.day().replace(day=1) - timedelta(days=1)
                month = f"{first.year}-{first.month:02d}"
            found = services.reports.monthly(scope, month)
    return report(found)


def _capabilities(
    services: AgentServices, context: ToolContext, _arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    found = services.integrations.list_all(context.scope)
    return {
        "providers": [
            {
                "provider": item.provider,
                "accessTier": code(item.capabilities.access_tier),
                "capabilities": {
                    code(capability): {
                        "value": code(state.value),
                        "basis": code(state.basis),
                        "observedAt": instant(state.observed_at),
                    }
                    for capability, state in sorted(item.capabilities.states.items())
                },
                "syncState": code(item.sync_state),
                "reason": code(item.reason),
                "lastAttemptAt": instant(item.last_attempt_at),
                "lastSuccessAt": instant(item.last_success_at),
                "completedThrough": day(item.cursor.completed_through),
            }
            for item in found
        ],
        "connected": bool(found),
    }


def plan_tools(services: AgentServices) -> list[Tool]:
    return [
        Tool(
            "getTrainingPlan",
            ToolKind.READ,
            "A training plan version: status, generator, phases, assumptions and the weeks "
            "with their workouts (type, sport, objective, planned duration, max RPE). "
            "Default: the ACTIVE version, from a week ago to three weeks ahead. Activation "
            "is never done by the coach: only the athlete activates a version.",
            schema(
                {
                    **RANGE_PROPERTIES,
                    "planId": ID,
                    "number": {"type": "integer", "minimum": 1},
                    "details": {
                        "type": "boolean",
                        "description": "Include each workout's structure (steps, exercises).",
                    },
                }
            ),
            partial(_plan, services),
        ),
        Tool(
            "getPlanExecutions",
            ToolKind.READ,
            "Planned workouts matched with the activities done (COMPLETE, PARTIAL, SKIPPED, "
            "REPLACED, UNPLANNED, PLANNED) with compliance ratios and session RPE. Default: "
            "the last 14 days.",
            schema(RANGE_PROPERTIES),
            partial(_executions, services),
        ),
        Tool(
            "getAdaptationDecisions",
            ToolKind.READ,
            "The adaptive loop's recorded decisions, newest first: trigger, decision "
            "(MAINTAIN, REDUCE, ...), reasons, evidence and the plan version proposed. The "
            "rules decide; the coach explains.",
            schema({"limit": {"type": "integer", "minimum": 1, "maximum": MAX_DECISIONS}}),
            partial(_decisions, services),
        ),
        Tool(
            "getReportData",
            ToolKind.READ,
            "The data part of a report computed in code: POST_ACTIVITY (activityId), WEEKLY "
            "(week YYYY-Www, default the last week) or MONTHLY (month YYYY-MM, default the "
            "last month). Sections that cannot be filled say why.",
            schema(
                {
                    "kind": {"enum": ["POST_ACTIVITY", "WEEKLY", "MONTHLY"]},
                    "activityId": ID,
                    "week": WEEK,
                    "month": MONTH,
                },
                "kind",
            ),
            partial(_report, services),
        ),
        Tool(
            "getProviderCapabilities",
            ToolKind.READ,
            "What each connected provider can do (SUPPORTED, UNSUPPORTED or UNKNOWN, with "
            "the basis and when it was observed) and its sync status. UNKNOWN never enables "
            "a feature.",
            schema(),
            partial(_capabilities, services),
        ),
    ]
