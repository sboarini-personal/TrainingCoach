"""Compute and store derived metrics (FR-MET-001, FR-MET-004).

Recomputing with the same calculation version and input snapshot returns the stored value;
any change supersedes it, keeping the previous value for lineage.
"""

from __future__ import annotations

from collections.abc import Mapping

from coach.application.ownership import OwnerScope
from coach.application.ports import Clock, DerivedValueRepository, IdFactory
from coach.metrics.derived import DerivedMetricValue, MetricInput, MetricSubject, derive
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
        current = self._values.current(scope, metric_id, subject)
        if current is None:
            return self._values.add(scope, candidate)
        if current.same_computation(candidate):
            return current
        return self._values.supersede(scope, current.value_id, candidate)
