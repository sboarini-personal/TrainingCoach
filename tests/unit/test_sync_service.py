"""Activity synchronisation (garmin spec 4.1; INT-GAR-007 to INT-GAR-010, INT-GAR-004)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from activity_world import SCOPE
from coach.application.events import EventName
from coach.application.providers import ProviderError, ProviderNotConfiguredError
from coach.application.sync import (
    CapabilityUnavailableError,
    SyncOutcome,
    SyncSettings,
)
from coach.domain.activity.issues import IssueKind
from coach.domain.coaching.state import Condition
from coach.domain.common.time import DateRange
from coach.domain.integration.capabilities import (
    Capability,
    CapabilityBasis,
    CapabilityState,
    CapabilityValue,
)
from coach.domain.integration.status import IntegrationReason, SyncState
from coach.domain.raw.source import DetectedType
from coach.integrations.garmin.capabilities import personal_capabilities
from fake_provider import DETAILS, FILE, LIST, FakeProvider
from sync_world import SYNC_NOW, SyncWorld

_R = IntegrationReason
TODAY = SYNC_NOW.date()  # 2026-10-26; the latest calendar date anywhere is 2026-10-27


def world(tmp_path: Path, **options: object) -> SyncWorld:
    return SyncWorld(tmp_path, **options)  # type: ignore[arg-type]


def with_capability(capability: Capability, value: CapabilityValue) -> FakeProvider:
    caps = personal_capabilities()
    state = CapabilityState(value, CapabilityBasis.CONFIGURED, "synthetic configuration")
    return FakeProvider(capabilities=replace(caps, states={**caps.states, capability: state}))


# ---------------------------------------------------------------- first sync


@pytest.mark.req("INT-GAR-007", "INT-GAR-009")
def test_the_first_sync_backfills_eight_weeks_in_windows(tmp_path: Path) -> None:
    sync = world(tmp_path)
    sync.fake.add(1001, date(2026, 10, 20))
    sync.fake.add(1002, date(2026, 10, 25), hour=9)
    sync.fake.add(1003, date(2026, 8, 1))  # before the backfill horizon
    report = sync.run()
    assert report.outcome is SyncOutcome.SYNCED
    assert report.requested == DateRange(date(2026, 9, 2), date(2026, 10, 27))
    assert (report.counts.windows, report.counts.listed, report.counts.imported) == (8, 2, 4)
    assert len(sync.activities.list_all(SCOPE)) == 2
    for activity_id in ("1001", "1002"):
        activity = sync.activities.find_by_provider_record(SCOPE, "garmin", activity_id)
        assert activity is not None
        assert {link.origin.value for link in activity.sources} == {"GARMIN_PERSONAL_API"}
        assert len(activity.sources) == 2  # the downloaded FIT and the provider record
    status = sync.status()
    assert (status.sync_state, status.last_success_at, status.reason) == (
        SyncState.SYNCED,
        SYNC_NOW,
        None,
    )
    assert status.cursor.completed_through == date(2026, 10, 25)
    assert (status.cursor.window, status.cursor.processed) == (None, ())
    pages = [raw for raw in sync.raw.list_all(SCOPE) if raw.category == "GARMIN_ACTIVITY_LIST"]
    assert len(pages) >= 2
    assert all(raw.request_window is not None for raw in pages)
    assert all(raw.detected_type is DetectedType.JSON for raw in pages)
    fit = status.capabilities.state(Capability.FIT_DOWNLOAD)
    assert (fit.value, fit.basis) == (CapabilityValue.SUPPORTED, CapabilityBasis.OBSERVED)
    assert Condition.INTEGRATION_ERROR not in sync.conditions_now()
    names = sync.events.names()
    assert EventName.INTEGRATION_SYNC_STARTED in names
    assert EventName.INTEGRATION_SYNC_SUCCEEDED in names
    assert names.count(EventName.INTEGRATION_CAPABILITY_OBSERVED) == 1


@pytest.mark.req("INT-GAR-007")
def test_the_file_is_fetched_before_the_record(tmp_path: Path) -> None:
    sync = world(tmp_path)
    sync.fake.add(1001, date(2026, 10, 20))
    sync.run()
    assert [call for call in sync.fake.calls if call[0] != LIST] == [
        (FILE, "1001"),
        (DETAILS, "1001"),
    ]


# ---------------------------------------------------------------- incremental sync


@pytest.mark.req("INT-GAR-007", "NFR-REL-001")
def test_a_later_sync_rescans_the_last_window_and_skips_known_activities(tmp_path: Path) -> None:
    sync = world(tmp_path)
    sync.fake.add(1001, date(2026, 10, 20))
    sync.run()
    sync.fake.calls.clear()
    sync.fake.add(1002, date(2026, 10, 24), hour=9)  # uploaded late, inside the last window
    sync.fake.add(1004, date(2026, 10, 27), hour=10)
    sync.clock.advance(days=1)
    report = sync.run()
    assert report.requested == DateRange(date(2026, 10, 19), date(2026, 10, 28))
    assert report.counts.known == 1
    assert sync.fake.count(DETAILS) == 2
    assert (DETAILS, "1001") not in sync.fake.calls
    assert sync.status().cursor.completed_through == date(2026, 10, 26)


@pytest.mark.req("INT-GAR-007", "INT-GAR-008")
def test_an_interrupted_sync_resumes_from_its_window(tmp_path: Path) -> None:
    sync = world(tmp_path)
    for number, day in enumerate((date(2026, 10, 14), date(2026, 10, 15), date(2026, 10, 16))):
        sync.fake.add(2001 + number, day)
    details_calls: list[str] = []

    def interrupt(method: str) -> None:
        if method == DETAILS:
            details_calls.append(method)
            if len(details_calls) == 3:
                raise ProviderError(_R.PROVIDER_PROTOCOL_ERROR, "HTTP_4XX")

    sync.fake.before_call = interrupt
    report = sync.run()
    assert report.outcome is SyncOutcome.PENDING
    status = sync.status()
    assert status.sync_state is SyncState.PENDING
    assert status.cursor.window == DateRange(date(2026, 10, 14), date(2026, 10, 20))
    assert status.cursor.processed == ("2001", "2002")
    sync.fake.before_call = None
    sync.fake.calls.clear()
    resumed = sync.run()
    assert resumed.outcome is SyncOutcome.SYNCED
    assert resumed.requested is not None
    assert resumed.requested.start == date(2026, 10, 14)
    assert [call for call in sync.fake.calls if call[0] == DETAILS] == [(DETAILS, "2003")]
    assert len(sync.activities.list_all(SCOPE)) == 3


# ---------------------------------------------------------------- degradation


@pytest.mark.req("INT-GAR-008", "NFR-REL-002")
def test_transient_failures_are_retried_with_backoff(tmp_path: Path) -> None:
    sync = world(tmp_path)
    sync.fake.add(1001, date(2026, 10, 20))
    sync.fake.fail(
        DETAILS,
        ProviderError(_R.PROVIDER_UNAVAILABLE, "HTTP_5XX"),
        ProviderError(_R.PROVIDER_RATE_LIMITED, "HTTP_429", retry_after=9),
    )
    report = sync.run()
    assert report.outcome is SyncOutcome.SYNCED
    assert sync.waits == [1.0, 9]
    assert sync.events.names().count(EventName.INTEGRATION_RETRY) == 2


@pytest.mark.req("INT-GAR-008", "INT-GAR-010")
def test_a_rate_limit_postpones_the_sync(tmp_path: Path) -> None:
    sync = world(tmp_path)
    sync.fake.add(1001, date(2026, 10, 20))
    sync.fake.fail(LIST, ProviderError(_R.PROVIDER_RATE_LIMITED, "HTTP_429", retry_after=3600))
    report = sync.run()
    status = sync.status()
    assert (report.outcome, status.reason, status.detail) == (
        SyncOutcome.PENDING,
        _R.PROVIDER_RATE_LIMITED,
        "HTTP_429",
    )
    assert status.not_before == SYNC_NOW + timedelta(seconds=3600)
    assert Condition.INTEGRATION_ERROR in sync.conditions_now()
    calls = len(sync.fake.calls)
    sync.clock.advance(minutes=30)
    assert sync.run().outcome is SyncOutcome.PENDING
    assert len(sync.fake.calls) == calls  # no request before notBefore
    sync.clock.advance(minutes=31)
    assert sync.run().outcome is SyncOutcome.SYNCED
    assert Condition.INTEGRATION_ERROR not in sync.conditions_now()


@pytest.mark.req("INT-GAR-008")
def test_an_auth_failure_is_not_retried_and_sets_integration_error(tmp_path: Path) -> None:
    sync = world(tmp_path)
    sync.fake.fail(LIST, ProviderError(_R.PROVIDER_AUTH_FAILED, "KEY_EXPIRED"))
    report = sync.run()
    status = sync.status()
    assert (report.outcome, status.reason, status.detail) == (
        SyncOutcome.PENDING,
        _R.PROVIDER_AUTH_FAILED,
        "KEY_EXPIRED",
    )
    assert sync.fake.count(LIST) == 1
    assert sync.waits == []
    assert status.last_success_at is None
    assert Condition.INTEGRATION_ERROR in sync.conditions_now()
    assert sync.events.events[-1] == (
        EventName.INTEGRATION_SYNC_FAILED,
        {"provider": "garmin", "reason": "PROVIDER_AUTH_FAILED", "detail": "KEY_EXPIRED"},
    )


@pytest.mark.req("INT-GAR-006")
def test_a_provider_without_a_key_is_not_configured_not_failing(tmp_path: Path) -> None:
    sync = world(tmp_path)
    sync.fake.fail(LIST, ProviderNotConfiguredError("garmin"))
    report = sync.run()
    assert report.outcome is SyncOutcome.NOT_CONFIGURED
    assert sync.status().sync_state is SyncState.NEVER_SYNCED
    assert Condition.INTEGRATION_ERROR not in sync.conditions_now()


# ---------------------------------------------------------------- capabilities


@pytest.mark.req("INT-GAR-003", "INT-GAR-004")
def test_activity_sync_needs_activity_read_and_pull(tmp_path: Path) -> None:
    sync = world(
        tmp_path, provider=with_capability(Capability.PULL_SUPPORT, CapabilityValue.UNKNOWN)
    )
    with pytest.raises(CapabilityUnavailableError) as missing:
        sync.run()
    assert missing.value.missing == frozenset({Capability.PULL_SUPPORT})
    assert sync.fake.calls == []


@pytest.mark.req("INT-GAR-003")
def test_without_fit_download_no_file_is_requested(tmp_path: Path) -> None:
    sync = world(
        tmp_path, provider=with_capability(Capability.FIT_DOWNLOAD, CapabilityValue.UNSUPPORTED)
    )
    sync.fake.add(1001, date(2026, 10, 20))
    assert sync.run().outcome is SyncOutcome.SYNCED
    assert sync.fake.count(FILE) == 0
    assert sync.activities.find_by_provider_record(SCOPE, "garmin", "1001") is not None


@pytest.mark.req("INT-GAR-004", "INT-GAR-007")
def test_history_before_the_horizon_is_a_probe_while_unknown(tmp_path: Path) -> None:
    sync = world(tmp_path)
    sync.fake.add(1003, date(2026, 8, 1))
    report = sync.run(since=date(2026, 7, 27))
    assert report.counts.imported == 2
    history = sync.status().capabilities.state(Capability.HISTORICAL_DATA)
    assert (history.value, history.basis) == (CapabilityValue.SUPPORTED, CapabilityBasis.OBSERVED)


@pytest.mark.req("INT-GAR-004")
def test_history_is_refused_when_unsupported(tmp_path: Path) -> None:
    sync = world(
        tmp_path, provider=with_capability(Capability.HISTORICAL_DATA, CapabilityValue.UNSUPPORTED)
    )
    with pytest.raises(CapabilityUnavailableError):
        sync.run(since=date(2026, 1, 1))
    assert sync.fake.calls == []
    assert sync.run(since=date(2026, 10, 1)).outcome is SyncOutcome.SYNCED


@pytest.mark.req("INT-GAR-004")
def test_an_empty_old_window_proves_nothing(tmp_path: Path) -> None:
    sync = world(tmp_path)
    sync.run(since=date(2026, 7, 27))
    history = sync.status().capabilities.state(Capability.HISTORICAL_DATA)
    assert (history.value, history.basis) == (CapabilityValue.UNKNOWN, CapabilityBasis.CONFIGURED)


# ---------------------------------------------------------------- payload outcomes


@pytest.mark.req("INT-GAR-007", "INT-FIL-004")
def test_a_rejected_record_opens_one_issue_and_is_not_retried(tmp_path: Path) -> None:
    sync = world(tmp_path)
    sync.fake.add(1001, date(2026, 10, 20), details=b"{not json", original=False)
    first = sync.run()
    assert (first.outcome, first.counts.rejected, first.counts.no_file) == (
        SyncOutcome.SYNCED,
        1,
        1,
    )
    issues = [
        issue for issue in sync.issues.list_all(SCOPE) if issue.kind is IssueKind.IMPORT_FAILED
    ]
    assert len(issues) == 1
    sync.fake.calls.clear()
    second = sync.run()
    assert second.counts.known == 1
    assert sync.fake.count(DETAILS) == 0
    assert len(sync.issues.list_all(SCOPE)) == 1


@pytest.mark.req("INT-GAR-007")
def test_gone_activities_and_missing_files_are_counted_not_invented(tmp_path: Path) -> None:
    sync = world(tmp_path)
    sync.fake.add(1001, date(2026, 10, 20), details=False, original=False)
    report = sync.run()
    assert (report.counts.gone, report.counts.no_file, report.counts.imported) == (1, 1, 0)
    assert sync.activities.list_all(SCOPE) == []


@pytest.mark.req("INT-GAR-007")
def test_windows_are_paged_and_runaway_paging_is_a_protocol_error(tmp_path: Path) -> None:
    sync = world(tmp_path, settings=SyncSettings(page_size=2, max_pages_per_window=3))
    for number in range(5):
        sync.fake.add(3000 + number, date(2026, 10, 20), hour=5 + number)
    report = sync.run()
    assert report.outcome is SyncOutcome.SYNCED
    assert report.counts.listed == 5
    assert [call for call in sync.fake.calls if call == (LIST, "2026-10-14:4")]
    for number in range(2):
        sync.fake.add(3005 + number, date(2026, 10, 21), hour=5 + number)
    sync.clock.advance(days=1)
    stuck = sync.run()
    assert stuck.outcome is SyncOutcome.PENDING
    assert sync.status().detail == "PAGINATION"


def test_sync_settings_are_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        SyncSettings(window_days=0)
    with pytest.raises(ValueError, match="100"):
        SyncSettings(page_size=101)


# ---------------------------------------------------------------- review findings


@pytest.mark.req("INT-GAR-010", "INT-GAR-008")
def test_a_long_retry_after_postpones_the_sync_whatever_the_status(tmp_path: Path) -> None:
    sync = world(tmp_path)
    sync.fake.fail(LIST, ProviderError(_R.PROVIDER_UNAVAILABLE, "HTTP_5XX", retry_after=3600))
    assert sync.run().outcome is SyncOutcome.PENDING
    assert sync.status().not_before == SYNC_NOW + timedelta(seconds=3600)
    assert sync.waits == []


@pytest.mark.req("INT-GAR-007", "INT-FIL-004")
def test_a_rejected_file_is_not_downloaded_again(tmp_path: Path) -> None:
    sync = world(tmp_path)
    sync.fake.add(1001, date(2026, 10, 20), details=False, original=b"not a zip")
    first = sync.run()
    assert (first.counts.rejected, first.counts.gone) == (1, 1)
    assert len(sync.issues.list_all(SCOPE)) == 1
    sync.fake.calls.clear()
    sync.run()
    assert sync.fake.count(FILE) == 0
    assert len(sync.issues.list_all(SCOPE)) == 1


@pytest.mark.req("INT-GAR-007", "INT-GAR-009", "SEC-UPL-003")
def test_an_oversized_file_is_recorded_and_the_activity_still_syncs(tmp_path: Path) -> None:
    sync = world(tmp_path)
    sync.fake.add(1001, date(2026, 10, 20))
    sync.fake.fail(FILE, ProviderError(_R.PROVIDER_PROTOCOL_ERROR, "RESPONSE_TOO_LARGE"))
    report = sync.run()
    assert report.outcome is SyncOutcome.SYNCED
    assert (report.counts.rejected, report.counts.imported) == (1, 1)
    (issue,) = sync.issues.list_all(SCOPE)
    assert (issue.kind, issue.code) == (IssueKind.IMPORT_FAILED, "TOO_LARGE")
    assert sync.activities.find_by_provider_record(SCOPE, "garmin", "1001") is not None
    sync.fake.calls.clear()
    sync.run()
    assert sync.fake.count(FILE) == 0


def test_a_window_never_lists_more_ids_than_the_cursor_holds() -> None:
    with pytest.raises(ValueError, match="1000"):
        SyncSettings(page_size=100, max_pages_per_window=11)
