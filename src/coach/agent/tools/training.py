"""Read tools over training data: activities, one activity in detail, data quality issues and
strength history (P13.2; tools contract section 3).

Activities are read in their EFFECTIVE view (corrections applied) and only with the
ACTIVITIES consent: sending them to the AI is processing (privacy spec 1, agent context
assembly). Every value carries its field path as evidence; names, descriptions and comments
are untrusted text. Streams are never sent (GPS positions above all, PRIV-CON-004): only
which streams exist.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta
from enum import Enum
from functools import partial
from typing import Any, Final
from uuid import UUID

from coach.agent.services import AgentServices
from coach.agent.tools.base import (
    ID,
    RANGE_PROPERTIES,
    UNITLESS,
    Tool,
    ToolContext,
    ToolKind,
    code,
    code_or_text,
    codes,
    day,
    evidence,
    instant,
    period,
    quantity,
    schema,
    untrusted,
)
from coach.domain.activity.activity import Activity
from coach.domain.activity.fields import (
    CATALOG,
    Cadence,
    CadenceUnit,
    FieldKind,
    HrZones,
    Pain,
    PowerSummary,
    ProviderMetric,
    Swolf,
)
from coach.domain.activity.issues import DataQualityIssue
from coach.domain.common.evidence import EvidenceKind, EvidenceRef
from coach.domain.common.quality import DataQuality
from coach.domain.common.sport import Sport
from coach.domain.common.time import DateRange
from coach.domain.consent.ledger import ConsentType
from coach.metrics.subjects import week_key

ACTIVITY_DAYS: Final = 14
STRENGTH_DAYS: Final = 28
MAX_ACTIVITIES: Final = 100
FIELD_UNITS: Final[Mapping[str, str]] = {
    "timing.elapsedSeconds": "s",
    "timing.movingSeconds": "s",
    "distance.meters": "m",
    "elevation.gainMeters": "m",
    "elevation.lossMeters": "m",
    "cardiovascular.averageHr": "bpm",
    "cardiovascular.maxHr": "bpm",
    "performance.averageSpeedMps": "m/s",
    "performance.maxSpeedMps": "m/s",
    "performance.averagePaceSecondsPerKm": "s/km",
    "performance.averagePaceSecondsPer100m": "s/100m",
    "performance.runningDynamics.strideLengthMeters": "m",
    "performance.runningDynamics.verticalOscillationMm": "mm",
    "performance.runningDynamics.groundContactTimeMs": "ms",
    "swimming.lengthCount": "lengths",
    "swimming.averageStrokeRate": "strokes/min",
    "training.load.external.workKilojoules": "kJ",
    "training.load.external.volumeLoadKg": "kg",
    "training.load.internal.sessionRpeLoad": "AU (RPE x min)",
    "subjective.rpe": "RPE 0-10",
    "subjective.soreness": "scale 1-5",
    "environment.temperatureC": "C",
    "environment.humidityPct": "%",
    "environment.windMps": "m/s",
    "environment.altitudeMeters": "m",
    "environment.poolLengthMeters": "m",
}
_CADENCE: Final = {
    CadenceUnit.STEPS_PER_MIN: "steps/min",
    CadenceUnit.RPM: "rpm",
    CadenceUnit.STROKES_PER_MIN: "strokes/min",
}
# Meters that are not a distance: never shown in km.
NOT_DISTANCES: Final = frozenset(
    {
        "elevation.gainMeters",
        "elevation.lossMeters",
        "environment.altitudeMeters",
        "environment.poolLengthMeters",
        "performance.runningDynamics.strideLengthMeters",
    }
)
SUMMARY_FIELDS: Final = (
    "classification.subtype",
    "metadata.name",
    "timing.originalLocalTime",
    "timing.elapsedSeconds",
    "timing.movingSeconds",
    "distance.meters",
    "cardiovascular.averageHr",
    "subjective.rpe",
    "training.load.internal.sessionRpeLoad",
)


def field_value(path: str, value: object, ref: EvidenceRef) -> Any:
    """An activity field as tool JSON (the field catalogue says what it is)."""
    if value is None:
        return None
    kind = CATALOG[path].kind
    match value:
        case bool():
            return value
        case int() | float() if kind in {FieldKind.NUMBER, FieldKind.INTEGER}:
            unit = FIELD_UNITS.get(path, UNITLESS)
            return quantity(float(value), unit, ref, display=path not in NOT_DISTANCES)
        case str() if kind is FieldKind.TEXT:
            return untrusted(value)
        case str():  # a subtype, an IANA timezone or a UTC offset, validated on input
            return value
        case Enum():
            return code(value)
        case datetime() | date():
            return value.isoformat()
        case UUID():
            return str(value)
        case Cadence():
            unit = _CADENCE[value.unit]
            return {
                "average": quantity(value.average, unit, ref),
                "max": quantity(value.max, unit, ref),
            }
        case PowerSummary():
            return {
                "source": code(value.source),
                "average": quantity(value.average_watts, "W", ref),
                "max": quantity(value.max_watts, "W", ref),
            }
        case HrZones():
            model = EvidenceRef(EvidenceKind.HR_ZONE_MODEL, value.zone_model_id)
            return {
                "zoneModelId": str(value.zone_model_id),
                "secondsInZone": {
                    "values": [round(item) for item in value.seconds_in_zone],
                    "unit": "s",
                    "evidence": evidence(ref, model),
                },
            }
        case Swolf():
            return {"value": quantity(value.value, "SWOLF", ref), "origin": code(value.origin)}
        case ProviderMetric():
            return {
                "value": quantity(value.value, "provider scale", ref),
                "provider": value.provider,
                "metricName": untrusted(value.metric_name),
                "origin": code(value.origin),
            }
        case Pain():
            return {
                "flag": value.flag,
                "intensity": None
                if value.intensity is None
                else quantity(value.intensity, "scale 0-10", ref),
                "location": untrusted(value.location),
            }
    raise TypeError(f"no tool form for {path}")


def _ref(view: Activity, path: str) -> EvidenceRef:
    return EvidenceRef(EvidenceKind.ACTIVITY, view.activity_id, field=path)


def quality(view: Activity) -> dict[str, Any]:
    found: DataQuality = view.quality
    return {
        "completeness": quantity(found.completeness, "share 0-1", _ref(view, "quality"), digits=2),
        "reliability": code(found.reliability),
        "confidence": code(found.confidence),
        "anomalies": [{"code": item.code, "field": item.field} for item in found.anomalies],
        "uncertaintyReasons": codes(found.uncertainty_reasons),
        "warnings": [code_or_text(item) for item in found.warnings],
    }


def summary(view: Activity) -> dict[str, Any]:
    fields = {
        path: field_value(path, view.values.get(path), _ref(view, path)) for path in SUMMARY_FIELDS
    }
    return {
        "activityId": str(view.activity_id),
        "trainingDay": day(view.training_day),
        "sport": code(view.sport),
        "status": code(view.status),
        "fields": fields,
        "quality": quality(view),
    }


def _activities(
    services: AgentServices, context: ToolContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    start, end = period(arguments, context, default_days=ACTIVITY_DAYS)
    views = services.analytics.activities(context.scope, DateRange(start, end))
    if "sport" in arguments:
        views = [view for view in views if view.sport.value == arguments["sport"]]
    views.sort(key=lambda view: view.start_time_utc, reverse=True)
    limit = int(arguments.get("limit", 50))
    return {
        "from": day(start),
        "to": day(end),
        "count": len(views),
        "truncated": len(views) > limit,
        "activities": [summary(view) for view in views[:limit]],
    }


def _detail(
    services: AgentServices, context: ToolContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    services.consents.require(context.scope, ConsentType.ACTIVITIES)
    activity_id = UUID(arguments["activityId"])
    view = services.activities.effective(context.scope, activity_id).activity
    metrics = services.analytics.activity_metrics(context.scope, activity_id)
    issues = [
        item
        for item in services.activities.issues(context.scope)
        if activity_id in {item.activity_id, item.related_activity_id}
    ]
    corrected = sorted(
        path for path, origin in view.provenance.items() if origin.override_id is not None
    )
    return {
        **summary(view),
        "fields": {
            path: field_value(path, value, _ref(view, path))
            for path, value in sorted(view.values.items())
            if path in CATALOG
        },
        "correctedFields": corrected,
        "streams": sorted({code(item.kind) for item in view.streams}, key=str),
        "sources": [
            {
                "origin": code(item.origin),
                "provider": item.provider,
                "linkKind": code(item.link_kind),
                "linkedAt": instant(item.linked_at),
            }
            for item in view.sources
        ],
        "metrics": [
            {
                "metricId": item.metric_id,
                "status": code(item.status),
                "value": quantity(
                    item.value,
                    item.unit,
                    EvidenceRef(EvidenceKind.DERIVED_METRIC, item.value_id),
                    digits=2,
                ),
                "confidence": code(item.input_confidence),
                "uncertaintyReasons": codes(item.uncertainty_reasons),
                "missingInputs": list(item.missing_inputs),
                "calculationVersion": item.calculation_version,
            }
            for item in metrics
        ],
        "openIssues": [issue(item) for item in issues],
    }


def issue(item: DataQualityIssue) -> dict[str, Any]:
    score = None
    if item.match_score is not None and item.activity_id is not None:
        ref = EvidenceRef(EvidenceKind.ACTIVITY, item.activity_id, field="dedup.matchScore")
        score = quantity(item.match_score, "score 0-1", ref, digits=2)
    return {
        "issueId": str(item.issue_id),
        "kind": code(item.kind),
        "code": item.code,
        "field": item.field,
        "activityId": None if item.activity_id is None else str(item.activity_id),
        "relatedActivityId": None
        if item.related_activity_id is None
        else str(item.related_activity_id),
        "matchScore": score,
        "detail": code_or_text(item.detail),
        "status": code(item.status),
        "resolution": code(item.resolution),
        "createdAt": instant(item.created_at),
        "closedAt": instant(item.closed_at),
    }


def _issues(
    services: AgentServices, context: ToolContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    services.consents.require(context.scope, ConsentType.ACTIVITIES)
    open_only = arguments.get("status", "OPEN") == "OPEN"
    found = services.activities.issues(context.scope, open_only=open_only)
    found.sort(key=lambda item: item.created_at, reverse=True)
    limit = int(arguments.get("limit", 50))
    return {
        "count": len(found),
        "truncated": len(found) > limit,
        "issues": [issue(item) for item in found[:limit]],
    }


def _strength(
    services: AgentServices, context: ToolContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    start, end = period(arguments, context, default_days=STRENGTH_DAYS)
    wanted = arguments.get("exerciseId")
    views = [
        view
        for view in services.analytics.activities(context.scope, DateRange(start, end))
        if view.sport is Sport.STRENGTH
    ]
    sessions = []
    for view in sorted(views, key=lambda item: item.start_time_utc):
        executed = services.strength.of_view(context.scope, view)
        sets = []
        for item in executed.sets:
            if wanted is not None and item.exercise_id != wanted:
                continue
            ref = EvidenceRef(
                EvidenceKind.STRENGTH_SET_RESULT,
                view.activity_id,
                field=f"{item.exercise_id or 'unidentified'}#{item.set_number}",
            )
            sets.append(
                {
                    "exerciseId": item.exercise_id,
                    "watchExercise": untrusted(item.watch_exercise),
                    "setNumber": item.set_number,
                    "source": code(item.source),
                    "reps": quantity(item.completed_reps, "reps", ref),
                    "load": quantity(item.actual_load_kg, "kg", ref),
                    "rpe": quantity(item.actual_rpe, "RPE 0-10", ref),
                    "rir": quantity(item.actual_rir, "reps in reserve", ref),
                    "duration": quantity(item.duration_seconds, "s", ref, digits=0),
                }
            )
        sessions.append(
            {
                "activityId": str(view.activity_id),
                "trainingDay": day(view.training_day),
                "watchSetsUnreadable": executed.watch_unreadable,
                "sets": sets,
            }
        )
    weeks = []
    monday = start - timedelta(days=start.weekday())
    while monday <= end:
        week = services.analytics.strength_week(context.scope, week_key(monday))
        volume = week.volume_load
        weeks.append(
            {
                "week": week_key(monday),
                "activities": week.activities,
                "sets": week.sets,
                "unattributedSets": week.unattributed,
                "unreadSessions": week.unread,
                "hardSetsByMuscle": {
                    code(item.muscle): {
                        **quantity(
                            item.hard_sets.known_total,
                            item.hard_sets.unit,
                            EvidenceRef(EvidenceKind.DERIVED_METRIC, item.hard_sets.value.value_id),
                        ),
                        "complete": item.hard_sets.complete,
                    }
                    for item in week.muscles
                },
                "volumeLoad": None
                if volume is None
                else {
                    **quantity(
                        volume.known_total,
                        volume.unit,
                        EvidenceRef(EvidenceKind.DERIVED_METRIC, volume.value.value_id),
                    ),
                    "complete": volume.complete,
                },
            }
        )
        monday += timedelta(days=7)
    return {"from": day(start), "to": day(end), "sessions": sessions, "weeks": weeks}


_SPORT: Final = {"enum": [item.value for item in Sport]}


def training_tools(services: AgentServices) -> list[Tool]:
    return [
        Tool(
            "getActivities",
            ToolKind.READ,
            "Activity summaries of a period, newest first: sport, subtype, name (untrusted "
            "text), durations, distance, average heart rate, RPE, session load and data "
            "quality. Default: the last 14 days; at most 92 days and 100 activities.",
            schema(
                {
                    **RANGE_PROPERTIES,
                    "sport": _SPORT,
                    "limit": {"type": "integer", "minimum": 1, "maximum": MAX_ACTIVITIES},
                }
            ),
            partial(_activities, services),
        ),
        Tool(
            "getActivityDetail",
            ToolKind.READ,
            "One activity in detail: every field with its unit, corrected fields, data "
            "quality, source summary, computed metrics and open data quality issues. No raw "
            "streams.",
            schema(
                {"activityId": {**ID, "description": "An activity from getActivities."}},
                "activityId",
            ),
            partial(_detail, services),
        ),
        Tool(
            "getDataQualityIssues",
            ToolKind.READ,
            "Data quality issues (possible duplicates, anomalies, missing data, suspicious "
            "text), newest first. Default: the open ones.",
            schema(
                {
                    "status": {"enum": ["OPEN", "ALL"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": MAX_ACTIVITIES},
                }
            ),
            partial(_issues, services),
        ),
        Tool(
            "getStrengthHistory",
            ToolKind.READ,
            "Strength sessions of a period with their executed sets (exercise, reps, load, "
            "RPE, RIR, source) and weekly hard sets by muscle and volume load. Default: the "
            "last 28 days.",
            schema(
                {
                    **RANGE_PROPERTIES,
                    "exerciseId": {
                        "type": "string",
                        "pattern": "^[a-z0-9_]{1,64}$",
                        "description": "A catalogue exercise ID to filter the sets.",
                    },
                }
            ),
            partial(_strength, services),
        ),
    ]
