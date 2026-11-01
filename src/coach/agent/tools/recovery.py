"""Read tools over recovery: daily metrics, check-ins, the recovery status of a day and the
detected insights (P13.2; tools contract section 3; recovery spec).

Daily provider metrics are read by consent category (RECOVERY, SLEEP, HRV,
PROVIDER_RECOVERY_METRICS): a metric whose consent is not granted is not sent, and the tool
says which categories are missing. Proprietary metrics are labelled with their provider and
never compared with another provider's. A recovery assessment's wearable signals follow the
same rule. Check-in notes and pain locations are untrusted text.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from functools import partial
from typing import Any, Final

from coach.agent.services import AgentServices
from coach.agent.tools.base import (
    DATE,
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
    untrusted,
)
from coach.domain.common.evidence import EvidenceKind, EvidenceRef
from coach.domain.consent.ledger import ConsentType
from coach.domain.recovery.assessment import RecoveryAssessment, RecoveryPrescription, Signal
from coach.domain.recovery.checkin import DailyCheckIn
from coach.domain.recovery.daily import METRICS, RecoveryMetric

RECOVERY_DAYS: Final = 14
CHECKIN_DAYS: Final = 7
INSIGHT_DAYS: Final = 28
SCALE: Final = "scale 1-5"
PAIN_SCALE: Final = "scale 0-10"
_DATE_FROM: Final = {"from": RANGE_PROPERTIES["from"], "to": RANGE_PROPERTIES["to"]}


def _not_consented(services: AgentServices, context: ToolContext) -> list[str]:
    granted = services.consents.granted(context.scope)
    needed = {spec.consent for spec in METRICS.values()}
    return sorted(item.value for item in needed - granted)


def _metrics(
    services: AgentServices, context: ToolContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    start, end = period(arguments, context, default_days=RECOVERY_DAYS)
    wanted = {RecoveryMetric(item) for item in arguments.get("metrics", list(RecoveryMetric))}
    found = services.recovery.daily(context.scope, until=end, days=(end - start).days + 1)
    days = []
    for when, metrics in found.items():
        values = {}
        for metric, item in sorted(metrics.items()):
            if metric not in wanted:
                continue
            spec = METRICS[metric]
            ref = EvidenceRef(EvidenceKind.DAILY_RECOVERY, item.observation_id)
            values[metric.value] = {
                **quantity(item.value, spec.unit, ref),
                "provider": item.provider,
                "proprietary": spec.consent is ConsentType.PROVIDER_RECOVERY_METRICS,
            }
        if values:
            days.append({"day": day(when), "metrics": values})
    return {
        "from": day(start),
        "to": day(end),
        "days": days,
        "consentMissing": _not_consented(services, context),
    }


def checkin(item: DailyCheckIn) -> dict[str, Any]:
    def scale(name: str, value: int | None) -> dict[str, Any]:
        ref = EvidenceRef(EvidenceKind.DAILY_CHECKIN, item.checkin_id, field=name)
        return quantity(value, SCALE, ref)

    pain = item.pain
    pain_ref = EvidenceRef(EvidenceKind.DAILY_CHECKIN, item.checkin_id, field="pain")
    return {
        "checkinId": str(item.checkin_id),
        "trainingDay": day(item.training_day),
        "recordedAt": instant(item.recorded_at),
        "sleepQuality": scale("sleepQuality", item.sleep_quality),
        "fatigue": scale("fatigue", item.fatigue),
        "soreness": scale("soreness", item.soreness),
        "stress": scale("stress", item.stress),
        "motivation": scale("motivation", item.motivation),
        "painFlag": item.pain_flag,
        "pain": None
        if pain is None
        else {
            "intensity": quantity(pain.intensity, PAIN_SCALE, pain_ref),
            "location": untrusted(pain.location),
            "onset": code(pain.onset),
            "possiblyAcute": pain.possibly_acute,
        },
        "redFlags": [code(flag) for flag in item.red_flags],
        "notes": untrusted(item.notes),
    }


def _checkins(
    services: AgentServices, context: ToolContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    start, end = period(arguments, context, default_days=CHECKIN_DAYS)
    found = services.recovery.checkins(context.scope, until=end, days=(end - start).days + 1)
    return {"from": day(start), "to": day(end), "checkins": [checkin(item) for item in found]}


def _signal_unit(name: str) -> str:
    if name in {item.value for item in RecoveryMetric}:
        return METRICS[RecoveryMetric(name)].unit
    return PAIN_SCALE if name == "pain" else SCALE


def _signal(
    item: Signal, assessment: RecoveryAssessment, granted: frozenset[ConsentType]
) -> dict[str, Any]:
    if item.wearable and METRICS[RecoveryMetric(item.name)].consent not in granted:
        return {"name": item.name, "group": code(item.group), "state": "NO_CONSENT"}
    unit = _signal_unit(item.name)
    refs = (EvidenceRef(EvidenceKind.RECOVERY_ASSESSMENT, assessment.assessment_id), *item.evidence)
    return {
        "name": item.name,
        "group": code(item.group),
        "state": code(item.state),
        "value": quantity(item.value, unit, *refs),
        "baselineMean": quantity(item.baseline_mean, unit, *refs),
        "baselineSd": quantity(item.baseline_sd, unit, *refs),
        "baselineReadings": item.baseline_readings,
        "consecutiveDays": item.consecutive_days,
    }


def prescription(item: RecoveryPrescription) -> dict[str, Any]:
    refs = (EvidenceRef(EvidenceKind.RECOVERY_ASSESSMENT, item.assessment_id), *item.evidence)
    return {
        "prescriptionId": str(item.prescription_id),
        "day": day(item.day),
        "type": code(item.type),
        "reason": code(item.reason),
        "blocksIntensity": item.blocks_intensity,
        "duration": quantity(item.duration_days, "days", *refs),
        "reevaluateAfter": day(item.reevaluate_after),
        "rulesVersion": item.rules_version,
        "evidence": evidence(*refs),
    }


def _status(
    services: AgentServices, context: ToolContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    when = date.fromisoformat(arguments["day"]) if "day" in arguments else context.day()
    found = services.assessments.history(context.scope, until=when, days=1)
    granted = services.consents.granted(context.scope)
    state = services.conditions.current(context.scope)
    assessment = None
    if found:
        item = found[-1]
        ref = EvidenceRef(EvidenceKind.RECOVERY_ASSESSMENT, item.assessment_id)
        assessment = {
            "assessmentId": str(item.assessment_id),
            "status": code(item.status),
            "reasons": [code(reason) for reason in item.reasons],
            "confidence": code(item.confidence),
            "uncertaintyReasons": codes(item.uncertainty_reasons),
            "pain": quantity(item.pain, PAIN_SCALE, ref),
            "signals": [_signal(signal, item, granted) for signal in item.signals],
            "assessedAt": instant(item.assessed_at),
            "rulesVersion": item.rules_version,
        }
    return {
        "day": day(when),
        "assessment": assessment,
        "prescriptionsInForce": [
            prescription(item) for item in services.assessments.prescriptions(context.scope, when)
        ],
        "conditions": codes(state.conditions),
        "lifecycle": code(state.lifecycle),
    }


def _insights(
    services: AgentServices, context: ToolContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    start, end = period(arguments, context, default_days=INSIGHT_DAYS)
    found = services.insights.recent(context.scope, until=end, days=(end - start).days + 1)
    return {
        "from": day(start),
        "to": day(end),
        "insights": [
            {
                "insightId": str(item.insight_id),
                "pattern": code(item.kind),
                "from": day(item.period.start),
                "to": day(item.period.end),
                "observation": item.observation,  # written in code from the evidence
                "relationship": code(item.relationship),
                "association": None
                if item.association is None
                else {
                    "variables": list(item.association.variables),
                    "sampleSize": item.association.sample_size,
                    "windowDays": item.association.window_days,
                },
                "confidence": code(item.confidence),
                "uncertaintyReasons": codes(item.uncertainty_reasons),
                "evidence": evidence(
                    EvidenceRef(EvidenceKind.INSIGHT, item.insight_id), *item.evidence
                ),
                "rulesVersion": item.rules_version,
            }
            for item in found
        ],
    }


def recovery_tools(services: AgentServices) -> list[Tool]:
    return [
        Tool(
            "getRecoveryMetrics",
            ToolKind.READ,
            "Daily recovery metrics from the provider (resting heart rate, nightly HRV, sleep "
            "duration, proprietary Body Battery) per day, only for the consented "
            "categories; says which consents are missing. Default: the last 14 days.",
            schema(
                {
                    **_DATE_FROM,
                    "metrics": {
                        "type": "array",
                        "items": {"enum": [item.value for item in RecoveryMetric]},
                        "uniqueItems": True,
                    },
                }
            ),
            partial(_metrics, services),
        ),
        Tool(
            "getDailyCheckIn",
            ToolKind.READ,
            "The athlete's daily check-ins (sleep quality, fatigue, soreness, stress and "
            "motivation on 1-5, pain 0-10 with location and onset, red flags, notes as "
            "untrusted text). Default: the last 7 days.",
            schema(_DATE_FROM),
            partial(_checkins, services),
        ),
        Tool(
            "getRecoveryStatus",
            ToolKind.READ,
            "The recovery assessment of a day (status, reasons, signals against the "
            "athlete's baseline, confidence), the recovery prescriptions in force and the "
            "active coaching conditions (SAFETY_HOLD, RECOVERY_BLOCK, ...). Default: today.",
            schema({"day": {**DATE, "description": "YYYY-MM-DD (default: today)."}}),
            partial(_status, services),
        ),
        Tool(
            "getInsights",
            ToolKind.READ,
            "Patterns detected in code from the athlete's data (associations, never causes) "
            "with their evidence and confidence. Default: the last 28 days.",
            schema(_DATE_FROM),
            partial(_insights, services),
        ),
    ]
