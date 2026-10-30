"""Pattern detection, rules 1.0.0 (insights spec section 2; FR-INS-002, FR-INS-003).

Pure functions: each detector reads derived values, assessments or check-ins and returns a
``Finding`` (the code-written part of an insight) or ``None``. An observation states numbers
present in its evidence, or computed here from them (a mean, a share, a ratio), and says how
they were computed. Relationships between variables are associations with their sample size
and window, never causes. Every threshold is an initial value under A-18, pending review.

Implemented in P11: MONOTONY, AGGRESSIVE_PROGRESSION, INSUFFICIENT_RECOVERY,
ACCUMULATED_FATIGUE, the worsening trends (HR_DRIFT, PACE_DETERIORATION, POWER_DEGRADATION,
TECHNICAL_EFFICIENCY), the interference patterns, the intensity distribution
(EXCESSIVE_INTENSITY, INSUFFICIENT_EASY) and SLEEP_PERFORMANCE. PLATEAU needs goal-relevant
metrics, ADHERENCE and PLANNED_VS_ACTUAL need plans: P12.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import Final
from uuid import UUID

from coach.domain.common.evidence import EvidenceKind, EvidenceRef
from coach.domain.common.quality import Confidence, UncertaintyReason, lowest_confidence
from coach.domain.common.time import DateRange
from coach.domain.insights import Association, Pattern, versions
from coach.domain.recovery.assessment import (
    WORSE_ABOVE,
    Reason,
    RecoveryAssessment,
    RecoveryStatus,
)
from coach.domain.recovery.checkin import DailyCheckIn
from coach.domain.strength.vocabulary import InterferenceLevel
from coach.metrics.derived import DerivedMetricValue, DerivedStatus
from coach.metrics.subjects import week_key
from coach.metrics.trends import Point, Trend
from coach.planning.interference import InterferenceAssessment, Rule

MONOTONY_ABOVE: Final = 2.0  # practical threshold traced to Foster's work, not his 1998 text
ACWR_FROM: Final = 1.5  # acute:chronic load ratio (Gabbett 2016), a contested index
CHRONIC_WEEKS: Final = 4
RECOVERY_DAYS: Final = 7
RECOVERY_MIN_DAYS: Final = 3
FATIGUE_DAYS: Final = 7
FATIGUE_CHECKINS: Final = 5
FATIGUE_MIN: Final = 3
TREND_WEEKS: Final = 8
TREND_MIN_CHANGE_PERCENT: Final = 5.0
INTERFERENCE_DAYS: Final = 7
INTENSITY_DAYS: Final = 14
INTENSITY_ZONES: Final = 5
INTENSITY_MIN_SESSIONS: Final = 3
INTENSITY_MIN_SECONDS: Final = 7200.0
EASY_SHARE_BELOW: Final = 0.70  # zones 1 and 2 (informed by Seiler and Kjerland 2006)
HARD_SHARE_ABOVE: Final = 0.20  # zones 4 and 5
ASSOCIATION_DAYS: Final = 56
ASSOCIATION_MIN_PAIRS: Final = 10
ASSOCIATION_MIN_RHO: Final = 0.5
ASSOCIATION_MEDIUM_PAIRS: Final = 20
ASSOCIATION_MEDIUM_RHO: Final = 0.6
_LEVELS: Final = tuple(InterferenceLevel)


@dataclass(frozen=True, slots=True)
class Finding:
    """What a detector found: the code-written part of an insight."""

    kind: Pattern
    period: DateRange
    observation: str
    evidence: tuple[EvidenceRef, ...]
    confidence: Confidence
    reasons: frozenset[UncertaintyReason] = frozenset()
    versions: tuple[tuple[str, str], ...] = ()
    association: Association | None = None


def _derived(value: DerivedMetricValue) -> EvidenceRef:
    return EvidenceRef(EvidenceKind.DERIVED_METRIC, value.value_id)


def _capped(confidence: Confidence | None, cap: Confidence) -> Confidence:
    found = lowest_confidence([cap] if confidence is None else [confidence, cap])
    return cap if found is None else found


def _number(value: float) -> str:
    return f"{value:.0f}"


# --------------------------------------------------------------------------- load


def monotony(
    monotony: DerivedMetricValue, strain: DerivedMetricValue, period: DateRange
) -> Finding | None:
    """MONOTONY: an ended week's training monotony above 2.0."""
    value = monotony.value
    if monotony.status is not DerivedStatus.COMPUTED or value is None or value <= MONOTONY_ABOVE:
        return None
    loads = " ".join(_number(record.value) for record in monotony.inputs)
    strain_text = "unknown" if strain.value is None else f"{_number(strain.value)} AU"
    observation = (
        f"Training monotony in {week_key(period.start)} was {value:.2f}, above the 2.0 of "
        f"pattern rules 1.0.0; strain {strain_text}; daily session RPE load Monday to Sunday: "
        f"{loads} AU."
    )
    return Finding(
        Pattern.MONOTONY,
        period,
        observation,
        (_derived(monotony), _derived(strain)),
        monotony.input_confidence or Confidence.LOW,
        monotony.uncertainty_reasons,
        versions(
            [
                (monotony.metric_id, monotony.calculation_version),
                (strain.metric_id, strain.calculation_version),
            ]
        ),
    )


@dataclass(frozen=True, slots=True)
class WeekLoad:
    week: str  # ISO week
    period: DateRange
    total: DerivedMetricValue | None  # the week's any.srpe_load_total; None: no activity


def aggressive_progression(acute: WeekLoad, chronic: Sequence[WeekLoad]) -> Finding | None:
    """AGGRESSIVE_PROGRESSION: an ended week's session RPE load at least 1.5 times the mean
    of the four weeks before it (a week without activity counts 0)."""
    if len(chronic) != CHRONIC_WEEKS or acute.total is None:
        return None
    weeks = [acute, *chronic]
    if any(
        item.total is not None
        and (item.total.status is not DerivedStatus.COMPUTED or item.total.value is None)
        for item in weeks
    ):
        return None  # an unknown load: not evaluated
    loads = [
        0.0 if item.total is None or item.total.value is None else item.total.value
        for item in weeks
    ]
    acute_load, mean = loads[0], math.fsum(loads[1:]) / CHRONIC_WEEKS
    if mean <= 0 or acute_load / mean < ACWR_FROM:
        return None
    before = ", ".join(
        f"{item.week} {_number(load)} AU" for item, load in zip(chronic, loads[1:], strict=True)
    )
    observation = (
        f"Session RPE load in {acute.week} was {_number(acute_load)} AU, "
        f"{acute_load / mean:.1f} times the mean of the four weeks before it ({before}; a week "
        "without activity counts 0 AU)."
    )
    totals = [item.total for item in weeks if item.total is not None]
    return Finding(
        Pattern.AGGRESSIVE_PROGRESSION,
        DateRange(min(item.period.start for item in chronic), acute.period.end),
        observation,
        tuple(_derived(total) for total in totals),
        _capped(
            lowest_confidence(t.input_confidence for t in totals if t.input_confidence),
            Confidence.MEDIUM,
        ),
        frozenset().union(*(total.uncertainty_reasons for total in totals)),
        versions((total.metric_id, total.calculation_version) for total in totals),
    )


# --------------------------------------------------------------------------- recovery


def insufficient_recovery(assessments: Iterable[RecoveryAssessment], until: date) -> Finding | None:
    """INSUFFICIENT_RECOVERY: REDUCE or REST (a safety hold aside) on 3 of the 7 days."""
    period = DateRange(until - timedelta(days=RECOVERY_DAYS - 1), until)
    low = sorted(
        (
            item
            for item in assessments
            if period.start <= item.day <= until
            and item.status in (RecoveryStatus.REDUCE, RecoveryStatus.REST)
            and item.reasons[0] is not Reason.SAFETY_HOLD
        ),
        key=lambda item: item.day,
    )
    if len(low) < RECOVERY_MIN_DAYS:
        return None
    days = ", ".join(f"{item.day} {item.status.value}" for item in low)
    observation = (
        f"Recovery was assessed REDUCE or REST on {len(low)} of the 7 days to {until}: {days}."
    )
    return Finding(
        Pattern.INSUFFICIENT_RECOVERY,
        period,
        observation,
        tuple(EvidenceRef(EvidenceKind.RECOVERY_ASSESSMENT, item.assessment_id) for item in low),
        lowest_confidence(item.confidence for item in low) or Confidence.LOW,
        frozenset().union(*(item.uncertainty_reasons for item in low)),
    )


def accumulated_fatigue(checkins: Iterable[DailyCheckIn], until: date) -> Finding | None:
    """ACCUMULATED_FATIGUE: fatigue 4 or more on 3 of the last 5 check-ins of the 7 days,
    the latest included."""
    period = DateRange(until - timedelta(days=FATIGUE_DAYS - 1), until)
    rated = sorted(
        (
            item
            for item in checkins
            if period.start <= item.training_day <= until and item.fatigue is not None
        ),
        key=lambda item: item.training_day,
    )[-FATIGUE_CHECKINS:]
    high = [item for item in rated if item.fatigue is not None and item.fatigue >= WORSE_ABOVE]
    if not rated or rated[-1] not in high or len(high) < FATIGUE_MIN:
        return None
    days = ", ".join(f"{item.training_day} {item.fatigue}" for item in rated)
    observation = (
        f"Fatigue was rated 4 or more of 5 on {len(high)} of the last {len(rated)} check-ins "
        f"to {until} ({days})."
    )
    return Finding(
        Pattern.ACCUMULATED_FATIGUE,
        period,
        observation,
        tuple(EvidenceRef(EvidenceKind.DAILY_CHECKIN, item.checkin_id) for item in rated),
        Confidence.MEDIUM,  # the athlete's own ratings, without a wearable signal
    )


# --------------------------------------------------------------------------- trends


class Worse(StrEnum):
    RISING = "RISING"
    FALLING = "FALLING"


@dataclass(frozen=True, slots=True)
class TrendWatch:
    kind: Pattern
    metric_id: str
    worse: Worse


TREND_WATCHES: Final = (
    TrendWatch(Pattern.HR_DRIFT, "run.hr_drift", Worse.RISING),
    TrendWatch(Pattern.PACE_DETERIORATION, "run.efficiency_hr", Worse.FALLING),
    TrendWatch(Pattern.POWER_DEGRADATION, "cyc.power_fade", Worse.RISING),
    TrendWatch(Pattern.TECHNICAL_EFFICIENCY, "swim.swolf_avg", Worse.RISING),
)


def worsening(
    watch: TrendWatch,
    *,
    label: str,
    points: Sequence[Point],
    trend: Trend | None,
    unit: str,
    period: DateRange,
    calculation_version: str,
) -> Finding | None:
    """A comparable group's trend (metrics spec 11) worsening by 5 % or more, with at least
    MEDIUM confidence; groups are never combined."""
    if trend is None or trend.confidence is Confidence.LOW:
        return None
    relative = trend.relative_change_percent
    if relative is None or abs(relative) < TREND_MIN_CHANGE_PERCENT:
        return None
    if (trend.change > 0) != (watch.worse is Worse.RISING):
        return None
    observation = (
        f"{watch.metric_id} of {label} sessions changed by {trend.change:+.2f} {unit} "
        f"({relative:+.1f} %) over the {period.days} days to {period.end}: least-squares trend "
        f"of {trend.sessions} sessions, t {trend.t_value:.1f}."
    )
    evidence = tuple(
        EvidenceRef(EvidenceKind.DERIVED_METRIC, point.value_id)
        if point.value_id is not None
        else EvidenceRef(EvidenceKind.ACTIVITY, point.activity_id, field=watch.metric_id)
        for point in points
    )
    return Finding(
        watch.kind,
        period,
        observation,
        evidence,
        trend.confidence,
        versions=versions([(watch.metric_id, calculation_version)]),
    )


# --------------------------------------------------------------------------- interference


def _at_least(level: InterferenceLevel, floor: InterferenceLevel) -> bool:
    return _LEVELS.index(level) >= _LEVELS.index(floor)


def interference(assessments: Sequence[InterferenceAssessment], period: DateRange) -> list[Finding]:
    """STRENGTH_ENDURANCE_INTERFERENCE (HIGH or above) and TENNIS_INTERFERENCE (MODERATE or
    above) among the combinations of the 7 days (strength spec 7)."""
    found = []
    groups = (
        (
            Pattern.TENNIS_INTERFERENCE,
            [
                item
                for item in assessments
                if item.rule is Rule.TENNIS_INTERVALS_HEAVY_LEGS
                and _at_least(item.level, InterferenceLevel.MODERATE)
            ],
        ),
        (
            Pattern.STRENGTH_ENDURANCE_INTERFERENCE,
            [
                item
                for item in assessments
                if item.rule is not Rule.TENNIS_INTERVALS_HEAVY_LEGS
                and _at_least(item.level, InterferenceLevel.HIGH)
            ],
        ),
    )
    for kind, items in groups:
        if not items:
            continue
        listed = ", ".join(f"{item.rule.value} {item.level.value}" for item in items)
        observation = (
            f"{len(items)} session combinations with interference in the 7 days to "
            f"{period.end}: {listed} (interference rules {items[0].rules_version})."
        )
        reasons = frozenset().union(*(item.reasons for item in items))
        evidence = tuple(dict.fromkeys(ref for item in items for ref in item.evidence))
        found.append(
            Finding(
                kind,
                period,
                observation,
                evidence,
                Confidence.LOW if reasons else Confidence.MEDIUM,
                reasons,
            )
        )
    return found


# --------------------------------------------------------------------------- intensity


@dataclass(frozen=True, slots=True)
class ZoneSession:
    activity_id: UUID
    seconds_in_zone: tuple[float, ...]  # Z1 first
    estimated: bool = False  # the zone model is an estimate (age-predicted, ADR-028)


def intensity(sessions: Sequence[ZoneSession], period: DateRange) -> list[Finding]:
    """EXCESSIVE_INTENSITY (zones 4 and 5 above 20 %) and INSUFFICIENT_EASY (zones 1 and 2
    below 70 %) of the heart-rate zone time of the 14 days, five-zone models only."""
    usable = [item for item in sessions if len(item.seconds_in_zone) == INTENSITY_ZONES]
    total = math.fsum(math.fsum(item.seconds_in_zone) for item in usable)
    if len(usable) < INTENSITY_MIN_SESSIONS or total < INTENSITY_MIN_SECONDS:
        return []
    easy = math.fsum(math.fsum(item.seconds_in_zone[:2]) for item in usable) / total
    hard = math.fsum(math.fsum(item.seconds_in_zone[3:]) for item in usable) / total
    hours = total / 3600
    observation = (
        f"In the {period.days} days to {period.end}, {easy:.0%} of {hours:.1f} h of heart-rate "
        f"zone time was in zones 1 and 2 and {hard:.0%} in zones 4 and 5 ({len(usable)} "
        "sessions, five-zone models)."
    )
    estimated = any(item.estimated for item in usable)
    reasons = frozenset({UncertaintyReason.ESTIMATED_VALUE}) if estimated else frozenset()
    evidence = tuple(
        EvidenceRef(EvidenceKind.ACTIVITY, item.activity_id, field="cardiovascular.hrZones")
        for item in usable
    )
    found = []
    for kind, applies in (
        (Pattern.EXCESSIVE_INTENSITY, hard > HARD_SHARE_ABOVE),
        (Pattern.INSUFFICIENT_EASY, easy < EASY_SHARE_BELOW),
    ):
        if applies:
            found.append(
                Finding(
                    kind,
                    period,
                    observation,
                    evidence,
                    Confidence.LOW if estimated else Confidence.MEDIUM,
                    reasons,
                    versions([("any.hr_zone_time", "1.0.0")]),
                )
            )
    return found


# --------------------------------------------------------------------------- association


@dataclass(frozen=True, slots=True)
class SleepPair:
    day: date
    sleep_seconds: float
    sleep: EvidenceRef
    efficiency: float  # run.efficiency_hr of a run of that training day
    run: EvidenceRef


def sleep_performance(
    pairs: Sequence[SleepPair], period: DateRange, *, sleep_variable: str, calculation_version: str
) -> Finding | None:
    """SLEEP_PERFORMANCE: the night's sleep duration and run efficiency, Spearman rank
    correlation of at least 0.5 in absolute value over 10 or more runs of the 56 days. An
    association, never a cause (FR-INS-003)."""
    if len(pairs) < ASSOCIATION_MIN_PAIRS:
        return None
    try:
        rho = statistics.correlation(
            [item.sleep_seconds for item in pairs],
            [item.efficiency for item in pairs],
            method="ranked",
        )
    except statistics.StatisticsError:  # a constant series: no correlation
        return None
    if abs(rho) < ASSOCIATION_MIN_RHO:
        return None
    observation = (
        f"Across {len(pairs)} runs in the {period.days} days to {period.end}, run.efficiency_hr "
        f"and the night's sleep duration were associated: Spearman rank correlation {rho:+.2f}. "
        "An association, not a cause."
    )
    strong = len(pairs) >= ASSOCIATION_MEDIUM_PAIRS and abs(rho) >= ASSOCIATION_MEDIUM_RHO
    return Finding(
        Pattern.SLEEP_PERFORMANCE,
        period,
        observation,
        tuple(ref for item in pairs for ref in (item.sleep, item.run)),
        Confidence.MEDIUM if strong else Confidence.LOW,
        versions=versions([("run.efficiency_hr", calculation_version)]),
        association=Association((sleep_variable, "run.efficiency_hr"), len(pairs), period.days),
    )
