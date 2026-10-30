"""Pattern detection into insights (insights spec; FR-INS-001 to FR-INS-003).

Detection gathers the derived values, assessments and check-ins each pattern reads, through
the services that enforce consent (a pattern whose data has no consent is not evaluated),
runs pattern rules 1.0.0 (``coach.planning.patterns``) and keeps what it finds as insights.
The observation is written in code; interpretation, hypothesis and recommendation stay null
until the AI coach (P13). A finding already stored is not stored again.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import Final

from coach.application.analytics import AnalyticsService
from coach.application.consent import ConsentGate
from coach.application.events import EventName
from coach.application.ownership import OwnerScope
from coach.application.ports import Clock, EventSink, IdFactory, OwnedRepository
from coach.application.recovery import RecoveryService
from coach.application.recovery_assessment import RecoveryAssessmentService
from coach.domain.activity.fields import StrokeType
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.evidence import EvidenceKind, EvidenceRef
from coach.domain.common.quality import UncertaintyReason
from coach.domain.common.time import DateRange
from coach.domain.consent.ledger import ConsentType
from coach.domain.insights import Insight, Pattern, Relationship, readable
from coach.domain.recovery.daily import RecoveryMetric
from coach.metrics.catalog import CATALOG, SRPE_LOAD_TOTAL
from coach.metrics.derived import MetricSubject, SubjectKind
from coach.metrics.subjects import week_key
from coach.planning import patterns
from coach.planning.patterns import Finding, SleepPair, WeekLoad

STROKES: Final = (
    StrokeType.FREESTYLE,
    StrokeType.BACKSTROKE,
    StrokeType.BREASTSTROKE,
    StrokeType.BUTTERFLY,
)
ACTIVITY_PATTERNS: Final = (
    Pattern.MONOTONY,
    Pattern.AGGRESSIVE_PROGRESSION,
    Pattern.HR_DRIFT,
    Pattern.PACE_DETERIORATION,
    Pattern.POWER_DEGRADATION,
    Pattern.TECHNICAL_EFFICIENCY,
    Pattern.STRENGTH_ENDURANCE_INTERFERENCE,
    Pattern.TENNIS_INTERFERENCE,
    Pattern.EXCESSIVE_INTENSITY,
    Pattern.INSUFFICIENT_EASY,
)


class NotEvaluated(StrEnum):
    NO_CONSENT = "NO_CONSENT"
    NEEDS_PLANS = "NEEDS_PLANS"  # workout executions and plans arrive in P12
    NEEDS_GOAL_METRICS = "NEEDS_GOAL_METRICS"  # goal-relevant metrics arrive in P12


@dataclass(frozen=True, slots=True)
class DetectionReport:
    day: date
    insights: tuple[Insight, ...]  # what detection finds now, stored before or now
    new: int
    not_evaluated: tuple[tuple[Pattern, NotEvaluated], ...]


class InsightService:
    def __init__(
        self,
        analytics: AnalyticsService,
        recovery: RecoveryService,
        assessments: RecoveryAssessmentService,
        insights: OwnedRepository[Insight],
        *,
        consents: ConsentGate,
        events: EventSink,
        clock: Clock,
        ids: IdFactory,
    ) -> None:
        self._analytics = analytics
        self._recovery = recovery
        self._assessments = assessments
        self._insights = insights
        self._consents = consents
        self._events = events
        self._clock = clock
        self._ids = ids

    def detect(self, scope: OwnerScope) -> DetectionReport:
        """Run pattern rules 1.0.0 up to today (the athlete's timezone)."""
        today = self._recovery.today(scope)
        granted = self._consents.granted(scope)
        skipped: list[tuple[Pattern, NotEvaluated]] = [
            (Pattern.PLATEAU, NotEvaluated.NEEDS_GOAL_METRICS),
            (Pattern.ADHERENCE, NotEvaluated.NEEDS_PLANS),
            (Pattern.PLANNED_VS_ACTUAL, NotEvaluated.NEEDS_PLANS),
        ]
        found: list[Finding] = []
        if ConsentType.ACTIVITIES in granted:
            found += self._load(scope, today)
            found += self._trends(scope, today)
            found += self._interference(scope, today)
            period = DateRange(today - timedelta(days=patterns.INTENSITY_DAYS - 1), today)
            found += patterns.intensity(self._analytics.zone_sessions(scope, period), period)
        else:
            skipped += [(kind, NotEvaluated.NO_CONSENT) for kind in ACTIVITY_PATTERNS]
        history = self._assessments.history(scope, until=today, days=patterns.RECOVERY_DAYS)
        checkins = self._recovery.checkins(scope, until=today, days=patterns.FATIGUE_DAYS)
        for finding in (
            patterns.insufficient_recovery(history, today),
            patterns.accumulated_fatigue(checkins, today),
        ):
            if finding is not None:
                found.append(finding)
        if {ConsentType.ACTIVITIES, ConsentType.SLEEP} <= granted:
            association = self._sleep_performance(scope, today)
            if association is not None:
                found.append(association)
        else:
            skipped.append((Pattern.SLEEP_PERFORMANCE, NotEvaluated.NO_CONSENT))
        insights, new = self._keep(scope, found)
        return DetectionReport(today, insights, new, tuple(skipped))

    def recent(self, scope: OwnerScope, *, until: date, days: int) -> Sequence[Insight]:
        """Stored insights whose days reach into the period and whose data may be read with
        the consents granted now (a revocation hides them at once), newest first."""
        start = until - timedelta(days=days - 1)
        granted = self._consents.granted(scope)
        found = [
            item
            for item in self._insights.list_all(scope)
            if item.period.end >= start
            and item.period.start <= until
            and readable(item.kind, granted)
        ]
        return sorted(found, key=lambda item: (item.generated_at, item.kind.value), reverse=True)

    # ------------------------------------------------------------------ gathering

    def _load(self, scope: OwnerScope, today: date) -> list[Finding]:
        last = week_key(today - timedelta(days=today.isoweekday()))  # the last ended week
        found = []
        monotony = self._analytics.monotony(scope, last, today=today)
        if monotony.monotony is not None and monotony.strain is not None:
            finding = patterns.monotony(monotony.monotony, monotony.strain, monotony.period)
            if finding is not None:
                found.append(finding)
        weeks = []
        start = MetricSubject(SubjectKind.WEEK, last).period().start
        for back in range(patterns.CHRONIC_WEEKS + 1):
            key = week_key(start - timedelta(weeks=back))
            report = self._analytics.week(scope, key)
            total = report.cross_sport.get(SRPE_LOAD_TOTAL.metric_id)
            weeks.append(WeekLoad(key, report.period, None if total is None else total.value))
        progression = patterns.aggressive_progression(
            weeks[0], weeks[1:], history_start=self._analytics.first_training_day(scope)
        )
        if progression is not None:
            found.append(progression)
        return found

    def _trends(self, scope: OwnerScope, today: date) -> list[Finding]:
        found = []
        for watch in patterns.TREND_WATCHES:
            definition = CATALOG.get(watch.metric_id)
            strokes: tuple[StrokeType | None, ...] = (None,)
            if definition.dimension is not None:
                strokes = STROKES
            for stroke in strokes:
                report = self._analytics.trend(
                    scope, watch.metric_id, until=today, weeks=patterns.TREND_WEEKS, stroke=stroke
                )
                for group in report.groups:
                    finding = patterns.worsening(
                        watch,
                        label=group.label,
                        points=group.points,
                        trend=group.trend,
                        unit=report.unit,
                        period=report.period,
                        calculation_version=definition.calculation_version,
                    )
                    if finding is not None:
                        found.append(finding)
        return found

    def _interference(self, scope: OwnerScope, today: date) -> list[Finding]:
        try:
            report = self._analytics.interference(
                scope, until=today, days=patterns.INTERFERENCE_DAYS
            )
        except DomainValidationError:  # strength sessions are not available here
            return []
        return patterns.interference(report.assessments, report.period)

    def _sleep_performance(self, scope: OwnerScope, today: date) -> Finding | None:
        weeks = patterns.ASSOCIATION_DAYS // 7
        report = self._analytics.trend(scope, "run.efficiency_hr", until=today, weeks=weeks)
        nights = self._recovery.daily(scope, until=today, days=patterns.ASSOCIATION_DAYS)
        pairs = []
        providers = set()
        for group in report.groups:
            for point in group.points:
                sleep = nights.get(point.day, {}).get(RecoveryMetric.SLEEP_DURATION)
                if sleep is None or point.value_id is None:
                    continue
                providers.add(sleep.provider)
                pairs.append(
                    SleepPair(
                        point.day,
                        sleep.value,
                        EvidenceRef(EvidenceKind.DAILY_RECOVERY, sleep.observation_id),
                        point.value,
                        EvidenceRef(EvidenceKind.DERIVED_METRIC, point.value_id),
                    )
                )
        if len(providers) != 1:
            return None  # no pairs, or sleep from providers that are never merged
        definition = CATALOG.get("run.efficiency_hr")
        finding = patterns.sleep_performance(
            pairs,
            report.period,
            sleep_variable=f"{providers.pop()}.sleep_duration",
            calculation_version=definition.calculation_version,
        )
        if finding is not None and len(report.groups) > 1:
            reasons = finding.reasons | {UncertaintyReason.NON_COMPARABLE_SESSION}
            finding = Finding(
                finding.kind,
                finding.period,
                finding.observation,
                finding.evidence,
                finding.confidence,
                reasons,
                finding.versions,
                finding.association,
            )
        return finding

    # ------------------------------------------------------------------ storage

    def _keep(self, scope: OwnerScope, found: Sequence[Finding]) -> tuple[tuple[Insight, ...], int]:
        stored = list(self._insights.list_all(scope))
        kept, new = [], 0
        for finding in found:
            candidate = Insight(
                insight_id=self._ids(),
                athlete_id=scope.athlete_id,
                kind=finding.kind,
                period=finding.period,
                observation=finding.observation,
                evidence=finding.evidence,
                confidence=finding.confidence,
                uncertainty_reasons=finding.reasons,
                generated_at=self._clock(),
                calculation_versions=finding.versions,
                relationship=Relationship.NONE
                if finding.association is None
                else Relationship.ASSOCIATION,
                association=finding.association,
            )
            known = next((item for item in stored if item.same_finding(candidate)), None)
            if known is None:
                known = self._insights.add(scope, candidate)
                stored.append(known)
                new += 1
                self._events.emit(EventName.INSIGHT_DETECTED, {"kind": known.kind.value})
            kept.append(known)
        return tuple(kept), new
