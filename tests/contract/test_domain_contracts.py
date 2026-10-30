"""Domain objects conform to the published contracts (spec/contracts)."""

from enum import Enum

import pytest

from coach.application.contracts import (
    availability_to_contract,
    consent_record_to_contract,
    goal_to_contract,
    profile_to_contract,
    sport_event_to_contract,
)
from coach.domain.athlete.experience import TennisProfile
from coach.domain.athlete.health import ConstraintSource, RestrictionKind
from coach.domain.athlete.physiology import SensorType, ZoneMethod, ZoneSource
from coach.domain.athlete.profile import AthleteProfile, ProfileUpdate
from coach.domain.athlete.resources import Equipment, TennisSessionType
from coach.domain.coaching.state import Condition, LifecycleState
from coach.domain.common.evidence import EvidenceKind
from coach.domain.common.provenance import MetricOrigin, ValueSource
from coach.domain.common.quality import Confidence, Reliability, UncertaintyReason
from coach.domain.common.sport import Sport
from coach.domain.common.time import TrainingDayBasis, Weekday
from coach.domain.consent.ledger import ConsentType
from coach.domain.goals.goal import GoalPriority, GoalStatus
from coach.domain.raw.source import DetectedType, MalwareScan, SourceOrigin
from coach.domain.strength.prescription import EffortCue, LoadKind
from coach.domain.strength.vocabulary import (
    BodyRegion,
    ContraindicationTag,
    InterferenceLevel,
    MovementPattern,
    MuscleGroup,
)
from coach.metrics.derived import DerivedStatus, SubjectKind
from samples import (
    ATHLETE,
    age_predicted_zones,
    full_profile,
    sample_availability,
    sample_consent_ledger,
    sample_event,
    sample_goal,
)
from schemas import enum_at, validation_errors

# ------------------------------------------------------------------ enum parity

ENUM_PAIRS: list[tuple[type[Enum], str, tuple[str, ...]]] = [
    (Sport, "common", ("$defs", "sport")),
    (Confidence, "common", ("$defs", "confidence")),
    (UncertaintyReason, "common", ("$defs", "uncertaintyReason")),
    (MetricOrigin, "common", ("$defs", "metricOrigin")),
    (EvidenceKind, "common", ("$defs", "evidenceRef", "properties", "kind")),
    (Reliability, "common", ("$defs", "dataQuality", "properties", "reliability")),
    (SourceOrigin, "common", ("$defs", "sourceOrigin")),
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
    (DetectedType, "raw-source", ("properties", "detectedType")),
    (MalwareScan, "raw-source", ("properties", "malwareScan")),
    (DerivedStatus, "derived-metric-value", ("properties", "status")),
    (SubjectKind, "derived-metric-value", ("$defs", "subject", "properties", "kind")),
    (LifecycleState, "coaching-state", ("properties", "lifecycle")),
    (Condition, "coaching-state", ("properties", "conditions", "items")),
    (MovementPattern, "workout", ("$defs", "movementPattern")),
    (MuscleGroup, "workout", ("$defs", "muscleGroup")),
    (Equipment, "exercise", ("properties", "equipment", "items")),
    (ContraindicationTag, "exercise", ("properties", "contraindicationTags", "items")),
    (BodyRegion, "workout", ("$defs", "bodyRegion")),
    (EffortCue, "workout", ("$defs", "strengthExercise", "properties", "effortCue")),
    (
        LoadKind,
        "workout",
        ("$defs", "strengthExercise", "properties", "load", "properties", "kind"),
    ),
    (
        InterferenceLevel,
        "workout",
        ("$defs", "strengthWorkout", "properties", "expectedInterference", "properties", "level"),
    ),
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


# ------------------------------------------------------------------ conformance


@pytest.mark.req("FR-PRF-001", "FR-PRF-003", "FR-PRF-006")
def test_full_profile_conforms_to_contract() -> None:
    assert validation_errors("athlete-profile", profile_to_contract(full_profile())) == []


@pytest.mark.req("FR-PRF-001", "FR-PRF-002", "NFR-REL-004")
def test_age_predicted_zone_model_conforms_to_contract_1_1() -> None:
    profile = full_profile()
    profile = profile.apply(
        ProfileUpdate(zone_models=(*profile.zone_models, age_predicted_zones()))
    )
    contract = profile_to_contract(profile)
    assert contract["schemaVersion"] == "1.2.0"
    assert validation_errors("athlete-profile", contract) == []
    declared_model, estimate = contract["heartRate"]["zoneModels"]
    assert "boundariesPercentMaxHr" not in declared_model
    assert estimate["source"] == "AGE_PREDICTED"
    assert estimate["boundariesPercentMaxHr"] == [51, 59, 71, 81, 90, 100]
    assert "boundariesBpm" not in estimate
    for broken in (
        {**estimate, "boundariesBpm": [100, 140]},  # an estimate never holds bpm
        {**estimate, "method": "HEART_RATE_RESERVE"},
        {**declared_model, "boundariesPercentMaxHr": [51, 59]},
        {key: value for key, value in estimate.items() if key != "boundariesPercentMaxHr"},
    ):
        contract["heartRate"]["zoneModels"] = [broken]
        assert validation_errors("athlete-profile", contract) != []


@pytest.mark.req("FR-PRF-002")
def test_never_recorded_is_absent_and_unknown_is_null() -> None:
    contract = profile_to_contract(full_profile())
    assert contract["physiology"]["weightKg"]["value"] is None
    assert "declaredAge" not in contract["physiology"]
    empty = profile_to_contract(AthleteProfile.new(ATHLETE))
    assert validation_errors("athlete-profile", empty) == []
    assert empty["physiology"] == {}


@pytest.mark.req("FR-PRF-004", "FR-PRF-005")
def test_availability_conforms_to_contract() -> None:
    contract = availability_to_contract(sample_availability())
    assert validation_errors("availability", contract) == []
    assert contract["recurring"]["dailyWindows"]["THURSDAY"] == [{"start": "07:00", "end": "08:30"}]


@pytest.mark.req("FR-GOL-001", "FR-GOL-002")
def test_goal_and_event_conform_to_contracts() -> None:
    assert validation_errors("sport-event", sport_event_to_contract(sample_event())) == []
    assert validation_errors("goal", goal_to_contract(sample_goal())) == []


@pytest.mark.req("PRIV-CON-001", "PRIV-CON-003")
def test_consent_records_conform_to_contract() -> None:
    for record in sample_consent_ledger().records:
        assert validation_errors("consent", consent_record_to_contract(record)) == []
