"""Report documents (reporting spec): ReportData -> report-data contract. Reports are read
models, never stored, so there is no decoder."""

from __future__ import annotations

from coach.application.contracts._json import (
    SCHEMA_VERSION,
    Json,
    evidence,
    instant,
    sorted_values,
)
from coach.domain.reports import ReportData, ReportItem, Section


def _item(item: ReportItem) -> Json:
    return {
        "label": item.label,
        "value": item.value,
        "unit": item.unit,
        "evidence": [evidence(ref) for ref in item.evidence],
        "confidence": None if item.confidence is None else item.confidence.value,
        "uncertaintyReasons": sorted_values(item.reasons),
    }


def _section(section: Section) -> Json:
    return {
        "name": section.name,
        "unavailable": None if section.unavailable is None else section.unavailable.value,
        "items": [_item(item) for item in section.items],
    }


def report_to_contract(report: ReportData) -> Json:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": report.kind.value,
        "athleteId": str(report.athlete_id),
        "subject": report.subject,
        "period": {"start": report.period.start.isoformat(), "end": report.period.end.isoformat()},
        "generatedAt": instant(report.generated_at),
        "sections": [_section(section) for section in report.sections],
        "confidence": report.confidence.value,
        "uncertaintyReasons": sorted_values(report.uncertainty_reasons),
        "missingData": [
            {
                "section": item.section,
                "reason": None if item.reason is None else item.reason.value,
                "item": item.item,
            }
            for item in report.missing_data
        ],
    }
