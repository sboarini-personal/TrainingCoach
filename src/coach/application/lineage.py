"""Lineage from a derived value or an insight down to its sources (NFR-TRC-001, AT-LIN-001).

Derived values, activities (down to their linked RAW sources) and RAW sources resolve; from
P11 so do insights (down to their evidence), recovery assessments (down to their signals'
evidence), daily recovery observations (down to their minimised RAW) and check-ins (the
athlete's own declaration, a leaf). Kinds without a repository here stay unresolved. A
reference that does not resolve for this athlete is reported as unresolved, never as another
athlete's data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from uuid import UUID

from coach.application.errors import NotFoundError
from coach.application.ownership import OwnerScope
from coach.application.ports import (
    ActivityRepository,
    DerivedValueRepository,
    OwnedRepository,
    RawSourceRepository,
)
from coach.domain.activity.activity import Activity
from coach.domain.common.evidence import EvidenceKind, EvidenceRef
from coach.domain.insights import Insight
from coach.domain.raw.source import RawSource
from coach.domain.recovery.assessment import RecoveryAssessment
from coach.domain.recovery.checkin import DailyCheckIn
from coach.domain.recovery.daily import RecoveryObservation
from coach.metrics.derived import DerivedMetricValue

MAX_DEPTH: Final = 32


class LineageError(RuntimeError):
    """The lineage graph is cyclic or deeper than allowed."""


@dataclass(frozen=True, slots=True)
class LineageNode:
    reference: EvidenceRef
    resolved: (
        DerivedMetricValue
        | Activity
        | RawSource
        | Insight
        | RecoveryAssessment
        | RecoveryObservation
        | DailyCheckIn
        | None
    )
    children: tuple[LineageNode, ...] = ()


class LineageService:
    def __init__(
        self,
        values: DerivedValueRepository,
        sources: RawSourceRepository,
        activities: ActivityRepository,
        *,
        insights: OwnedRepository[Insight] | None = None,
        assessments: OwnedRepository[RecoveryAssessment] | None = None,
        observations: OwnedRepository[RecoveryObservation] | None = None,
        checkins: OwnedRepository[DailyCheckIn] | None = None,
    ) -> None:
        self._values = values
        self._sources = sources
        self._activities = activities
        self._insights = insights
        self._assessments = assessments
        self._observations = observations
        self._checkins = checkins

    def trace(self, scope: OwnerScope, value_id: UUID) -> LineageNode:
        """Lineage of an owned derived value; raises NotFoundError for anything else."""
        root = self._values.get(scope, value_id)
        reference = EvidenceRef(EvidenceKind.DERIVED_METRIC, value_id)
        return self._expand(scope, reference, root, depth=0, seen=frozenset({value_id}))

    def trace_insight(self, scope: OwnerScope, insight_id: UUID) -> LineageNode:
        """Lineage of an owned insight; raises NotFoundError for anything else."""
        if self._insights is None:
            raise NotFoundError
        insight = self._insights.get(scope, insight_id)
        reference = EvidenceRef(EvidenceKind.INSIGHT, insight_id)
        return self._branch(
            scope, reference, insight, insight.evidence, depth=0, seen=frozenset({insight_id})
        )

    def raw_sources(self, node: LineageNode) -> list[RawSource]:
        found = [node.resolved] if isinstance(node.resolved, RawSource) else []
        for child in node.children:
            found.extend(source for source in self.raw_sources(child) if source not in found)
        return found

    def _expand(
        self,
        scope: OwnerScope,
        reference: EvidenceRef,
        value: DerivedMetricValue,
        *,
        depth: int,
        seen: frozenset[UUID],
    ) -> LineageNode:
        if depth >= MAX_DEPTH:
            raise LineageError("lineage is deeper than allowed")
        children = []
        for record in value.inputs:
            child = record.evidence
            if child.ref_id in seen:
                raise LineageError("lineage contains a cycle")
            children.append(
                self._resolve(scope, child, depth=depth + 1, seen=seen | {child.ref_id})
            )
        return LineageNode(reference, value, tuple(children))

    def _branch(
        self,
        scope: OwnerScope,
        reference: EvidenceRef,
        resolved: Insight | RecoveryAssessment,
        evidence: tuple[EvidenceRef, ...],
        *,
        depth: int,
        seen: frozenset[UUID],
    ) -> LineageNode:
        if depth >= MAX_DEPTH:
            raise LineageError("lineage is deeper than allowed")
        children = []
        for child in evidence:
            if child.ref_id in seen:
                raise LineageError("lineage contains a cycle")
            children.append(
                self._resolve(scope, child, depth=depth + 1, seen=seen | {child.ref_id})
            )
        return LineageNode(reference, resolved, tuple(children))

    def _resolve(
        self, scope: OwnerScope, reference: EvidenceRef, *, depth: int, seen: frozenset[UUID]
    ) -> LineageNode:
        try:
            match reference.kind:
                case EvidenceKind.DERIVED_METRIC:
                    value = self._values.get(scope, reference.ref_id)
                    return self._expand(scope, reference, value, depth=depth, seen=seen)
                case EvidenceKind.RAW_SOURCE:
                    return LineageNode(reference, self._sources.get(scope, reference.ref_id))
                case EvidenceKind.ACTIVITY:
                    return self._activity(scope, reference)
                case EvidenceKind.INSIGHT if self._insights is not None:
                    insight = self._insights.get(scope, reference.ref_id)
                    return self._branch(
                        scope, reference, insight, insight.evidence, depth=depth, seen=seen
                    )
                case EvidenceKind.RECOVERY_ASSESSMENT if self._assessments is not None:
                    found = self._assessments.get(scope, reference.ref_id)
                    return self._branch(
                        scope, reference, found, found.evidence, depth=depth, seen=seen
                    )
                case EvidenceKind.DAILY_RECOVERY if self._observations is not None:
                    observation = self._observations.get(scope, reference.ref_id)
                    source_ref = EvidenceRef(EvidenceKind.RAW_SOURCE, observation.raw_source_id)
                    child = self._resolve(scope, source_ref, depth=depth + 1, seen=seen)
                    return LineageNode(reference, observation, (child,))
                case EvidenceKind.DAILY_CHECKIN if self._checkins is not None:
                    return LineageNode(reference, self._checkins.get(scope, reference.ref_id))
                case _:
                    return LineageNode(reference, None)
        except NotFoundError:
            return LineageNode(reference, None)

    def _activity(self, scope: OwnerScope, reference: EvidenceRef) -> LineageNode:
        activity = self._activities.get(scope, reference.ref_id)
        children = []
        for link in activity.sources:
            source_ref = EvidenceRef(EvidenceKind.RAW_SOURCE, link.source_id)
            try:
                children.append(LineageNode(source_ref, self._sources.get(scope, link.source_id)))
            except NotFoundError:
                children.append(LineageNode(source_ref, None))
        return LineageNode(reference, activity, tuple(children))
