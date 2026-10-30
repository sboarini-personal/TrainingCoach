"""Recovery documents (recovery spec): DailyCheckIn <-> daily-checkin contract,
RecoveryObservation <-> recovery-observation, RecoveryAssessment <-> recovery-assessment and
RecoveryPrescription <-> recovery-prescription."""

from __future__ import annotations

from datetime import date
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
    sorted_values,
)
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.quality import Confidence, UncertaintyReason
from coach.domain.recovery.assessment import (
    PrescriptionType,
    RecoveryAssessment,
    RecoveryPrescription,
    RecoveryStatus,
    Signal,
    SignalState,
)
from coach.domain.recovery.assessment import Reason as AssessmentReason
from coach.domain.recovery.checkin import CheckInPain, DailyCheckIn, PainOnset, RedFlag
from coach.domain.recovery.daily import METRICS, RecoveryMetric, RecoveryObservation


def checkin_to_contract(checkin: DailyCheckIn) -> Json:
    pain = checkin.pain
    return {
        "schemaVersion": SCHEMA_VERSION,
        "checkinId": str(checkin.checkin_id),
        "athleteId": str(checkin.athlete_id),
        "version": checkin.version,
        "recordedAt": instant(checkin.recorded_at),
        "trainingDay": checkin.training_day.isoformat(),
        "sleepQuality": checkin.sleep_quality,
        "fatigue": checkin.fatigue,
        "soreness": checkin.soreness,
        "stress": checkin.stress,
        "motivation": checkin.motivation,
        "painFlag": checkin.pain_flag,
        "pain": None
        if pain is None
        else {
            "intensity": pain.intensity,
            "location": pain.location,
            "onset": None if pain.onset is None else pain.onset.value,
        },
        "redFlags": [flag.value for flag in checkin.red_flags],
        "detectedFlags": [flag.value for flag in checkin.detected_flags],
        "notes": checkin.notes,
        "supersedes": None if checkin.supersedes is None else str(checkin.supersedes),
    }


def checkin_from_contract(document: Document) -> DailyCheckIn:
    pain = document["pain"]
    return DailyCheckIn(
        checkin_id=UUID(document["checkinId"]),
        athlete_id=UUID(document["athleteId"]),
        version=document["version"],
        recorded_at=parse_instant(document["recordedAt"]),
        training_day=date.fromisoformat(document["trainingDay"]),
        sleep_quality=document["sleepQuality"],
        fatigue=document["fatigue"],
        soreness=document["soreness"],
        stress=document["stress"],
        motivation=document["motivation"],
        pain_flag=document["painFlag"],
        pain=None
        if pain is None
        else CheckInPain(
            intensity=pain["intensity"],
            location=pain["location"],
            onset=None if pain["onset"] is None else PainOnset(pain["onset"]),
        ),
        red_flags=tuple(RedFlag(flag) for flag in document["redFlags"]),
        detected_flags=tuple(RedFlag(flag) for flag in document.get("detectedFlags", [])),
        notes=document["notes"],
        supersedes=parse_optional_uuid(document["supersedes"]),
    )


def recovery_observation_to_contract(observation: RecoveryObservation) -> Json:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "observationId": str(observation.observation_id),
        "athleteId": str(observation.athlete_id),
        "version": observation.version,
        "day": observation.day.isoformat(),
        "metric": observation.metric.value,
        "value": observation.value,
        "unit": METRICS[observation.metric].unit,
        "provider": observation.provider,
        "rawSourceId": str(observation.raw_source_id),
        "sourcePath": observation.source_path,
        "receivedAt": instant(observation.received_at),
    }


def recovery_observation_from_contract(document: Document) -> RecoveryObservation:
    metric = RecoveryMetric(document["metric"])
    if document["unit"] != METRICS[metric].unit:
        raise DomainValidationError(f"{metric} is measured in {METRICS[metric].unit}")
    return RecoveryObservation(
        observation_id=UUID(document["observationId"]),
        athlete_id=UUID(document["athleteId"]),
        version=document["version"],
        day=date.fromisoformat(document["day"]),
        metric=metric,
        value=float(document["value"]),
        provider=document["provider"],
        raw_source_id=UUID(document["rawSourceId"]),
        source_path=document["sourcePath"],
        received_at=parse_instant(document["receivedAt"]),
    )


def _signal_to_contract(signal: Signal) -> Json:
    return {
        "name": signal.name,
        "group": signal.group.value,
        "state": signal.state.value,
        "value": signal.value,
        "baselineMean": signal.baseline_mean,
        "baselineSd": signal.baseline_sd,
        "baselineReadings": signal.baseline_readings,
        "consecutiveDays": signal.consecutive_days,
        "evidence": [evidence(ref) for ref in signal.evidence],
    }


def _optional_float(value: float | None) -> float | None:
    return None if value is None else float(value)


def _signal_from_contract(document: Document) -> Signal:
    return Signal(
        name=document["name"],
        state=SignalState(document["state"]),
        value=_optional_float(document["value"]),
        baseline_mean=_optional_float(document["baselineMean"]),
        baseline_sd=_optional_float(document["baselineSd"]),
        baseline_readings=document["baselineReadings"],
        consecutive_days=document["consecutiveDays"],
        evidence=tuple(parse_evidence(ref) for ref in document["evidence"]),
    )


def assessment_to_contract(assessment: RecoveryAssessment) -> Json:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "assessmentId": str(assessment.assessment_id),
        "athleteId": str(assessment.athlete_id),
        "version": assessment.version,
        "day": assessment.day.isoformat(),
        "status": assessment.status.value,
        "reasons": [reason.value for reason in assessment.reasons],
        "signals": [_signal_to_contract(signal) for signal in assessment.signals],
        "pain": assessment.pain,
        "confidence": assessment.confidence.value,
        "uncertaintyReasons": sorted_values(assessment.uncertainty_reasons),
        "evidence": [evidence(ref) for ref in assessment.evidence],
        "assessedAt": instant(assessment.assessed_at),
        "rulesVersion": assessment.rules_version,
    }


def assessment_from_contract(document: Document) -> RecoveryAssessment:
    return RecoveryAssessment(
        assessment_id=UUID(document["assessmentId"]),
        athlete_id=UUID(document["athleteId"]),
        version=document["version"],
        day=date.fromisoformat(document["day"]),
        status=RecoveryStatus(document["status"]),
        reasons=tuple(AssessmentReason(reason) for reason in document["reasons"]),
        signals=tuple(_signal_from_contract(signal) for signal in document["signals"]),
        pain=document["pain"],
        confidence=Confidence(document["confidence"]),
        uncertainty_reasons=frozenset(
            UncertaintyReason(reason) for reason in document["uncertaintyReasons"]
        ),
        assessed_at=parse_instant(document["assessedAt"]),
        rules_version=document["rulesVersion"],
    )


def prescription_to_contract(prescription: RecoveryPrescription) -> Json:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "prescriptionId": str(prescription.prescription_id),
        "athleteId": str(prescription.athlete_id),
        "version": prescription.version,
        "assessmentId": str(prescription.assessment_id),
        "day": prescription.day.isoformat(),
        "type": prescription.type.value,
        "reason": prescription.reason.value,
        "evidence": [evidence(ref) for ref in prescription.evidence],
        "durationDays": prescription.duration_days,
        "reevaluateAfter": prescription.reevaluate_after.isoformat(),
        "createdAt": instant(prescription.created_at),
        "rulesVersion": prescription.rules_version,
    }


def prescription_from_contract(document: Document) -> RecoveryPrescription:
    return RecoveryPrescription(
        prescription_id=UUID(document["prescriptionId"]),
        athlete_id=UUID(document["athleteId"]),
        version=document["version"],
        assessment_id=UUID(document["assessmentId"]),
        day=date.fromisoformat(document["day"]),
        type=PrescriptionType(document["type"]),
        reason=AssessmentReason(document["reason"]),
        evidence=tuple(parse_evidence(ref) for ref in document["evidence"]),
        duration_days=document["durationDays"],
        reevaluate_after=date.fromisoformat(document["reevaluateAfter"]),
        created_at=parse_instant(document["createdAt"]),
        rules_version=document["rulesVersion"],
    )
