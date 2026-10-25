from dataclasses import fields
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import pytest

from coach.domain.athlete.experience import (
    FtpValue,
    RaceResult,
    RunningExperience,
    TennisExperience,
)
from coach.domain.athlete.health import (
    ConstraintSource,
    HealthConstraint,
    HealthDeclaration,
    Restriction,
    RestrictionKind,
)
from coach.domain.athlete.physiology import HeartRateZoneModel, ZoneMethod, ZoneSource
from coach.domain.athlete.profile import AthleteProfile, ProfileUpdate
from coach.domain.athlete.resources import PoolAccess, Resources, TennisSlot
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.provenance import FieldValue, ValueSource
from coach.domain.common.sport import Sport
from coach.domain.common.time import Weekday

NOW = datetime(2026, 10, 24, 18, 0, tzinfo=UTC)
TODAY = date(2026, 10, 24)
ATHLETE = UUID("0193b2a0-0000-7000-8000-000000000001")
ZONE_MODEL = UUID("0193b2a0-0000-7000-8000-000000000301")
CONSTRAINT = UUID("0193b2a0-0000-7000-8000-000000000321")


def declared(value: Any) -> FieldValue[Any]:
    return FieldValue.declared(value, at=NOW)


def updated(**changes: Any) -> AthleteProfile:
    return AthleteProfile.new(ATHLETE).apply(ProfileUpdate(**changes))


# ------------------------------------------------------------------ profile basics


@pytest.mark.req("FR-PRF-001", "FR-PRF-002")
def test_new_profile_has_nothing_recorded() -> None:
    profile = AthleteProfile.new(ATHLETE)
    assert profile.athlete_id == ATHLETE
    assert profile.version == 1
    assert profile.height_cm is None
    assert profile.max_hr is None
    assert profile.health is None


@pytest.mark.req("FR-PRF-002")
def test_unknown_answer_is_stored_as_unknown_not_invented() -> None:
    profile = updated(weight_kg=FieldValue.unknown(at=NOW))
    assert profile.weight_kg is not None
    assert profile.weight_kg.value is None


@pytest.mark.req("FR-PRF-001")
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("height_cm", 99.0),
        ("height_cm", 251.0),
        ("weight_kg", 29.0),
        ("resting_hr", 24),
        ("max_hr", 231),
        ("declared_age", 17),
    ],
)
def test_out_of_range_physiology_is_rejected(field: str, value: float) -> None:
    with pytest.raises(DomainValidationError):
        updated(**{field: declared(value)})


@pytest.mark.req("FR-PRF-001")
def test_resting_heart_rate_must_be_below_maximum() -> None:
    with pytest.raises(DomainValidationError):
        updated(resting_hr=declared(115), max_hr=declared(110))
    assert updated(resting_hr=declared(52), max_hr=declared(181)).max_hr == declared(181)


@pytest.mark.req("FR-PRF-001")
def test_timezone_must_be_a_known_iana_zone() -> None:
    with pytest.raises(DomainValidationError):
        updated(timezone=declared("Mars/Olympus_Mons"))
    assert updated(timezone=declared("America/Argentina/Buenos_Aires")).timezone is not None


@pytest.mark.req("FR-PRF-007")
def test_language_defaults_to_spanish_until_declared() -> None:
    assert AthleteProfile.new(ATHLETE).effective_language == "es"
    assert updated(language=declared("en")).effective_language == "en"
    with pytest.raises(DomainValidationError):
        updated(language=declared("english!"))


@pytest.mark.req("SEC-AUTHZ-003")
def test_profile_update_cannot_carry_identity_or_version() -> None:
    names = {field.name for field in fields(ProfileUpdate)}
    assert "athlete_id" not in names
    assert "version" not in names
    with pytest.raises(TypeError):
        ProfileUpdate(version=7)  # type: ignore[call-arg]


@pytest.mark.req("FR-PRF-001")
def test_update_keeps_fields_it_does_not_mention() -> None:
    profile = updated(height_cm=declared(178.0)).apply(ProfileUpdate(weight_kg=declared(72.0)))
    assert profile.height_cm == declared(178.0)
    assert profile.weight_kg == declared(72.0)


@pytest.mark.req("FR-PRF-001")
def test_display_name_is_cleaned_and_capped() -> None:
    assert updated(display_name=declared("Ana\x00 Runner")).display_name == declared("Ana Runner")
    with pytest.raises(DomainValidationError):
        updated(display_name=declared("x" * 81))


@pytest.mark.req("FR-PRF-001")
def test_adulthood_is_checked_against_a_date() -> None:
    assert updated(declared_age=declared(45)).is_adult(on=TODAY) is True
    assert updated(birth_date=declared(date(2010, 1, 1))).is_adult(on=TODAY) is False
    assert AthleteProfile.new(ATHLETE).is_adult(on=TODAY) is None


# ------------------------------------------------------------------ heart-rate zones


@pytest.mark.req("FR-PRF-001")
def test_zone_boundaries_must_ascend_within_range() -> None:
    def model(boundaries: tuple[int, ...]) -> HeartRateZoneModel:
        return HeartRateZoneModel(
            zone_model_id=ZONE_MODEL,
            method=ZoneMethod.PERCENT_MAX_HR,
            source=ZoneSource.ATHLETE_DECLARED,
            boundaries_bpm=boundaries,
            valid_from=TODAY,
        )

    assert model((100, 120, 140)).zone_count == 2
    for bad in ((100,), (100, 100, 140), (20, 100), (100, 240)):
        with pytest.raises(DomainValidationError):
            model(bad)


@pytest.mark.req("FR-PRF-002")
def test_no_age_predicted_zone_source_exists() -> None:
    assert {source.value for source in ZoneSource} == {
        "ATHLETE_DECLARED",
        "PROVIDER",
        "CALCULATED_FROM_MEASURED_ANCHORS",
    }


# ------------------------------------------------------------------ sport experience


@pytest.mark.req("FR-PRF-003")
def test_experience_is_stored_independently_per_sport() -> None:
    running = RunningExperience(
        years_experience=6,
        weekly_distance_km=30,
        recent_10k=RaceResult(2940, date(2026, 9, 14), ValueSource.ATHLETE_DECLARED),
    )
    no_tennis = TennisExperience(sessions_per_week=0)
    profile = updated(running_experience=declared(running), tennis_experience=declared(no_tennis))
    assert profile.experience_for(Sport.RUNNING) == declared(running)
    assert profile.experience_for(Sport.TENNIS) == declared(no_tennis)
    assert profile.experience_for(Sport.CYCLING) is None


@pytest.mark.req("FR-PRF-003")
def test_experience_values_are_validated() -> None:
    with pytest.raises(DomainValidationError):
        RunningExperience(weekly_distance_km=-5)
    with pytest.raises(DomainValidationError):
        RaceResult(0, TODAY, ValueSource.ATHLETE_DECLARED)
    with pytest.raises(DomainValidationError):
        FtpValue(watts=20, on=TODAY, measured=True)


# ------------------------------------------------------------------ health constraints


def _constraint(**overrides: Any) -> HealthConstraint:
    values: dict[str, Any] = {
        "constraint_id": CONSTRAINT,
        "description": "Left Achilles discomfort",
        "affected_regions": ("achilles",),
        "affected_sports": frozenset({Sport.RUNNING}),
        "restrictions": (Restriction(RestrictionKind.NO_IMPACT),),
        "declared_at": NOW,
        "valid_until": date(2026, 11, 30),
        "source": ConstraintSource.ATHLETE,
    }
    values.update(overrides)
    return HealthConstraint(**values)


@pytest.mark.req("FR-PRF-006")
def test_explicit_no_constraints_declaration_is_recorded() -> None:
    declaration = HealthDeclaration(declared_at=NOW, constraints=())
    assert declaration.declares_none
    assert updated(health=declared(declaration)).health == declared(declaration)


@pytest.mark.req("FR-PRF-006")
def test_constraint_is_active_until_its_end_date() -> None:
    constraint = _constraint()
    declaration = HealthDeclaration(declared_at=NOW, constraints=(constraint,))
    assert declaration.active_constraints(on=date(2026, 11, 30)) == (constraint,)
    assert declaration.active_constraints(on=date(2026, 12, 1)) == ()


@pytest.mark.req("FR-PRF-006")
def test_restrictions_require_their_parameters() -> None:
    with pytest.raises(DomainValidationError):
        Restriction(RestrictionKind.MAX_RPE)
    with pytest.raises(DomainValidationError):
        Restriction(RestrictionKind.NO_SPORT)
    assert Restriction(RestrictionKind.MAX_RPE, max_rpe=6).max_rpe == 6
    assert Restriction(RestrictionKind.NO_SPORT, sport=Sport.TENNIS).sport is Sport.TENNIS


@pytest.mark.req("FR-PRF-006")
def test_constraint_description_is_untrusted_text() -> None:
    assert _constraint(description="Knee\x07 pain").description == "Knee pain"
    with pytest.raises(DomainValidationError):
        _constraint(description="x" * 501)
    with pytest.raises(DomainValidationError):
        _constraint(description="   ")


# ------------------------------------------------------------------ resources


@pytest.mark.req("FR-PRF-004")
def test_resources_are_validated() -> None:
    resources = Resources(
        pool=PoolAccess(available=True, length_m=25.0),
        gym=True,
        tennis_schedule=(TennisSlot(Weekday.WEDNESDAY, start_minute=19 * 60, duration_minutes=90),),
    )
    assert updated(resources=declared(resources)).resources == declared(resources)
    with pytest.raises(DomainValidationError):
        PoolAccess(available=True, length_m=0)
    with pytest.raises(DomainValidationError):
        TennisSlot(Weekday.WEDNESDAY, start_minute=19 * 60, duration_minutes=0)
