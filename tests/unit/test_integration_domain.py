"""Provider capabilities, sync cursor and integration status (provider-capabilities spec)."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.time import DateRange
from coach.domain.integration.capabilities import (
    FEATURE_REQUIREMENTS,
    AccessTier,
    Capability,
    CapabilityBasis,
    CapabilityState,
    CapabilityValue,
    Feature,
    ProviderCapabilities,
)
from coach.domain.integration.status import (
    MAX_PROCESSED,
    IntegrationReason,
    IntegrationStatus,
    SyncCursor,
    SyncState,
)
from samples import ATHLETE, NOW

SUPPORTED, UNSUPPORTED, UNKNOWN = (
    CapabilityValue.SUPPORTED,
    CapabilityValue.UNSUPPORTED,
    CapabilityValue.UNKNOWN,
)


def configured(value: CapabilityValue = SUPPORTED) -> CapabilityState:
    return CapabilityState(value, CapabilityBasis.CONFIGURED, "documentation (2026-10-26)")


def capabilities(**values: CapabilityValue) -> ProviderCapabilities:
    states = {capability: configured(UNKNOWN) for capability in Capability}
    for name, value in values.items():
        states[Capability(name)] = configured(value)
    return ProviderCapabilities("garmin", AccessTier.PERSONAL, states)


def status(**overrides: object) -> IntegrationStatus:
    values: dict[str, object] = {
        "integration_id": UUID("0193b2a0-0000-7000-8000-0000000000aa"),
        "athlete_id": ATHLETE,
        "provider": "garmin",
        "capabilities": capabilities(activityRead=SUPPORTED, pullSupport=SUPPORTED),
    }
    values.update(overrides)
    return IntegrationStatus(**values)  # type: ignore[arg-type]


# ---------------------------------------------------------------- capabilities


@pytest.mark.req("INT-GAR-003")
def test_capabilities_name_every_capability_of_the_model() -> None:
    assert [capability.value for capability in Capability] == [
        "activityRead",
        "healthRead",
        "fitDownload",
        "pullSupport",
        "pushSupport",
        "historicalData",
        "trainingPublish",
        "coursesPublish",
    ]
    states = {capability: configured() for capability in Capability}
    del states[Capability.HEALTH_READ]
    with pytest.raises(DomainValidationError, match="every capability"):
        ProviderCapabilities("garmin", AccessTier.PERSONAL, states)


@pytest.mark.req("INT-GAR-003")
def test_feature_gating_table() -> None:
    assert {
        Feature.ACTIVITY_SYNC: frozenset({Capability.ACTIVITY_READ, Capability.PULL_SUPPORT}),
        Feature.RECOVERY_SYNC: frozenset({Capability.HEALTH_READ}),
        Feature.FIT_ENRICHMENT: frozenset({Capability.FIT_DOWNLOAD}),
        Feature.HISTORICAL_BACKFILL: frozenset({Capability.HISTORICAL_DATA}),
        Feature.WEBHOOK: frozenset({Capability.PUSH_SUPPORT}),
        Feature.WORKOUT_PUBLICATION: frozenset({Capability.TRAINING_PUBLISH}),
        Feature.COURSE_PUBLICATION: frozenset({Capability.COURSES_PUBLISH}),
    } == FEATURE_REQUIREMENTS


@pytest.mark.req("INT-GAR-003", "INT-GAR-004")
def test_a_feature_is_available_only_when_every_capability_is_supported() -> None:
    caps = capabilities(activityRead=SUPPORTED, pullSupport=UNKNOWN, trainingPublish=UNSUPPORTED)
    assert caps.missing_for(Feature.ACTIVITY_SYNC) == frozenset({Capability.PULL_SUPPORT})
    assert not caps.available(Feature.ACTIVITY_SYNC)
    assert not caps.available(Feature.WORKOUT_PUBLICATION)
    assert caps.supported == frozenset({Capability.ACTIVITY_READ})
    ready = capabilities(activityRead=SUPPORTED, pullSupport=SUPPORTED)
    assert ready.available(Feature.ACTIVITY_SYNC)


@pytest.mark.req("INT-GAR-004")
def test_observed_states_carry_their_time_and_configured_ones_do_not() -> None:
    with pytest.raises(DomainValidationError, match="time"):
        CapabilityState(SUPPORTED, CapabilityBasis.OBSERVED, "sync")
    with pytest.raises(DomainValidationError, match="time"):
        CapabilityState(SUPPORTED, CapabilityBasis.CONFIGURED, "doc", observed_at=NOW)
    with pytest.raises(DomainValidationError, match="UNKNOWN"):
        CapabilityState(UNKNOWN, CapabilityBasis.OBSERVED, "sync", observed_at=NOW)
    with pytest.raises(DomainValidationError, match="source"):
        CapabilityState(SUPPORTED, CapabilityBasis.CONFIGURED, " ")


@pytest.mark.req("INT-GAR-004")
def test_an_observation_wins_over_configuration_and_survives_reconfiguration() -> None:
    caps = capabilities(fitDownload=SUPPORTED)
    observed = caps.observe(Capability.HISTORICAL_DATA, SUPPORTED, at=NOW, source="sync")
    state = observed.state(Capability.HISTORICAL_DATA)
    assert (state.value, state.basis, state.observed_at) == (
        SUPPORTED,
        CapabilityBasis.OBSERVED,
        NOW,
    )
    fresh = capabilities(fitDownload=UNSUPPORTED)
    merged = observed.reconfigured(fresh)
    assert merged.state(Capability.HISTORICAL_DATA) == state
    assert merged.value(Capability.FIT_DOWNLOAD) is UNSUPPORTED
    with pytest.raises(DomainValidationError, match="provider"):
        observed.reconfigured(replace(fresh, provider="other"))


# ---------------------------------------------------------------- windows and cursor


@pytest.mark.req("INT-GAR-007")
def test_date_ranges_are_inclusive_and_split_into_windows() -> None:
    whole = DateRange(date(2026, 10, 1), date(2026, 10, 16))
    assert whole.days == 16
    assert whole.split(7) == [
        DateRange(date(2026, 10, 1), date(2026, 10, 7)),
        DateRange(date(2026, 10, 8), date(2026, 10, 14)),
        DateRange(date(2026, 10, 15), date(2026, 10, 16)),
    ]
    with pytest.raises(DomainValidationError):
        DateRange(date(2026, 10, 2), date(2026, 10, 1))
    with pytest.raises(DomainValidationError):
        whole.split(0)


@pytest.mark.req("INT-GAR-007")
def test_cursor_tracks_processed_ids_of_the_window_in_progress() -> None:
    window = DateRange(date(2026, 10, 1), date(2026, 10, 7))
    cursor = SyncCursor().start(window).processed_id("11").processed_id("12")
    assert cursor.processed == ("11", "12")
    assert cursor.processed_id("11") == cursor
    assert cursor.start(window) == cursor
    later = DateRange(date(2026, 10, 8), date(2026, 10, 14))
    assert cursor.start(later).processed == ()
    done = cursor.complete(through=date(2026, 10, 7))
    assert (done.completed_through, done.window, done.processed) == (date(2026, 10, 7), None, ())
    assert done.complete(through=date(2026, 9, 30)).completed_through == date(2026, 10, 7)
    with pytest.raises(DomainValidationError, match="window"):
        SyncCursor(processed=("1",))
    with pytest.raises(DomainValidationError, match="unique"):
        SyncCursor(window=window, processed=("1", "1"))
    with pytest.raises(DomainValidationError):
        SyncCursor(window=window, processed=tuple(str(i) for i in range(MAX_PROCESSED + 1)))
    with pytest.raises(DomainValidationError):
        SyncCursor().processed_id("1")


# ---------------------------------------------------------------- status


@pytest.mark.req("INT-GAR-008")
def test_status_lifecycle_records_freshness_and_reasons() -> None:
    initial = status()
    assert (initial.sync_state, initial.last_success_at, initial.reason) == (
        SyncState.NEVER_SYNCED,
        None,
        None,
    )
    started = initial.started(NOW)
    assert started.last_attempt_at == NOW
    later = NOW + timedelta(minutes=5)
    failed = started.failed(
        later,
        IntegrationReason.PROVIDER_RATE_LIMITED,
        "HTTP_429",
        not_before=later + timedelta(minutes=10),
    )
    assert (failed.sync_state, failed.reason, failed.detail) == (
        SyncState.PENDING,
        IntegrationReason.PROVIDER_RATE_LIMITED,
        "HTTP_429",
    )
    assert failed.postponed(later)
    assert not failed.postponed(later + timedelta(minutes=10))
    synced = failed.started(later).succeeded(later + timedelta(minutes=1))
    assert (synced.sync_state, synced.reason, synced.detail, synced.not_before) == (
        SyncState.SYNCED,
        None,
        None,
        None,
    )
    assert synced.last_success_at == later + timedelta(minutes=1)


@pytest.mark.req("INT-GAR-008")
def test_status_invariants() -> None:
    with pytest.raises(DomainValidationError, match="reason"):
        status(sync_state=SyncState.PENDING)
    with pytest.raises(DomainValidationError, match="reason"):
        status(
            sync_state=SyncState.SYNCED,
            last_success_at=NOW,
            reason=IntegrationReason.PROVIDER_UNAVAILABLE,
        )
    with pytest.raises(DomainValidationError, match="success"):
        status(sync_state=SyncState.SYNCED)
    with pytest.raises(DomainValidationError, match="detail"):
        status(detail="HTTP_500")
    with pytest.raises(DomainValidationError):
        status(
            sync_state=SyncState.PENDING,
            reason=IntegrationReason.PROVIDER_UNAVAILABLE,
            detail="not a code",
        )
    with pytest.raises(DomainValidationError, match="provider"):
        status(provider="strava")
    with pytest.raises(DomainValidationError):
        status(last_attempt_at=datetime(2026, 10, 24, 18, 0))  # noqa: DTZ001 - naive on purpose
    assert status(last_attempt_at=NOW.astimezone(UTC)).entity_id == status().integration_id
