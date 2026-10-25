"""Lineage from a derived value down to its sources (NFR-TRC-001; AT-LIN-001 partial).

Derived values, activities (down to their linked RAW sources) and RAW sources resolve.
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from coach.application.errors import NotFoundError
from coach.application.lineage import LineageService
from coach.application.ownership import OwnerScope
from coach.application.ports import ActivityChange
from coach.domain.activity.observation import SourceKind
from coach.domain.common.evidence import EvidenceKind, EvidenceRef
from coach.domain.common.quality import Confidence
from coach.infrastructure.memory import (
    InMemoryActivityRepository,
    InMemoryDerivedValueRepository,
    InMemoryRawSourceRepository,
)
from coach.metrics.derived import DerivedMetricValue, DerivedStatus, InputRecord, MetricSubject
from samples import (
    ACTIVITY,
    ATHLETE,
    GARMIN_SOURCE,
    OTHER_ATHLETE,
    SHA_B,
    sample_activity,
    sample_derived_value,
    sample_observation,
    sample_raw_source,
)

SCOPE = OwnerScope(ATHLETE)
NOW = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)
RAW_ID = UUID("0193b2a0-0000-7000-8000-000000000202")
WEEK_VALUE_ID = UUID(int=50)


def weekly_load(inputs: tuple[InputRecord, ...]) -> DerivedMetricValue:
    return DerivedMetricValue(
        value_id=WEEK_VALUE_ID,
        athlete_id=ATHLETE,
        metric_id="any.weekly_volume_by_sport",
        calculation_version="1.0.0",
        subject=MetricSubject.week(NOW.date()),
        status=DerivedStatus.COMPUTED,
        value=310,
        unit="AU",
        inputs=inputs,
        missing_inputs=(),
        input_confidence=Confidence.MEDIUM,
        computed_at=NOW,
    )


@pytest.fixture
def stores() -> tuple[LineageService, InMemoryDerivedValueRepository, InMemoryRawSourceRepository]:
    values = InMemoryDerivedValueRepository()
    sources = InMemoryRawSourceRepository()
    return LineageService(values, sources, InMemoryActivityRepository()), values, sources


@pytest.mark.req("NFR-TRC-001")
def test_lineage_walks_derived_values_down_to_raw_sources(
    stores: tuple[LineageService, InMemoryDerivedValueRepository, InMemoryRawSourceRepository],
) -> None:
    lineage, values, sources = stores
    session = values.add(SCOPE, sample_derived_value())
    raw, _ = sources.add(SCOPE, sample_raw_source())
    week = values.add(
        SCOPE,
        weekly_load(
            (
                InputRecord(
                    "session_load",
                    310,
                    EvidenceRef(EvidenceKind.DERIVED_METRIC, session.value_id),
                    Confidence.MEDIUM,
                ),
                InputRecord(
                    "file_bytes",
                    1,
                    EvidenceRef(EvidenceKind.RAW_SOURCE, raw.raw_source_id),
                    Confidence.HIGH,
                ),
            )
        ),
    )
    root = lineage.trace(SCOPE, week.value_id)
    assert root.resolved == week
    derived_child, raw_child = root.children
    assert derived_child.resolved == session
    assert {child.reference.kind for child in derived_child.children} == {EvidenceKind.ACTIVITY}
    assert all(child.resolved is None for child in derived_child.children)
    assert raw_child.resolved == raw
    assert lineage.raw_sources(root) == [raw]


@pytest.mark.req("NFR-TRC-001")
def test_superseded_values_remain_traceable(
    stores: tuple[LineageService, InMemoryDerivedValueRepository, InMemoryRawSourceRepository],
) -> None:
    lineage, values, _ = stores
    first = values.add(SCOPE, sample_derived_value(value_id=UUID(int=1)))
    values.supersede(SCOPE, first.value_id, sample_derived_value(value_id=UUID(int=2), rpe=6))
    traced = lineage.trace(SCOPE, first.value_id).resolved
    assert isinstance(traced, DerivedMetricValue)
    assert traced.superseded_by == UUID(int=2)


@pytest.mark.req("SEC-IDOR-001")
def test_lineage_of_another_athletes_value_is_not_found(
    stores: tuple[LineageService, InMemoryDerivedValueRepository, InMemoryRawSourceRepository],
) -> None:
    lineage, values, _ = stores
    theirs = values.add(
        OwnerScope(OTHER_ATHLETE), sample_derived_value(OTHER_ATHLETE, value_id=UUID(int=9))
    )
    with pytest.raises(NotFoundError):
        lineage.trace(SCOPE, theirs.value_id)


@pytest.mark.req("NFR-TRC-001")
def test_references_to_missing_or_foreign_sources_stay_unresolved(
    stores: tuple[LineageService, InMemoryDerivedValueRepository, InMemoryRawSourceRepository],
) -> None:
    lineage, values, sources = stores
    sources.add(OwnerScope(OTHER_ATHLETE), sample_raw_source(OTHER_ATHLETE, raw_source_id=RAW_ID))
    week = values.add(
        SCOPE,
        weekly_load(
            (InputRecord("x", 1, EvidenceRef(EvidenceKind.RAW_SOURCE, RAW_ID), Confidence.HIGH),)
        ),
    )
    (child,) = lineage.trace(SCOPE, week.value_id).children
    assert child.resolved is None


@pytest.mark.req("NFR-TRC-001", "FR-ACT-002")
def test_activity_evidence_resolves_down_to_its_linked_raw_sources() -> None:
    values = InMemoryDerivedValueRepository()
    sources = InMemoryRawSourceRepository()
    activities = InMemoryActivityRepository()
    lineage = LineageService(values, sources, activities)
    fit, _ = sources.add(SCOPE, sample_raw_source())
    activity = sample_activity(sample_observation(), sample_observation(SourceKind.PROVIDER_RECORD))
    activities.save(SCOPE, [ActivityChange(activity)])
    session = values.add(SCOPE, sample_derived_value())
    root = lineage.trace(SCOPE, session.value_id)
    activity_nodes = [child for child in root.children if child.resolved is not None]
    assert activity_nodes
    assert all(node.resolved == activity.with_version(1) for node in activity_nodes)
    raw_children = activity_nodes[0].children
    assert [child.reference.ref_id for child in raw_children] == [fit.raw_source_id, GARMIN_SOURCE]
    assert raw_children[0].resolved == fit
    assert raw_children[1].resolved is None  # the provider payload is not stored here
    assert lineage.raw_sources(root) == [fit]


@pytest.mark.req("SEC-IDOR-001", "NFR-TRC-001")
def test_another_athletes_activity_stays_unresolved() -> None:
    values = InMemoryDerivedValueRepository()
    activities = InMemoryActivityRepository()
    lineage = LineageService(values, InMemoryRawSourceRepository(), activities)
    theirs = sample_activity(sample_observation(athlete_id=OTHER_ATHLETE, sha256=SHA_B))
    activities.save(OwnerScope(OTHER_ATHLETE), [ActivityChange(theirs)])
    session = values.add(SCOPE, sample_derived_value())
    assert theirs.activity_id == ACTIVITY
    assert all(child.resolved is None for child in lineage.trace(SCOPE, session.value_id).children)
