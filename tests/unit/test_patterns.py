"""Pattern detection, rules 1.0.0 (insights spec 2 and 3; FR-INS-001 to FR-INS-003).
Synthetic values only."""

from __future__ import annotations

import itertools
import math
import re
from dataclasses import replace
from datetime import date, timedelta
from typing import Any
from uuid import UUID

import pytest

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.evidence import EvidenceKind, EvidenceRef
from coach.domain.common.quality import Confidence, UncertaintyReason
from coach.domain.common.sport import Sport
from coach.domain.common.time import DateRange
from coach.domain.insights import Association, Insight, Pattern, Relationship
from coach.domain.plans.execution import ExecutionStatus
from coach.domain.recovery.assessment import Reason, RecoveryAssessment, RecoveryStatus
from coach.domain.recovery.checkin import DailyCheckIn
from coach.domain.strength.vocabulary import InterferenceLevel
from coach.metrics.catalog import DAY_LOADS, SRPE_LOAD_TOTAL, TRAINING_MONOTONY, TRAINING_STRAIN
from coach.metrics.derived import (
    DerivedMetricValue,
    MetricInput,
    MetricSubject,
    SubjectKind,
    derive,
    derive_total,
)
from coach.metrics.trends import Point, Trend
from coach.planning import patterns
from coach.planning.interference import InterferenceAssessment, Rule
from coach.planning.patterns import PlannedSession, SleepPair, WeekLoad, ZoneSession
from samples import ATHLETE, NOW

IDS = itertools.count(5000)
UNTIL = date(2026, 10, 25)
WEEK = MetricSubject(SubjectKind.WEEK, "2026-W42")
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def ref(kind: EvidenceKind = EvidenceKind.ACTIVITY) -> EvidenceRef:
    return EvidenceRef(kind, UUID(int=next(IDS)))


def week_value(loads: list[float]) -> tuple[DerivedMetricValue, DerivedMetricValue]:
    inputs = {
        spec.name: MetricInput(value, ref(EvidenceKind.DERIVED_METRIC), Confidence.HIGH)
        for spec, value in zip(DAY_LOADS, loads, strict=True)
    }
    made = [
        derive(
            definition,
            inputs,
            value_id=UUID(int=next(IDS)),
            athlete_id=ATHLETE,
            subject=WEEK,
            computed_at=NOW,
        )
        for definition in (TRAINING_MONOTONY, TRAINING_STRAIN)
    ]
    return made[0], made[1]


def week_total(week: str, *contributions: float | None) -> WeekLoad:
    subject = MetricSubject(SubjectKind.WEEK, week)
    total = derive_total(
        SRPE_LOAD_TOTAL,
        [MetricInput(value, ref(), Confidence.MEDIUM) for value in contributions],
        value_id=UUID(int=next(IDS)),
        athlete_id=ATHLETE,
        subject=subject,
        computed_at=NOW,
    )
    return WeekLoad(week, subject.period(), total)


def no_activity(week: str) -> WeekLoad:
    return WeekLoad(week, MetricSubject(SubjectKind.WEEK, week).period(), None)


# --------------------------------------------------------------------------- load


@pytest.mark.req("FR-INS-002", "FR-INS-001")
def test_monotony_above_two_is_a_finding_stated_from_its_evidence() -> None:
    monotony, strain = week_value([500, 500, 500, 500, 500, 500, 0])
    finding = patterns.monotony(monotony, strain, WEEK.period())
    assert finding is not None
    assert finding.kind is Pattern.MONOTONY
    assert finding.evidence == (
        EvidenceRef(EvidenceKind.DERIVED_METRIC, monotony.value_id),
        EvidenceRef(EvidenceKind.DERIVED_METRIC, strain.value_id),
    )
    assert f"{monotony.value:.2f}" in finding.observation
    assert "500 500 500 500 500 500 0 AU" in finding.observation
    assert dict(finding.versions) == {
        "any.training_monotony": "1.0.0",
        "any.training_strain": "1.0.0",
    }
    varied = week_value([0, 450, 0, 630, 0, 900, 0])
    assert patterns.monotony(*varied, WEEK.period()) is None


@pytest.mark.req("FR-INS-002", "FR-INS-003")
def test_a_load_spike_against_the_four_weeks_before_is_a_finding() -> None:
    acute = week_total("2026-W43", 900.0, 700.0)
    chronic = [
        week_total("2026-W42", 800.0),
        week_total("2026-W41", 500.0, 500.0),
        week_total("2026-W40", 900.0),
        no_activity("2026-W39"),  # counts 0
    ]
    finding = patterns.aggressive_progression(acute, chronic, history_start=date(2026, 9, 1))
    assert finding is not None
    assert finding.observation.startswith(
        "Session RPE load in 2026-W43 was 1600 AU, 2.4 times the mean of the four weeks before"
    )
    assert "2026-W39 0 AU" in finding.observation
    assert finding.confidence is Confidence.MEDIUM  # a contested index: never HIGH
    assert finding.period == DateRange(date(2026, 9, 21), date(2026, 10, 25))
    assert len(finding.evidence) == 4
    unknown = [week_total("2026-W42", None), *chronic[1:]]
    assert patterns.aggressive_progression(acute, unknown, history_start=date(2026, 9, 1)) is None
    steady = week_total("2026-W43", 900.0)
    assert patterns.aggressive_progression(steady, chronic, history_start=date(2026, 9, 1)) is None
    rested = [no_activity(f"2026-W{week}") for week in (42, 41, 40, 39)]
    assert patterns.aggressive_progression(acute, rested, history_start=date(2026, 9, 1)) is None
    # a new athlete: weeks before the first record are unknown, not rest
    for first in (date(2026, 9, 28), None):
        assert patterns.aggressive_progression(acute, chronic, history_start=first) is None
    # the first record inside the earliest of the four weeks: evaluated
    inside = patterns.aggressive_progression(acute, chronic, history_start=date(2026, 9, 27))
    assert inside is not None


# --------------------------------------------------------------------------- recovery


def assessment(day: date, status: RecoveryStatus, reason: Reason) -> RecoveryAssessment:
    return RecoveryAssessment(
        assessment_id=UUID(int=next(IDS)),
        athlete_id=ATHLETE,
        day=day,
        status=status,
        reasons=(reason,),
        signals=(),
        pain=None,
        confidence=Confidence.MEDIUM,
        uncertainty_reasons=frozenset({UncertaintyReason.MISSING_DATA}),
        assessed_at=NOW,
    )


@pytest.mark.req("FR-INS-002", "FR-REC-003")
def test_recovery_reduced_on_three_of_seven_days_is_a_finding() -> None:
    days = [UNTIL - timedelta(days=offset) for offset in range(7)]
    found = [
        assessment(days[0], RecoveryStatus.REDUCE, Reason.TWO_GROUPS),
        assessment(days[2], RecoveryStatus.REST, Reason.THREE_GROUPS),
        assessment(days[3], RecoveryStatus.NORMAL, Reason.NO_DEVIATION),
        assessment(days[5], RecoveryStatus.REST, Reason.SAFETY_HOLD),  # the hold: not recovery
    ]
    assert patterns.insufficient_recovery(found, UNTIL) is None
    found.append(assessment(days[6], RecoveryStatus.REDUCE, Reason.PERSISTENT_SIGNAL))
    finding = patterns.insufficient_recovery(found, UNTIL)
    assert finding is not None
    assert finding.observation.startswith("Recovery was assessed REDUCE or REST on 3 of the 7 days")
    assert {item.kind for item in finding.evidence} == {EvidenceKind.RECOVERY_ASSESSMENT}
    assert finding.reasons == {UncertaintyReason.MISSING_DATA}


def checkin(day: date, fatigue: int) -> DailyCheckIn:
    return DailyCheckIn(
        checkin_id=UUID(int=next(IDS)),
        athlete_id=ATHLETE,
        recorded_at=NOW,
        training_day=day,
        fatigue=fatigue,
    )


@pytest.mark.req("FR-INS-002", "FR-REC-001")
def test_fatigue_building_over_days_is_a_finding() -> None:
    ratings = [3, 4, 4, 2, 4]  # oldest first; the latest is high
    found = [
        checkin(UNTIL - timedelta(days=4 - index), value) for index, value in enumerate(ratings)
    ]
    finding = patterns.accumulated_fatigue(found, UNTIL)
    assert finding is not None
    assert "on 3 of the last 5 check-ins" in finding.observation
    assert len(finding.evidence) == 5
    found[-1] = checkin(UNTIL, 2)  # the latest is low: not building now
    assert patterns.accumulated_fatigue(found, UNTIL) is None


# --------------------------------------------------------------------------- trends


def points(count: int) -> list[Point]:
    return [
        Point(
            UNTIL - timedelta(days=5 * index),
            3.0 + index,
            UUID(int=index + 1),
            UUID(int=100 + index),
        )
        for index in range(count)
    ]


@pytest.mark.req("FR-INS-002", "FR-RUN-002")
@pytest.mark.parametrize(
    ("trend", "found"),
    [
        (Trend(6, 0.1, 5.6, 9.0, 2.4, Confidence.MEDIUM), True),
        (Trend(6, 0.1, 5.6, 9.0, 1.2, Confidence.LOW), False),  # a LOW trend is not a change
        (Trend(6, 0.01, 0.5, 3.0, 2.4, Confidence.MEDIUM), False),  # below 5 %
        (Trend(6, -0.1, -5.6, -9.0, -2.4, Confidence.MEDIUM), False),  # drift falling: better
    ],
)
def test_a_worsening_trend_of_one_comparable_group_is_a_finding(trend: Trend, found: bool) -> None:
    watch = patterns.TREND_WATCHES[0]
    period = DateRange(UNTIL - timedelta(days=55), UNTIL)
    finding = patterns.worsening(
        watch,
        label="RUNNING ROAD FLAT",
        points=points(6),
        trend=trend,
        unit="%",
        period=period,
        calculation_version="1.0.0",
    )
    assert (finding is not None) == found
    if finding is not None:
        assert finding.kind is Pattern.HR_DRIFT
        assert finding.observation.startswith(
            "run.hr_drift of RUNNING ROAD FLAT sessions changed by +5.60 % (+9.0 %)"
        )
        assert finding.evidence[0] == EvidenceRef(EvidenceKind.DERIVED_METRIC, UUID(int=100))
        assert finding.confidence is Confidence.MEDIUM


# --------------------------------------------------------------------------- interference


def combination(
    rule: Rule, level: InterferenceLevel, *reasons: UncertaintyReason
) -> InterferenceAssessment:
    return InterferenceAssessment(
        rule=rule,
        level=level,
        base_level=level,
        affected_regions=(),
        sessions=(UUID(int=next(IDS)), UUID(int=next(IDS))),
        evidence=(ref(), ref()),
        reasons=frozenset(reasons),
    )


@pytest.mark.req("FR-INS-002", "FR-STR-007")
def test_interference_combinations_are_findings_by_kind() -> None:
    period = DateRange(UNTIL - timedelta(days=6), UNTIL)
    found = patterns.interference(
        [
            combination(Rule.TENNIS_INTERVALS_HEAVY_LEGS, InterferenceLevel.MODERATE),
            combination(Rule.HEAVY_LEGS_BEFORE_KEY_RUN, InterferenceLevel.HIGH),
            combination(Rule.HEAVY_LEGS_BEFORE_INTERVALS, InterferenceLevel.MODERATE),  # below
            combination(
                Rule.CALF_ACHILLES_LOAD, InterferenceLevel.HIGH, UncertaintyReason.MISSING_DATA
            ),
        ],
        period,
    )
    kinds = {item.kind: item for item in found}
    assert set(kinds) == {Pattern.TENNIS_INTERFERENCE, Pattern.STRENGTH_ENDURANCE_INTERFERENCE}
    tennis = kinds[Pattern.TENNIS_INTERFERENCE]
    assert "1 session combinations" in tennis.observation
    assert tennis.confidence is Confidence.MEDIUM
    strength = kinds[Pattern.STRENGTH_ENDURANCE_INTERFERENCE]
    assert "HEAVY_LEGS_BEFORE_KEY_RUN HIGH, CALF_ACHILLES_LOAD HIGH" in strength.observation
    assert strength.confidence is Confidence.LOW  # one combination is uncertain
    assert patterns.interference([], period) == []


# --------------------------------------------------------------------------- intensity


@pytest.mark.req("FR-INS-002", "FR-MET-005")
def test_the_intensity_distribution_flags_too_much_hard_and_too_little_easy() -> None:
    period = DateRange(UNTIL - timedelta(days=13), UNTIL)
    hard = ZoneSession(UUID(int=1), (600.0, 600.0, 600.0, 900.0, 300.0))
    sessions = [hard, hard, hard, ZoneSession(UUID(int=2), (100.0, 100.0))]  # 2 zones: left out
    found = {item.kind: item for item in patterns.intensity(sessions, period)}
    assert set(found) == {Pattern.EXCESSIVE_INTENSITY, Pattern.INSUFFICIENT_EASY}
    observation = found[Pattern.EXCESSIVE_INTENSITY].observation
    assert (
        "40.0% of 2.5 h of heart-rate zone time was in zones 1 and 2 and 40.0% in zones 4 and 5"
        in (observation)
    )
    easy = ZoneSession(UUID(int=3), (1800.0, 1200.0, 300.0, 100.0, 0.0))
    assert patterns.intensity([easy, easy, easy], period) == []
    estimated = ZoneSession(UUID(int=4), hard.seconds_in_zone, estimated=True)
    (first, *_) = patterns.intensity([estimated, hard, hard], period)
    assert (first.confidence, first.reasons) == (
        Confidence.LOW,
        frozenset({UncertaintyReason.ESTIMATED_VALUE}),
    )
    assert patterns.intensity([hard, hard], period) == []  # too few sessions


# --------------------------------------------------------------------------- association


def pair(index: int, sleep: float, efficiency: float) -> SleepPair:
    day = UNTIL - timedelta(days=index)
    return SleepPair(
        day, sleep, ref(EvidenceKind.DAILY_RECOVERY), efficiency, ref(EvidenceKind.DERIVED_METRIC)
    )


@pytest.mark.req("FR-INS-002", "FR-INS-003")
def test_sleep_and_performance_are_an_association_never_a_cause() -> None:
    period = DateRange(UNTIL - timedelta(days=55), UNTIL)
    pairs = [pair(index, 20_000 + 500 * index, 1.40 + 0.01 * index) for index in range(12)]
    finding = patterns.sleep_performance(
        pairs, period, sleep_variable="garmin.sleep_duration", calculation_version="1.0.0"
    )
    assert finding is not None
    assert finding.association == Association(
        ("garmin.sleep_duration", "run.efficiency_hr"), 12, 56
    )
    assert "Spearman rank correlation +1.00" in finding.observation
    assert finding.observation.endswith("An association, not a cause.")
    assert finding.confidence is Confidence.LOW  # fewer than 20 pairs
    lowered = finding.observation.lower()
    for causal in ("because", "caused", "leads to", "due to", "results in"):
        assert causal not in lowered
    assert len(finding.evidence) == 24
    flat = [pair(index, 25_000, 1.5) for index in range(12)]
    assert (
        patterns.sleep_performance(
            flat, period, sleep_variable="garmin.sleep_duration", calculation_version="1.0.0"
        )
        is None
    )
    few = pairs[:9]
    assert (
        patterns.sleep_performance(
            few, period, sleep_variable="garmin.sleep_duration", calculation_version="1.0.0"
        )
        is None
    )


# --------------------------------------------------------------------------- model


def insight(**changes: Any) -> Insight:
    fields: dict[str, Any] = {
        "insight_id": UUID(int=1),
        "athlete_id": ATHLETE,
        "kind": Pattern.MONOTONY,
        "period": WEEK.period(),
        "observation": "Training monotony in 2026-W42 was 2.45.",
        "evidence": (ref(EvidenceKind.DERIVED_METRIC),),
        "confidence": Confidence.HIGH,
        "uncertainty_reasons": frozenset(),
        "generated_at": NOW,
    }
    fields.update(changes)
    return Insight(**fields)


@pytest.mark.req("FR-INS-001", "FR-INS-003")
@pytest.mark.parametrize(
    "changes",
    [
        {"evidence": ()},
        {"observation": "  "},
        {"relationship": Relationship.ASSOCIATION},  # without its variables
        {"association": Association(("a.b", "c.d"), 3, 7)},  # without ASSOCIATION
        {"calculation_versions": (("z.y", "1.0.0"), ("a.b", "1.0.0"))},  # not sorted
        {"interpretation": "x" * 1001},
    ],
)
def test_an_insight_keeps_its_parts_apart_and_states_evidence(changes: dict[str, Any]) -> None:
    insight()
    with pytest.raises(DomainValidationError):
        insight(**changes)


@pytest.mark.req("FR-INS-003")
def test_there_is_no_causal_relationship() -> None:
    assert {item.value for item in Relationship} == {"NONE", "ASSOCIATION"}
    with pytest.raises(DomainValidationError):
        Association(("garmin.sleep_duration",), 10, 56)  # an association names two variables


@pytest.mark.req("FR-INS-001", "FR-AGT-004")
def test_observation_numbers_trace_to_evidence_values() -> None:
    monotony, strain = week_value([500, 500, 500, 500, 500, 500, 0])
    finding = patterns.monotony(monotony, strain, WEEK.period())
    assert finding is not None
    stated = {float(item) for item in NUMBER.findall(finding.observation)}
    assert monotony.value is not None
    assert strain.value is not None
    known = {round(monotony.value, 2), round(strain.value), 500.0, 0.0}
    rule_and_labels = {2.0, 1.0, 0.0, 2026.0, 42.0}  # the threshold, rules 1.0.0, the ISO week
    assert stated <= known | rule_and_labels


@pytest.mark.req("FR-INS-002")
def test_percent_metrics_need_a_change_of_one_point_and_an_exact_fit_is_named() -> None:
    period = DateRange(UNTIL - timedelta(days=55), UNTIL)
    kwargs: dict[str, Any] = {
        "label": "RUNNING ROAD FLAT",
        "points": points(6),
        "unit": "%",
        "period": period,
        "calculation_version": "1.0.0",
    }
    drift = patterns.TREND_WATCHES[0]
    small = Trend(6, 0.015, 0.8, 30.0, 3.0, Confidence.MEDIUM)  # +30 % of a small mean
    assert patterns.worsening(drift, trend=small, **kwargs) is None
    exact = Trend(6, 0.05, 2.8, 40.0, math.inf, Confidence.HIGH)
    finding = patterns.worsening(drift, trend=exact, **kwargs)
    assert finding is not None
    assert finding.observation.endswith("least-squares trend of 6 sessions, an exact linear fit.")


# --------------------------------------------------------------------------- plans (P12)

PLAN = EvidenceRef(EvidenceKind.PLAN_VERSION, UUID(int=0x501))
PLAN_PERIOD = DateRange(UNTIL - timedelta(days=13), UNTIL)


def session(
    offset: int,
    status: ExecutionStatus,
    ratio: float | None = None,
    *,
    rpe: float | None = None,
    target: float | None = None,
) -> PlannedSession:
    done = status not in (ExecutionStatus.SKIPPED, ExecutionStatus.PLANNED)
    return PlannedSession(
        workout_id=UUID(int=0x700 + offset),
        day=UNTIL - timedelta(days=offset),
        status=status,
        duration_ratio=ratio,
        session_rpe=rpe,
        target_rpe=target,
        activity_ids=(UUID(int=0x800 + offset),) if done else (),
    )


@pytest.mark.req("FR-INS-002", "FR-PLN-007")
def test_adherence_counts_what_was_not_done_as_planned() -> None:
    complete = [session(offset, ExecutionStatus.COMPLETE, 1.0) for offset in (1, 3, 5, 7)]
    missed = [
        session(2, ExecutionStatus.SKIPPED),
        session(4, ExecutionStatus.PARTIAL, 0.5),
        session(6, ExecutionStatus.REPLACED),
    ]
    upcoming = [session(-1, ExecutionStatus.PLANNED)]  # not due: not counted
    found = patterns.adherence(complete + missed + upcoming, PLAN_PERIOD, PLAN)
    assert found is not None
    assert found.kind is Pattern.ADHERENCE
    assert found.observation == (
        "3 of the 7 planned workouts due in the 14 days to 2026-10-25 were not done as planned "
        "(1 skipped, 1 partial, 1 replaced): 43 %."
    )
    assert found.evidence[0] == PLAN
    assert (
        EvidenceRef(EvidenceKind.WORKOUT_EXECUTION, UUID(int=0x702), note="SKIPPED")
        in found.evidence
    )
    assert EvidenceRef(EvidenceKind.ACTIVITY, UUID(int=0x804)) in found.evidence
    few = complete * 2 + missed  # 3 of 11: under 30 %
    assert patterns.adherence(few, PLAN_PERIOD, PLAN) is None
    assert patterns.adherence(complete + missed[:2], PLAN_PERIOD, PLAN) is None  # only 2


@pytest.mark.req("FR-INS-002", "FR-PLN-007")
def test_planned_vs_actual_finds_a_systematic_deviation() -> None:
    short = [
        session(offset, ExecutionStatus.PARTIAL, ratio)
        for offset, ratio in ((1, 0.6), (3, 0.7), (5, 0.75))
    ]
    found = patterns.planned_vs_actual(short, PLAN_PERIOD, PLAN)
    assert found is not None
    assert found.observation == (
        "In the 14 days to 2026-10-25, the duration done averaged 68 % of the plan over 3 "
        "workouts (from 60 % to 75 %)."
    )
    harder = [
        session(offset, ExecutionStatus.COMPLETE, 1.0, rpe=6, target=4) for offset in (1, 3, 5)
    ]
    effort = patterns.planned_vs_actual(harder, PLAN_PERIOD, PLAN)
    assert effort is not None
    assert (
        "the session RPE averaged +2.0 above the highest target over 3 workouts"
        in effort.observation
    )
    balanced = [
        session(offset, ExecutionStatus.COMPLETE, 0.95, rpe=4, target=4) for offset in (1, 3, 5)
    ]
    assert patterns.planned_vs_actual(balanced, PLAN_PERIOD, PLAN) is None
    assert patterns.planned_vs_actual(short[:2], PLAN_PERIOD, PLAN) is None  # 2 workouts


@pytest.mark.req("FR-INS-002", "FR-GOL-003")
@pytest.mark.parametrize(
    ("relative", "found"),
    [(1.0, True), (-3.0, True), (2.5, False), (-6.0, False)],  # flat, slight dip; better; worse
)
def test_plateau_is_a_goal_metric_that_stopped_improving(relative: float, found: bool) -> None:
    watch = patterns.GOAL_METRICS[Sport.RUNNING]  # run.efficiency_hr, higher is better
    trend = Trend(
        sessions=8,
        slope_per_day=0.0,
        change=relative / 100,
        relative_change_percent=relative,
        t_value=1.0,
        confidence=Confidence.MEDIUM,
    )
    period = DateRange(UNTIL - timedelta(days=55), UNTIL)
    finding = patterns.plateau(
        watch,
        label="RUNNING ROAD FLAT",
        points=points(8),
        trend=trend,
        unit="m/beat",
        period=period,
        calculation_version="1.0.0",
    )
    assert (finding is not None) == found
    if finding is not None:
        assert finding.kind is Pattern.PLATEAU
        assert "no improvement of 2 % or more" in finding.observation
    few = replace(trend, sessions=5)
    assert (
        patterns.plateau(
            watch,
            label="x",
            points=points(5),
            trend=few,
            unit="m/beat",
            period=period,
            calculation_version="1.0.0",
        )
        is None
    )
    assert Sport.CYCLING not in patterns.GOAL_METRICS  # no goal metric for cycling in 1.1.0
