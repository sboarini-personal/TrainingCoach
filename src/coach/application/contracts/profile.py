"""AthleteProfile <-> athlete-profile contract (urn:coach:schema:athlete-profile:1)."""

from __future__ import annotations

from datetime import date
from typing import Any, Final
from uuid import UUID

from coach.application.contracts._json import (
    Document,
    Json,
    instant,
    minute_of_day,
    optional_date,
    parse_instant,
    parse_minute_of_day,
    parse_optional_date,
    put,
    sorted_values,
    take,
)
from coach.domain.athlete.experience import (
    CyclingExperience,
    FtpValue,
    LoadReference,
    RaceResult,
    RunningExperience,
    StrengthExperience,
    SwimmingExperience,
    TennisExperience,
    TennisProfile,
)
from coach.domain.athlete.health import (
    ConstraintSource,
    HealthConstraint,
    HealthDeclaration,
    Restriction,
    RestrictionKind,
)
from coach.domain.athlete.physiology import (
    HeartRateZoneModel,
    Sensor,
    SensorType,
    ZoneMethod,
    ZoneSource,
)
from coach.domain.athlete.profile import AthleteProfile
from coach.domain.athlete.resources import (
    Equipment,
    PoolAccess,
    Resources,
    TennisSessionType,
    TennisSlot,
)
from coach.domain.common.provenance import ValueSource
from coach.domain.common.sport import Sport
from coach.domain.common.time import Weekday

# 1.1.0 added age-predicted zone models (profile spec 1.1, ADR-028).
PROFILE_SCHEMA_VERSION: Final = "1.1.0"


# ------------------------------------------------------------------ experience


def _race(result: RaceResult | None) -> Json | None:
    if result is None:
        return None
    return {
        "timeSeconds": result.time_seconds,
        "date": result.on.isoformat(),
        "source": result.source.value,
    }


def _parse_race(document: Document | None) -> RaceResult | None:
    if document is None:
        return None
    return RaceResult(
        document["timeSeconds"],
        date.fromisoformat(document["date"]),
        ValueSource(document["source"]),
    )


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


def _parse_running(document: Document) -> RunningExperience:
    return RunningExperience(
        years_experience=document["yearsExperience"],
        weekly_distance_km=document["weeklyDistanceKm"],
        longest_distance_km=document["longestDistanceKm"],
        recent_5k=_parse_race(document["recent5k"]),
        recent_10k=_parse_race(document["recent10k"]),
        recent_half_marathon=_parse_race(document["recentHalfMarathon"]),
        recent_marathon=_parse_race(document["recentMarathon"]),
    )


def _cycling(experience: CyclingExperience) -> Json:
    ftp = experience.ftp
    return {
        "yearsExperience": experience.years_experience,
        "weeklyDistanceKm": experience.weekly_distance_km,
        "usualRideDistanceKm": experience.usual_ride_distance_km,
        "longestRideKm": experience.longest_ride_km,
        "ftp": None
        if ftp is None
        else {"watts": ftp.watts, "date": ftp.on.isoformat(), "measured": ftp.measured},
        "powerMeterAvailable": experience.power_meter_available,
    }


def _parse_cycling(document: Document) -> CyclingExperience:
    ftp = document["ftp"]
    return CyclingExperience(
        years_experience=document["yearsExperience"],
        weekly_distance_km=document["weeklyDistanceKm"],
        usual_ride_distance_km=document["usualRideDistanceKm"],
        longest_ride_km=document["longestRideKm"],
        ftp=None
        if ftp is None
        else FtpValue(ftp["watts"], date.fromisoformat(ftp["date"]), ftp["measured"]),
        power_meter_available=document["powerMeterAvailable"],
    )


def _swimming(experience: SwimmingExperience) -> Json:
    return {
        "yearsExperience": experience.years_experience,
        "weeklyDistanceM": experience.weekly_distance_m,
        "typicalPace100mSeconds": experience.typical_pace_100m_seconds,
        "longestDistanceM": experience.longest_distance_m,
        "poolLengthM": experience.pool_length_m,
    }


def _parse_swimming(document: Document) -> SwimmingExperience:
    return SwimmingExperience(
        years_experience=document["yearsExperience"],
        weekly_distance_m=document["weeklyDistanceM"],
        typical_pace_100m_seconds=document["typicalPace100mSeconds"],
        longest_distance_m=document["longestDistanceM"],
        pool_length_m=document["poolLengthM"],
    )


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


def _parse_strength(document: Document) -> StrengthExperience:
    return StrengthExperience(
        years_experience=document["yearsExperience"],
        sessions_per_week=document["sessionsPerWeek"],
        familiar_exercise_ids=tuple(document["familiarExerciseIds"]),
        approximate_loads=tuple(
            LoadReference(
                load["exerciseId"], load["loadKg"], load["reps"], date.fromisoformat(load["date"])
            )
            for load in document["approximateLoads"]
        ),
        rpe_familiar=document["rpeFamiliar"],
        rir_familiar=document["rirFamiliar"],
    )


def _tennis(experience: TennisExperience) -> Json:
    return {
        "sessionsPerWeek": experience.sessions_per_week,
        "typicalDurationMinutes": experience.typical_duration_minutes,
        "typicalRpe": experience.typical_rpe,
        "profile": None if experience.profile is None else experience.profile.value,
    }


def _parse_tennis(document: Document) -> TennisExperience:
    profile = document["profile"]
    return TennisExperience(
        sessions_per_week=document["sessionsPerWeek"],
        typical_duration_minutes=document["typicalDurationMinutes"],
        typical_rpe=document["typicalRpe"],
        profile=None if profile is None else TennisProfile(profile),
    )


# ------------------------------------------------------------------ resources and health


def _resources(resources: Resources) -> Json:
    pool = resources.pool
    return {
        "pool": None if pool is None else {"available": pool.available, "lengthM": pool.length_m},
        "bicycle": resources.bicycle,
        "smartTrainer": resources.smart_trainer,
        "gym": resources.gym,
        "strengthEquipment": sorted_values(resources.strength_equipment),
        "tennisSchedule": [
            {
                "weekday": slot.weekday.name,
                "start": minute_of_day(slot.start_minute),
                "durationMinutes": slot.duration_minutes,
                "sessionType": slot.session_type.value,
            }
            for slot in resources.tennis_schedule
        ],
    }


def _parse_resources(document: Document) -> Resources:
    pool = document["pool"]
    return Resources(
        pool=None if pool is None else PoolAccess(pool["available"], pool["lengthM"]),
        bicycle=document["bicycle"],
        smart_trainer=document["smartTrainer"],
        gym=document["gym"],
        strength_equipment=frozenset(Equipment(item) for item in document["strengthEquipment"]),
        tennis_schedule=tuple(
            TennisSlot(
                Weekday[slot["weekday"]],
                parse_minute_of_day(slot["start"]),
                slot["durationMinutes"],
                TennisSessionType(slot["sessionType"]),
            )
            for slot in document["tennisSchedule"]
        ),
    )


def _constraint(constraint: HealthConstraint) -> Json:
    return {
        "constraintId": str(constraint.constraint_id),
        "description": constraint.description,
        "affectedRegions": list(constraint.affected_regions),
        "affectedSports": sorted_values(constraint.affected_sports),
        "restrictions": [
            {
                "kind": restriction.kind.value,
                "maxRpe": restriction.max_rpe,
                "sport": None if restriction.sport is None else restriction.sport.value,
            }
            for restriction in constraint.restrictions
        ],
        "declaredAt": instant(constraint.declared_at),
        "validUntil": optional_date(constraint.valid_until),
        "source": constraint.source.value,
    }


def _parse_constraint(document: Document) -> HealthConstraint:
    return HealthConstraint(
        constraint_id=UUID(document["constraintId"]),
        description=document["description"],
        affected_regions=tuple(document["affectedRegions"]),
        affected_sports=frozenset(Sport(sport) for sport in document["affectedSports"]),
        restrictions=tuple(
            Restriction(
                RestrictionKind(item["kind"]),
                max_rpe=item["maxRpe"],
                sport=None if item["sport"] is None else Sport(item["sport"]),
            )
            for item in document["restrictions"]
        ),
        declared_at=parse_instant(document["declaredAt"]),
        valid_until=parse_optional_date(document["validUntil"]),
        source=ConstraintSource(document["source"]),
    )


def _health(declaration: HealthDeclaration) -> Json:
    return {
        "declaredAt": instant(declaration.declared_at),
        "constraints": [_constraint(constraint) for constraint in declaration.constraints],
    }


def _parse_health(document: Document) -> HealthDeclaration:
    return HealthDeclaration(
        declared_at=parse_instant(document["declaredAt"]),
        constraints=tuple(_parse_constraint(item) for item in document["constraints"]),
    )


# ------------------------------------------------------------------ devices and zones


def _sensors(sensors: tuple[Sensor, ...]) -> list[Json]:
    return [
        {
            "type": sensor.sensor_type.value,
            "model": sensor.model,
            "reliableForTraining": sensor.reliable_for_training,
        }
        for sensor in sensors
    ]


def _parse_sensors(items: list[Any]) -> tuple[Sensor, ...]:
    return tuple(
        Sensor(SensorType(item["type"]), item["model"], item["reliableForTraining"])
        for item in items
    )


def _zone_model(model: HeartRateZoneModel) -> Json:
    document: Json = {
        "zoneModelId": str(model.zone_model_id),
        "method": model.method.value,
        "source": model.source.value,
    }
    if model.estimated:
        document["boundariesPercentMaxHr"] = list(model.boundaries_percent_max_hr)
    else:
        document["boundariesBpm"] = list(model.boundaries_bpm)
    document["validFrom"] = model.valid_from.isoformat()
    return document


def _parse_zone_model(document: Document) -> HeartRateZoneModel:
    return HeartRateZoneModel(
        zone_model_id=UUID(document["zoneModelId"]),
        method=ZoneMethod(document["method"]),
        source=ZoneSource(document["source"]),
        boundaries_bpm=tuple(document.get("boundariesBpm", ())),
        boundaries_percent_max_hr=tuple(document.get("boundariesPercentMaxHr", ())),
        valid_from=date.fromisoformat(document["validFrom"]),
    )


# ------------------------------------------------------------------ profile


def profile_to_contract(profile: AthleteProfile) -> Json:
    identity: Json = {}
    put(identity, "displayName", profile.display_name)
    physiology: Json = {}
    put(physiology, "birthDate", profile.birth_date, date.isoformat)
    put(physiology, "declaredAge", profile.declared_age)
    put(physiology, "heightCm", profile.height_cm)
    put(physiology, "weightKg", profile.weight_kg)
    heart_rate: Json = {"zoneModels": [_zone_model(model) for model in profile.zone_models]}
    put(heart_rate, "restingHr", profile.resting_hr)
    put(heart_rate, "maxHr", profile.max_hr)
    put(heart_rate, "zoneMethod", profile.zone_method)
    preferences: Json = {}
    put(preferences, "timezone", profile.timezone)
    put(preferences, "metricUnits", profile.metric_units)
    put(preferences, "language", profile.language)
    devices: Json = {}
    put(devices, "garminModel", profile.garmin_model)
    put(devices, "sensors", profile.sensors, _sensors)
    experience: Json = {}
    put(experience, "running", profile.running_experience, _running)
    put(experience, "cycling", profile.cycling_experience, _cycling)
    put(experience, "swimming", profile.swimming_experience, _swimming)
    put(experience, "strength", profile.strength_experience, _strength)
    put(experience, "tennis", profile.tennis_experience, _tennis)
    contract: Json = {
        "schemaVersion": PROFILE_SCHEMA_VERSION,
        "athleteId": str(profile.athlete_id),
        "version": profile.version,
        "identity": identity,
        "physiology": physiology,
        "heartRate": heart_rate,
        "preferences": preferences,
        "devices": devices,
        "experience": experience,
    }
    put(contract, "resources", profile.resources, _resources)
    put(contract, "health", profile.health, _health)
    return contract


def profile_from_contract(document: Document) -> AthleteProfile:
    identity = document["identity"]
    physiology = document["physiology"]
    heart_rate = document["heartRate"]
    preferences = document["preferences"]
    devices = document["devices"]
    experience = document["experience"]
    return AthleteProfile(
        athlete_id=UUID(document["athleteId"]),
        version=document["version"],
        display_name=take(identity, "displayName"),
        birth_date=take(physiology, "birthDate", date.fromisoformat),
        declared_age=take(physiology, "declaredAge"),
        height_cm=take(physiology, "heightCm"),
        weight_kg=take(physiology, "weightKg"),
        resting_hr=take(heart_rate, "restingHr"),
        max_hr=take(heart_rate, "maxHr"),
        zone_method=take(heart_rate, "zoneMethod", ZoneMethod),
        zone_models=tuple(_parse_zone_model(model) for model in heart_rate["zoneModels"]),
        timezone=take(preferences, "timezone"),
        metric_units=take(preferences, "metricUnits"),
        language=take(preferences, "language"),
        garmin_model=take(devices, "garminModel"),
        sensors=take(devices, "sensors", _parse_sensors),
        running_experience=take(experience, "running", _parse_running),
        cycling_experience=take(experience, "cycling", _parse_cycling),
        swimming_experience=take(experience, "swimming", _parse_swimming),
        strength_experience=take(experience, "strength", _parse_strength),
        tennis_experience=take(experience, "tennis", _parse_tennis),
        resources=take(document, "resources", _parse_resources),
        health=take(document, "health", _parse_health),
    )
