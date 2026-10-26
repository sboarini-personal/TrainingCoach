"""Synthetic domain objects shared by tests. No real athlete data."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from coach.application.credentials import CredentialKind, CredentialRecord
from coach.domain.activity.activity import Activity, ActivityStatus, LinkKind, SourceLink
from coach.domain.activity.fields import (
    Cadence,
    CadenceUnit,
    HrSensor,
    HrZones,
    Pain,
    PowerSource,
    PowerSummary,
    ProviderMetric,
    StreamKind,
    StreamRef,
    StrokeType,
    Surface,
    Swolf,
    Terrain,
)
from coach.domain.activity.issues import DataQualityIssue
from coach.domain.activity.matching import DEDUP_RULES_VERSION
from coach.domain.activity.observation import Observation, SourceKind
from coach.domain.activity.overrides import DataOverride, EntityType
from coach.domain.activity.resolution import RESOLUTION_RULES_VERSION, resolve
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
from coach.domain.athlete.profile import AthleteProfile, ProfileUpdate
from coach.domain.athlete.resources import (
    Equipment,
    PoolAccess,
    Resources,
    TennisSessionType,
    TennisSlot,
)
from coach.domain.availability.availability import (
    Availability,
    AvailabilityException,
    DayDefaults,
    TimeWindow,
    WeeklyAvailability,
)
from coach.domain.coaching.state import CoachingState, Condition, LifecycleState
from coach.domain.common.evidence import EvidenceKind, EvidenceRef
from coach.domain.common.provenance import FieldValue, MetricOrigin, Provenance, ValueSource
from coach.domain.common.quality import Anomaly, Confidence, UncertaintyReason
from coach.domain.common.sport import Sport
from coach.domain.common.time import Weekday
from coach.domain.consent.ledger import ConsentLedger, ConsentType
from coach.domain.goals.goal import Goal, GoalBaseline, GoalPriority, GoalStatus, SportEvent
from coach.domain.integration.capabilities import (
    AccessTier,
    Capability,
    CapabilityBasis,
    CapabilityState,
    CapabilityValue,
    ProviderCapabilities,
)
from coach.domain.integration.status import IntegrationStatus
from coach.domain.raw.source import DetectedType, MalwareScan, RawSource, SourceOrigin
from coach.metrics.catalog import SRPE_LOAD
from coach.metrics.derived import DerivedMetricValue, MetricInput, MetricSubject, derive
from coach.metrics.quality import QUALITY_RULES_VERSION, assess

NOW = datetime(2026, 10, 24, 18, 0, tzinfo=UTC)
ATHLETE = UUID("0193b2a0-0000-7000-8000-000000000001")
OTHER_ATHLETE = UUID("0193b2a0-0000-7000-8000-00000000000b")
ACTIVITY = UUID("0193b2a0-0000-7000-8000-000000000101")
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
GARMIN_SOURCE = UUID("0193b2a0-0000-7000-8000-000000000201")
FIT_SOURCE = UUID("0193b2a0-0000-7000-8000-000000000202")
MANUAL_SOURCE = UUID("0193b2a0-0000-7000-8000-000000000203")
USER = UUID("0193b2a0-0000-7000-8000-0000000000a1")
START = datetime(2026, 10, 20, 21, 0, tzinfo=UTC)
BUENOS_AIRES = ZoneInfo("America/Argentina/Buenos_Aires")


def declared(value: Any) -> FieldValue[Any]:
    return FieldValue.declared(value, at=NOW)


def full_profile(athlete_id: UUID = ATHLETE) -> AthleteProfile:
    return AthleteProfile.new(athlete_id).apply(
        ProfileUpdate(
            display_name=declared("Synthetic Athlete"),
            birth_date=declared(date(1981, 5, 17)),
            height_cm=declared(178.0),
            weight_kg=FieldValue.unknown(at=NOW),
            resting_hr=FieldValue(52, Provenance(ValueSource.PROVIDER, NOW, provider="garmin")),
            max_hr=declared(181),
            zone_method=declared(ZoneMethod.PERCENT_MAX_HR),
            zone_models=(
                HeartRateZoneModel(
                    zone_model_id=UUID("0193b2a0-0000-7000-8000-000000000301"),
                    method=ZoneMethod.PERCENT_MAX_HR,
                    source=ZoneSource.ATHLETE_DECLARED,
                    boundaries_bpm=(100, 120, 140, 158, 170, 185),
                    valid_from=date(2026, 10, 1),
                ),
            ),
            timezone=declared("America/Argentina/Buenos_Aires"),
            metric_units=declared(True),
            language=declared("es"),
            garmin_model=declared("synthetic watch"),
            sensors=declared((Sensor(SensorType.HR_STRAP, reliable_for_training=True),)),
            running_experience=declared(
                RunningExperience(
                    years_experience=6,
                    weekly_distance_km=30,
                    recent_10k=RaceResult(2940, date(2026, 9, 14), ValueSource.ATHLETE_DECLARED),
                )
            ),
            cycling_experience=declared(
                CyclingExperience(ftp=FtpValue(220, date(2026, 8, 1), measured=True))
            ),
            swimming_experience=declared(SwimmingExperience(pool_length_m=25)),
            strength_experience=declared(
                StrengthExperience(
                    sessions_per_week=2,
                    familiar_exercise_ids=("ex.goblet_squat",),
                    approximate_loads=(
                        LoadReference("ex.goblet_squat", 24, 10, date(2026, 10, 1)),
                    ),
                    rpe_familiar=True,
                )
            ),
            tennis_experience=declared(
                TennisExperience(sessions_per_week=1, profile=TennisProfile.PRACTICE)
            ),
            resources=declared(
                Resources(
                    pool=PoolAccess(available=True, length_m=25),
                    gym=True,
                    strength_equipment=frozenset({Equipment.DUMBBELLS, Equipment.BENCH}),
                    tennis_schedule=(
                        TennisSlot(Weekday.WEDNESDAY, 19 * 60, 90, TennisSessionType.PRACTICE),
                    ),
                )
            ),
            health=declared(
                HealthDeclaration(
                    declared_at=NOW,
                    constraints=(
                        HealthConstraint(
                            constraint_id=UUID("0193b2a0-0000-7000-8000-000000000321"),
                            description="Left Achilles discomfort",
                            affected_regions=("achilles",),
                            affected_sports=frozenset({Sport.RUNNING}),
                            restrictions=(
                                Restriction(RestrictionKind.MAX_RPE, max_rpe=6),
                                Restriction(RestrictionKind.NO_SPORT, sport=Sport.TENNIS),
                            ),
                            declared_at=NOW,
                            valid_until=date(2026, 11, 30),
                            source=ConstraintSource.ATHLETE,
                        ),
                    ),
                )
            ),
        )
    )


def sample_availability(athlete_id: UUID = ATHLETE) -> Availability:
    return Availability.new(
        athlete_id,
        recurring=WeeklyAvailability(
            days_per_week=5,
            maximum_hours_per_week=7.5,
            preferred_rest_days=frozenset({Weekday.MONDAY}),
            daily_windows={
                Weekday.THURSDAY: (TimeWindow(time(7, 0), time(8, 30)),),
                Weekday.SATURDAY: (
                    TimeWindow(time(8, 0), time(9, 0)),
                    TimeWindow(time(17, 0), time(18, 30)),
                ),
            },
            restrictions="No sessions after 21:00",
        ),
        defaults=DayDefaults(
            maximum_duration_minutes=45, allowed_sports=frozenset({Sport.RUNNING})
        ),
    ).with_exception(
        AvailabilityException(
            day=date(2026, 11, 5),
            available=True,
            maximum_duration_minutes=30,
            allowed_sports=frozenset({Sport.SWIMMING}),
            reason="Work trip",
        )
    )


def sample_event(athlete_id: UUID = ATHLETE) -> SportEvent:
    return SportEvent(
        event_id=UUID("0193b2a0-0000-7000-8000-000000000611"),
        athlete_id=athlete_id,
        name="Synthetic City 10K",
        sport=Sport.RUNNING,
        event_date=date(2026, 12, 13),
        distance_meters=10_000,
        priority=GoalPriority.A,
        expected_conditions="Flat, warm",
    )


def sample_goal(athlete_id: UUID = ATHLETE, goal_id: UUID | None = None) -> Goal:
    return Goal.new(
        goal_id=goal_id or UUID("0193b2a0-0000-7000-8000-000000000601"),
        athlete_id=athlete_id,
        sport=Sport.RUNNING,
        target_date=date(2026, 12, 13),
        priority=GoalPriority.A,
        created_at=NOW,
        distance_meters=10_000,
        target_time_seconds=2700,
        event_id=UUID("0193b2a0-0000-7000-8000-000000000611"),
        baseline=GoalBaseline(
            captured_at=NOW,
            evidence=(EvidenceRef(EvidenceKind.ACTIVITY, ACTIVITY, field="timing", note="10K"),),
            summary="Declared 10K in 49:00",
        ),
    ).transition(GoalStatus.ACTIVE)


def sample_consent_ledger(athlete_id: UUID = ATHLETE) -> ConsentLedger:
    return (
        ConsentLedger.new(athlete_id)
        .grant(
            consent_id=UUID("0193b2a0-0000-7000-8000-000000001001"),
            consent_type=ConsentType.HRV,
            policy_version="1.0",
            purpose="Use HRV as a recovery signal.",
            at=NOW,
        )
        .revoke(
            consent_id=UUID("0193b2a0-0000-7000-8000-000000001002"),
            consent_type=ConsentType.HRV,
            at=NOW,
        )
        .decline(
            consent_id=UUID("0193b2a0-0000-7000-8000-000000001003"),
            consent_type=ConsentType.AI_PROCESSING,
            policy_version="1.0",
            purpose="Coaching conversation with an external LLM.",
            at=NOW,
        )
    )


def sample_coaching_state(athlete_id: UUID = ATHLETE) -> CoachingState:
    return CoachingState(
        athlete_id=athlete_id,
        lifecycle=LifecycleState.TRAINING,
        conditions=frozenset({Condition.INTEGRATION_ERROR, Condition.RECOVERY_BLOCK}),
    )


def sample_raw_source(
    athlete_id: UUID = ATHLETE, sha256: str = SHA_A, raw_source_id: UUID | None = None
) -> RawSource:
    return RawSource(
        raw_source_id=raw_source_id or UUID("0193b2a0-0000-7000-8000-000000000202"),
        athlete_id=athlete_id,
        origin=SourceOrigin.FILE_UPLOAD,
        category="ACTIVITY_FILE",
        received_at=NOW,
        sha256=sha256,
        byte_size=182_044,
        detected_type=DetectedType.FIT,
        original_filename="morning_run.fit",
        malware_scan=MalwareScan.NOT_SCANNED,
    )


def sample_derived_value(
    athlete_id: UUID = ATHLETE,
    value_id: UUID | None = None,
    rpe: float = 5,
    subject: MetricSubject | None = None,
) -> DerivedMetricValue:
    return derive(
        SRPE_LOAD,
        {
            "rpe": MetricInput(
                rpe, EvidenceRef(EvidenceKind.ACTIVITY, ACTIVITY, "subjective.rpe"), Confidence.HIGH
            ),
            "session_duration_seconds": MetricInput(
                3720,
                EvidenceRef(EvidenceKind.ACTIVITY, ACTIVITY, "timing.elapsedSeconds"),
                Confidence.MEDIUM,
                frozenset({UncertaintyReason.ESTIMATED_VALUE}),
            ),
        },
        value_id=value_id or UUID("0193b2a0-0000-7000-8000-000000000903"),
        athlete_id=athlete_id,
        subject=subject or MetricSubject.activity(ACTIVITY),
        computed_at=NOW,
    )


def sample_credential_record(athlete_id: UUID = ATHLETE) -> CredentialRecord:
    return CredentialRecord(
        credential_id=UUID("0193b2a0-0000-7000-8000-000000001101"),
        athlete_id=athlete_id,
        kind=CredentialKind.GARMIN_PERSONAL_API_KEY,
        created_at=NOW,
        rotated_at=None,
        expires_on=date(2027, 1, 23),
    )


RUN_VALUES: Mapping[str, object] = {
    "classification.sport": Sport.RUNNING,
    "classification.subtype": "ROAD",
    "timing.startTimeUtc": START,
    "timing.utcOffset": "-03:00",
    "timing.elapsedSeconds": 3720.0,
    "timing.movingSeconds": 3650.0,
    "distance.meters": 10050.5,
    "elevation.gainMeters": 85.0,
    "cardiovascular.averageHr": 148,
    "cardiovascular.maxHr": 171,
    "cardiovascular.hrSensor": HrSensor.OPTICAL,
    "performance.averageSpeedMps": 2.753,
    "performance.cadence": Cadence(172.0, 184.0, CadenceUnit.STEPS_PER_MIN),
}
_ORIGINS = {
    SourceKind.FIT_FILE: SourceOrigin.FILE_UPLOAD,
    SourceKind.OTHER_FILE: SourceOrigin.FILE_UPLOAD,
    SourceKind.PROVIDER_RECORD: SourceOrigin.GARMIN_PERSONAL_API,
    SourceKind.ATHLETE_ENTRY: SourceOrigin.MANUAL_ENTRY,
}
_DEFAULT_SOURCES = {
    SourceKind.FIT_FILE: (FIT_SOURCE, SHA_A),
    SourceKind.OTHER_FILE: (FIT_SOURCE, SHA_A),
    SourceKind.PROVIDER_RECORD: (GARMIN_SOURCE, SHA_B),
    SourceKind.ATHLETE_ENTRY: (MANUAL_SOURCE, SHA_C),
}


def sample_observation(
    kind: SourceKind = SourceKind.FIT_FILE,
    *,
    raw_source_id: UUID | None = None,
    sha256: str | None = None,
    values: Mapping[str, object] | None = None,
    updates: Mapping[str, object] | None = None,
    without: Iterable[str] = (),
    athlete_id: UUID = ATHLETE,
    received_at: datetime = NOW,
    provider_activity_id: str = "synthetic-1001",
    part: int = 0,
    **extra: Any,
) -> Observation:
    """A synthetic run observation; ``updates`` and ``without`` adjust its field values."""
    default_id, default_sha = _DEFAULT_SOURCES[kind]
    fields = dict(RUN_VALUES if values is None else values)
    fields.update(updates or {})
    for path in without:
        fields.pop(path, None)
    record = kind is SourceKind.PROVIDER_RECORD
    return Observation(
        raw_source_id=raw_source_id or default_id,
        part=part,
        athlete_id=athlete_id,
        origin=_ORIGINS[kind],
        kind=kind,
        raw_sha256=sha256 or default_sha,
        received_at=received_at,
        normaliser="synthetic",
        normaliser_version="1.0.0",
        values=fields,
        provider="garmin" if record else None,
        provider_activity_id=provider_activity_id if record else None,
        **extra,
    )


def tennis_values(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "classification.sport": Sport.TENNIS,
        "classification.subtype": "MATCH",
        "timing.startTimeUtc": START,
        "timing.elapsedSeconds": 5400.0,
    }
    values.update(changes)
    return values


def sample_activity(
    *observations: Observation,
    activity_id: UUID = ACTIVITY,
    status: ActivityStatus = ActivityStatus.ACTIVE,
    imported_at: datetime = NOW,
    merged_into: UUID | None = None,
    athlete_timezone: ZoneInfo = BUENOS_AIRES,
) -> Activity:
    """An activity resolved from ``observations`` (default: one FIT run), linked in order."""
    sources = observations or (sample_observation(),)
    resolution = resolve(sources, athlete_timezone=athlete_timezone)
    links = tuple(
        SourceLink.of(
            source,
            link_kind=LinkKind.PRIMARY if index == 0 else LinkKind.ENRICHMENT,
            linked_at=imported_at + timedelta(minutes=index),
            match_score=None if index == 0 else 1.0,
            dedup_rules_version=None if index == 0 else DEDUP_RULES_VERSION,
        )
        for index, source in enumerate(sources)
    )
    return Activity(
        activity_id=activity_id,
        athlete_id=sources[0].athlete_id,
        status=status,
        sources=links,
        imported_at=imported_at,
        resolution_rules_version=RESOLUTION_RULES_VERSION,
        quality_rules_version=QUALITY_RULES_VERSION,
        values=resolution.values,
        provenance=resolution.provenance,
        quality=assess(
            resolution.values, imported_at=imported_at, source_anomalies=resolution.anomalies
        ),
        streams=resolution.streams,
        merged_into=merged_into,
    )


def rich_observation() -> Observation:
    """A provider record carrying every composite field kind and a stream."""
    return sample_observation(
        SourceKind.PROVIDER_RECORD,
        updates={
            "classification.providerType": "running",
            "metadata.name": "Long run",
            "metadata.description": None,
            "timing.originalTimezone": "America/Argentina/Buenos_Aires",
            "elevation.lossMeters": 83.0,
            "performance.maxSpeedMps": 3.9,
            "performance.power": PowerSummary(PowerSource.ESTIMATED, 240.0, None),
            "performance.runningDynamics.groundContactTimeMs": 245.0,
            "swimming.strokeType": StrokeType.MIXED,
            "swimming.swolf": Swolf(38.0, MetricOrigin.RAW_PROVIDER),
            "training.aerobicEffect": ProviderMetric(
                3.4, "garmin", "aerobic training effect", MetricOrigin.PROVIDER_DERIVED
            ),
            "training.anaerobicEffect": None,
            "training.load.internal.providerLoad": ProviderMetric(
                None, "garmin", "training load", MetricOrigin.RAW_PROVIDER
            ),
            "training.load.external.workKilojoules": 812.0,
            "environment.temperatureC": -2.5,
            "environment.terrain": Terrain.TRAIL,
        },
        source_paths={"metadata.name": "activityName"},
        streams=(StreamRef(StreamKind.TIME_SERIES, SHA_D),),
        anomalies=(Anomaly("DISTANCE_GPS_JUMP", "distance.meters", "1 segments above 12 m/s"),),
    )


def rich_activity() -> Activity:
    """An enriched activity with the derived and linked fields the EFFECTIVE view fills."""
    entry = sample_observation(
        SourceKind.ATHLETE_ENTRY,
        updates={
            "subjective.rpe": 6.5,
            "subjective.soreness": 2,
            "subjective.pain": Pain(flag=True, intensity=3, location="left calf"),
            "subjective.comments": "Felt heavy",
            "environment.surface": Surface.OTHER,
        },
    )
    activity = sample_activity(sample_observation(), rich_observation(), entry)
    return replace(
        activity,
        values={
            **activity.values,
            "cardiovascular.hrZones": HrZones(UUID(int=7), (120.0, 900.0, 2100.0)),
            "training.load.internal.sessionRpeLoad": 403.0,
            "strength.strengthSessionId": None,
        },
    )


def sample_override(**changes: Any) -> DataOverride:
    values: dict[str, Any] = {
        "override_id": UUID("0193b2a0-0000-7000-8000-000000000501"),
        "athlete_id": ATHLETE,
        "entity_type": EntityType.ACTIVITY,
        "entity_id": ACTIVITY,
        "field": "distance.meters",
        "original_value": 10050.5,
        "corrected_value": 10000.0,
        "reason": "Measured course",
        "corrected_at": NOW,
        "corrected_by": USER,
    }
    values.update(changes)
    return DataOverride(**values)


def sample_issue(athlete_id: UUID = ATHLETE) -> DataQualityIssue:
    return DataQualityIssue.for_anomaly(
        issue_id=UUID("0193b2a0-0000-7000-8000-000000000601"),
        athlete_id=athlete_id,
        activity_id=ACTIVITY,
        anomaly=Anomaly("HR_OUT_OF_RANGE", "cardiovascular.maxHr", "245 above 230"),
        created_at=NOW,
    )


def sample_capabilities(**values: CapabilityValue) -> ProviderCapabilities:
    """Configured capabilities, UNKNOWN unless given (keyword: capability name)."""
    states = {
        capability: CapabilityState(
            values.get(capability.value, CapabilityValue.UNKNOWN),
            CapabilityBasis.CONFIGURED,
            "synthetic configuration",
        )
        for capability in Capability
    }
    return ProviderCapabilities("garmin", AccessTier.PERSONAL, states)


def sample_integration_status(athlete_id: UUID = ATHLETE) -> IntegrationStatus:
    return IntegrationStatus(
        integration_id=UUID("0193b2a0-0000-7000-8000-000000000301"),
        athlete_id=athlete_id,
        provider="garmin",
        capabilities=sample_capabilities(
            activityRead=CapabilityValue.SUPPORTED, pullSupport=CapabilityValue.SUPPORTED
        ),
    )
