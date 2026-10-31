"""Goal feasibility, rules 1.0.0 (goals spec sections 4 and 5; FR-GOL-003 to FR-GOL-005).

A deterministic pipeline, each step computed here and never by the AI coach (ADR-008):

1. Current state: the best evidence of current performance for the goal's sport and
   distance. Rules 1.0.0 use the athlete's declared running race results of the last 12
   months, carried to the goal distance with Riegel's endurance equation, T2 = T1 x
   (D2 / D1) ^ 1.06 (Riegel PS. Athletic records and human endurance. Am Sci.
   1981;69(3):285-290). The result nearest the goal distance is used, the most recent on a
   tie. Confidence: HIGH for the same distance within 6 months; MEDIUM within 6 months and
   half to twice the distance, or the same distance within 12 months; LOW otherwise
   (extrapolations are least reliable, the marathon above all).
2. Performance gap: target minus current, in seconds over the goal distance; UNKNOWN without
   a target time or a current state.
3. Available time: the weeks to the target date and the weekly hours of the availability.
4. Required progression: the gap per available week, in seconds and as a share of the
   current time.
5. Classification: LIKELY (no gap, or 0.25 % a week or less), POSSIBLE (0.5 % a week or
   less), UNLIKELY (more); at most POSSIBLE with fewer than 2 hours a week. INSUFFICIENT_DATA
   whenever the current state's confidence is LOW or the gap is UNKNOWN.

When the classification is not LIKELY or its confidence is not HIGH, the desired, realistic
and minimum satisfactory goals are presented: the target; the current time improved at the
LIKELY rate over the weeks available (never faster than the target); the current time. An
outcome is never guaranteed (FR-GOL-005). Thresholds are initial values under A-18.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final
from uuid import UUID

from coach.domain.athlete.experience import RaceResult, RunningExperience
from coach.domain.athlete.profile import AthleteProfile
from coach.domain.availability.availability import Availability
from coach.domain.common.evidence import EvidenceKind, EvidenceRef
from coach.domain.common.quality import Confidence
from coach.domain.common.sport import Sport
from coach.domain.goals.goal import Goal

FEASIBILITY_RULES_VERSION: Final = "1.0.0"
RIEGEL_EXPONENT: Final = 1.06
RIEGEL_1981: Final = (
    "Riegel PS. Athletic records and human endurance. Am Sci. 1981;69(3):285-290 "
    "(PMID 7235349): T2 = T1 x (D2 / D1) ^ 1.06"
)
RESULT_DAYS: Final = 365
RECENT_DAYS: Final = 183
SAME_DISTANCE: Final = 0.05  # within 5 %
NEAR_DISTANCE: Final = 2.0  # half to twice the distance
LIKELY_RATE: Final = 0.0025  # 0.25 % of the current time a week
POSSIBLE_RATE: Final = 0.005
MIN_HOURS: Final = 2.0
_RACES: Final = (
    ("recent_5k", 5_000.0),
    ("recent_10k", 10_000.0),
    ("recent_half_marathon", 21_097.5),
    ("recent_marathon", 42_195.0),
)


class Classification(StrEnum):
    LIKELY = "LIKELY"
    POSSIBLE = "POSSIBLE"
    UNLIKELY = "UNLIKELY"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class FeasibilityReason(StrEnum):
    NO_TARGET_TIME = "NO_TARGET_TIME"  # a goal to finish: not assessed in rules 1.0.0
    NO_DISTANCE = "NO_DISTANCE"
    SPORT_NOT_SUPPORTED = "SPORT_NOT_SUPPORTED"  # rules 1.0.0 assess running only
    NO_COMPARABLE_RESULT = "NO_COMPARABLE_RESULT"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    FEW_HOURS = "FEW_HOURS"


@dataclass(frozen=True, slots=True)
class CurrentState:
    seconds: float  # predicted for the goal distance
    source_distance_m: float
    source_seconds: float
    source_date: date
    confidence: Confidence
    evidence: EvidenceRef
    method: str = RIEGEL_1981


@dataclass(frozen=True, slots=True, kw_only=True)
class FeasibilityAssessment:
    goal_id: UUID
    assessed_on: date
    classification: Classification
    confidence: Confidence
    reasons: tuple[FeasibilityReason, ...]
    current: CurrentState | None
    gap_seconds: float | None
    weeks_available: float
    hours_per_week: float | None
    required_seconds_per_week: float | None
    required_percent_per_week: float | None
    desired_seconds: float | None
    realistic_seconds: float | None
    minimum_seconds: float | None
    rules_version: str = FEASIBILITY_RULES_VERSION

    @property
    def presents_alternatives(self) -> bool:
        """Desired, realistic and minimum goals are presented (FR-GOL-004)."""
        return self.classification is not Classification.LIKELY or (
            self.confidence is not Confidence.HIGH
        )


def _clock(seconds: float) -> str:
    total = round(seconds)
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"


def current_state(
    profile: AthleteProfile, distance_m: float, *, today: date
) -> CurrentState | None:
    """The declared running result nearest the distance, carried to it (Riegel)."""
    known = profile.running_experience
    running = None if known is None else known.value
    if not isinstance(running, RunningExperience):
        return None
    candidates: list[tuple[float, int, str, float, RaceResult]] = []
    for name, distance in _RACES:
        result: RaceResult | None = getattr(running, name)
        if result is None:
            continue
        age = (today - result.on).days
        if not 0 <= age <= RESULT_DAYS:
            continue
        candidates.append((abs(math.log(distance_m / distance)), age, name, distance, result))
    if not candidates:
        return None
    _gap, age, name, distance, result = min(candidates, key=lambda item: (item[0], item[1]))
    ratio = distance_m / distance
    same = abs(ratio - 1) <= SAME_DISTANCE
    near = 1 / NEAR_DISTANCE <= ratio <= NEAR_DISTANCE
    if same and age <= RECENT_DAYS:
        confidence = Confidence.HIGH
    elif (near and age <= RECENT_DAYS) or same:
        confidence = Confidence.MEDIUM
    else:
        confidence = Confidence.LOW
    evidence = EvidenceRef(
        EvidenceKind.ATHLETE_PROFILE,
        profile.athlete_id,
        field=f"runningExperience.{name}",
        note=f"{_clock(result.time_seconds)} on {result.on.isoformat()}",
    )
    return CurrentState(
        seconds=result.time_seconds * ratio**RIEGEL_EXPONENT,
        source_distance_m=distance,
        source_seconds=result.time_seconds,
        source_date=result.on,
        confidence=confidence,
        evidence=evidence,
    )


def assess_feasibility(
    goal: Goal,
    profile: AthleteProfile,
    availability: Availability | None,
    *,
    today: date,
) -> FeasibilityAssessment:
    weeks = max((goal.target_date - today).days, 0) / 7
    hours = (
        None
        if availability is None or availability.recurring is None
        else availability.recurring.maximum_hours_per_week
    )
    reasons: list[FeasibilityReason] = []
    current = None
    if goal.sport is not Sport.RUNNING:
        reasons.append(FeasibilityReason.SPORT_NOT_SUPPORTED)
    elif goal.distance_meters is None:
        reasons.append(FeasibilityReason.NO_DISTANCE)
    else:
        current = current_state(profile, goal.distance_meters, today=today)
        if current is None:
            reasons.append(FeasibilityReason.NO_COMPARABLE_RESULT)
    if goal.target_time_seconds is None:
        reasons.append(FeasibilityReason.NO_TARGET_TIME)
    gap = None
    per_week = percent = None
    if current is not None and goal.target_time_seconds is not None:
        gap = current.seconds - goal.target_time_seconds  # seconds to gain; <= 0: none
        if gap > 0:
            per_week = gap / weeks if weeks > 0 else math.inf
            percent = per_week / current.seconds * 100 if weeks > 0 else math.inf
        else:
            per_week = percent = 0.0
    confidence = Confidence.LOW if current is None else current.confidence
    if confidence is Confidence.LOW and current is not None:
        reasons.append(FeasibilityReason.LOW_CONFIDENCE)
    if gap is None or confidence is Confidence.LOW or percent is None:
        classification = Classification.INSUFFICIENT_DATA
    elif percent <= LIKELY_RATE * 100:
        classification = Classification.LIKELY
    elif percent <= POSSIBLE_RATE * 100:
        classification = Classification.POSSIBLE
    else:
        classification = Classification.UNLIKELY
    if (
        hours is not None
        and hours < MIN_HOURS
        and classification is Classification.LIKELY
        and gap is not None
        and gap > 0
    ):
        classification = Classification.POSSIBLE
        reasons.append(FeasibilityReason.FEW_HOURS)
    realistic = minimum = None
    if current is not None and goal.target_time_seconds is not None:
        improved = current.seconds * (1 - LIKELY_RATE * weeks)
        realistic = max(improved, goal.target_time_seconds)
        minimum = current.seconds
    return FeasibilityAssessment(
        goal_id=goal.goal_id,
        assessed_on=today,
        classification=classification,
        confidence=confidence,
        reasons=tuple(reasons),
        current=current,
        gap_seconds=gap,
        weeks_available=weeks,
        hours_per_week=hours,
        required_seconds_per_week=per_week,
        required_percent_per_week=percent,
        desired_seconds=goal.target_time_seconds,
        realistic_seconds=realistic,
        minimum_seconds=minimum,
    )
