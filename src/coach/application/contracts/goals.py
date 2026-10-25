"""Goal and SportEvent <-> goal and sport-event contracts."""

from __future__ import annotations

from datetime import date
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
)
from coach.domain.common.sport import Sport
from coach.domain.goals.goal import Goal, GoalBaseline, GoalPriority, GoalStatus, SportEvent


def goal_to_contract(goal: Goal) -> Json:
    baseline = goal.baseline
    return {
        "schemaVersion": SCHEMA_VERSION,
        "goalId": str(goal.goal_id),
        "athleteId": str(goal.athlete_id),
        "version": goal.version,
        "eventId": optional_uuid(goal.event_id),
        "sport": goal.sport.value,
        "distanceMeters": goal.distance_meters,
        "targetTimeSeconds": goal.target_time_seconds,
        "targetDate": goal.target_date.isoformat(),
        "priority": goal.priority.value,
        "status": goal.status.value,
        "createdAt": instant(goal.created_at),
        "baseline": None
        if baseline is None
        else {
            "capturedAt": instant(baseline.captured_at),
            "evidence": [evidence(reference) for reference in baseline.evidence],
            "summary": baseline.summary,
        },
    }


def goal_from_contract(document: Document) -> Goal:
    baseline = document["baseline"]
    return Goal(
        goal_id=UUID(document["goalId"]),
        athlete_id=UUID(document["athleteId"]),
        version=document["version"],
        event_id=parse_optional_uuid(document["eventId"]),
        sport=Sport(document["sport"]),
        distance_meters=document["distanceMeters"],
        target_time_seconds=document["targetTimeSeconds"],
        target_date=date.fromisoformat(document["targetDate"]),
        priority=GoalPriority(document["priority"]),
        status=GoalStatus(document["status"]),
        created_at=parse_instant(document["createdAt"]),
        baseline=None
        if baseline is None
        else GoalBaseline(
            captured_at=parse_instant(baseline["capturedAt"]),
            evidence=tuple(parse_evidence(item) for item in baseline["evidence"]),
            summary=baseline["summary"],
        ),
    )


def sport_event_to_contract(event: SportEvent) -> Json:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "eventId": str(event.event_id),
        "athleteId": str(event.athlete_id),
        "version": event.version,
        "name": event.name,
        "sport": event.sport.value,
        "date": event.event_date.isoformat(),
        "distanceMeters": event.distance_meters,
        "location": event.location,
        "priority": event.priority.value,
        "expectedConditions": event.expected_conditions,
    }


def sport_event_from_contract(document: Document) -> SportEvent:
    return SportEvent(
        event_id=UUID(document["eventId"]),
        athlete_id=UUID(document["athleteId"]),
        version=document["version"],
        name=document["name"],
        sport=Sport(document["sport"]),
        event_date=date.fromisoformat(document["date"]),
        distance_meters=document["distanceMeters"],
        location=document["location"],
        priority=GoalPriority(document["priority"]),
        expected_conditions=document["expectedConditions"],
    )
