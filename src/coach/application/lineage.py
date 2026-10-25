"""Lineage from a derived value down to its sources (NFR-TRC-001).

Derived values, activities (down to their linked RAW sources) and RAW sources resolve; other
evidence kinds (check-ins, ...) stay unresolved until their repositories exist. A reference
that does not resolve for this athlete is reported as unresolved, never as another athlete's
data.
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
    RawSourceRepository,
)
from coach.domain.activity.activity import Activity
from coach.domain.common.evidence import EvidenceKind, EvidenceRef
from coach.domain.raw.source import RawSource
from coach.metrics.derived import DerivedMetricValue

MAX_DEPTH: Final = 32


class LineageError(RuntimeError):
    """The lineage graph is cyclic or deeper than allowed."""


@dataclass(frozen=True, slots=True)
class LineageNode:
    reference: EvidenceRef
    resolved: DerivedMetricValue | Activity | RawSource | None
    children: tuple[LineageNode, ...] = ()


class LineageService:
    def __init__(
        self,
        values: DerivedValueRepository,
        sources: RawSourceRepository,
        activities: ActivityRepository,
    ) -> None:
        self._values = values
        self._sources = sources
        self._activities = activities

    def trace(self, scope: OwnerScope, value_id: UUID) -> LineageNode:
        """Lineage of an owned derived value; raises NotFoundError for anything else."""
        root = self._values.get(scope, value_id)
        reference = EvidenceRef(EvidenceKind.DERIVED_METRIC, value_id)
        return self._expand(scope, reference, root, depth=0, seen=frozenset({value_id}))

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

    def _resolve(
        self, scope: OwnerScope, reference: EvidenceRef, *, depth: int, seen: frozenset[UUID]
    ) -> LineageNode:
        try:
            if reference.kind is EvidenceKind.DERIVED_METRIC:
                value = self._values.get(scope, reference.ref_id)
                return self._expand(scope, reference, value, depth=depth, seen=seen)
            if reference.kind is EvidenceKind.RAW_SOURCE:
                return LineageNode(reference, self._sources.get(scope, reference.ref_id))
            if reference.kind is EvidenceKind.ACTIVITY:
                return self._activity(scope, reference)
        except NotFoundError:
            return LineageNode(reference, None)
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
