"""Read tools over the athlete's declarations: profile, goals and events, availability,
readiness and goal feasibility (P13.2; tools contract section 3).

A declared value is evidence of itself (kinds ATHLETE_PROFILE, GOAL, SPORT_EVENT,
AVAILABILITY). The profile states for every field whether it is declared, UNKNOWN (the
athlete does not know) or not declared yet, and where each value came from. The birth date
is sent as an age only (minimisation, PRIV-CON-003).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
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
from coach.domain.athlete.physiology import age_on
from coach.domain.athlete.profile import AthleteProfile
from coach.domain.common.evidence import EvidenceKind, EvidenceRef
from coach.domain.common.provenance import FieldValue
from coach.domain.common.sport import Sport
from coach.domain.goals.goal import Goal, GoalStatus, SportEvent
from coach.domain.onboarding.planner import OnboardingField

SECTIONS: Final = ("basics", "physiology", "devices", "experience", "resources", "health")
CATALOGUE_CODES: Final = frozenset({"exercise_id", "familiar_exercise_ids"})
AVAILABILITY_DAYS: Final = 14  # the default: today and the next 13 days
_SPORTS: Final = tuple(sport for sport in Sport if sport is not Sport.OTHER)
_F: Final = OnboardingField
# How each onboarding answer is recorded: a coach tool for the allow-listed fields, else the
# athlete's own command (consents and health declarations never go through the AI coach).
ANSWERS: Final[Mapping[OnboardingField, Mapping[str, str]]] = {
    _F.CONSENT_DECISIONS: {"command": "coach consent grant <type> | coach consent decline <type>"},
    _F.DISPLAY_NAME: {"command": "coach profile name <name>"},
    _F.BIRTH_DATE_OR_AGE: {"command": "coach profile birth-date YYYY-MM-DD"},
    _F.TIMEZONE: {"command": "coach profile timezone <IANA name>"},
    _F.UNITS: {"tool": "updateAthleteProfile", "field": "metricUnits"},
    _F.LANGUAGE: {"tool": "updateAthleteProfile", "field": "language"},
    _F.HEIGHT: {"tool": "updateAthleteProfile", "field": "heightCm"},
    _F.WEIGHT: {"status": "NOT_COLLECTED_YET"},  # body weight: a later phase (D-11)
    _F.HEALTH_CONSTRAINTS: {"command": "coach profile health"},
    _F.PRIMARY_GOAL: {"tool": "saveGoal", "then": "coach goal activate <goal id>"},
    _F.GOAL_SPORT_EXPERIENCE: {"command": "coach profile experience <sport>"},
    _F.OTHER_SPORTS_EXPERIENCE: {"command": "coach profile experience <sport>"},
    _F.RESTING_HR: {"tool": "updateAthleteProfile", "field": "restingHr"},
    _F.MAX_HR: {"tool": "updateAthleteProfile", "field": "maxHr"},
    _F.WEEKLY_AVAILABILITY: {"command": "coach availability set"},
    _F.RESOURCES: {"command": "coach profile resources"},
    _F.STRENGTH_EXPERIENCE: {"command": "coach profile experience strength"},
    _F.TENNIS: {"command": "coach profile experience tennis"},
    _F.GARMIN_MODEL: {"command": "coach profile sensors"},
    _F.SENSORS: {"command": "coach profile sensors"},
}


class _Fields:
    """The profile's fields with their UNKNOWN markers and provenance."""

    def __init__(self, athlete_id: UUID) -> None:
        self.athlete_id = athlete_id
        self.data: dict[str, Any] = {}
        self.unknown: list[dict[str, str]] = []
        self.provenance: dict[str, str] = {}

    def ref(self, name: str) -> EvidenceRef:
        return EvidenceRef(EvidenceKind.ATHLETE_PROFILE, self.athlete_id, field=name)

    def missing(self, name: str, status: str) -> None:
        self.data[name] = None
        self.unknown.append({"field": name, "status": status})

    def put[T](
        self, name: str, stored: FieldValue[T] | None, render: Callable[[T, EvidenceRef], Any]
    ) -> None:
        if stored is None:
            self.missing(name, "NOT_DECLARED")
            return
        self.provenance[name] = stored.provenance.source.value
        if stored.value is None:
            self.missing(name, "UNKNOWN")
            return
        self.data[name] = render(stored.value, self.ref(name))

    def number(self, name: str, stored: FieldValue[Any] | None, unit: str) -> None:
        self.put(name, stored, lambda value, ref: quantity(float(value), unit, ref))

    def value(self, name: str, stored: FieldValue[Any] | None) -> None:
        self.put(name, stored, lambda value, ref: to_json(value, ref, plain=CATALOGUE_CODES))


def _age(fields: _Fields, profile: AthleteProfile, today: date | None) -> None:
    birth = profile.birth_date
    if birth is not None and birth.value is not None:
        fields.provenance["age"] = birth.provenance.source.value
        if today is None:
            fields.missing("age", "TIMEZONE_REQUIRED")
        else:
            fields.data["age"] = quantity(age_on(birth.value, today), "years", fields.ref("age"))
        return
    fields.number("age", profile.declared_age, "years")


def _profile(
    services: AgentServices, context: ToolContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    profile = services.inputs.profile(context.scope)
    wanted = set(arguments.get("sections", SECTIONS))
    fields = _Fields(context.scope.athlete_id)
    if "basics" in wanted:
        fields.put("displayName", profile.display_name, lambda value, _ref: untrusted(value))
        _age(fields, profile, context.today)
        fields.number("heightCm", profile.height_cm, "cm")
        fields.number("weightKg", profile.weight_kg, "kg")
        fields.put("timezone", profile.timezone, lambda value, _ref: value)  # an IANA name
        fields.put("language", profile.language, lambda value, _ref: value)  # a language tag
        fields.value("metricUnits", profile.metric_units)
    if "physiology" in wanted:
        fields.number("restingHr", profile.resting_hr, "bpm")
        fields.number("maxHr", profile.max_hr, "bpm")
        fields.value("zoneMethod", profile.zone_method)
        fields.data["heartRateZones"] = [
            {
                "zoneModelId": str(model.zone_model_id),
                "method": code(model.method),
                "source": code(model.source),
                "validFrom": day(model.valid_from),
                "estimated": model.estimated,
                "boundaries": {
                    "values": list(model.boundaries_bpm),
                    "unit": "bpm",
                    "evidence": evidence(
                        EvidenceRef(EvidenceKind.HR_ZONE_MODEL, model.zone_model_id)
                    ),
                },
            }
            for model in profile.zone_models
        ]
    if "devices" in wanted:
        fields.put("garminModel", profile.garmin_model, lambda value, _ref: untrusted(value))
        fields.value("sensors", profile.sensors)
    if "experience" in wanted:
        for sport in _SPORTS:
            fields.value(f"{sport.value.lower()}Experience", profile.experience_for(sport))
    if "resources" in wanted:
        fields.value("resources", profile.resources)
    if "health" in wanted:
        fields.put(
            "health",
            profile.health,
            lambda value, ref: {
                "declaresNone": value.declares_none,
                **to_json(value, ref),
            },
        )
    return {
        "version": profile.version,  # for updateAthleteProfile
        **fields.data,
        "unknown": fields.unknown,
        "provenance": fields.provenance,
    }


def goal_json(goal: Goal) -> dict[str, Any]:
    ref = EvidenceRef(EvidenceKind.GOAL, goal.goal_id)
    baseline = goal.baseline
    return {
        "goalId": str(goal.goal_id),
        "sport": code(goal.sport),
        "priority": code(goal.priority),
        "status": code(goal.status),
        "isPrimary": goal.is_active_primary,
        "targetDate": day(goal.target_date),
        "distance": quantity(goal.distance_meters, "m", ref),
        "targetTime": quantity(goal.target_time_seconds, "s", ref),
        "eventId": None if goal.event_id is None else str(goal.event_id),
        "baseline": None
        if baseline is None
        else {
            "capturedAt": instant(baseline.captured_at),
            "summary": baseline.summary,  # written in code (feasibility rules)
            "evidence": evidence(*baseline.evidence),
        },
    }


def _event(event: SportEvent) -> dict[str, Any]:
    ref = EvidenceRef(EvidenceKind.SPORT_EVENT, event.event_id)
    return {
        "eventId": str(event.event_id),
        "name": untrusted(event.name),
        "sport": code(event.sport),
        "date": day(event.event_date),
        "priority": code(event.priority),
        "distance": quantity(event.distance_meters, "m", ref),
        "location": untrusted(event.location),
        "expectedConditions": untrusted(event.expected_conditions),
    }


def _goals(
    services: AgentServices, context: ToolContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    goals = services.inputs.goals(context.scope)
    if "status" in arguments:
        goals = [item for item in goals if item.status.value == arguments["status"]]
    return {
        "goals": [goal_json(item) for item in goals],
        "events": [_event(item) for item in services.inputs.events(context.scope)],
    }


def _availability(
    services: AgentServices, context: ToolContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    if "from" in arguments or "to" in arguments:
        start, end = period(arguments, context, default_days=AVAILABILITY_DAYS)
    else:
        start = context.day()
        end = start + timedelta(days=AVAILABILITY_DAYS - 1)
    stored = services.inputs.availability(context.scope)
    if stored is None:
        return {"declared": False, "weekly": None, "defaults": None, "days": []}
    ref = EvidenceRef(EvidenceKind.AVAILABILITY, context.scope.athlete_id)
    days = []
    for offset in range((end - start).days + 1):
        resolved = stored.resolve(start + timedelta(days=offset))
        exception = stored.exceptions.get(resolved.day)
        days.append(
            {
                "day": day(resolved.day),
                "available": resolved.available,
                "plannable": resolved.plannable,
                "maximumDuration": quantity(resolved.maximum_duration_minutes, "min", ref),
                "allowedSports": codes(resolved.allowed_sports),
                "windows": to_json(resolved.windows, ref),
                "basis": code(resolved.basis),
                "exceptionReason": None if exception is None else untrusted(exception.reason),
            }
        )
    return {
        "declared": True,
        "weekly": to_json(stored.recurring, ref),
        "defaults": to_json(stored.defaults, ref),
        "days": days,
    }


def _readiness(
    services: AgentServices, context: ToolContext, _arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    report = services.inputs.readiness(context.scope, advance=False)
    sufficiency = report.sufficiency
    return {
        "status": code(report.readiness.status),
        "missingRequired": [code(item) for item in report.readiness.missing_required],
        "missingPreferred": [code(item) for item in report.readiness.missing_preferred],
        "lifecycle": code(report.lifecycle),
        "dataIncomplete": [code(item) for item in report.incomplete],
        "history": None
        if sufficiency is None
        else {
            "sufficient": sufficiency.sufficient,
            "gaps": [code(gap) for gap in sufficiency.gaps],
        },
        "rulesVersion": report.readiness.rules_version,
    }


def _questions(
    services: AgentServices, context: ToolContext, _arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    batch = services.inputs.questions(context.scope)
    if batch is None:
        return {"complete": True, "questions": []}
    return {
        "complete": False,
        "group": batch.group.value,
        "inputClass": code(batch.input_class),
        "questions": [
            {"field": field.value, "answerWith": dict(ANSWERS[field])} for field in batch.fields
        ],
    }


def _feasibility(
    services: AgentServices, context: ToolContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    goal_id = UUID(arguments["goalId"]) if "goalId" in arguments else None
    found = services.inputs.feasibility(context.scope, goal_id)
    goal = EvidenceRef(EvidenceKind.GOAL, found.goal_id)
    plan = EvidenceRef(EvidenceKind.AVAILABILITY, context.scope.athlete_id)
    current = found.current
    basis = (goal,) if current is None else (goal, current.evidence)
    return {
        "goalId": str(found.goal_id),
        "assessedOn": day(found.assessed_on),
        "classification": code(found.classification),
        "confidence": code(found.confidence),
        "reasons": [code(item) for item in found.reasons],
        "presentsAlternatives": found.presents_alternatives,
        "current": None
        if current is None
        else {
            "predictedTime": quantity(current.seconds, "s", current.evidence, digits=0),
            "fromDistance": quantity(current.source_distance_m, "m", current.evidence),
            "fromTime": quantity(current.source_seconds, "s", current.evidence, digits=0),
            "fromDate": day(current.source_date),
            "confidence": code(current.confidence),
            "method": current.method,
        },
        "desiredTime": quantity(found.desired_seconds, "s", goal, digits=0),
        "realisticTime": quantity(found.realistic_seconds, "s", *basis, digits=0),
        "minimumTime": quantity(found.minimum_seconds, "s", *basis, digits=0),
        "gap": quantity(found.gap_seconds, "s", *basis, digits=0),
        "weeksAvailable": quantity(found.weeks_available, "weeks", goal),
        "hoursPerWeek": quantity(found.hours_per_week, "h/week", plan),
        "requiredImprovementPerWeek": quantity(found.required_seconds_per_week, "s/week", *basis),
        "requiredImprovementPercentPerWeek": quantity(
            found.required_percent_per_week, "%/week", *basis, digits=2
        ),
        "rulesVersion": found.rules_version,
    }


def athlete_tools(services: AgentServices) -> list[Tool]:
    return [
        Tool(
            "getAthleteProfile",
            ToolKind.READ,
            "The athlete's profile: basics (age, height, weight, timezone, language), "
            "physiology (resting and max heart rate, zone models), devices, sport experience, "
            "resources and declared health constraints. Every field says whether it is "
            "declared, UNKNOWN or not declared yet, and where it came from.",
            schema(
                {
                    "sections": {
                        "type": "array",
                        "items": {"enum": list(SECTIONS)},
                        "uniqueItems": True,
                        "description": "Sections to read (default: all).",
                    }
                }
            ),
            partial(_profile, services),
        ),
        Tool(
            "getGoals",
            ToolKind.READ,
            "The athlete's goals (sport, date, distance, target time, priority, status, "
            "baseline evidence) and declared sport events.",
            schema({"status": {"enum": [item.value for item in GoalStatus]}}),
            partial(_goals, services),
        ),
        Tool(
            "getAvailability",
            ToolKind.READ,
            "The effective availability per day (available, maximum minutes, allowed sports, "
            "time windows, basis) plus the weekly declaration. Default: today and the next "
            "13 days; at most 92 days.",
            schema(RANGE_PROPERTIES),
            partial(_availability, services),
        ),
        Tool(
            "getReadiness",
            ToolKind.READ,
            "Whether the athlete is ready for a plan (READY, READY_PROVISIONAL, NOT_READY): "
            "the missing required and preferred inputs, history sufficiency and the "
            "lifecycle state. Reads only; changes nothing.",
            schema(),
            partial(_readiness, services),
        ),
        Tool(
            "getOnboardingQuestions",
            ToolKind.READ,
            "The next onboarding questions, chosen by the application: at most five related "
            "fields, in order, never one already known. Ask exactly these, in one message, "
            "and say how each answer is recorded (a tool, or the athlete's own command).",
            schema(),
            partial(_questions, services),
        ),
        Tool(
            "getGoalFeasibility",
            ToolKind.READ,
            "The feasibility of a goal computed now by the feasibility rules: "
            "classification, confidence, reasons, the current level with its evidence and "
            "the realistic and minimum alternatives. Default: the primary goal.",
            schema({"goalId": {**ID, "description": "A goal from getGoals."}}),
            partial(_feasibility, services),
        ),
    ]
