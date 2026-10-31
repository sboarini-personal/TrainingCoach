"""Definition of Ready and baseline sufficiency, rules 1.0.0 (onboarding spec sections 2 and
3; FR-ONB-003 to FR-ONB-005). Pure functions; thresholds are initial values under A-18.

Readiness: NOT_READY while a REQUIRED input is missing (no plan may be created), PROVISIONAL
when only PREFERRED inputs are missing (only a provisional plan with its assumptions), READY
otherwise. A REQUIRED input recorded as UNKNOWN within its freshness window is satisfied (the
onboarding planner's rule); the approximate current level is satisfied by the goal sport's
experience or by its recent activities.

History for the goal sport is insufficient (baseline collection) with fewer than 6 of its
activities in 28 days, or without a reliable intensity anchor: a known maximum heart rate, a
zone model that is not age-predicted, a race result within 12 months, or (cycling) an FTP
from a measured power source.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Final

from coach.domain.athlete.experience import RunningExperience
from coach.domain.athlete.profile import AthleteProfile
from coach.domain.common.provenance import ValueSource
from coach.domain.common.sport import Sport
from coach.domain.onboarding.planner import (
    FieldState,
    OnboardingField,
    OnboardingSnapshot,
    field_state,
)

READINESS_RULES_VERSION: Final = "1.0.0"
HISTORY_WEEKS: Final = 8
RECOVERY_DAYS: Final = 14
BASELINE_DAYS: Final = 28
BASELINE_MIN_ACTIVITIES: Final = 6
RACE_RESULT_DAYS: Final = 365
FTP_DAYS: Final = 183  # six months (onboarding spec freshness)


class ReadinessStatus(StrEnum):
    NOT_READY = "NOT_READY"
    PROVISIONAL = "PROVISIONAL"
    READY = "READY"


class ReadinessInput(StrEnum):
    PROFILE = "PROFILE"
    PRIMARY_GOAL = "PRIMARY_GOAL"
    CURRENT_LEVEL = "CURRENT_LEVEL"
    WEEKLY_AVAILABILITY = "WEEKLY_AVAILABILITY"
    HEALTH_CONSTRAINTS = "HEALTH_CONSTRAINTS"
    RESOURCES = "RESOURCES"
    STRENGTH_EXPERIENCE = "STRENGTH_EXPERIENCE"
    TENNIS_SCHEDULE = "TENNIS_SCHEDULE"
    ACTIVITY_HISTORY = "ACTIVITY_HISTORY"
    RECOVERY_HISTORY = "RECOVERY_HISTORY"
    GARMIN_INTEGRATION = "GARMIN_INTEGRATION"


LABELS: Final = {
    ReadinessInput.PROFILE: "athlete profile (name, timezone, birth date or age)",
    ReadinessInput.PRIMARY_GOAL: "primary goal with target date",
    ReadinessInput.CURRENT_LEVEL: "approximate current level for the goal sport",
    ReadinessInput.WEEKLY_AVAILABILITY: "weekly availability",
    ReadinessInput.HEALTH_CONSTRAINTS: "declared health constraints",
    ReadinessInput.RESOURCES: "equipment and resources",
    ReadinessInput.STRENGTH_EXPERIENCE: "strength experience",
    ReadinessInput.TENNIS_SCHEDULE: "tennis schedule",
    ReadinessInput.ACTIVITY_HISTORY: "recent activity history (8 weeks)",
    ReadinessInput.RECOVERY_HISTORY: "recovery history (14 days)",
    ReadinessInput.GARMIN_INTEGRATION: "Garmin integration",
}
REQUIRED: Final[dict[ReadinessInput, tuple[OnboardingField, ...]]] = {
    ReadinessInput.PROFILE: (
        OnboardingField.DISPLAY_NAME,
        OnboardingField.TIMEZONE,
        OnboardingField.BIRTH_DATE_OR_AGE,
    ),
    ReadinessInput.PRIMARY_GOAL: (OnboardingField.PRIMARY_GOAL,),
    ReadinessInput.CURRENT_LEVEL: (OnboardingField.GOAL_SPORT_EXPERIENCE,),
    ReadinessInput.WEEKLY_AVAILABILITY: (OnboardingField.WEEKLY_AVAILABILITY,),
    ReadinessInput.HEALTH_CONSTRAINTS: (OnboardingField.HEALTH_CONSTRAINTS,),
    ReadinessInput.RESOURCES: (OnboardingField.RESOURCES,),
    ReadinessInput.STRENGTH_EXPERIENCE: (OnboardingField.STRENGTH_EXPERIENCE,),
    ReadinessInput.TENNIS_SCHEDULE: (OnboardingField.TENNIS,),
}


@dataclass(frozen=True, slots=True)
class HistoryFacts:
    """What the data says, counted by the application up to today."""

    activities: int  # ACTIVE activities in the 8 weeks
    goal_sport_activities: int  # of the primary goal's sport in the 8 weeks
    goal_sport_recent: int  # of the primary goal's sport in the 28 days
    recovery_days: int  # days with a check-in or daily recovery data in the 14 days
    garmin_connected: bool


@dataclass(frozen=True, slots=True)
class Readiness:
    status: ReadinessStatus
    missing_required: tuple[ReadinessInput, ...]
    missing_preferred: tuple[ReadinessInput, ...]
    rules_version: str = READINESS_RULES_VERSION


def assess_readiness(
    snapshot: OnboardingSnapshot, facts: HistoryFacts, *, now: datetime
) -> Readiness:
    missing: list[ReadinessInput] = []
    for item, fields in REQUIRED.items():
        satisfied = all(
            field_state(snapshot, field, now=now) is FieldState.SATISFIED for field in fields
        )
        if not satisfied and item is ReadinessInput.CURRENT_LEVEL:
            satisfied = snapshot.primary_goal is not None and facts.goal_sport_activities > 0
        if not satisfied:
            missing.append(item)
    preferred = [
        item
        for item, present in (
            (ReadinessInput.ACTIVITY_HISTORY, facts.activities > 0),
            (ReadinessInput.RECOVERY_HISTORY, facts.recovery_days > 0),
            (ReadinessInput.GARMIN_INTEGRATION, facts.garmin_connected),
        )
        if not present
    ]
    if missing:
        status = ReadinessStatus.NOT_READY
    elif preferred:
        status = ReadinessStatus.PROVISIONAL
    else:
        status = ReadinessStatus.READY
    return Readiness(status, tuple(missing), tuple(preferred))


# --------------------------------------------------------------------------- baseline


class HistoryGap(StrEnum):
    FEW_ACTIVITIES = "FEW_ACTIVITIES"  # fewer than 6 of the goal sport in 28 days
    NO_INTENSITY_ANCHOR = "NO_INTENSITY_ANCHOR"


@dataclass(frozen=True, slots=True)
class HistorySufficiency:
    sufficient: bool
    gaps: tuple[HistoryGap, ...]


def _recent_race(running: RunningExperience | None, today: date) -> bool:
    if running is None:
        return False
    results = (
        running.recent_5k,
        running.recent_10k,
        running.recent_half_marathon,
        running.recent_marathon,
    )
    return any(
        result is not None and timedelta(0) <= today - result.on <= timedelta(RACE_RESULT_DAYS)
        for result in results
    )


def reliable_anchor(profile: AthleteProfile, sport: Sport, *, today: date) -> bool:
    """A reliable intensity anchor exists (onboarding spec 3; age formulas never count)."""
    max_hr = profile.max_hr
    if (
        max_hr is not None
        and max_hr.value is not None
        and max_hr.provenance.source is not ValueSource.DERIVED
    ):
        return True
    if any(model.reliable_anchor for model in profile.zone_models):
        return True
    running = profile.running_experience
    if _recent_race(None if running is None else running.value, today):
        return True
    if sport is Sport.CYCLING:
        cycling = profile.cycling_experience
        ftp = None if cycling is None or cycling.value is None else cycling.value.ftp
        if ftp is not None and ftp.measured and today - ftp.on <= timedelta(FTP_DAYS):
            return True
    return False


def history_sufficiency(
    profile: AthleteProfile, sport: Sport, *, recent_activities: int, today: date
) -> HistorySufficiency:
    gaps = []
    if recent_activities < BASELINE_MIN_ACTIVITIES:
        gaps.append(HistoryGap.FEW_ACTIVITIES)
    if not reliable_anchor(profile, sport, today=today):
        gaps.append(HistoryGap.NO_INTENSITY_ANCHOR)
    return HistorySufficiency(not gaps, tuple(gaps))
