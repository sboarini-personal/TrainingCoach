"""Insight documents (insights spec): Insight <-> insight contract."""

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
    sorted_values,
)
from coach.domain.common.quality import Confidence, UncertaintyReason
from coach.domain.common.time import DateRange
from coach.domain.insights import (
    PATTERN_RULES_VERSION,
    AgentVersion,
    Association,
    Insight,
    Pattern,
    Relationship,
)


def insight_to_contract(insight: Insight) -> Json:
    document: Json = {
        "schemaVersion": SCHEMA_VERSION,
        "insightId": str(insight.insight_id),
        "athleteId": str(insight.athlete_id),
        "version": insight.version,
        "kind": insight.kind.value,
        "period": {
            "start": insight.period.start.isoformat(),
            "end": insight.period.end.isoformat(),
        },
        "rulesVersion": insight.rules_version,
        "observation": insight.observation,
        "interpretation": insight.interpretation,
        "hypothesis": insight.hypothesis,
        "recommendation": insight.recommendation,
        "relationship": insight.relationship.value,
        "evidence": [evidence(ref) for ref in insight.evidence],
        "confidence": insight.confidence.value,
        "uncertaintyReasons": sorted_values(insight.uncertainty_reasons),
        "generatedAt": instant(insight.generated_at),
        "calculationVersions": dict(insight.calculation_versions),
        "agentVersion": None
        if insight.agent_version is None
        else {
            "promptVersion": insight.agent_version.prompt_version,
            "modelId": insight.agent_version.model_id,
        },
    }
    association = insight.association
    if association is not None:
        document["association"] = {
            "variables": list(association.variables),
            "sampleSize": association.sample_size,
            "windowDays": association.window_days,
        }
    return document


def insight_from_contract(document: Document) -> Insight:
    association = document.get("association")
    agent = document["agentVersion"]
    generated_at = parse_instant(document["generatedAt"])
    period = document.get("period")
    return Insight(
        insight_id=UUID(document["insightId"]),
        athlete_id=UUID(document["athleteId"]),
        version=document.get("version", 1),
        kind=Pattern(document["kind"]),
        period=DateRange(date.fromisoformat(period["start"]), date.fromisoformat(period["end"]))
        if period is not None
        else DateRange(generated_at.date(), generated_at.date()),
        rules_version=document.get("rulesVersion", PATTERN_RULES_VERSION),
        observation=document["observation"],
        interpretation=document["interpretation"],
        hypothesis=document["hypothesis"],
        recommendation=document["recommendation"],
        relationship=Relationship(document["relationship"]),
        association=None
        if association is None
        else Association(
            tuple(association["variables"]),
            association["sampleSize"],
            association["windowDays"],
        ),
        evidence=tuple(parse_evidence(ref) for ref in document["evidence"]),
        confidence=Confidence(document["confidence"]),
        uncertainty_reasons=frozenset(
            UncertaintyReason(reason) for reason in document["uncertaintyReasons"]
        ),
        generated_at=generated_at,
        calculation_versions=tuple(sorted(document["calculationVersions"].items())),
        agent_version=None
        if agent is None
        else AgentVersion(agent["promptVersion"], agent["modelId"]),
    )
