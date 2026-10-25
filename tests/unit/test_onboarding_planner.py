from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID

import pytest

from coach.domain.athlete.experience import (
    CyclingExperience,
    RunningExperience,
    StrengthExperience,
    SwimmingExperience,
    TennisExperience,
)
from coach.domain.athlete.health import HealthDeclaration
from coach.domain.athlete.profile import AthleteProfile, ProfileUpdate
from coach.domain.athlete.resources import Resources
from coach.domain.availability.availability import Availability, TimeWindow, WeeklyAvailability
from coach.domain.common.provenance import FieldValue
from coach.domain.common.sport import Sport
from coach.domain.common.time import Weekday
from coach.domain.consent.ledger import ConsentLedger, ConsentType
from coach.domain.goals.goal import Goal, GoalPriority
from coach.domain.onboarding.planner import (
    MAX_QUESTIONS_PER_TURN,
    FieldState,
    InputClass,
    OnboardingField,
    OnboardingSnapshot,
    QuestionGroup,
    field_state,
    next_questions,
)

NOW = datetime(2026, 10, 24, 18, 0, tzinfo=UTC)
ATHLETE = UUID("0193b2a0-0000-7000-8000-000000000001")
F = OnboardingField


def declared(value: Any, *, days_ago: int = 0) -> FieldValue[Any]:
    return FieldValue.declared(value, at=NOW - timedelta(days=days_ago))


def consents() -> ConsentLedger:
    return ConsentLedger.new(ATHLETE).grant(
        consent_id=UUID("0193b2a0-0000-7000-8000-000000001001"),
        consent_type=ConsentType.AI_PROCESSING,
        policy_version="1.0",
        purpose="Coaching conversation with an external LLM.",
        at=NOW,
    )


def primary_goal(sport: Sport = Sport.RUNNING) -> Goal:
    return Goal.new(
        goal_id=UUID("0193b2a0-0000-7000-8000-000000000601"),
        athlete_id=ATHLETE,
        sport=sport,
        target_date=date(2026, 12, 13),
        priority=GoalPriority.A,
        created_at=NOW,
    )


def weekly_availability() -> Availability:
    return Availability.new(
        ATHLETE,
        recurring=WeeklyAvailability(
            days_per_week=4,
            maximum_hours_per_week=6,
            daily_windows={Weekday.TUESDAY: (TimeWindow(time(7, 0), time(8, 0)),)},
        ),
    )


IDENTITY = ProfileUpdate(
    display_name=declared("Synthetic Athlete"),
    declared_age=declared(45),
    timezone=declared("America/Argentina/Buenos_Aires"),
)
ALL_REQUIRED = ProfileUpdate(
    display_name=declared("Synthetic Athlete"),
    declared_age=declared(45),
    timezone=declared("America/Argentina/Buenos_Aires"),
    health=declared(HealthDeclaration(declared_at=NOW)),
    running_experience=declared(RunningExperience(weekly_distance_km=30)),
    resources=declared(Resources(gym=True)),
    strength_experience=declared(StrengthExperience(sessions_per_week=1)),
    tennis_experience=declared(TennisExperience(sessions_per_week=0)),
)
ALL_PREFERRED = ProfileUpdate(
    metric_units=declared(True),
    language=declared("es"),
    height_cm=declared(178.0),
    weight_kg=FieldValue.unknown(at=NOW),
    cycling_experience=declared(CyclingExperience()),
    swimming_experience=declared(SwimmingExperience()),
    resting_hr=declared(52),
    max_hr=declared(181),
    garmin_model=declared("synthetic watch"),
    sensors=declared(()),
)


def snapshot(*updates: ProfileUpdate, complete_plan_inputs: bool = False) -> OnboardingSnapshot:
    profile = AthleteProfile.new(ATHLETE)
    for update in updates:
        profile = profile.apply(update)
    return OnboardingSnapshot(
        profile=profile,
        consents=consents(),
        availability=weekly_availability() if complete_plan_inputs else None,
        goals=(primary_goal(),) if complete_plan_inputs else (),
    )


@pytest.mark.req("FR-ONB-001")
def test_consent_step_comes_first() -> None:
    empty = OnboardingSnapshot(
        profile=AthleteProfile.new(ATHLETE), consents=ConsentLedger.new(ATHLETE)
    )
    batch = next_questions(empty, now=NOW)
    assert batch is not None
    assert (batch.group, batch.fields) == (QuestionGroup.CONSENT, (F.CONSENT_DECISIONS,))


@pytest.mark.req("FR-ONB-001")
def test_groups_are_small_related_and_required_first() -> None:
    batch = next_questions(snapshot(), now=NOW)
    assert batch is not None
    assert batch.group is QuestionGroup.IDENTITY
    assert batch.input_class is InputClass.REQUIRED
    assert len(batch.fields) <= MAX_QUESTIONS_PER_TURN
    assert batch.fields == (F.DISPLAY_NAME, F.BIRTH_DATE_OR_AGE, F.TIMEZONE, F.UNITS, F.LANGUAGE)


@pytest.mark.req("FR-ONB-001", "FR-ONB-002")
def test_answered_group_is_not_asked_again() -> None:
    batch = next_questions(snapshot(IDENTITY), now=NOW)
    assert batch is not None
    assert batch.group is QuestionGroup.HEALTH
    assert batch.fields == (F.HEALTH_CONSTRAINTS,)


@pytest.mark.req("FR-ONB-001")
def test_groups_follow_the_specified_order() -> None:
    seen: list[QuestionGroup] = []
    batch = next_questions(snapshot(IDENTITY), now=NOW)
    assert batch is not None
    seen.append(batch.group)
    with_health = snapshot(
        IDENTITY, ProfileUpdate(health=declared(HealthDeclaration(declared_at=NOW)))
    )
    next_batch = next_questions(with_health, now=NOW)
    assert next_batch is not None
    seen.append(next_batch.group)
    assert seen == [QuestionGroup.HEALTH, QuestionGroup.GOAL]


@pytest.mark.req("FR-ONB-001")
def test_goal_sport_experience_is_asked_after_the_primary_goal() -> None:
    profile = (
        AthleteProfile.new(ATHLETE)
        .apply(IDENTITY)
        .apply(ProfileUpdate(health=declared(HealthDeclaration(declared_at=NOW))))
    )
    snap = OnboardingSnapshot(profile=profile, consents=consents(), goals=(primary_goal(),))
    batch = next_questions(snap, now=NOW)
    assert batch is not None
    assert batch.group is QuestionGroup.CURRENT_LEVEL
    assert batch.fields[0] is F.GOAL_SPORT_EXPERIENCE


@pytest.mark.req("FR-ONB-001")
def test_preferred_inputs_are_asked_after_all_required_inputs() -> None:
    batch = next_questions(snapshot(ALL_REQUIRED, complete_plan_inputs=True), now=NOW)
    assert batch is not None
    assert batch.input_class is InputClass.PREFERRED
    assert batch.group is QuestionGroup.IDENTITY
    assert batch.fields == (F.UNITS, F.LANGUAGE, F.HEIGHT, F.WEIGHT)


@pytest.mark.req("FR-ONB-002")
def test_onboarding_is_complete_when_nothing_is_pending() -> None:
    assert (
        next_questions(snapshot(ALL_REQUIRED, ALL_PREFERRED, complete_plan_inputs=True), now=NOW)
        is None
    )


@pytest.mark.req("FR-ONB-002")
def test_unknown_answer_is_not_reasked_within_its_freshness_window() -> None:
    recent = snapshot(ProfileUpdate(weight_kg=FieldValue.unknown(at=NOW - timedelta(days=10))))
    assert field_state(recent, F.WEIGHT, now=NOW) is FieldState.SATISFIED
    stale = snapshot(ProfileUpdate(weight_kg=declared(72.0, days_ago=91)))
    assert field_state(stale, F.WEIGHT, now=NOW) is FieldState.PENDING


@pytest.mark.req("FR-ONB-002")
def test_health_declaration_is_reconfirmed_after_30_days() -> None:
    old = HealthDeclaration(declared_at=NOW - timedelta(days=31))
    assert (
        field_state(
            snapshot(ProfileUpdate(health=declared(old, days_ago=31))),
            F.HEALTH_CONSTRAINTS,
            now=NOW,
        )
        is FieldState.PENDING
    )
    assert (
        field_state(
            snapshot(ProfileUpdate(health=declared(old, days_ago=29))),
            F.HEALTH_CONSTRAINTS,
            now=NOW,
        )
        is FieldState.SATISFIED
    )


@pytest.mark.req("FR-ONB-002")
def test_explicit_no_tennis_satisfies_the_tennis_question() -> None:
    no_tennis = snapshot(
        ProfileUpdate(tennis_experience=declared(TennisExperience(sessions_per_week=0)))
    )
    assert field_state(no_tennis, F.TENNIS, now=NOW) is FieldState.SATISFIED
