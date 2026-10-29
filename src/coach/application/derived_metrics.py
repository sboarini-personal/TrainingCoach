"""Compute and store derived metrics (FR-MET-001, FR-MET-004).

Recomputing with the same calculation version and input snapshot returns the stored value;
any change supersedes it, keeping the previous value for lineage.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from coach.application.ownership import OwnerScope
from coach.application.ports import Clock, DerivedValueRepository, IdFactory
from coach.metrics.derived import (
    DerivedMetricValue,
    DerivedStatus,
    MetricInput,
    MetricSubject,
    derive,
    derive_total,
    not_applicable,
)
from coach.metrics.registry import MetricRegistry


class DerivedMetricService:
    def __init__(
        self,
        registry: MetricRegistry,
        values: DerivedValueRepository,
        *,
        clock: Clock,
        ids: IdFactory,
    ) -> None:
        self._registry = registry
        self._values = values
        self._clock = clock
        self._ids = ids

    def compute(
        self,
        scope: OwnerScope,
        metric_id: str,
        subject: MetricSubject,
        inputs: Mapping[str, MetricInput],
    ) -> DerivedMetricValue:
        candidate = derive(
            self._registry.get(metric_id),
            inputs,
            value_id=self._ids(),
            athlete_id=scope.athlete_id,
            subject=subject,
            computed_at=self._clock(),
        )
        return self._store(scope, candidate)

    def compute_total(
        self,
        scope: OwnerScope,
        metric_id: str,
        subject: MetricSubject,
        contributions: Sequence[MetricInput],
    ) -> DerivedMetricValue:
        """A period total (metrics spec 9.3), stored like any derived value."""
        candidate = derive_total(
            self._registry.get(metric_id),
            contributions,
            value_id=self._ids(),
            athlete_id=scope.athlete_id,
            subject=subject,
            computed_at=self._clock(),
        )
        return self._store(scope, candidate)

    def retire(
        self, scope: OwnerScope, metric_id: str, subject: MetricSubject
    ) -> DerivedMetricValue | None:
        """Supersede a current value with NOT_APPLICABLE (metrics spec section 4, P8)."""
        current = self._values.current(scope, metric_id, subject)
        if current is None or current.status is DerivedStatus.NOT_APPLICABLE:
            return current
        candidate = not_applicable(
            self._registry.get(metric_id),
            value_id=self._ids(),
            athlete_id=scope.athlete_id,
            subject=subject,
            computed_at=self._clock(),
        )
        return self._values.supersede(scope, current.value_id, candidate)

    def current(
        self, scope: OwnerScope, metric_id: str, subject: MetricSubject
    ) -> DerivedMetricValue | None:
        """The current value; a retired (NOT_APPLICABLE) one reads as no value."""
        current = self._values.current(scope, metric_id, subject)
        if current is None or current.status is DerivedStatus.NOT_APPLICABLE:
            return None
        return current

    def _store(self, scope: OwnerScope, candidate: DerivedMetricValue) -> DerivedMetricValue:
        metric_id, subject = candidate.metric_id, candidate.subject
        current = self._values.current(scope, metric_id, subject)
        if current is None:
            return self._values.add(scope, candidate)
        if current.same_computation(candidate):
            return current
        return self._values.supersede(scope, current.value_id, candidate)
