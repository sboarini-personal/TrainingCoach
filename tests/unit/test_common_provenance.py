from datetime import UTC, datetime, timedelta

import pytest

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.provenance import FieldValue, Provenance, ValueSource

NOW = datetime(2026, 10, 24, 18, 0, tzinfo=UTC)


@pytest.mark.req("FR-PRF-002")
def test_unknown_answer_is_recorded_as_unknown_with_provenance() -> None:
    field: FieldValue[float] = FieldValue.unknown(at=NOW)
    assert field.value is None
    assert not field.is_known
    assert field.provenance.source is ValueSource.ATHLETE_DECLARED
    assert field.provenance.recorded_at == NOW


@pytest.mark.req("FR-PRF-002")
def test_declared_value_is_known() -> None:
    field = FieldValue.declared(72.5, at=NOW)
    assert field.is_known
    assert field.value == 72.5


def test_provider_values_name_their_provider() -> None:
    with pytest.raises(DomainValidationError):
        Provenance(source=ValueSource.PROVIDER, recorded_at=NOW)
    provenance = Provenance(source=ValueSource.PROVIDER, recorded_at=NOW, provider="garmin")
    assert provenance.provider == "garmin"


def test_derived_values_name_their_metric() -> None:
    with pytest.raises(DomainValidationError):
        Provenance(source=ValueSource.DERIVED, recorded_at=NOW)
    assert Provenance(source=ValueSource.DERIVED, recorded_at=NOW, metric_id="any.srpe_load")


def test_provenance_requires_aware_timestamp() -> None:
    with pytest.raises(DomainValidationError):
        Provenance(source=ValueSource.ATHLETE_DECLARED, recorded_at=datetime(2026, 10, 24))  # noqa: DTZ001


@pytest.mark.req("FR-ONB-002")
def test_freshness_window() -> None:
    field = FieldValue.declared(70.0, at=NOW - timedelta(days=91))
    assert not field.recorded_within(timedelta(days=90), now=NOW)
    assert field.recorded_within(timedelta(days=120), now=NOW)
    assert field.recorded_within(None, now=NOW)
