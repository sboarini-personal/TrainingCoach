"""Analytics reads: period totals, the week report and recomputation (metrics spec 9.3 to 9.5).

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
from datetime import UTC, datetime, time, timedelta
from typing import Final
from uuid import UUID

from coach.application.activities import ActivityService
from coach.application.activity_metrics import field_evidence
from coach.application.consent import ConsentGate
from coach.application.derived_metrics import DerivedMetricService
from coach.application.errors import NotFoundError
from coach.application.ownership import OwnerScope
from coach.application.ports import ActivityRepository, Batch, OverrideRepository
from coach.domain.activity.activity import Activity
from coach.domain.activity.overrides import TRAINING_DAY_FIELD, EntityType
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.evidence import EvidenceKind, EvidenceRef
from coach.domain.common.quality import UncertaintyReason
from coach.domain.common.sport import Sport
from coach.domain.common.time import DateRange, TrainingDayBasis
from coach.domain.consent.ledger import ConsentType
from coach.metrics.catalog import PERIOD_TOTALS, PeriodTotal
from coach.metrics.derived import (
    DerivedMetricValue,
    DerivedStatus,
    MetricInput,
    MetricSubject,
    SubjectKind,
)
from coach.metrics.registry import MetricDefinition

# A training day is a local date: the activity starts at most 14 h away from it in UTC.
_START_MARGIN: Final = timedelta(days=2)
_BASIS_FIELD: Final = "timing.trainingDayBasis"
RECOMPUTE_CHUNK: Final = 50  # activities per unit of work: short write locks (ADR-027)


@dataclass(frozen=True, slots=True)
class TotalReport:
    """A stored period total, with what a partial total still says (metrics spec 9.4)."""

    value: DerivedMetricValue
    known_total: float
    unknown: int

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
        batch: Batch = nullcontext,
    ) -> None:
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
                {
                    item.definition.metric_id: self._total(
                        scope, item, subject.with_dimension(sport.value), group
                    )
                    for item in self._totals
                    if item.applies_to(sport)
                },
            )
            for sport, group in sorted(by_sport.items(), key=lambda entry: entry[0].value)
        )
        cross_sport = (
            {
                item.definition.metric_id: self._total(scope, item, subject, members)
                for item in self._totals
                if item.definition.cross_sport
            }
            if members
            else {}
        )
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

    def _total(
        self,
        scope: OwnerScope,
        item: PeriodTotal,
        subject: MetricSubject,
        members: Sequence[Activity],
    ) -> TotalReport:
        contributions = [self._contribution(scope, item, activity) for activity in members]
        value = self._metrics.compute_total(
            scope, item.definition.metric_id, subject, contributions
        )
        known = [entry.value for entry in contributions if entry.value is not None]
        return TotalReport(value, math.fsum(known), len(contributions) - len(known))

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
