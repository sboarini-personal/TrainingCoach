"""Recovery documents (recovery spec): DailyCheckIn <-> daily-checkin contract and
RecoveryObservation <-> recovery-observation contract."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from coach.application.contracts._json import (
    SCHEMA_VERSION,
    Document,
    Json,
    instant,
    parse_instant,
    parse_optional_uuid,
)
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
    return RecoveryObservation(
        observation_id=UUID(document["observationId"]),
        athlete_id=UUID(document["athleteId"]),
        version=document["version"],
        day=date.fromisoformat(document["day"]),
        metric=RecoveryMetric(document["metric"]),
        value=float(document["value"]),
        provider=document["provider"],
        raw_source_id=UUID(document["rawSourceId"]),
        source_path=document["sourcePath"],
        received_at=parse_instant(document["receivedAt"]),
    )
