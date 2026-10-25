"""Computing and storing derived metrics (FR-MET-001, FR-MET-004, AT-MET-001)."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from coach.application.derived_metrics import DerivedMetricService
from coach.application.ownership import OwnerScope
from coach.domain.common.evidence import EvidenceKind, EvidenceRef
from coach.domain.common.quality import Confidence
from coach.infrastructure.memory import InMemoryDerivedValueRepository
from coach.metrics.catalog import CATALOG
from coach.metrics.derived import DerivedStatus, MetricInput, MetricSubject
from samples import ACTIVITY, ATHLETE

SCOPE = OwnerScope(ATHLETE)
NOW = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)
SUBJECT = MetricSubject.activity(ACTIVITY)


def _inputs(rpe: float) -> dict[str, MetricInput]:
    return {
        "rpe": MetricInput(
            rpe, EvidenceRef(EvidenceKind.ACTIVITY, ACTIVITY, "subjective.rpe"), Confidence.HIGH
        ),
        "session_duration_seconds": MetricInput(
            3600,
            EvidenceRef(EvidenceKind.ACTIVITY, ACTIVITY, "timing.elapsedSeconds"),
            Confidence.HIGH,
        ),
    }


@pytest.fixture
def service() -> tuple[DerivedMetricService, InMemoryDerivedValueRepository]:
    counter = iter(range(1, 100))
    values = InMemoryDerivedValueRepository()
    return DerivedMetricService(
        CATALOG, values, clock=lambda: NOW, ids=lambda: UUID(int=next(counter))
    ), values


@pytest.mark.req("FR-MET-001", "FR-MET-004")
@pytest.mark.acceptance("AT-MET-001")
def test_metric_is_computed_in_code_and_stored_with_provenance(
    service: tuple[DerivedMetricService, InMemoryDerivedValueRepository],
) -> None:
    metrics, values = service
    stored = metrics.compute(SCOPE, "any.srpe_load", SUBJECT, _inputs(6))
    assert (stored.status, stored.value) == (DerivedStatus.COMPUTED, 360)
    assert stored.calculation_version == "1.0.0"
    assert values.get(SCOPE, stored.value_id) == stored


@pytest.mark.req("FR-MET-001", "NFR-REL-001")
@pytest.mark.acceptance("AT-MET-001")
def test_recomputing_the_same_inputs_returns_the_stored_value(
    service: tuple[DerivedMetricService, InMemoryDerivedValueRepository],
) -> None:
    metrics, values = service
    first = metrics.compute(SCOPE, "any.srpe_load", SUBJECT, _inputs(6))
    again = metrics.compute(SCOPE, "any.srpe_load", SUBJECT, _inputs(6))
    assert again == first
    assert len(values.history(SCOPE, "any.srpe_load", SUBJECT)) == 1


@pytest.mark.req("FR-MET-004", "NFR-TRC-001")
def test_changed_inputs_supersede_and_keep_history(
    service: tuple[DerivedMetricService, InMemoryDerivedValueRepository],
) -> None:
    metrics, values = service
    first = metrics.compute(SCOPE, "any.srpe_load", SUBJECT, _inputs(6))
    corrected = metrics.compute(SCOPE, "any.srpe_load", SUBJECT, _inputs(7))
    assert corrected.value == 420
    assert values.get(SCOPE, first.value_id).superseded_by == corrected.value_id
    assert values.current(SCOPE, "any.srpe_load", SUBJECT) == corrected


def test_unknown_metric_is_rejected(
    service: tuple[DerivedMetricService, InMemoryDerivedValueRepository],
) -> None:
    with pytest.raises(KeyError):
        service[0].compute(SCOPE, "any.not_registered", SUBJECT, {})
