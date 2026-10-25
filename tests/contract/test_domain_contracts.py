"""Domain objects conform to the published contracts (spec/contracts)."""

from datetime import UTC, date, datetime, time
from enum import Enum
from typing import Any
from uuid import UUID

import pytest

from coach.application.contracts import (
    availability_to_contract,
    consent_record_to_contract,
    goal_to_contract,
    profile_to_contract,
    sport_event_to_contract,
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
from coach.domain.common.evidence import EvidenceKind, EvidenceRef
from coach.domain.common.provenance import FieldValue, MetricOrigin, Provenance, ValueSource
from coach.domain.common.quality import Confidence, Reliability, UncertaintyReason
from coach.domain.common.sport import Sport
from coach.domain.common.time import TrainingDayBasis, Weekday
from coach.domain.consent.ledger import ConsentLedger, ConsentType
from coach.domain.goals.goal import Goal, GoalBaseline, GoalPriority, GoalStatus, SportEvent
from schemas import enum_at, validation_errors

NOW = datetime(2026, 10, 24, 18, 0, tzinfo=UTC)
ATHLETE = UUID("0193b2a0-0000-7000-8000-000000000001")


# ------------------------------------------------------------------ enum parity

ENUM_PAIRS: list[tuple[type[Enum], str, tuple[str, ...]]] = [
    (Sport, "common", ("$defs", "sport")),
    (Confidence, "common", ("$defs", "confidence")),
    (UncertaintyReason, "common", ("$defs", "uncertaintyReason")),
    (MetricOrigin, "common", ("$defs", "metricOrigin")),
    (EvidenceKind, "common", ("$defs", "evidenceRef", "properties", "kind")),
    (Reliability, "common", ("$defs", "dataQuality", "properties", "reliability")),
    (TrainingDayBasis, "activity", ("properties", "timing", "properties", "trainingDayBasis")),
    (ConsentType, "consent", ("properties", "consentType")),
    (GoalPriority, "goal", ("$defs", "priority")),
    (GoalStatus, "goal", ("properties", "status")),
    (ValueSource, "athlete-profile", ("$defs", "provenance", "properties", "source")),
    (ZoneMethod, "athlete-profile", ("$defs", "zoneModel", "properties", "method")),
    (ZoneSource, "athlete-profile", ("$defs", "zoneModel", "properties", "source")),
    (SensorType, "athlete-profile", ("$defs", "sensor", "properties", "type")),
    (TennisProfile, "athlete-profile", ("$defs", "tennisExperience", "properties", "profile")),
    (ConstraintSource, "athlete-profile", ("$defs", "healthConstraint", "properties", "source")),
]


@pytest.mark.req("NFR-REL-004")
@pytest.mark.parametrize(
    ("enum", "schema", "path"), ENUM_PAIRS, ids=lambda v: getattr(v, "__name__", None)
)
def test_domain_enum_matches_contract(enum: type[Enum], schema: str, path: tuple[str, ...]) -> None:
    assert {member.value for member in enum} == enum_at(schema, *path)


@pytest.mark.req("NFR-REL-004")
def test_nested_enums_match_contract() -> None:
    profile = ("athlete-profile", "$defs")
    resources = (*profile, "resources", "properties")
    constraint = (*profile, "healthConstraint", "properties")
    assert {e.value for e in Equipment} == enum_at(*resources, "strengthEquipment", "items")
    assert {e.value for e in TennisSessionType} == enum_at(
        *resources, "tennisSchedule", "items", "properties", "sessionType"
    )
    assert {e.value for e in RestrictionKind} == enum_at(
        *constraint, "restrictions", "items", "properties", "kind"
    )
    assert {day.name for day in Weekday} == enum_at("common", "$defs", "weekday")


# ------------------------------------------------------------------ profile


def _declared(value: Any) -> FieldValue[Any]:
    return FieldValue.declared(value, at=NOW)


def full_profile() -> AthleteProfile:
    return AthleteProfile.new(ATHLETE).apply(
        ProfileUpdate(
            display_name=_declared("Synthetic Athlete"),
            birth_date=_declared(date(1981, 5, 17)),
            height_cm=_declared(178.0),
            weight_kg=FieldValue.unknown(at=NOW),
            resting_hr=FieldValue(52, Provenance(ValueSource.PROVIDER, NOW, provider="garmin")),
            max_hr=_declared(181),
            zone_method=_declared(ZoneMethod.PERCENT_MAX_HR),
            zone_models=(
                HeartRateZoneModel(
                    zone_model_id=UUID("0193b2a0-0000-7000-8000-000000000301"),
                    method=ZoneMethod.PERCENT_MAX_HR,
                    source=ZoneSource.ATHLETE_DECLARED,
                    boundaries_bpm=(100, 120, 140, 158, 170, 185),
                    valid_from=date(2026, 10, 1),
                ),
            ),
            timezone=_declared("America/Argentina/Buenos_Aires"),
            metric_units=_declared(True),
            language=_declared("es"),
            garmin_model=_declared("synthetic watch"),
            sensors=_declared((Sensor(SensorType.HR_STRAP, reliable_for_training=True),)),
            running_experience=_declared(
                RunningExperience(
                    years_experience=6,
                    weekly_distance_km=30,
                    recent_10k=RaceResult(2940, date(2026, 9, 14), ValueSource.ATHLETE_DECLARED),
                )
            ),
            cycling_experience=_declared(
                CyclingExperience(ftp=FtpValue(220, date(2026, 8, 1), measured=True))
            ),
            swimming_experience=_declared(SwimmingExperience(pool_length_m=25)),
            strength_experience=_declared(
                StrengthExperience(
                    sessions_per_week=2,
                    familiar_exercise_ids=("ex.goblet_squat",),
                    approximate_loads=(
                        LoadReference("ex.goblet_squat", 24, 10, date(2026, 10, 1)),
                    ),
                    rpe_familiar=True,
                )
            ),
            tennis_experience=_declared(
                TennisExperience(sessions_per_week=1, profile=TennisProfile.PRACTICE)
            ),
            resources=_declared(
                Resources(
                    pool=PoolAccess(available=True, length_m=25),
                    gym=True,
                    strength_equipment=frozenset({Equipment.DUMBBELLS, Equipment.BENCH}),
                    tennis_schedule=(
                        TennisSlot(Weekday.WEDNESDAY, 19 * 60, 90, TennisSessionType.PRACTICE),
                    ),
                )
            ),
            health=_declared(
                HealthDeclaration(
                    declared_at=NOW,
                    constraints=(
                        HealthConstraint(
                            constraint_id=UUID("0193b2a0-0000-7000-8000-000000000321"),
                            description="Left Achilles discomfort",
                            affected_regions=("achilles",),
                            affected_sports=frozenset({Sport.RUNNING}),
                            restrictions=(Restriction(RestrictionKind.MAX_RPE, max_rpe=6),),
                            declared_at=NOW,
                            valid_until=date(2026, 11, 30),
                            source=ConstraintSource.ATHLETE,
                        ),
                    ),
                )
            ),
        )
    )


@pytest.mark.req("FR-PRF-001", "FR-PRF-003", "FR-PRF-006")
def test_full_profile_conforms_to_contract() -> None:
    assert validation_errors("athlete-profile", profile_to_contract(full_profile())) == []


@pytest.mark.req("FR-PRF-002")
def test_never_recorded_is_absent_and_unknown_is_null() -> None:
    contract = profile_to_contract(full_profile())
    assert contract["physiology"]["weightKg"]["value"] is None
    assert "declaredAge" not in contract["physiology"]
    empty = profile_to_contract(AthleteProfile.new(ATHLETE))
    assert validation_errors("athlete-profile", empty) == []
    assert empty["physiology"] == {}


# ------------------------------------------------------------------ availability


@pytest.mark.req("FR-PRF-004", "FR-PRF-005")
def test_availability_conforms_to_contract() -> None:
    availability = Availability.new(
        ATHLETE,
        recurring=WeeklyAvailability(
            days_per_week=5,
            maximum_hours_per_week=7.5,
            preferred_rest_days=frozenset({Weekday.MONDAY}),
            daily_windows={Weekday.THURSDAY: (TimeWindow(time(7, 0), time(8, 30)),)},
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
    contract = availability_to_contract(availability)
    assert validation_errors("availability", contract) == []
    assert contract["recurring"]["dailyWindows"] == {
        "THURSDAY": [{"start": "07:00", "end": "08:30"}]
    }


# ------------------------------------------------------------------ goals and events


@pytest.mark.req("FR-GOL-001", "FR-GOL-002")
def test_goal_and_event_conform_to_contracts() -> None:
    event = SportEvent(
        event_id=UUID("0193b2a0-0000-7000-8000-000000000611"),
        athlete_id=ATHLETE,
        name="Synthetic City 10K",
        sport=Sport.RUNNING,
        event_date=date(2026, 12, 13),
        distance_meters=10_000,
        priority=GoalPriority.A,
    )
    goal = Goal.new(
        goal_id=UUID("0193b2a0-0000-7000-8000-000000000601"),
        athlete_id=ATHLETE,
        sport=Sport.RUNNING,
        target_date=date(2026, 12, 13),
        priority=GoalPriority.A,
        created_at=NOW,
        distance_meters=10_000,
        target_time_seconds=2700,
        event_id=event.event_id,
        baseline=GoalBaseline(
            captured_at=NOW,
            evidence=(
                EvidenceRef(EvidenceKind.ACTIVITY, UUID("0193b2a0-0000-7000-8000-000000000101")),
            ),
        ),
    ).transition(GoalStatus.ACTIVE)
    assert validation_errors("sport-event", sport_event_to_contract(event)) == []
    assert validation_errors("goal", goal_to_contract(goal)) == []


# ------------------------------------------------------------------ consent


@pytest.mark.req("PRIV-CON-001", "PRIV-CON-003")
def test_consent_records_conform_to_contract() -> None:
    ledger = (
        ConsentLedger.new(ATHLETE)
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
    for record in ledger.records:
        assert validation_errors("consent", consent_record_to_contract(record)) == []
