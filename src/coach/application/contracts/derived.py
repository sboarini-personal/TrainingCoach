"""DerivedMetricValue <-> derived-metric-value contract."""

from __future__ import annotations

from uuid import UUID

from coach.application.contracts._json import (
    SCHEMA_VERSION,
    Document,
    Json,
    evidence,
    instant,
    optional_uuid,
    parse_evidence,
    parse_instant,
    parse_optional_uuid,
    sorted_values,
)
from coach.domain.common.quality import Confidence, UncertaintyReason
from coach.metrics.derived import (
    DerivedMetricValue,
    DerivedStatus,
    InputRecord,
    MetricSubject,
    SubjectKind,
)


def derived_value_to_contract(value: DerivedMetricValue) -> Json:
    confidence = value.input_confidence
    return {
        "schemaVersion": SCHEMA_VERSION,
        "valueId": str(value.value_id),
        "athleteId": str(value.athlete_id),
        "metricId": value.metric_id,
        "calculationVersion": value.calculation_version,
        "subject": {"kind": value.subject.kind.value, "key": value.subject.key},
        "status": value.status.value,
        "value": value.value,
        "unit": value.unit,
        "inputs": [
            {
                "name": record.name,
                "value": record.value,
                "evidence": evidence(record.evidence),
                "confidence": record.confidence.value,
            }
            for record in value.inputs
        ],
        "missingInputs": list(value.missing_inputs),
        "inputConfidence": None if confidence is None else confidence.value,
        "uncertaintyReasons": sorted_values(value.uncertainty_reasons),
        "computedAt": instant(value.computed_at),
        "supersededBy": optional_uuid(value.superseded_by),
    }


def derived_value_from_contract(document: Document) -> DerivedMetricValue:
    confidence = document["inputConfidence"]
    return DerivedMetricValue(
        value_id=UUID(document["valueId"]),
        athlete_id=UUID(document["athleteId"]),
        metric_id=document["metricId"],
        calculation_version=document["calculationVersion"],
        subject=MetricSubject(SubjectKind(document["subject"]["kind"]), document["subject"]["key"]),
        status=DerivedStatus(document["status"]),
        value=document["value"],
        unit=document["unit"],
        inputs=tuple(
            InputRecord(
                item["name"],
                item["value"],
                parse_evidence(item["evidence"]),
                Confidence(item["confidence"]),
            )
            for item in document["inputs"]
        ),
        missing_inputs=tuple(document["missingInputs"]),
        input_confidence=None if confidence is None else Confidence(confidence),
        uncertainty_reasons=frozenset(
            UncertaintyReason(item) for item in document["uncertaintyReasons"]
        ),
        computed_at=parse_instant(document["computedAt"]),
        superseded_by=parse_optional_uuid(document["supersededBy"]),
    )
