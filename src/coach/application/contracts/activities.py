"""Activities, source observations, overrides and quality issues <-> their contracts.

Field values are converted per field kind (coach.domain.activity.fields). Activity documents
nest fields by path (``distance.meters`` -> ``{"distance": {"meters": ...}}``); observation
documents keep the flat path keys.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime
from typing import Any, Final
from uuid import UUID

from coach.application.contracts._json import (
    Document,
    Json,
    instant,
    optional_instant,
    optional_uuid,
    parse_instant,
    parse_optional_instant,
    parse_optional_uuid,
    sorted_values,
)
from coach.domain.activity.activity import (
    Activity,
    ActivityStatus,
    FieldProvenance,
    LinkKind,
    SourceLink,
)
from coach.domain.activity.fields import (
    CATALOG,
    Cadence,
    CadenceUnit,
    FieldKind,
    FieldSpec,
    HrZones,
    Pain,
    PowerSource,
    PowerSummary,
    ProviderMetric,
    StreamKind,
    StreamRef,
    Swolf,
    field_spec,
)
from coach.domain.activity.issues import (
    DataQualityIssue,
    IssueKind,
    IssueResolution,
    IssueStatus,
)
from coach.domain.activity.observation import Observation, SourceKind
from coach.domain.activity.overrides import DataOverride, EntityType
from coach.domain.common.provenance import MetricOrigin
from coach.domain.common.quality import (
    Anomaly,
    Confidence,
    DataQuality,
    Reliability,
    UncertaintyReason,
)
from coach.domain.raw.source import SourceOrigin

# Contract versions written by this code (spec/README.md change process). P9 added the power
# sensor field (activity 1.1.0, observation 1.3.0, override 1.1.0).
ACTIVITY_SCHEMA_VERSION: Final = "1.1.0"
OBSERVATION_SCHEMA_VERSION: Final = "1.3.0"
OVERRIDE_SCHEMA_VERSION: Final = "1.1.0"
ISSUE_SCHEMA_VERSION: Final = "1.1.0"


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _cadence(value: Cadence) -> Json:
    return {"average": value.average, "max": value.max, "unit": value.unit.value}


def _parse_cadence(document: Document) -> Cadence:
    return Cadence(
        _optional_float(document["average"]),
        _optional_float(document["max"]),
        CadenceUnit(document["unit"]),
    )


def _power(value: PowerSummary) -> Json:
    return {
        "source": value.source.value,
        "averageWatts": value.average_watts,
        "maxWatts": value.max_watts,
    }


def _parse_power(document: Document) -> PowerSummary:
    return PowerSummary(
        PowerSource(document["source"]),
        _optional_float(document["averageWatts"]),
        _optional_float(document["maxWatts"]),
    )


def _zones(value: HrZones) -> Json:
    return {"zoneModelId": str(value.zone_model_id), "secondsInZone": list(value.seconds_in_zone)}


def _parse_zones(document: Document) -> HrZones:
    return HrZones(
        UUID(document["zoneModelId"]),
        tuple(float(seconds) for seconds in document["secondsInZone"]),
    )


def _swolf(value: Swolf) -> Json:
    return {"value": value.value, "origin": value.origin.value}


def _parse_swolf(document: Document) -> Swolf:
    return Swolf(float(document["value"]), MetricOrigin(document["origin"]))


def _provider_metric(value: ProviderMetric) -> Json:
    return {
        "value": value.value,
        "provider": value.provider,
        "metricName": value.metric_name,
        "origin": value.origin.value,
    }


def _parse_provider_metric(document: Document) -> ProviderMetric:
    return ProviderMetric(
        _optional_float(document["value"]),
        document["provider"],
        document["metricName"],
        MetricOrigin(document["origin"]),
    )


def _pain(value: Pain) -> Json:
    return {"flag": value.flag, "intensity": value.intensity, "location": value.location}


def _parse_pain(document: Document) -> Pain:
    return Pain(document["flag"], document.get("intensity"), document.get("location"))


_TO_JSON: Final[Mapping[FieldKind, Callable[[Any], Any]]] = {
    FieldKind.NUMBER: lambda value: value,
    FieldKind.INTEGER: lambda value: value,
    FieldKind.TEXT: lambda value: value,
    FieldKind.ENUM: lambda value: value.value,
    FieldKind.SUBTYPE: lambda value: value,
    FieldKind.INSTANT: instant,
    FieldKind.LOCAL_DATETIME: lambda value: value.isoformat(),
    FieldKind.DATE: lambda value: value.isoformat(),
    FieldKind.TIMEZONE: lambda value: value,
    FieldKind.UTC_OFFSET: lambda value: value,
    FieldKind.UUID: str,
    FieldKind.CADENCE: _cadence,
    FieldKind.POWER: _power,
    FieldKind.HR_ZONES: _zones,
    FieldKind.SWOLF: _swolf,
    FieldKind.PROVIDER_METRIC: _provider_metric,
    FieldKind.PAIN: _pain,
}
_FROM_JSON: Final[Mapping[FieldKind, Callable[[Any], Any]]] = {
    FieldKind.NUMBER: lambda value: value,
    FieldKind.INTEGER: lambda value: value,
    FieldKind.TEXT: lambda value: value,
    FieldKind.ENUM: lambda value: value,
    FieldKind.SUBTYPE: lambda value: value,
    FieldKind.INSTANT: parse_instant,
    FieldKind.LOCAL_DATETIME: datetime.fromisoformat,
    FieldKind.DATE: date.fromisoformat,
    FieldKind.TIMEZONE: lambda value: value,
    FieldKind.UTC_OFFSET: lambda value: value,
    FieldKind.UUID: UUID,
    FieldKind.CADENCE: _parse_cadence,
    FieldKind.POWER: _parse_power,
    FieldKind.HR_ZONES: _parse_zones,
    FieldKind.SWOLF: _parse_swolf,
    FieldKind.PROVIDER_METRIC: _parse_provider_metric,
    FieldKind.PAIN: _parse_pain,
}


def field_to_json(spec: FieldSpec, value: object) -> Any:
    return None if value is None else _TO_JSON[spec.kind](value)


def field_from_json(spec: FieldSpec, value: Any) -> object:
    return spec.check(None if value is None else _FROM_JSON[spec.kind](value))


def _anomalies(anomalies: Iterable[Anomaly]) -> list[Json]:
    documents = []
    for anomaly in anomalies:
        document: Json = {"code": str(anomaly.code), "field": anomaly.field}
        if anomaly.detail:
            document["detail"] = anomaly.detail
        documents.append(document)
    return documents


def _parse_anomalies(documents: Iterable[Document]) -> tuple[Anomaly, ...]:
    return tuple(Anomaly(item["code"], item["field"], item.get("detail", "")) for item in documents)


def quality_to_contract(quality: DataQuality) -> Json:
    return {
        "completeness": quality.completeness,
        "reliability": quality.reliability.value,
        "confidence": quality.confidence.value,
        "anomalies": _anomalies(quality.anomalies),
        "uncertaintyReasons": sorted_values(quality.uncertainty_reasons),
        "warnings": list(quality.warnings),
    }


def quality_from_contract(document: Document) -> DataQuality:
    return DataQuality(
        completeness=float(document["completeness"]),
        reliability=Reliability(document["reliability"]),
        confidence=Confidence(document["confidence"]),
        anomalies=_parse_anomalies(document["anomalies"]),
        uncertainty_reasons=frozenset(
            UncertaintyReason(reason) for reason in document["uncertaintyReasons"]
        ),
        warnings=tuple(document.get("warnings", ())),
    )


def _streams(streams: Iterable[StreamRef]) -> list[Json]:
    return [{"kind": stream.kind.value, "blobSha256": stream.blob_sha256} for stream in streams]


def _parse_streams(documents: Iterable[Document]) -> tuple[StreamRef, ...]:
    return tuple(StreamRef(StreamKind(item["kind"]), item["blobSha256"]) for item in documents)


def _link(link: SourceLink) -> Json:
    return {
        "sourceId": str(link.source_id),
        "part": link.part,
        "origin": link.origin.value,
        "provider": link.provider,
        "providerActivityId": link.provider_activity_id,
        "fileHash": link.file_hash,
        "linkKind": link.link_kind.value,
        "matchScore": link.match_score,
        "dedupRulesVersion": link.dedup_rules_version,
        "linkedAt": instant(link.linked_at),
    }


def _parse_link(document: Document) -> SourceLink:
    return SourceLink(
        source_id=UUID(document["sourceId"]),
        part=document["part"],
        origin=SourceOrigin(document["origin"]),
        provider=document["provider"],
        provider_activity_id=document["providerActivityId"],
        file_hash=document["fileHash"],
        link_kind=LinkKind(document["linkKind"]),
        match_score=document.get("matchScore"),
        dedup_rules_version=document.get("dedupRulesVersion"),
        linked_at=parse_instant(document["linkedAt"]),
    )


def _field_provenance(origin: FieldProvenance) -> Json:
    document: Json = {"sourceId": str(origin.source_id), "rule": origin.rule}
    if origin.path is not None:
        document["path"] = origin.path
    if origin.override_id is not None:
        document["overrideId"] = str(origin.override_id)
    return document


def _parse_field_provenance(document: Document) -> FieldProvenance:
    return FieldProvenance(
        UUID(document["sourceId"]),
        document["rule"],
        document.get("path"),
        parse_optional_uuid(document.get("overrideId")),
    )


def activity_to_contract(activity: Activity) -> Json:
    document: Json = {
        "schemaVersion": ACTIVITY_SCHEMA_VERSION,
        "id": str(activity.activity_id),
        "athleteId": str(activity.athlete_id),
        "version": activity.version,
        "status": activity.status.value,
    }
    if activity.merged_into is not None:
        document["mergedIntoId"] = str(activity.merged_into)
    document["provenance"] = {
        "sources": [_link(link) for link in activity.sources],
        "importedAt": instant(activity.imported_at),
        "resolutionRulesVersion": activity.resolution_rules_version,
        "qualityRulesVersion": activity.quality_rules_version,
        "fields": {path: _field_provenance(origin) for path, origin in activity.provenance.items()},
    }
    for path, value in activity.values.items():
        *groups, leaf = path.split(".")
        target = document
        for group in groups:
            target = target.setdefault(group, {})
        target[leaf] = field_to_json(field_spec(path), value)
    document["streams"] = _streams(activity.streams)
    document["quality"] = quality_to_contract(activity.quality)
    return document


def _nested(document: Document, path: str) -> tuple[bool, Any]:
    node: Any = document
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return False, None
        node = node[part]
    return True, node


def activity_from_contract(document: Document) -> Activity:
    values: dict[str, object] = {}
    for path, spec in CATALOG.items():
        present, raw = _nested(document, path)
        if present:
            values[path] = field_from_json(spec, raw)
    provenance = document["provenance"]
    return Activity(
        activity_id=UUID(document["id"]),
        athlete_id=UUID(document["athleteId"]),
        version=document["version"],
        status=ActivityStatus(document["status"]),
        merged_into=parse_optional_uuid(document.get("mergedIntoId")),
        sources=tuple(_parse_link(link) for link in provenance["sources"]),
        imported_at=parse_instant(provenance["importedAt"]),
        resolution_rules_version=provenance["resolutionRulesVersion"],
        quality_rules_version=provenance["qualityRulesVersion"],
        values=values,
        provenance={
            path: _parse_field_provenance(origin) for path, origin in provenance["fields"].items()
        },
        streams=_parse_streams(document.get("streams", ())),
        quality=quality_from_contract(document["quality"]),
    )


def observation_to_contract(observation: Observation) -> Json:
    return {
        "schemaVersion": OBSERVATION_SCHEMA_VERSION,
        "rawSourceId": str(observation.raw_source_id),
        "part": observation.part,
        "athleteId": str(observation.athlete_id),
        "origin": observation.origin.value,
        "sourceKind": observation.kind.value,
        "provider": observation.provider,
        "providerActivityId": observation.provider_activity_id,
        "rawSha256": observation.raw_sha256,
        "receivedAt": instant(observation.received_at),
        "normaliser": {"name": observation.normaliser, "version": observation.normaliser_version},
        "values": {
            path: field_to_json(field_spec(path), value)
            for path, value in observation.values.items()
        },
        "sourcePaths": dict(observation.source_paths),
        "streams": _streams(observation.streams),
        "anomalies": _anomalies(observation.anomalies),
    }


def observation_from_contract(document: Document) -> Observation:
    return Observation(
        raw_source_id=UUID(document["rawSourceId"]),
        part=document["part"],
        athlete_id=UUID(document["athleteId"]),
        origin=SourceOrigin(document["origin"]),
        kind=SourceKind(document["sourceKind"]),
        provider=document["provider"],
        provider_activity_id=document["providerActivityId"],
        raw_sha256=document["rawSha256"],
        received_at=parse_instant(document["receivedAt"]),
        normaliser=document["normaliser"]["name"],
        normaliser_version=document["normaliser"]["version"],
        values={
            path: field_from_json(field_spec(path), value)
            for path, value in document["values"].items()
        },
        source_paths=dict(document["sourcePaths"]),
        streams=_parse_streams(document["streams"]),
        anomalies=_parse_anomalies(document["anomalies"]),
    )


def override_to_contract(override: DataOverride) -> Json:
    spec = field_spec(override.field)
    return {
        "schemaVersion": OVERRIDE_SCHEMA_VERSION,
        "overrideId": str(override.override_id),
        "athleteId": str(override.athlete_id),
        "entityType": override.entity_type.value,
        "entityId": str(override.entity_id),
        "field": override.field,
        "originalValue": field_to_json(spec, override.original_value),
        "correctedValue": field_to_json(spec, override.corrected_value),
        "reason": override.reason,
        "correctedAt": instant(override.corrected_at),
        "correctedBy": str(override.corrected_by),
        "supersedesOverrideId": optional_uuid(override.supersedes_override_id),
    }


def override_from_contract(document: Document) -> DataOverride:
    spec = field_spec(document["field"])
    original = document["originalValue"]
    return DataOverride(
        override_id=UUID(document["overrideId"]),
        athlete_id=UUID(document["athleteId"]),
        entity_type=EntityType(document["entityType"]),
        entity_id=UUID(document["entityId"]),
        field=document["field"],
        original_value=None if original is None else _FROM_JSON[spec.kind](original),
        corrected_value=field_from_json(spec, document["correctedValue"]),
        reason=document["reason"],
        corrected_at=parse_instant(document["correctedAt"]),
        corrected_by=UUID(document["correctedBy"]),
        supersedes_override_id=parse_optional_uuid(document["supersedesOverrideId"]),
    )


def issue_to_contract(issue: DataQualityIssue) -> Json:
    document: Json = {
        "schemaVersion": ISSUE_SCHEMA_VERSION,
        "issueId": str(issue.issue_id),
        "athleteId": str(issue.athlete_id),
        "version": issue.version,
        "kind": issue.kind.value,
        "code": issue.code,
        "field": issue.field,
    }
    if issue.activity_id is not None:
        document["activityId"] = str(issue.activity_id)
    if issue.ingestion_id is not None:
        document["ingestionId"] = str(issue.ingestion_id)
    if issue.detail is not None:
        document["detail"] = issue.detail
    if issue.kind is IssueKind.AMBIGUOUS_MATCH:
        document["relatedActivityId"] = str(issue.related_activity_id)
        document["matchScore"] = issue.match_score
        document["dedupRulesVersion"] = issue.dedup_rules_version
    document.update(
        {
            "status": issue.status.value,
            "createdAt": instant(issue.created_at),
            "closedAt": optional_instant(issue.closed_at),
            "resolution": None if issue.resolution is None else issue.resolution.value,
        }
    )
    return document


def issue_from_contract(document: Document) -> DataQualityIssue:
    resolution = document["resolution"]
    return DataQualityIssue(
        issue_id=UUID(document["issueId"]),
        athlete_id=UUID(document["athleteId"]),
        version=document["version"],
        kind=IssueKind(document["kind"]),
        activity_id=parse_optional_uuid(document.get("activityId")),
        ingestion_id=parse_optional_uuid(document.get("ingestionId")),
        code=document["code"],
        field=document["field"],
        detail=document.get("detail"),
        related_activity_id=parse_optional_uuid(document.get("relatedActivityId")),
        match_score=document.get("matchScore"),
        dedup_rules_version=document.get("dedupRulesVersion"),
        status=IssueStatus(document["status"]),
        created_at=parse_instant(document["createdAt"]),
        closed_at=parse_optional_instant(document["closedAt"]),
        resolution=None if resolution is None else IssueResolution(resolution),
    )
