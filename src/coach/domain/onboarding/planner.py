"""Deterministic onboarding question planner (FR-ONB-001, FR-ONB-002).

Code, not the LLM, decides which information to request next. First pass: the first group
(in order) with a pending REQUIRED field, listing its pending REQUIRED fields and then its
pending PREFERRED fields. Second pass: the first group with a pending PREFERRED field. A field
recorded within its freshness window is satisfied even when it was answered as UNKNOWN.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Final

from coach.domain.athlete.profile import AthleteProfile
from coach.domain.availability.availability import Availability
from coach.domain.common.provenance import FieldValue
from coach.domain.common.sport import Sport
from coach.domain.consent.ledger import ConsentLedger, ConsentType
from coach.domain.goals.goal import Goal, GoalPriority, GoalStatus

MAX_QUESTIONS_PER_TURN: Final = 5


class QuestionGroup(StrEnum):
    CONSENT = "G0"
    IDENTITY = "G1"
    HEALTH = "G2"
    GOAL = "G3"
    CURRENT_LEVEL = "G4"
    AVAILABILITY = "G5"
    STRENGTH = "G6"
    TENNIS = "G7"
    DEVICES = "G8"


class InputClass(StrEnum):
    REQUIRED = "REQUIRED"
    PREFERRED = "PREFERRED"


class OnboardingField(StrEnum):
    CONSENT_DECISIONS = "CONSENT_DECISIONS"
    DISPLAY_NAME = "DISPLAY_NAME"
    BIRTH_DATE_OR_AGE = "BIRTH_DATE_OR_AGE"
    TIMEZONE = "TIMEZONE"
    UNITS = "UNITS"
    LANGUAGE = "LANGUAGE"
    HEIGHT = "HEIGHT"
    WEIGHT = "WEIGHT"
    HEALTH_CONSTRAINTS = "HEALTH_CONSTRAINTS"
    PRIMARY_GOAL = "PRIMARY_GOAL"
    GOAL_SPORT_EXPERIENCE = "GOAL_SPORT_EXPERIENCE"
    OTHER_SPORTS_EXPERIENCE = "OTHER_SPORTS_EXPERIENCE"
    RESTING_HR = "RESTING_HR"
    MAX_HR = "MAX_HR"
    WEEKLY_AVAILABILITY = "WEEKLY_AVAILABILITY"
    RESOURCES = "RESOURCES"
    STRENGTH_EXPERIENCE = "STRENGTH_EXPERIENCE"
    TENNIS = "TENNIS"
    GARMIN_MODEL = "GARMIN_MODEL"
    SENSORS = "SENSORS"


class FieldState(StrEnum):
    PENDING = "PENDING"
    SATISFIED = "SATISFIED"


@dataclass(frozen=True, slots=True)
class FieldRule:
    field: OnboardingField
    group: QuestionGroup
    input_class: InputClass
    freshness: timedelta | None = None


_F, _G, _REQ, _PREF = OnboardingField, QuestionGroup, InputClass.REQUIRED, InputClass.PREFERRED
_DAYS = timedelta(days=1)

# Order matters: groups in sequence, fields in asking order within a group.
FIELD_RULES: Final[tuple[FieldRule, ...]] = (
    FieldRule(_F.CONSENT_DECISIONS, _G.CONSENT, _REQ),
    FieldRule(_F.DISPLAY_NAME, _G.IDENTITY, _REQ),
    FieldRule(_F.BIRTH_DATE_OR_AGE, _G.IDENTITY, _REQ),
    FieldRule(_F.TIMEZONE, _G.IDENTITY, _REQ),
    FieldRule(_F.UNITS, _G.IDENTITY, _PREF),
    FieldRule(_F.LANGUAGE, _G.IDENTITY, _PREF),
    FieldRule(_F.HEIGHT, _G.IDENTITY, _PREF),
    FieldRule(_F.WEIGHT, _G.IDENTITY, _PREF, 90 * _DAYS),
    FieldRule(_F.HEALTH_CONSTRAINTS, _G.HEALTH, _REQ, 30 * _DAYS),
    FieldRule(_F.PRIMARY_GOAL, _G.GOAL, _REQ),
    FieldRule(_F.GOAL_SPORT_EXPERIENCE, _G.CURRENT_LEVEL, _REQ),
    FieldRule(_F.OTHER_SPORTS_EXPERIENCE, _G.CURRENT_LEVEL, _PREF),
    FieldRule(_F.RESTING_HR, _G.CURRENT_LEVEL, _PREF, 180 * _DAYS),
    FieldRule(_F.MAX_HR, _G.CURRENT_LEVEL, _PREF, 180 * _DAYS),
    FieldRule(_F.WEEKLY_AVAILABILITY, _G.AVAILABILITY, _REQ),
    FieldRule(_F.RESOURCES, _G.AVAILABILITY, _REQ),
    FieldRule(_F.STRENGTH_EXPERIENCE, _G.STRENGTH, _REQ),
    FieldRule(_F.TENNIS, _G.TENNIS, _REQ),
    FieldRule(_F.GARMIN_MODEL, _G.DEVICES, _PREF),
    FieldRule(_F.SENSORS, _G.DEVICES, _PREF),
)
_RULES_BY_FIELD: Final = {rule.field: rule for rule in FIELD_RULES}
_ENDURANCE: Final = (Sport.RUNNING, Sport.CYCLING, Sport.SWIMMING)


@dataclass(frozen=True, slots=True)
class OnboardingSnapshot:
    profile: AthleteProfile
    consents: ConsentLedger
    availability: Availability | None = None
    goals: tuple[Goal, ...] = ()

    @property
    def primary_goal(self) -> Goal | None:
        open_statuses = (GoalStatus.DRAFT, GoalStatus.ACTIVE)
        return next(
            (
                goal
                for goal in self.goals
                if goal.priority is GoalPriority.A and goal.status in open_statuses
            ),
            None,
        )


@dataclass(frozen=True, slots=True)
class QuestionBatch:
    group: QuestionGroup
    fields: tuple[OnboardingField, ...]
    input_class: InputClass


def _recorded(value: FieldValue[Any] | None, rule: FieldRule, now: datetime) -> bool:
    return value is not None and value.recorded_within(rule.freshness, now=now)


def _goal_sport_experience(snapshot: OnboardingSnapshot, rule: FieldRule, now: datetime) -> bool:
    goal = snapshot.primary_goal
    if goal is None:
        return False
    if goal.sport is Sport.OTHER:
        return True
    return _recorded(snapshot.profile.experience_for(goal.sport), rule, now)


def _other_sports_experience(snapshot: OnboardingSnapshot, rule: FieldRule, now: datetime) -> bool:
    goal = snapshot.primary_goal
    goal_sport = goal.sport if goal is not None else None
    return all(
        _recorded(snapshot.profile.experience_for(sport), rule, now)
        for sport in _ENDURANCE
        if sport is not goal_sport
    )


def _profile_field(name: str) -> Callable[[OnboardingSnapshot, FieldRule, datetime], bool]:
    def check(snapshot: OnboardingSnapshot, rule: FieldRule, now: datetime) -> bool:
        value: FieldValue[Any] | None = getattr(snapshot.profile, name)
        return _recorded(value, rule, now)

    return check


_CHECKS: Final[dict[OnboardingField, Callable[[OnboardingSnapshot, FieldRule, datetime], bool]]] = {
    _F.CONSENT_DECISIONS: lambda s, r, n: s.consents.has_decision(ConsentType.AI_PROCESSING),
    _F.DISPLAY_NAME: _profile_field("display_name"),
    _F.BIRTH_DATE_OR_AGE: lambda s, r, n: (
        _recorded(s.profile.birth_date, r, n) or _recorded(s.profile.declared_age, r, n)
    ),
    _F.TIMEZONE: _profile_field("timezone"),
    _F.UNITS: _profile_field("metric_units"),
    _F.LANGUAGE: _profile_field("language"),
    _F.HEIGHT: _profile_field("height_cm"),
    _F.WEIGHT: _profile_field("weight_kg"),
    _F.HEALTH_CONSTRAINTS: _profile_field("health"),
    _F.PRIMARY_GOAL: lambda s, r, n: s.primary_goal is not None,
    _F.GOAL_SPORT_EXPERIENCE: _goal_sport_experience,
    _F.OTHER_SPORTS_EXPERIENCE: _other_sports_experience,
    _F.RESTING_HR: _profile_field("resting_hr"),
    _F.MAX_HR: _profile_field("max_hr"),
    _F.WEEKLY_AVAILABILITY: lambda s, r, n: (
        s.availability is not None and s.availability.recurring is not None
    ),
    _F.RESOURCES: _profile_field("resources"),
    _F.STRENGTH_EXPERIENCE: _profile_field("strength_experience"),
    _F.TENNIS: _profile_field("tennis_experience"),
    _F.GARMIN_MODEL: _profile_field("garmin_model"),
    _F.SENSORS: _profile_field("sensors"),
}


def field_state(
    snapshot: OnboardingSnapshot, field: OnboardingField, *, now: datetime
) -> FieldState:
    rule = _RULES_BY_FIELD[field]
    satisfied = _CHECKS[field](snapshot, rule, now)
    return FieldState.SATISFIED if satisfied else FieldState.PENDING


def next_questions(
    snapshot: OnboardingSnapshot, *, now: datetime, max_questions: int = MAX_QUESTIONS_PER_TURN
) -> QuestionBatch | None:
    pending = [
        rule
        for rule in FIELD_RULES
        if field_state(snapshot, rule.field, now=now) is FieldState.PENDING
    ]
    for input_class in (InputClass.REQUIRED, InputClass.PREFERRED):
        group = next((rule.group for rule in pending if rule.input_class is input_class), None)
        if group is None:
            continue
        in_group = [rule for rule in pending if rule.group is group]
        ordered = [rule for rule in in_group if rule.input_class is InputClass.REQUIRED] + [
            rule for rule in in_group if rule.input_class is InputClass.PREFERRED
        ]
        return QuestionBatch(
            group=group,
            fields=tuple(rule.field for rule in ordered[:max_questions]),
            input_class=input_class,
        )
    return None
