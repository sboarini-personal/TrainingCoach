"""Analytics reads: period totals, the week report, recomputation (metrics spec 9.3 to 9.5)
the strength week: hard sets per muscle group and volume load (strength spec 8), and the
progression decisions of each exercise from its execution history (strength spec 6).

Every read requires the ACTIVITIES consent and is owner-scoped. Period totals are materialised
when requested from the EFFECTIVE views of the period's ACTIVE activities: an unchanged
snapshot returns the stored value, a changed one supersedes it, so a reader never sees a stale
total. Distances and elevation are summed within one sport only (FR-MET-006); external and
internal load stay separate (FR-MET-005).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Final
from uuid import UUID

from coach.application.activities import ActivityService
from coach.application.activity_metrics import field_evidence
from coach.application.consent import ConsentGate
from coach.application.derived_metrics import DerivedMetricService
from coach.application.errors import NotFoundError
from coach.application.ownership import OwnerScope
from coach.application.ports import ActivityRepository, Batch, OverrideRepository
from coach.application.strength import StrengthService
from coach.domain.activity.activity import Activity
from coach.domain.activity.fields import Cadence, CadenceUnit, Pain, StrokeType
from coach.domain.activity.overrides import TRAINING_DAY_FIELD, EntityType
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.evidence import EvidenceKind, EvidenceRef
from coach.domain.common.quality import UncertaintyReason
from coach.domain.common.sport import Sport
from coach.domain.common.time import DateRange, TrainingDayBasis
from coach.domain.consent.ledger import ConsentType
from coach.domain.strength.progression import (
    EXPOSURES,
    Exposure,
    PainReport,
    ProgressionDecision,
    decide,
)
from coach.domain.strength.session import StrengthSetResult
from coach.domain.strength.vocabulary import MuscleGroup
from coach.metrics.catalog import (
    CATALOG,
    PERIOD_TOTALS,
    STR_VOLUME_LOAD_TOTAL,
    STR_WEEKLY_SETS_BY_MUSCLE,
    PeriodTotal,
)
from coach.metrics.derived import (
    DerivedMetricValue,
    DerivedStatus,
    MetricInput,
    MetricSubject,
    SubjectKind,
)
from coach.metrics.registry import MetricDefinition
from coach.metrics.strength import is_hard, muscle_shares
from coach.metrics.subjects import DimensionKind
from coach.metrics.trends import Point, Trend, group_of
from coach.metrics.trends import trend as trend_of

# A training day is a local date: the activity starts at most 14 h away from it in UTC.
_START_MARGIN: Final = timedelta(days=2)
_BASIS_FIELD: Final = "timing.trainingDayBasis"
RECOMPUTE_CHUNK: Final = 50  # activities per unit of work: short write locks (ADR-027)
CADENCE_FIELD: Final = "performance.cadence"  # the average running cadence (metrics spec 11)
PAIN_FIELD: Final = "subjective.pain"
MAX_TREND_WEEKS: Final = 104


@dataclass(frozen=True, slots=True)
class TrendGroup:
    label: str
    points: tuple[Point, ...]
    trend: Trend | None  # None: fewer than 5 sessions


@dataclass(frozen=True, slots=True)
class TrendReport:
    """Comparable groups of one metric over a window, never combined (metrics spec 11)."""

    metric_id: str
    unit: str
    period: DateRange
    groups: tuple[TrendGroup, ...]
    without_value: int

    @property
    def across_groups(self) -> frozenset[UncertaintyReason]:
        """What a statement comparing the groups must carry."""
        if len(self.groups) > 1:
            return frozenset({UncertaintyReason.NON_COMPARABLE_SESSION})
        return frozenset()


@dataclass(frozen=True, slots=True)
class TotalReport:
    """A stored period total, with what a partial total still says (metrics spec 9.4)."""

    value: DerivedMetricValue
    known_total: float
    unknown: int
    left_out: int = 0  # activities without a value of a qualifying-only total (P9)

    @property
    def metric_id(self) -> str:
        return self.value.metric_id

    @property
    def unit(self) -> str:
        return self.value.unit

    @property
    def complete(self) -> bool:
        return self.value.status is DerivedStatus.COMPUTED


@dataclass(frozen=True, slots=True)
class SportTotals:
    sport: Sport
    activities: int
    totals: Mapping[str, TotalReport]


@dataclass(frozen=True, slots=True)
class PeriodReport:
    subject: MetricSubject
    period: DateRange
    activities: int
    sports: tuple[SportTotals, ...]
    cross_sport: Mapping[str, TotalReport]


@dataclass(frozen=True, slots=True)
class MuscleSets:
    muscle: MuscleGroup
    hard_sets: TotalReport  # unknown sets: their effort (RIR, RPE) is not known


@dataclass(frozen=True, slots=True)
class StrengthWeek:
    """Hard sets per muscle group and volume load of an ISO week (strength spec 8)."""

    subject: MetricSubject
    period: DateRange
    activities: int  # ACTIVE strength activities of the week
    sets: int  # executed sets
    unattributed: int  # sets of an exercise the watch did not name: no muscle group
    muscles: tuple[MuscleSets, ...]  # only the muscle groups the week's sets worked
    volume_load: TotalReport | None


class AnalyticsService:
    def __init__(
        self,
        *,
        activities: ActivityService,
        store: ActivityRepository,
        overrides: OverrideRepository,
        metrics: DerivedMetricService,
        consents: ConsentGate,
        totals: Sequence[PeriodTotal] = PERIOD_TOTALS,
        strength: StrengthService | None = None,
        batch: Batch = nullcontext,
    ) -> None:
        self._strength = strength
        self._activities = activities
        self._store = store
        self._overrides = overrides
        self._metrics = metrics
        self._consents = consents
        self._totals = tuple(totals)
        self._batch = batch

    # ------------------------------------------------------------------ periods

    def week(self, scope: OwnerScope, week: str) -> PeriodReport:
        return self.period(scope, MetricSubject(SubjectKind.WEEK, week))

    def period(self, scope: OwnerScope, subject: MetricSubject) -> PeriodReport:
        """Totals of a DAY or WEEK, per sport and across sports (metrics spec 9.3, 9.4)."""
        self._consents.require(scope, ConsentType.ACTIVITIES)
        if subject.dimension is not None:
            raise DomainValidationError("a period report covers every sport")
        with self._batch():
            return self._period(scope, subject)

    def _period(self, scope: OwnerScope, subject: MetricSubject) -> PeriodReport:
        period = subject.period()
        members = self._members(scope, period)
        by_sport: dict[Sport, list[Activity]] = {}
        for activity in members:
            by_sport.setdefault(activity.sport, []).append(activity)
        sports = tuple(
            SportTotals(
                sport,
                len(group),
                self._totals_of(scope, subject.with_dimension(sport.value), sport, group),
            )
            for sport, group in sorted(by_sport.items(), key=lambda entry: entry[0].value)
        )
        cross_sport: dict[str, TotalReport] = {}
        if members:
            for item in self._totals:
                if item.definition.cross_sport:
                    report = self._total(scope, item, subject, members)
                    if report is not None:
                        cross_sport[item.definition.metric_id] = report
        self._retire_stale(scope, subject, sports, cross_sport)
        return PeriodReport(subject, period, len(members), sports, cross_sport)

    def _retire_stale(
        self,
        scope: OwnerScope,
        subject: MetricSubject,
        sports: Sequence[SportTotals],
        cross_sport: Mapping[str, TotalReport],
    ) -> None:
        """Retire the stored totals of this period that no longer have contributions."""
        produced = {
            (metric_id, item.sport.value) for item in sports for metric_id in item.totals
        } | {(metric_id, None) for metric_id in cross_sport}
        for item in self._totals:
            metric_id = item.definition.metric_id
            dimensions: list[str | None] = [
                sport.value for sport in Sport if item.applies_to(sport)
            ]
            if item.definition.cross_sport:
                dimensions.append(None)
            for dimension in dimensions:
                if (metric_id, dimension) not in produced:
                    self._metrics.retire(scope, metric_id, subject.with_dimension(dimension))

    def _members(self, scope: OwnerScope, period: DateRange) -> list[Activity]:
        """ACTIVE activities (EFFECTIVE view) whose training day falls in the period."""
        first = datetime.combine(period.start, time(), UTC) - _START_MARGIN
        last = datetime.combine(period.end, time(), UTC) + _START_MARGIN
        candidates = {
            item.activity_id: item for item in self._store.starting_between(scope, first, last)
        }
        for activity_id in self._overrides.entities_with_field(
            scope, EntityType.ACTIVITY, TRAINING_DAY_FIELD
        ):
            if activity_id not in candidates:
                try:
                    candidates[activity_id] = self._store.get(scope, activity_id)
                except NotFoundError:  # pragma: no cover - an override names a stored activity
                    continue
        members = []
        for stored in candidates.values():
            if not stored.counts_toward_totals:
                continue
            view = self._activities.effective(scope, stored.activity_id).activity
            if period.start <= view.training_day <= period.end:
                members.append(view)
        return sorted(members, key=lambda item: (item.start_time_utc, str(item.activity_id)))

    def _totals_of(
        self,
        scope: OwnerScope,
        subject: MetricSubject,
        sport: Sport,
        group: Sequence[Activity],
    ) -> dict[str, TotalReport]:
        totals = {}
        for item in self._totals:
            if not item.applies_to(sport):
                continue
            report = self._total(scope, item, subject, group)
            if report is not None:
                totals[item.definition.metric_id] = report
        return totals

    def _total(
        self,
        scope: OwnerScope,
        item: PeriodTotal,
        subject: MetricSubject,
        members: Sequence[Activity],
    ) -> TotalReport | None:
        """A period total; ``None`` when a qualifying-only total has no activity with a value
        (it is then retired with the period's stale totals)."""
        left_out = 0
        if item.qualifying_only and item.activity_metric is not None:
            metric_id = item.activity_metric.metric_id
            qualifying = [
                activity
                for activity in members
                if self._metrics.current(
                    scope, metric_id, MetricSubject.activity(activity.activity_id)
                )
                is not None
            ]
            left_out = len(members) - len(qualifying)
            if not qualifying:
                return None
            members = qualifying
        contributions = [self._contribution(scope, item, activity) for activity in members]
        value = self._metrics.compute_total(
            scope, item.definition.metric_id, subject, contributions
        )
        known = [entry.value for entry in contributions if entry.value is not None]
        return TotalReport(value, math.fsum(known), len(contributions) - len(known), left_out)

    def _contribution(
        self, scope: OwnerScope, item: PeriodTotal, activity: Activity
    ) -> MetricInput:
        reasons: set[UncertaintyReason] = set()
        if activity.values.get(_BASIS_FIELD) is TrainingDayBasis.ATHLETE_TIMEZONE_ESTIMATED:
            reasons.add(UncertaintyReason.ESTIMATED_VALUE)
        if item.field is not None:
            return _field_contribution(item, item.field, activity, reasons)
        if item.activity_metric is not None:
            return self._metric_contribution(scope, item.activity_metric, activity, reasons)
        raise DomainValidationError("a period total adds up a field or a per-activity metric")

    def _metric_contribution(
        self,
        scope: OwnerScope,
        metric: MetricDefinition,
        activity: Activity,
        reasons: set[UncertaintyReason],
    ) -> MetricInput:
        current = self._metrics.current(
            scope, metric.metric_id, MetricSubject.activity(activity.activity_id)
        )
        if current is None:
            evidence = EvidenceRef(EvidenceKind.ACTIVITY, activity.activity_id)
            return MetricInput(None, evidence, activity.quality.confidence, frozenset(reasons))
        return MetricInput(
            current.value,
            EvidenceRef(EvidenceKind.DERIVED_METRIC, current.value_id),
            current.input_confidence or activity.quality.confidence,
            frozenset(reasons | current.uncertainty_reasons),
        )

    # ------------------------------------------------------------------ strength

    def strength_week(self, scope: OwnerScope, week: str) -> StrengthWeek:
        """Hard sets per muscle group (str.weekly_sets_by_muscle) and volume load of a week,
        from the executed sets of its ACTIVE strength activities (strength spec 8)."""
        self._consents.require(scope, ConsentType.ACTIVITIES)
        if self._strength is None:
            raise DomainValidationError("strength sessions are not available here")
        subject = MetricSubject(SubjectKind.WEEK, week)
        with self._batch():
            members = [
                view
                for view in self._members(scope, subject.period())
                if view.sport is Sport.STRENGTH
            ]
            by_muscle: dict[MuscleGroup, list[MetricInput]] = {}
            sets = unattributed = 0
            for view in members:
                reasons: set[UncertaintyReason] = set()
                if view.values.get(_BASIS_FIELD) is TrainingDayBasis.ATHLETE_TIMEZONE_ESTIMATED:
                    reasons.add(UncertaintyReason.ESTIMATED_VALUE)
                for item in self._strength.of_view(scope, view).sets:
                    sets += 1
                    shares = muscle_shares(item, self._strength.catalogue)
                    if not shares:
                        unattributed += 1
                        continue
                    hard = is_hard(item)
                    evidence = EvidenceRef(
                        EvidenceKind.STRENGTH_SET_RESULT,
                        view.activity_id,
                        note=f"{item.exercise_id}#{item.set_number}",
                    )
                    for muscle, share in shares.items():
                        value = None if hard is None else (share if hard else 0.0)
                        by_muscle.setdefault(muscle, []).append(
                            MetricInput(
                                value, evidence, view.quality.confidence, frozenset(reasons)
                            )
                        )
            muscles = []
            metric_id = STR_WEEKLY_SETS_BY_MUSCLE.metric_id
            for muscle in MuscleGroup:
                muscle_subject = subject.with_dimension(muscle.value)
                contributions = by_muscle.get(muscle)
                if not contributions:
                    self._metrics.retire(scope, metric_id, muscle_subject)
                    continue
                stored = self._metrics.compute_total(
                    scope, metric_id, muscle_subject, contributions
                )
                known = [entry.value for entry in contributions if entry.value is not None]
                report = TotalReport(stored, math.fsum(known), len(contributions) - len(known))
                muscles.append(MuscleSets(muscle, report))
            volume = self._strength_volume(scope, subject, members)
        return StrengthWeek(
            subject, subject.period(), len(members), sets, unattributed, tuple(muscles), volume
        )

    def progression(
        self,
        scope: OwnerScope,
        *,
        until: date,
        weeks: int = 12,
        exercise_id: str | None = None,
    ) -> list[ProgressionDecision]:
        """A decision per exercise done in the weeks ending ``until`` (or for one exercise),
        from its executed sets and the pain reported since its last two exposures (strength
        spec 6). Decisions are read models: nothing is stored."""
        self._consents.require(scope, ConsentType.ACTIVITIES)
        if self._strength is None:
            raise DomainValidationError("strength sessions are not available here")
        if not 1 <= weeks <= MAX_TREND_WEEKS:
            raise DomainValidationError(f"weeks must be 1 to {MAX_TREND_WEEKS}")
        catalogue = self._strength.catalogue
        if exercise_id is not None and exercise_id not in catalogue:
            raise DomainValidationError(f"unknown exercise: {exercise_id}")
        period = DateRange(until - timedelta(days=weeks * 7 - 1), until)
        by_exercise: dict[str, list[Exposure]] = {}
        pains: list[PainReport] = []
        with self._batch():
            for view in self._members(scope, period):
                pain = view.values.get(PAIN_FIELD)
                if isinstance(pain, Pain) and pain.flag:
                    pains.append(PainReport(view.activity_id, view.training_day, pain))
                if view.sport is not Sport.STRENGTH:
                    continue
                grouped: dict[str, list[StrengthSetResult]] = {}
                for item in self._strength.of_view(scope, view).sets:
                    if item.exercise_id is not None:
                        grouped.setdefault(item.exercise_id, []).append(item)
                for found, sets in grouped.items():
                    by_exercise.setdefault(found, []).append(
                        Exposure(view.activity_id, view.training_day, tuple(sets))
                    )
        decisions = []
        for found in [exercise_id] if exercise_id is not None else sorted(by_exercise):
            exposures = by_exercise.get(found, [])
            recent = exposures[-EXPOSURES:]  # members come in time order
            since = recent[0].training_day if recent else period.start
            covered = [report for report in pains if report.training_day >= since]
            decisions.append(decide(catalogue.get(found), exposures, covered))
        return decisions

    def _strength_volume(
        self, scope: OwnerScope, subject: MetricSubject, members: Sequence[Activity]
    ) -> TotalReport | None:
        item = next(
            (total for total in self._totals if total.definition is STR_VOLUME_LOAD_TOTAL), None
        )
        sport_subject = subject.with_dimension(Sport.STRENGTH.value)
        if item is None or not members:
            return None
        report = self._total(scope, item, sport_subject, members)
        if report is None:
            self._metrics.retire(scope, item.definition.metric_id, sport_subject)
        return report

    # ------------------------------------------------------------------ trends

    def trend(
        self,
        scope: OwnerScope,
        metric_id: str,
        *,
        until: date,
        weeks: int,
        stroke: StrokeType | None = None,
    ) -> TrendReport:
        """Comparable groups and their trends over the weeks ending ``until`` (metrics spec 11)."""
        self._consents.require(scope, ConsentType.ACTIVITIES)
        if not 1 <= weeks <= MAX_TREND_WEEKS:
            raise DomainValidationError(f"weeks must be 1 to {MAX_TREND_WEEKS}")
        unit = self._trend_unit(metric_id, stroke)
        period = DateRange(until - timedelta(days=weeks * 7 - 1), until)
        with self._batch():
            members = self._members(scope, period)
        grouped: dict[str, list[Point]] = {}
        without = 0
        for view in members:
            value = self._trend_value(scope, metric_id, view, stroke)
            if value is None:
                without += 1
                continue
            point = Point(view.training_day, value, view.activity_id)
            grouped.setdefault(group_of(view, stroke), []).append(point)
        groups = tuple(
            TrendGroup(label, tuple(points), trend_of(points, weeks * 7))
            for label, points in sorted(grouped.items())
        )
        return TrendReport(metric_id, unit, period, groups, without)

    def _trend_unit(self, metric_id: str, stroke: StrokeType | None) -> str:
        if metric_id == CADENCE_FIELD:
            if stroke is not None:
                raise DomainValidationError("cadence has no stroke")
            return "steps/min"
        if metric_id not in CATALOG:
            raise DomainValidationError(f"unknown metric: {metric_id}")
        definition = CATALOG.get(metric_id)
        if SubjectKind.ACTIVITY not in definition.subject_kinds:
            raise DomainValidationError(f"{metric_id} is not a per-activity metric")
        if definition.dimension is DimensionKind.ZONE:
            raise DomainValidationError(f"{metric_id} is per zone: no trend in V1")
        if (definition.dimension is DimensionKind.STROKE) != (stroke is not None):
            raise DomainValidationError(f"{metric_id} needs a stroke exactly when it is per stroke")
        return definition.unit

    def _trend_value(
        self, scope: OwnerScope, metric_id: str, view: Activity, stroke: StrokeType | None
    ) -> float | None:
        if metric_id == CADENCE_FIELD:
            cadence = view.values.get(CADENCE_FIELD)
            if view.sport is not Sport.RUNNING or not isinstance(cadence, Cadence):
                return None
            return cadence.average if cadence.unit is CadenceUnit.STEPS_PER_MIN else None
        current = self._metrics.current(
            scope, metric_id, MetricSubject.activity(view.activity_id, stroke=stroke)
        )
        if current is None or current.status is not DerivedStatus.COMPUTED:
            return None
        return current.value

    # ------------------------------------------------------------------ activities

    def activity_metrics(self, scope: OwnerScope, activity_id: UUID) -> list[DerivedMetricValue]:
        """Current per-activity values that apply to the activity as it is now."""
        self._consents.require(scope, ConsentType.ACTIVITIES)
        view = self._activities.effective(scope, activity_id).activity
        return self._activities.engine.current(scope, view)

    def recompute(self, scope: OwnerScope) -> int:
        """Recompute the per-activity metrics of every live activity; returns their number."""
        self._consents.require(scope, ConsentType.ACTIVITIES)
        live = [stored.activity_id for stored in self._store.list_all(scope) if stored.is_live]
        for first in range(0, len(live), RECOMPUTE_CHUNK):
            with self._batch():
                for activity_id in live[first : first + RECOMPUTE_CHUNK]:
                    view = self._activities.effective(scope, activity_id).activity
                    self._activities.engine.recompute(scope, view)
        return len(live)


def _field_contribution(
    item: PeriodTotal, field: str, activity: Activity, reasons: set[UncertaintyReason]
) -> MetricInput:
    value = activity.number(field)
    if any(anomaly.field == field for anomaly in activity.quality.anomalies):
        reasons.add(UncertaintyReason.SENSOR_QUALITY)
    (spec,) = item.definition.required_inputs
    if value is not None:
        try:
            spec.check(value)
        except DomainValidationError:
            value = None
            reasons.add(UncertaintyReason.SENSOR_QUALITY)
    return MetricInput(
        value, field_evidence(activity, field), activity.quality.confidence, frozenset(reasons)
    )
