"""Synthetic domain objects shared by tests. No real athlete data."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any
from uuid import UUID

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
from coach.domain.common.provenance import FieldValue, Provenance, ValueSource
from coach.domain.common.quality import Confidence, UncertaintyReason
from coach.domain.common.sport import Sport
from coach.domain.common.time import Weekday
from coach.domain.consent.ledger import ConsentLedger, ConsentType
from coach.domain.goals.goal import Goal, GoalBaseline, GoalPriority, GoalStatus, SportEvent
from coach.domain.raw.source import DetectedType, MalwareScan, RawSource, SourceOrigin
from coach.metrics.catalog import SRPE_LOAD
from coach.metrics.derived import DerivedMetricValue, MetricInput, MetricSubject, derive

NOW = datetime(2026, 10, 24, 18, 0, tzinfo=UTC)
ATHLETE = UUID("0193b2a0-0000-7000-8000-000000000001")
OTHER_ATHLETE = UUID("0193b2a0-0000-7000-8000-00000000000b")
ACTIVITY = UUID("0193b2a0-0000-7000-8000-000000000101")
SHA_A = "a" * 64


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
