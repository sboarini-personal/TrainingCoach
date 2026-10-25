"""Map domain aggregates to the JSON contracts in spec/contracts (schema version 1.x).

Conventions: a tracked field never recorded is omitted; a field recorded as UNKNOWN becomes
``{"value": null, "provenance": ...}``. Timestamps are UTC with ``Z``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, time
from enum import Enum
from typing import Any, Final

from coach.domain.athlete.experience import (
    CyclingExperience,
    FtpValue,
    RaceResult,
    RunningExperience,
    StrengthExperience,
    SwimmingExperience,
    TennisExperience,
)
from coach.domain.athlete.health import HealthConstraint, HealthDeclaration
from coach.domain.athlete.physiology import HeartRateZoneModel, Sensor
from coach.domain.athlete.profile import AthleteProfile
from coach.domain.athlete.resources import Resources
from coach.domain.availability.availability import (
    Availability,
    AvailabilityException,
    TimeWindow,
)
from coach.domain.common.evidence import EvidenceRef
from coach.domain.common.provenance import FieldValue, Provenance
from coach.domain.common.sport import Sport
from coach.domain.consent.ledger import ConsentRecord
from coach.domain.goals.goal import Goal, SportEvent

SCHEMA_VERSION: Final = "1.0.0"
type Json = dict[str, Any]


def _instant(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _optional_instant(value: datetime | None) -> str | None:
    return None if value is None else _instant(value)


def _optional_date(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _clock(value: time) -> str:
    return value.strftime("%H:%M")


def _minute_of_day(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _sorted_values(values: Iterable[Enum]) -> list[str]:
    return sorted(str(value.value) for value in values)


def _sports(sports: frozenset[Sport] | None) -> list[str] | None:
    return None if sports is None else _sorted_values(sports)


def _provenance(provenance: Provenance) -> Json:
    return {
        "source": provenance.source.value,
        "recordedAt": _instant(provenance.recorded_at),
        "provider": provenance.provider,
        "metricId": provenance.metric_id,
    }


def _as_is(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _put[T](
    target: Json, key: str, field: FieldValue[T] | None, convert: Callable[[T], Any] = _as_is
) -> None:
    """Add a tracked field; omit it when it was never recorded."""
    if field is None:
        return
    raw = field.value
    value = None if raw is None else convert(raw)
    target[key] = {"value": value, "provenance": _provenance(field.provenance)}


# ------------------------------------------------------------------ profile


def _race(result: RaceResult | None) -> Json | None:
    if result is None:
        return None
    return {
        "timeSeconds": result.time_seconds,
        "date": result.on.isoformat(),
        "source": result.source.value,
    }


def _ftp(ftp: FtpValue | None) -> Json | None:
    if ftp is None:
        return None
    return {"watts": ftp.watts, "date": ftp.on.isoformat(), "measured": ftp.measured}


def _running(experience: RunningExperience) -> Json:
    return {
        "yearsExperience": experience.years_experience,
        "weeklyDistanceKm": experience.weekly_distance_km,
        "longestDistanceKm": experience.longest_distance_km,
        "recent5k": _race(experience.recent_5k),
        "recent10k": _race(experience.recent_10k),
        "recentHalfMarathon": _race(experience.recent_half_marathon),
        "recentMarathon": _race(experience.recent_marathon),
    }


def _cycling(experience: CyclingExperience) -> Json:
    return {
        "yearsExperience": experience.years_experience,
        "weeklyDistanceKm": experience.weekly_distance_km,
        "usualRideDistanceKm": experience.usual_ride_distance_km,
        "longestRideKm": experience.longest_ride_km,
        "ftp": _ftp(experience.ftp),
        "powerMeterAvailable": experience.power_meter_available,
    }


def _swimming(experience: SwimmingExperience) -> Json:
    return {
        "yearsExperience": experience.years_experience,
        "weeklyDistanceM": experience.weekly_distance_m,
        "typicalPace100mSeconds": experience.typical_pace_100m_seconds,
        "longestDistanceM": experience.longest_distance_m,
        "poolLengthM": experience.pool_length_m,
    }


def _strength(experience: StrengthExperience) -> Json:
    return {
        "yearsExperience": experience.years_experience,
        "sessionsPerWeek": experience.sessions_per_week,
        "familiarExerciseIds": list(experience.familiar_exercise_ids),
        "approximateLoads": [
            {
                "exerciseId": load.exercise_id,
                "loadKg": load.load_kg,
                "reps": load.reps,
                "date": load.on.isoformat(),
            }
            for load in experience.approximate_loads
        ],
        "rpeFamiliar": experience.rpe_familiar,
        "rirFamiliar": experience.rir_familiar,
    }


def _tennis(experience: TennisExperience) -> Json:
    return {
        "sessionsPerWeek": experience.sessions_per_week,
        "typicalDurationMinutes": experience.typical_duration_minutes,
        "typicalRpe": experience.typical_rpe,
        "profile": None if experience.profile is None else experience.profile.value,
    }


def _resources(resources: Resources) -> Json:
    pool = resources.pool
    return {
        "pool": None if pool is None else {"available": pool.available, "lengthM": pool.length_m},
        "bicycle": resources.bicycle,
        "smartTrainer": resources.smart_trainer,
        "gym": resources.gym,
        "strengthEquipment": _sorted_values(resources.strength_equipment),
        "tennisSchedule": [
            {
                "weekday": slot.weekday.name,
                "start": _minute_of_day(slot.start_minute),
                "durationMinutes": slot.duration_minutes,
                "sessionType": slot.session_type.value,
            }
            for slot in resources.tennis_schedule
        ],
    }


def _constraint(constraint: HealthConstraint) -> Json:
    return {
        "constraintId": str(constraint.constraint_id),
        "description": constraint.description,
        "affectedRegions": list(constraint.affected_regions),
        "affectedSports": _sorted_values(constraint.affected_sports),
        "restrictions": [
            {
                "kind": restriction.kind.value,
                "maxRpe": restriction.max_rpe,
                "sport": None if restriction.sport is None else restriction.sport.value,
            }
            for restriction in constraint.restrictions
        ],
        "declaredAt": _instant(constraint.declared_at),
        "validUntil": _optional_date(constraint.valid_until),
        "source": constraint.source.value,
    }


def _health(declaration: HealthDeclaration) -> Json:
    return {
        "declaredAt": _instant(declaration.declared_at),
        "constraints": [_constraint(constraint) for constraint in declaration.constraints],
    }


def _sensors(sensors: tuple[Sensor, ...]) -> list[Json]:
    return [
        {
            "type": sensor.sensor_type.value,
            "model": sensor.model,
            "reliableForTraining": sensor.reliable_for_training,
        }
        for sensor in sensors
    ]


def _zone_model(model: HeartRateZoneModel) -> Json:
    return {
        "zoneModelId": str(model.zone_model_id),
        "method": model.method.value,
        "source": model.source.value,
        "boundariesBpm": list(model.boundaries_bpm),
        "validFrom": model.valid_from.isoformat(),
    }


def profile_to_contract(profile: AthleteProfile) -> Json:
    identity: Json = {}
    _put(identity, "displayName", profile.display_name)
    physiology: Json = {}
    _put(physiology, "birthDate", profile.birth_date, date.isoformat)
    _put(physiology, "declaredAge", profile.declared_age)
    _put(physiology, "heightCm", profile.height_cm)
    _put(physiology, "weightKg", profile.weight_kg)
    heart_rate: Json = {"zoneModels": [_zone_model(model) for model in profile.zone_models]}
    _put(heart_rate, "restingHr", profile.resting_hr)
    _put(heart_rate, "maxHr", profile.max_hr)
    _put(heart_rate, "zoneMethod", profile.zone_method)
    preferences: Json = {}
    _put(preferences, "timezone", profile.timezone)
    _put(preferences, "metricUnits", profile.metric_units)
    _put(preferences, "language", profile.language)
    devices: Json = {}
    _put(devices, "garminModel", profile.garmin_model)
    _put(devices, "sensors", profile.sensors, _sensors)
    experience: Json = {}
    _put(experience, "running", profile.running_experience, _running)
    _put(experience, "cycling", profile.cycling_experience, _cycling)
    _put(experience, "swimming", profile.swimming_experience, _swimming)
    _put(experience, "strength", profile.strength_experience, _strength)
    _put(experience, "tennis", profile.tennis_experience, _tennis)
    contract: Json = {
        "schemaVersion": SCHEMA_VERSION,
        "athleteId": str(profile.athlete_id),
        "version": profile.version,
        "identity": identity,
        "physiology": physiology,
        "heartRate": heart_rate,
        "preferences": preferences,
        "devices": devices,
        "experience": experience,
    }
    _put(contract, "resources", profile.resources, _resources)
    _put(contract, "health", profile.health, _health)
    return contract


# ------------------------------------------------------------------ availability


def _windows(windows: tuple[TimeWindow, ...]) -> list[Json]:
    return [{"start": _clock(window.start), "end": _clock(window.end)} for window in windows]


def _exception(exception: AvailabilityException) -> Json:
    return {
        "date": exception.day.isoformat(),
        "available": exception.available,
        "maximumDurationMinutes": exception.maximum_duration_minutes,
        "allowedSports": _sports(exception.allowed_sports),
        "reason": exception.reason,
    }


def availability_to_contract(availability: Availability) -> Json:
    recurring = availability.recurring
    defaults = availability.defaults
    return {
        "schemaVersion": SCHEMA_VERSION,
        "athleteId": str(availability.athlete_id),
        "version": availability.version,
        "recurring": None
        if recurring is None
        else {
            "daysPerWeek": recurring.days_per_week,
            "maximumHoursPerWeek": recurring.maximum_hours_per_week,
            "preferredRestDays": [day.name for day in sorted(recurring.preferred_rest_days)],
            "dailyWindows": {
                day.name: _windows(windows)
                for day, windows in sorted(recurring.daily_windows.items())
            },
            "doubleSessionsAllowed": recurring.double_sessions_allowed,
            "restrictions": recurring.restrictions,
        },
        "defaults": None
        if defaults is None
        else {
            "maximumDurationMinutes": defaults.maximum_duration_minutes,
            "allowedSports": _sports(defaults.allowed_sports),
        },
        "exceptions": [
            _exception(availability.exceptions[day]) for day in sorted(availability.exceptions)
        ],
    }


# ------------------------------------------------------------------ goals and events


def _evidence(reference: EvidenceRef) -> Json:
    contract: Json = {"kind": reference.kind.value, "id": str(reference.ref_id)}
    if reference.field is not None:
        contract["field"] = reference.field
    if reference.note is not None:
        contract["note"] = reference.note
    return contract


def goal_to_contract(goal: Goal) -> Json:
    baseline = goal.baseline
    return {
        "schemaVersion": SCHEMA_VERSION,
        "goalId": str(goal.goal_id),
        "athleteId": str(goal.athlete_id),
        "version": goal.version,
        "eventId": None if goal.event_id is None else str(goal.event_id),
        "sport": goal.sport.value,
        "distanceMeters": goal.distance_meters,
        "targetTimeSeconds": goal.target_time_seconds,
        "targetDate": goal.target_date.isoformat(),
        "priority": goal.priority.value,
        "status": goal.status.value,
        "createdAt": _instant(goal.created_at),
        "baseline": None
        if baseline is None
        else {
            "capturedAt": _instant(baseline.captured_at),
            "evidence": [_evidence(reference) for reference in baseline.evidence],
            "summary": baseline.summary,
        },
    }


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


# ------------------------------------------------------------------ consent


def consent_record_to_contract(record: ConsentRecord) -> Json:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "consentId": str(record.consent_id),
        "athleteId": str(record.athlete_id),
        "consentType": record.consent_type.value,
        "granted": record.granted,
        "grantedAt": _optional_instant(record.granted_at),
        "revokedAt": _optional_instant(record.revoked_at),
        "policyVersion": record.policy_version,
        "purpose": record.purpose,
        "recordedAt": _instant(record.recorded_at),
    }
