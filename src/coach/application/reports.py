"""The data part of the post-activity, weekly and monthly reports (reporting spec;
FR-RPT-001 to FR-RPT-003; P11).

Every section is built in code from the read models that enforce consent (analytics, the
recovery assessment, insights), and every number carries its evidence. A section the data
cannot fill yet is listed as not available: plans and adherence (P12), goal progress and the
forecast (P12), the narrative and recommendations (the AI coach, P13), or a missing consent.
Reports are read models: nothing is stored.
"""

from __future__ import annotations

import calendar
import itertools
import math
from collections.abc import Callable, Sequence
from datetime import date, timedelta
from typing import Final
from uuid import UUID

from coach.application.activities import ActivityService
from coach.application.activity_metrics import field_evidence
from coach.application.analytics import AnalyticsService, TotalReport
from coach.application.coaching import CoachingConditions
from coach.application.consent import ConsentGate
from coach.application.insights import InsightService
from coach.application.ownership import OwnerScope
from coach.application.ports import Clock
from coach.application.recovery_assessment import RecoveryAssessmentService
from coach.domain.activity.activity import Activity
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.evidence import EvidenceKind, EvidenceRef
from coach.domain.common.quality import UncertaintyReason
from coach.domain.common.sport import Sport
from coach.domain.common.time import DateRange
from coach.domain.consent.ledger import ConsentType
from coach.domain.insights import Insight, Pattern
from coach.domain.recovery.assessment import RecoveryAssessment, RecoveryStatus
from coach.domain.reports import (
    ReportData,
    ReportItem,
    ReportKind,
    Section,
    Unavailable,
    lowest,
    union,
)
from coach.metrics.catalog import SRPE_LOAD_TOTAL
from coach.metrics.derived import DerivedMetricValue, DerivedStatus, MetricSubject, SubjectKind
from coach.metrics.subjects import week_key

ELAPSED_TOTAL: Final = "any.duration_elapsed"
TENNIS_LOAD: Final = "ten.neuromuscular_load"
FIVE_ZONES: Final = 5
EVOLUTION_WEEKS: Final = 5  # the weeks to a month's end
EVOLUTION_METRICS: Final = ("run.efficiency_hr", "run.hr_drift", "cyc.power_fade")
TREND_PATTERNS: Final = frozenset(
    {
        Pattern.HR_DRIFT,
        Pattern.PACE_DETERIORATION,
        Pattern.POWER_DEGRADATION,
        Pattern.TECHNICAL_EFFICIENCY,
        Pattern.MONOTONY,
        Pattern.AGGRESSIVE_PROGRESSION,
    }
)
SUMMARY_FIELDS: Final = (
    ("timing.elapsedSeconds", "elapsed time", "s"),
    ("timing.movingSeconds", "moving time", "s"),
    ("distance.meters", "distance", "m"),
    ("elevation.gainMeters", "elevation gain", "m"),
    ("cardiovascular.averageHr", "average heart rate", "bpm"),
    ("subjective.rpe", "session RPE", "CR-10"),
)


def month_period(month: str) -> DateRange:
    """The days of a calendar month written YYYY-MM."""
    try:
        year, number = (int(part) for part in month.split("-"))
        last = calendar.monthrange(year, number)[1]
        return DateRange(date(year, number, 1), date(year, number, last))
    except ValueError:
        raise DomainValidationError(f"invalid month {month!r}; for example 2026-10") from None


def _derived(label: str, value: DerivedMetricValue) -> ReportItem:
    number = value.value if value.status is DerivedStatus.COMPUTED else None
    return ReportItem(
        label=label,
        value=number,
        unit=value.unit,
        evidence=(EvidenceRef(EvidenceKind.DERIVED_METRIC, value.value_id),),
        confidence=value.input_confidence,
        reasons=value.uncertainty_reasons,
    )


def _total(label: str, report: TotalReport) -> ReportItem:
    return _derived(label, report.value)


def _activities(label: str, views: Sequence[Activity]) -> ReportItem:
    return ReportItem(
        label=label,
        value=float(len(views)),
        evidence=tuple(EvidenceRef(EvidenceKind.ACTIVITY, view.activity_id) for view in views),
        confidence=lowest(view.quality.confidence for view in views),
    )


def _insight(item: Insight) -> ReportItem:
    return ReportItem(
        label=item.kind.value,
        value=item.observation,
        evidence=(EvidenceRef(EvidenceKind.INSIGHT, item.insight_id),),
        confidence=item.confidence,
        reasons=item.uncertainty_reasons,
    )


def _status(item: RecoveryAssessment) -> ReportItem:
    return ReportItem(
        label=f"recovery {item.day}",
        value=f"{item.status.value} ({item.reasons[0].value})",
        evidence=(EvidenceRef(EvidenceKind.RECOVERY_ASSESSMENT, item.assessment_id),),
        confidence=item.confidence,
        reasons=item.uncertainty_reasons,
    )


def _sum(label: str, values: Sequence[DerivedMetricValue], unit: str) -> ReportItem:
    """A sum of stored totals (one at least); unknown when one of them is."""
    known = all(item.status is DerivedStatus.COMPUTED and item.value is not None for item in values)
    return ReportItem(
        label=label,
        value=math.fsum(item.value or 0.0 for item in values) if known else None,
        unit=unit,
        evidence=tuple(EvidenceRef(EvidenceKind.DERIVED_METRIC, item.value_id) for item in values),
        confidence=lowest(item.input_confidence for item in values),
        reasons=union([item.uncertainty_reasons for item in values]),
    )


class ReportService:
    def __init__(
        self,
        *,
        activities: ActivityService,
        analytics: AnalyticsService,
        assessments: RecoveryAssessmentService,
        insights: InsightService,
        conditions: CoachingConditions,
        consents: ConsentGate,
        today: Callable[[OwnerScope], date],
        clock: Clock,
    ) -> None:
        self._activities = activities
        self._analytics = analytics
        self._assessments = assessments
        self._insights = insights
        self._conditions = conditions
        self._consents = consents
        self._today = today
        self._clock = clock

    # ------------------------------------------------------------------ post-activity

    def post_activity(self, scope: OwnerScope, activity_id: UUID) -> ReportData:
        """Summary, Planned vs Actual, What Went Well, Deviations, Insights, Recovery, Plan
        Impact (FR-RPT-001)."""
        self._consents.require(scope, ConsentType.ACTIVITIES)
        view = self._activities.effective(scope, activity_id).activity
        day = view.training_day
        summary = [
            ReportItem(label="sport", value=view.sport.value),
            ReportItem(label="training day", value=day.isoformat()),
        ]
        for path, label, unit in SUMMARY_FIELDS:
            number = view.number(path)
            if number is None:
                continue
            summary.append(
                ReportItem(
                    label=label,
                    value=number,
                    unit=unit,
                    evidence=(field_evidence(view, path),),
                    confidence=view.quality.confidence,
                )
            )
        for value in self._analytics.activity_metrics(scope, activity_id):
            dimension = value.subject.dimension
            label = value.metric_id if dimension is None else f"{value.metric_id} {dimension}"
            summary.append(_derived(label, value))
        insights = [
            _insight(item)
            for item in self._insights.recent(scope, until=day, days=1)
            if item.period.start <= day <= item.period.end
        ]
        recovery = [_status(item) for item in self._assessments.history(scope, until=day, days=1)]
        sections = (
            Section("Summary", tuple(summary)),
            Section.not_available("Planned vs Actual", Unavailable.NEEDS_PLANS),
            Section.not_available("What Went Well", Unavailable.NARRATIVE),
            Section.not_available("Deviations", Unavailable.NEEDS_PLANS),
            self._section("Insights", insights),
            self._section("Recovery", recovery),
            Section.not_available("Plan Impact", Unavailable.NEEDS_PLANS),
        )
        return ReportData(
            kind=ReportKind.POST_ACTIVITY,
            athlete_id=scope.athlete_id,
            period=DateRange(day, day),
            subject=str(activity_id),
            sections=sections,
            generated_at=self._clock(),
        )

    # ------------------------------------------------------------------ weekly

    def weekly(self, scope: OwnerScope, week: str) -> ReportData:
        """Adherence, volume by sport, intensity distribution, strength by muscle group,
        tennis load, recovery, trends, alerts, progression toward the A goal, changes for the
        following week (FR-RPT-002)."""
        subject = MetricSubject(SubjectKind.WEEK, week)
        period = subject.period()
        activity_data = ConsentType.ACTIVITIES in self._consents.granted(scope)
        recovery = [
            _status(item)
            for item in self._assessments.history(scope, until=period.end, days=period.days)
        ]
        insights = self._insights.recent(scope, until=period.end, days=period.days)
        trends = [_insight(item) for item in insights if item.kind in TREND_PATTERNS]
        alerts = [_insight(item) for item in insights if item.kind not in TREND_PATTERNS]
        alerts = self._conditions_now(scope) + alerts
        if activity_data:
            trends = self._monotony(scope, subject.key) + trends
            measured: tuple[Section, ...] = (
                self._volume(scope, subject),
                self._intensity(scope, period),
                self._strength(scope, subject.key),
                self._tennis(scope, subject, period),
            )
        else:
            measured = tuple(
                Section.not_available(name, Unavailable.NO_CONSENT)
                for name in (
                    "Volume by sport",
                    "Intensity distribution",
                    "Strength by muscle group",
                    "Tennis load",
                )
            )
        sections = (
            Section.not_available("Adherence", Unavailable.NEEDS_PLANS),
            *measured,
            self._section("Recovery", recovery),
            self._section("Trends", trends),
            Section("Alerts", tuple(alerts)),
            Section.not_available("Progression toward the A goal", Unavailable.NEEDS_GOAL_METRICS),
            Section.not_available("Changes for the following week", Unavailable.NEEDS_PLANS),
        )
        return ReportData(
            kind=ReportKind.WEEKLY,
            athlete_id=scope.athlete_id,
            period=period,
            subject=subject.key,
            sections=sections,
            generated_at=self._clock(),
        )

    # ------------------------------------------------------------------ monthly

    def monthly(self, scope: OwnerScope, month: str) -> ReportData:
        """Performance evolution, comparison with the previous period, consistency, training
        load, recovery, strength progression, risks, cautious forecast, recommendations
        (FR-RPT-003). The forecast is not available before goals and plans exist; it would
        never promise an outcome (FR-GOL-005)."""
        period = month_period(month)
        activity_data = ConsentType.ACTIVITIES in self._consents.granted(scope)
        assessments = self._assessments.history(scope, until=period.end, days=period.days)
        recovery = []
        for status in RecoveryStatus:
            matching = [item for item in assessments if item.status is status]
            if matching:
                recovery.append(
                    ReportItem(
                        label=f"days {status.value}",
                        value=float(len(matching)),
                        evidence=tuple(
                            EvidenceRef(EvidenceKind.RECOVERY_ASSESSMENT, item.assessment_id)
                            for item in matching
                        ),
                        confidence=lowest(item.confidence for item in matching),
                    )
                )
        risks = [
            _insight(item)
            for item in self._insights.recent(scope, until=period.end, days=period.days)
        ]
        if activity_data:
            weeks = self._weeks(period)
            measured: tuple[Section, ...] = (
                self._evolution(scope, period),
                self._comparison(scope, period),
                self._consistency(scope, period),
                self._load(scope, weeks),
            )
            strength = self._strength_weeks(scope, weeks)
        else:
            measured = tuple(
                Section.not_available(name, Unavailable.NO_CONSENT)
                for name in (
                    "Performance evolution",
                    "Comparison with the previous period",
                    "Consistency",
                    "Training load",
                )
            )
            strength = Section.not_available("Strength progression", Unavailable.NO_CONSENT)
        sections = (
            *measured,
            self._section("Recovery", recovery),
            strength,
            self._section("Risks", risks),
            Section.not_available("Cautious forecast", Unavailable.NEEDS_GOAL_METRICS),
            Section.not_available("Recommendations", Unavailable.NARRATIVE),
        )
        return ReportData(
            kind=ReportKind.MONTHLY,
            athlete_id=scope.athlete_id,
            period=period,
            subject=month,
            sections=sections,
            generated_at=self._clock(),
        )

    # ------------------------------------------------------------------ sections

    @staticmethod
    def _section(name: str, items: Sequence[ReportItem]) -> Section:
        if not items:
            return Section.not_available(name, Unavailable.NO_DATA)
        return Section(name, tuple(items))

    def _conditions_now(self, scope: OwnerScope) -> list[ReportItem]:
        return [
            ReportItem(label="condition", value=condition.value)
            for condition in sorted(self._conditions.current(scope).conditions)
        ]

    def _volume(self, scope: OwnerScope, subject: MetricSubject) -> Section:
        report = self._analytics.period(scope, subject)
        if not report.activities:
            return Section.not_available("Volume by sport", Unavailable.NO_DATA)
        views = self._analytics.activities(scope, report.period)
        items = []
        for sport in report.sports:
            group = [view for view in views if view.sport is sport.sport]
            items.append(_activities(f"{sport.sport.value} activities", group))
            items.extend(
                _total(f"{sport.sport.value} {metric_id}", total)
                for metric_id, total in sport.totals.items()
            )
        items.extend(
            _total(f"all sports {metric_id}", total)
            for metric_id, total in report.cross_sport.items()
        )
        return Section("Volume by sport", tuple(items))

    def _intensity(self, scope: OwnerScope, period: DateRange) -> Section:
        sessions = [
            item
            for item in self._analytics.zone_sessions(scope, period)
            if len(item.seconds_in_zone) == FIVE_ZONES
        ]
        if not sessions:
            return Section.not_available("Intensity distribution", Unavailable.NO_DATA)
        evidence = tuple(
            EvidenceRef(EvidenceKind.ACTIVITY, item.activity_id, field="cardiovascular.hrZones")
            for item in sessions
        )
        reasons = (
            frozenset({UncertaintyReason.ESTIMATED_VALUE})
            if any(item.estimated for item in sessions)
            else frozenset()
        )
        items = [
            ReportItem(
                label=f"Z{zone + 1} time (five-zone models)",
                value=math.fsum(item.seconds_in_zone[zone] for item in sessions),
                unit="s",
                evidence=evidence,
                reasons=reasons,
            )
            for zone in range(FIVE_ZONES)
        ]
        return Section("Intensity distribution", tuple(items))

    def _strength(self, scope: OwnerScope, week: str) -> Section:
        try:
            found = self._analytics.strength_week(scope, week)
        except DomainValidationError:  # strength sessions are not available here
            return Section.not_available("Strength by muscle group", Unavailable.NO_DATA)
        if not found.activities:
            return Section.not_available("Strength by muscle group", Unavailable.NO_DATA)
        items = [
            _total(f"hard sets {muscle.muscle.value}", muscle.hard_sets) for muscle in found.muscles
        ]
        if found.volume_load is not None:
            items.append(_total("volume load", found.volume_load))
        return Section("Strength by muscle group", tuple(items))

    def _tennis(self, scope: OwnerScope, subject: MetricSubject, period: DateRange) -> Section:
        views = [
            view for view in self._analytics.activities(scope, period) if view.sport is Sport.TENNIS
        ]
        if not views:
            return Section.not_available("Tennis load", Unavailable.NO_DATA)
        items = [_activities("tennis sessions", views)]
        for view in views:
            for value in self._analytics.activity_metrics(scope, view.activity_id):
                if value.metric_id == TENNIS_LOAD:
                    items.append(_derived(f"{TENNIS_LOAD} {view.training_day}", value))
        report = self._analytics.period(scope, subject)
        tennis = next((item for item in report.sports if item.sport is Sport.TENNIS), None)
        if tennis is not None and SRPE_LOAD_TOTAL.metric_id in tennis.totals:
            items.append(
                _total("tennis session RPE load", tennis.totals[SRPE_LOAD_TOTAL.metric_id])
            )
        return Section("Tennis load", tuple(items))

    def _monotony(self, scope: OwnerScope, week: str) -> list[ReportItem]:
        found = self._analytics.monotony(scope, week, today=self._today(scope))
        items = []
        for value in (found.monotony, found.strain):
            if value is not None:
                items.append(_derived(value.metric_id, value))
        return items

    @staticmethod
    def _weeks(period: DateRange) -> list[str]:
        first = period.start - timedelta(days=period.start.weekday())
        return [
            week_key(first + timedelta(weeks=index))
            for index in range((period.end - first).days // 7 + 1)
        ]

    def _evolution(self, scope: OwnerScope, period: DateRange) -> Section:
        items = []
        for metric_id in EVOLUTION_METRICS:
            report = self._analytics.trend(
                scope, metric_id, until=period.end, weeks=EVOLUTION_WEEKS
            )
            for group in report.groups:
                trend = group.trend
                if trend is None:
                    continue
                items.append(
                    ReportItem(
                        label=f"{metric_id} {group.label} change over {report.period.days} days",
                        value=trend.change,
                        unit=report.unit,
                        evidence=tuple(
                            EvidenceRef(EvidenceKind.DERIVED_METRIC, point.value_id)
                            for point in group.points
                            if point.value_id is not None
                        ),
                        confidence=trend.confidence,
                    )
                )
        return self._section("Performance evolution", items)

    def _days_total(
        self, scope: OwnerScope, period: DateRange, metric_id: str
    ) -> list[DerivedMetricValue]:
        found = []
        for offset in range(period.days):
            day = period.start + timedelta(days=offset)
            report = self._analytics.period(scope, MetricSubject(SubjectKind.DAY, day.isoformat()))
            total = report.cross_sport.get(metric_id)
            if total is not None:
                found.append(total.value)
        return found

    def _comparison(self, scope: OwnerScope, period: DateRange) -> Section:
        before_end = period.start - timedelta(days=1)
        before = month_period(f"{before_end.year}-{before_end.month:02d}")
        items = []
        for label, span in (("this month", period), ("previous month", before)):
            views = self._analytics.activities(scope, span)
            if views:
                items.append(_activities(f"{label} activities", views))
            for metric_id, unit in ((SRPE_LOAD_TOTAL.metric_id, "AU"), (ELAPSED_TOTAL, "s")):
                values = self._days_total(scope, span, metric_id)
                if values:
                    items.append(_sum(f"{label} {metric_id}", values, unit))
        return self._section("Comparison with the previous period", items)

    def _consistency(self, scope: OwnerScope, period: DateRange) -> Section:
        views = self._analytics.activities(scope, period)
        if not views:
            return Section.not_available("Consistency", Unavailable.NO_DATA)
        days = sorted({view.training_day for view in views})
        gaps = [(later - earlier).days - 1 for earlier, later in itertools.pairwise(days)]
        evidence = tuple(EvidenceRef(EvidenceKind.ACTIVITY, view.activity_id) for view in views)
        confidence = lowest(view.quality.confidence for view in views)
        items = [
            _activities("sessions", views),
            ReportItem(
                label="training days",
                value=float(len(days)),
                evidence=evidence,
                confidence=confidence,
            ),
        ]
        if gaps:  # a gap needs two training days: never an invented 0
            items.append(
                ReportItem(
                    label="longest gap between training days",
                    value=float(max(gaps)),
                    unit="days",
                    evidence=evidence,
                    confidence=confidence,
                )
            )
        return Section("Consistency", tuple(items))

    def _load(self, scope: OwnerScope, weeks: Sequence[str]) -> Section:
        items = []
        for week in weeks:
            report = self._analytics.week(scope, week)
            total = report.cross_sport.get(SRPE_LOAD_TOTAL.metric_id)
            if total is not None:
                items.append(_total(f"{week} session RPE load", total))
            items.extend(self._monotony(scope, week))
        return self._section("Training load", items)

    def _strength_weeks(self, scope: OwnerScope, weeks: Sequence[str]) -> Section:
        items = []
        for week in weeks:
            try:
                found = self._analytics.strength_week(scope, week)
            except DomainValidationError:  # strength sessions are not available here
                break
            if found.volume_load is not None:
                items.append(_total(f"{week} volume load", found.volume_load))
        return self._section("Strength progression", items)
