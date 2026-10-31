"""Plan documents (planning spec): PlannedWorkout <-> workout contract, PlanVersion <->
training-plan contract (its content hash recomputed and checked on every read) and
TrainingPlan <-> plan-header contract."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any, Final
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
from coach.application.contracts.strength import (
    strength_prescription_from_contract,
    strength_prescription_to_contract,
)
from coach.domain.athlete.resources import TennisSessionType
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.quality import Confidence, UncertaintyReason
from coach.domain.common.sport import Sport
from coach.domain.plans.adaptation import (
    AdaptationDecision,
    AdaptationReason,
    AdaptationTrigger,
    Decision,
)
from coach.domain.plans.execution import ExecutionLink
from coach.domain.plans.plan import (
    EstimatedLoad,
    Generator,
    GeneratorKind,
    Phase,
    PhaseBlock,
    PlanContent,
    PlanStatus,
    PlanVersion,
    SportLoad,
    SupersededReason,
    TrainingPlan,
    TrainingWeek,
)
from coach.domain.plans.workout import (
    EnduranceWorkout,
    EvaluationSession,
    HeartRateTarget,
    IntensityTarget,
    PaceTarget,
    PaceUnit,
    PlannedWorkout,
    PowerTarget,
    RecoverySession,
    RecoveryType,
    Repeat,
    RpeRange,
    Step,
    StepKind,
    TennisSession,
    WorkoutType,
)

DAY_NAMES: Final = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _number(value: Any) -> float | None:
    return None if value is None else float(value)


# --------------------------------------------------------------------------- workouts


def _target_to(target: IntensityTarget) -> Json:
    document: Json = {"rpe": {"min": target.rpe.low, "max": target.rpe.high}}
    if target.heart_rate is not None:
        document["heartRate"] = {
            "zoneModelId": str(target.heart_rate.zone_model_id),
            "minBpm": target.heart_rate.min_bpm,
            "maxBpm": target.heart_rate.max_bpm,
        }
    if target.pace is not None:
        document["pace"] = {
            "unit": target.pace.unit.value,
            "min": target.pace.low,
            "max": target.pace.high,
        }
    if target.power is not None:
        document["power"] = {
            "minWatts": target.power.min_watts,
            "maxWatts": target.power.max_watts,
        }
    return document


def _target_from(document: Document) -> IntensityTarget:
    heart = document.get("heartRate")
    pace = document.get("pace")
    power = document.get("power")
    return IntensityTarget(
        rpe=RpeRange(float(document["rpe"]["min"]), float(document["rpe"]["max"])),
        heart_rate=None
        if heart is None
        else HeartRateTarget(UUID(heart["zoneModelId"]), heart["minBpm"], heart["maxBpm"]),
        pace=None
        if pace is None
        else PaceTarget(PaceUnit(pace["unit"]), float(pace["min"]), float(pace["max"])),
        power=None
        if power is None
        else PowerTarget(float(power["minWatts"]), float(power["maxWatts"])),
    )


def _step_to(step: Step) -> Json:
    document: Json = {
        "kind": step.kind.value,
        "durationSeconds": step.duration_seconds,
        "distanceMeters": step.distance_meters,
        "target": _target_to(step.target),
    }
    if step.notes is not None:
        document["notes"] = step.notes
    return document


def _step_from(document: Document) -> Step:
    return Step(
        kind=StepKind(document["kind"]),
        duration_seconds=_number(document["durationSeconds"]),
        distance_meters=_number(document["distanceMeters"]),
        target=_target_from(document["target"]),
        notes=document.get("notes"),
    )


def _main_to(item: Step | Repeat) -> Json:
    if isinstance(item, Repeat):
        return {
            "kind": "REPEAT",
            "count": item.count,
            "steps": [_step_to(step) for step in item.steps],
        }
    return _step_to(item)


def _main_from(document: Document) -> Step | Repeat:
    if document["kind"] == "REPEAT":
        return Repeat(document["count"], tuple(_step_from(step) for step in document["steps"]))
    return _step_from(document)


def _endurance_to(item: EnduranceWorkout) -> Json:
    return {
        "sport": item.sport.value,
        "durationSeconds": item.duration_seconds,
        "distanceMeters": item.distance_meters,
        "warmup": [_step_to(step) for step in item.warmup],
        "mainSet": [_main_to(step) for step in item.main_set],
        "cooldown": [_step_to(step) for step in item.cooldown],
        "techniqueFocus": item.technique_focus,
        "stopConditions": list(item.stop_conditions),
        "evaluationMetrics": list(item.evaluation_metrics),
    }


def _endurance_from(document: Document) -> EnduranceWorkout:
    return EnduranceWorkout(
        sport=Sport(document["sport"]),
        duration_seconds=_number(document["durationSeconds"]),
        distance_meters=_number(document["distanceMeters"]),
        warmup=tuple(_step_from(step) for step in document["warmup"]),
        main_set=tuple(_main_from(step) for step in document["mainSet"]),
        cooldown=tuple(_step_from(step) for step in document["cooldown"]),
        technique_focus=document["techniqueFocus"],
        stop_conditions=tuple(document["stopConditions"]),
        evaluation_metrics=tuple(document["evaluationMetrics"]),
    )


def workout_to_contract(workout: PlannedWorkout) -> Json:
    document: Json = {
        "schemaVersion": SCHEMA_VERSION,
        "id": str(workout.workout_id),
        "type": workout.type.value,
        "trainingDay": workout.training_day.isoformat(),
        "order": workout.order,
        "isKeyWorkout": workout.is_key,
        "objective": workout.objective,
        "explanation": {"what": workout.what, "why": workout.why},
    }
    if workout.endurance is not None:
        document["endurance"] = _endurance_to(workout.endurance)
    if workout.strength is not None:
        document["strength"] = strength_prescription_to_contract(workout.strength)
    if workout.tennis is not None:
        document["tennis"] = {
            "sessionType": workout.tennis.session_type.value,
            "durationSeconds": workout.tennis.duration_seconds,
            "expectedRpe": workout.tennis.expected_rpe,
            "recreational": True,
            "countsAsTrainingLoad": True,
        }
    if workout.recovery is not None:
        document["recovery"] = {
            "recoveryType": workout.recovery.recovery_type.value,
            "sport": None if workout.recovery.sport is None else workout.recovery.sport.value,
            "durationSeconds": workout.recovery.duration_seconds,
            "description": workout.recovery.description,
        }
    if workout.evaluation is not None:
        evaluation = workout.evaluation
        document["evaluation"] = {
            "sport": evaluation.sport.value,
            "protocolId": evaluation.protocol_id,
            "description": evaluation.description,
            "submaximal": evaluation.submaximal,
            "requiresOptIn": evaluation.requires_opt_in,
            "safetyNotes": list(evaluation.safety_notes),
            **(
                {}
                if evaluation.duration_seconds is None
                else {"durationSeconds": evaluation.duration_seconds}
            ),
        }
    return document


def workout_from_contract(document: Document) -> PlannedWorkout:
    tennis = document.get("tennis")
    recovery = document.get("recovery")
    evaluation = document.get("evaluation")
    endurance = document.get("endurance")
    strength = document.get("strength")
    return PlannedWorkout(
        workout_id=UUID(document["id"]),
        type=WorkoutType(document["type"]),
        training_day=date.fromisoformat(document["trainingDay"]),
        order=document["order"],
        is_key=document["isKeyWorkout"],
        objective=document["objective"],
        what=document["explanation"]["what"],
        why=document["explanation"]["why"],
        endurance=None if endurance is None else _endurance_from(endurance),
        strength=None if strength is None else strength_prescription_from_contract(strength),
        tennis=None
        if tennis is None
        else TennisSession(
            TennisSessionType(tennis["sessionType"]),
            tennis["durationSeconds"],
            float(tennis["expectedRpe"]),
        ),
        recovery=None
        if recovery is None
        else RecoverySession(
            RecoveryType(recovery["recoveryType"]),
            recovery["durationSeconds"],
            recovery["description"],
            None if recovery["sport"] is None else Sport(recovery["sport"]),
        ),
        evaluation=None
        if evaluation is None
        else EvaluationSession(
            sport=Sport(evaluation["sport"]),
            protocol_id=evaluation["protocolId"],
            description=evaluation["description"],
            submaximal=evaluation["submaximal"],
            requires_opt_in=evaluation["requiresOptIn"],
            safety_notes=tuple(evaluation["safetyNotes"]),
            duration_seconds=evaluation.get("durationSeconds"),
        ),
    )


# --------------------------------------------------------------------------- plans


def _week_to(week: TrainingWeek) -> Json:
    load = week.estimated_load
    return {
        "weekIndex": week.week_index,
        "startDate": week.start.isoformat(),
        "phase": week.phase.value,
        "primaryGoal": week.primary_goal,
        "estimatedLoad": {
            "sessionRpeLoad": load.session_rpe_load,
            "bySport": {
                sport.value: {
                    "durationSeconds": item.duration_seconds,
                    "distanceMeters": item.distance_meters,
                }
                for sport, item in sorted(load.by_sport.items(), key=lambda entry: entry[0].value)
            },
        },
        "days": {
            name: [workout_to_contract(item) for item in workouts]
            for name, workouts in zip(DAY_NAMES, week.days, strict=True)
        },
        "keyWorkouts": [str(item) for item in week.key_workouts],
        "strengthSessions": [str(item) for item in week.strength_sessions],
        "tennisSessions": [str(item) for item in week.tennis_sessions],
        "recoveryDays": [item.isoformat() for item in week.recovery_days],
        "monitoringMetrics": list(week.monitoring_metrics),
        "adaptationRules": list(week.adaptation_rules),
    }


def _week_from(document: Document) -> TrainingWeek:
    load = document["estimatedLoad"]
    return TrainingWeek(
        week_index=document["weekIndex"],
        start=date.fromisoformat(document["startDate"]),
        phase=Phase(document["phase"]),
        primary_goal=document["primaryGoal"],
        estimated_load=EstimatedLoad(
            _number(load["sessionRpeLoad"]),
            {
                Sport(name): SportLoad(
                    _number(item["durationSeconds"]), _number(item["distanceMeters"])
                )
                for name, item in load["bySport"].items()
            },
        ),
        days=tuple(
            tuple(workout_from_contract(item) for item in document["days"][name])
            for name in DAY_NAMES
        ),
        monitoring_metrics=tuple(document["monitoringMetrics"]),
        adaptation_rules=tuple(document["adaptationRules"]),
    )


def content_document(content: PlanContent) -> Json:
    """The content fields of a version, as hashed (training-plan contract)."""
    return {
        "provisional": content.provisional,
        "assumptions": list(content.assumptions),
        "startDate": content.start.isoformat(),
        "endDate": content.end.isoformat(),
        "phases": [
            {
                "phase": item.phase.value,
                "startDate": item.start.isoformat(),
                "endDate": item.end.isoformat(),
                "focus": item.focus,
            }
            for item in content.phases
        ],
        "weeks": [_week_to(week) for week in content.weeks],
    }


def _canonical(value: Any) -> Any:
    """Whole floats as integers, so 4 and 4.0 hash alike after a JSON round trip."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def content_hash(content: PlanContent) -> str:
    """SHA-256 of the canonical JSON content (sorted keys, no whitespace, UTF-8, whole
    numbers without a fraction)."""
    canonical = json.dumps(
        _canonical(content_document(content)),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def plan_version_to_contract(version: PlanVersion) -> Json:
    generator = version.generator
    return {
        "schemaVersion": SCHEMA_VERSION,
        "planId": str(version.plan_id),
        "version": version.number,
        "revision": version.version,
        "parentVersion": version.parent_number,
        "status": version.status.value,
        "supersededReason": None
        if version.superseded_reason is None
        else version.superseded_reason.value,
        "athleteId": str(version.athlete_id),
        "goalId": str(version.goal_id),
        "generatedAt": instant(version.generated_at),
        "activationReason": version.activation_reason,
        "generator": {
            "kind": generator.kind.value,
            "engineVersion": generator.engine_version,
            "promptVersion": generator.prompt_version,
            "modelId": generator.model_id,
            "decisionId": None if generator.decision_id is None else str(generator.decision_id),
        },
        "contentHash": version.content_hash,
        **content_document(version.content),
    }


def plan_version_from_contract(document: Document) -> PlanVersion:
    generator = document["generator"]
    content = PlanContent(
        start=date.fromisoformat(document["startDate"]),
        end=date.fromisoformat(document["endDate"]),
        phases=tuple(
            PhaseBlock(
                Phase(item["phase"]),
                date.fromisoformat(item["startDate"]),
                date.fromisoformat(item["endDate"]),
                item["focus"],
            )
            for item in document["phases"]
        ),
        weeks=tuple(_week_from(item) for item in document["weeks"]),
        provisional=document["provisional"],
        assumptions=tuple(document["assumptions"]),
    )
    if content_hash(content) != document["contentHash"]:
        raise DomainValidationError("a plan version's content does not match its hash")
    reason = document.get("supersededReason")
    return PlanVersion(
        plan_id=UUID(document["planId"]),
        number=document["version"],
        parent_number=document["parentVersion"],
        status=PlanStatus(document["status"]),
        superseded_reason=None if reason is None else SupersededReason(reason),
        athlete_id=UUID(document["athleteId"]),
        goal_id=UUID(document["goalId"]),
        generated_at=parse_instant(document["generatedAt"]),
        activation_reason=document["activationReason"],
        generator=Generator(
            kind=GeneratorKind(generator["kind"]),
            engine_version=generator["engineVersion"],
            prompt_version=generator["promptVersion"],
            model_id=generator["modelId"],
            decision_id=parse_optional_uuid(generator["decisionId"]),
        ),
        content=content,
        content_hash=document["contentHash"],
        version=document.get("revision", 1),
    )


def adaptation_decision_to_contract(decision: AdaptationDecision) -> Json:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "decisionId": str(decision.decision_id),
        "athleteId": str(decision.athlete_id),
        "decidedAt": instant(decision.decided_at),
        "trainingDay": decision.training_day.isoformat(),
        "trigger": decision.trigger.value,
        "decision": decision.decision.value,
        "reasons": [item.value for item in decision.reasons],
        "evidence": [evidence(item) for item in decision.evidence],
        "interpretation": decision.interpretation,
        "uncertaintyReasons": sorted(item.value for item in decision.uncertainty_reasons),
        "confidence": decision.confidence.value,
        "affectedWorkoutIds": [str(item) for item in decision.affected_workouts],
        "planId": None if decision.plan_id is None else str(decision.plan_id),
        "fromVersion": decision.from_version,
        "proposedVersion": decision.proposed_version,
        "rulesVersion": decision.rules_version,
        "version": decision.version,
    }


def adaptation_decision_from_contract(document: Document) -> AdaptationDecision:
    return AdaptationDecision(
        decision_id=UUID(document["decisionId"]),
        athlete_id=UUID(document["athleteId"]),
        decided_at=parse_instant(document["decidedAt"]),
        training_day=date.fromisoformat(document["trainingDay"]),
        trigger=AdaptationTrigger(document["trigger"]),
        decision=Decision(document["decision"]),
        reasons=tuple(AdaptationReason(item) for item in document["reasons"]),
        evidence=tuple(parse_evidence(item) for item in document["evidence"]),
        interpretation=document["interpretation"],
        uncertainty_reasons=frozenset(
            UncertaintyReason(item) for item in document["uncertaintyReasons"]
        ),
        confidence=Confidence(document["confidence"]),
        affected_workouts=tuple(UUID(item) for item in document["affectedWorkoutIds"]),
        plan_id=parse_optional_uuid(document["planId"]),
        from_version=document["fromVersion"],
        proposed_version=document["proposedVersion"],
        rules_version=document["rulesVersion"],
        version=document["version"],
    )


def execution_link_to_contract(link: ExecutionLink) -> Json:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "athleteId": str(link.athlete_id),
        "activityId": str(link.activity_id),
        "plannedWorkoutId": None
        if link.planned_workout_id is None
        else str(link.planned_workout_id),
        "linkedAt": instant(link.linked_at),
        "version": link.version,
    }


def execution_link_from_contract(document: Document) -> ExecutionLink:
    return ExecutionLink(
        athlete_id=UUID(document["athleteId"]),
        activity_id=UUID(document["activityId"]),
        planned_workout_id=parse_optional_uuid(document["plannedWorkoutId"]),
        linked_at=parse_instant(document["linkedAt"]),
        version=document["version"],
    )


def plan_header_to_contract(plan: TrainingPlan) -> Json:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "planId": str(plan.plan_id),
        "athleteId": str(plan.athlete_id),
        "goalId": str(plan.goal_id),
        "version": plan.version,
        "createdAt": instant(plan.created_at),
        "latestVersion": plan.latest_number,
        "activeVersion": plan.active_number,
    }


def plan_header_from_contract(document: Document) -> TrainingPlan:
    return TrainingPlan(
        plan_id=UUID(document["planId"]),
        athlete_id=UUID(document["athleteId"]),
        goal_id=UUID(document["goalId"]),
        version=document["version"],
        created_at=parse_instant(document["createdAt"]),
        latest_number=document["latestVersion"],
        active_number=document["activeVersion"],
    )
