"""Activity synchronisation over the FitnessProvider port (INT-GAR-007, INT-GAR-008, ADR-026).

One provider-agnostic algorithm (garmin spec section 4.1): date windows listed page by page,
every page stored as RAW, every new activity's original file and record imported through the
ingestion pipeline, a cursor saved after every activity so an interrupted sync resumes, and
capabilities observed at runtime. A provider failure, after the retry policy, keeps the
progress made, marks the sync PENDING and activates INTEGRATION_ERROR; local data is never
touched and nothing is fabricated to fill the gap.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, fields
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Final

from coach.application.coaching import CoachingConditions
from coach.application.consent import ConsentGate
from coach.application.errors import ApplicationError
from coach.application.events import EventName
from coach.application.files import FileRejectedError
from coach.application.ingestion import ImportResult, IngestionService
from coach.application.ownership import OwnerScope
from coach.application.ports import (
    ActivityRepository,
    BlobStore,
    Clock,
    EventSink,
    IdFactory,
    IngestionRecordRepository,
    OwnedRepository,
    RawSourceRepository,
)
from coach.application.providers import (
    JSON_MEDIA_TYPE,
    ZIP_MEDIA_TYPE,
    ActivityPage,
    ActivityRef,
    FitnessProvider,
    ProviderError,
    ProviderNotConfiguredError,
    RawPayload,
)
from coach.application.raw_storage import RawStorage
from coach.application.retry import Retrier
from coach.domain.coaching.state import Condition
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.time import DateRange
from coach.domain.consent.ledger import ConsentType
from coach.domain.ingestion.record import IngestionOutcome, RejectionReason
from coach.domain.integration.capabilities import (
    Capability,
    CapabilityBasis,
    CapabilityValue,
    Feature,
)
from coach.domain.integration.status import MAX_PROCESSED, IntegrationReason, IntegrationStatus
from coach.domain.raw.source import DetectedType, MalwareScan, provider_origin

# Calendar dates exist from UTC-12 to UTC+14 at any instant (garmin spec 4.1, step 2).
LATEST_AHEAD: Final = timedelta(hours=14)
EARLIEST_BEHIND: Final = timedelta(hours=12)
OBSERVED_SOURCE: Final = "observed during a sync"
SUCCESS_REASON: Final = "SYNC_SUCCEEDED"
OVERSIZED: Final = "RESPONSE_TOO_LARGE"


class _Skipped:
    """A payload that was recorded as rejected without being fetched whole."""


_SKIPPED: Final = _Skipped()


@dataclass(frozen=True, slots=True, kw_only=True)
class SyncSettings:
    window_days: int = 7
    initial_backfill_weeks: int = 8
    page_size: int = 20
    max_pages_per_window: int = 50

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise DomainValidationError(f"sync setting {item.name} must be a positive integer")
        if self.page_size > 100:
            raise DomainValidationError("a page holds at most 100 activities")
        if self.page_size * self.max_pages_per_window > MAX_PROCESSED:
            raise DomainValidationError(f"a window lists at most {MAX_PROCESSED} activities")


class SyncOutcome(StrEnum):
    SYNCED = "SYNCED"
    PENDING = "PENDING"
    NOT_CONFIGURED = "NOT_CONFIGURED"


class CapabilityUnavailableError(ApplicationError):
    """The feature needs capabilities that are not SUPPORTED; nothing was requested."""

    def __init__(self, missing: frozenset[Capability]) -> None:
        names = ", ".join(sorted(capability.value for capability in missing))
        super().__init__(f"capability unavailable: {names}")
        self.missing = missing


@dataclass(slots=True)
class SyncCounts:
    windows: int = 0
    pages: int = 0
    listed: int = 0
    known: int = 0
    imported: int = 0
    duplicates: int = 0
    rejected: int = 0
    no_file: int = 0
    gone: int = 0

    def record(self, result: ImportResult) -> None:
        outcome = result.record.outcome
        if outcome is IngestionOutcome.IMPORTED:
            self.imported += 1
        elif outcome is IngestionOutcome.DUPLICATE:
            self.duplicates += 1
        else:
            self.rejected += 1

    def as_fields(self) -> dict[str, str]:
        return {item.name: str(getattr(self, item.name)) for item in fields(self)}


@dataclass(frozen=True, slots=True)
class SyncReport:
    outcome: SyncOutcome
    status: IntegrationStatus | None
    counts: SyncCounts
    requested: DateRange | None = None


@dataclass(slots=True)
class _Run:
    scope: OwnerScope
    provider: FitnessProvider
    status: IntegrationStatus
    horizon: date
    earliest_today: date
    counts: SyncCounts


class SyncService:
    def __init__(
        self,
        *,
        providers: Callable[[OwnerScope], FitnessProvider],
        statuses: OwnedRepository[IntegrationStatus],
        blobs: BlobStore,
        raw_sources: RawSourceRepository,
        ingestion: IngestionService,
        activities: ActivityRepository,
        records: IngestionRecordRepository,
        consents: ConsentGate,
        conditions: CoachingConditions,
        retrier: Retrier,
        events: EventSink,
        clock: Clock,
        ids: IdFactory,
        settings: SyncSettings | None = None,
    ) -> None:
        self._providers = providers
        self._statuses = statuses
        self._raw = RawStorage(blobs, raw_sources, clock=clock, ids=ids)
        self._ingestion = ingestion
        self._activities = activities
        self._records = records
        self._consents = consents
        self._conditions = conditions
        self._retrier = retrier
        self._events = events
        self._clock = clock
        self._ids = ids
        self.settings = settings or SyncSettings()

    def status(self, scope: OwnerScope, provider: str) -> IntegrationStatus | None:
        return next(
            (item for item in self._statuses.list_all(scope) if item.provider == provider), None
        )

    def sync(self, scope: OwnerScope, *, since: date | None = None) -> SyncReport:
        """Synchronise activities; provider failures end in the report, never in an error."""
        self._consents.require(scope, ConsentType.ACTIVITIES)
        provider = self._providers(scope)
        status = self._current(scope, provider)
        missing = status.capabilities.missing_for(Feature.ACTIVITY_SYNC)
        if missing:
            raise CapabilityUnavailableError(missing)
        now = self._clock()
        counts = SyncCounts()
        if status.postponed(now):
            return SyncReport(SyncOutcome.PENDING, status, counts)
        requested, horizon = self._range(status, since, now)
        run = _Run(scope, provider, status, horizon, (now - EARLIEST_BEHIND).date(), counts)
        self._save(run, status.started(now))
        self._events.emit(EventName.INTEGRATION_SYNC_STARTED, {"provider": provider.provider})
        try:
            for window in requested.split(self.settings.window_days):
                self._window(run, window)
                counts.windows += 1
        except ProviderNotConfiguredError:
            return SyncReport(SyncOutcome.NOT_CONFIGURED, run.status, counts, requested)
        except ProviderError as error:
            return self._failed(run, error, requested)
        self._save(run, run.status.succeeded(self._clock()))
        self._conditions.clear(scope, Condition.INTEGRATION_ERROR, reason=SUCCESS_REASON)
        self._events.emit(
            EventName.INTEGRATION_SYNC_SUCCEEDED,
            {"provider": provider.provider, **counts.as_fields()},
        )
        return SyncReport(SyncOutcome.SYNCED, run.status, counts, requested)

    # ------------------------------------------------------------------ setup

    def _current(self, scope: OwnerScope, provider: FitnessProvider) -> IntegrationStatus:
        configured = provider.get_capabilities()
        existing = self.status(scope, provider.provider)
        if existing is None:
            return self._statuses.add(
                scope,
                IntegrationStatus(
                    integration_id=self._ids(),
                    athlete_id=scope.athlete_id,
                    provider=provider.provider,
                    capabilities=configured,
                ),
            )
        capabilities = existing.capabilities.reconfigured(configured)
        if capabilities == existing.capabilities:
            return existing
        return self._statuses.update(
            scope, existing.with_capabilities(capabilities), expected_version=existing.version
        )

    def _range(
        self, status: IntegrationStatus, since: date | None, now: datetime
    ) -> tuple[DateRange, date]:
        latest = (now + LATEST_AHEAD).date()
        horizon = latest - timedelta(weeks=self.settings.initial_backfill_weeks, days=-1)
        cursor = status.cursor
        if since is not None:
            history = status.capabilities.value(Capability.HISTORICAL_DATA)
            if since < horizon and history is CapabilityValue.UNSUPPORTED:
                raise CapabilityUnavailableError(frozenset({Capability.HISTORICAL_DATA}))
            start = since
        elif cursor.window is not None:
            start = cursor.window.start  # resume the interrupted window
        elif cursor.completed_through is not None:
            # Re-scan the last completed window: activities may be uploaded late.
            start = cursor.completed_through - timedelta(days=self.settings.window_days - 1)
        else:
            start = horizon
        return DateRange(min(start, latest), latest), horizon

    # ------------------------------------------------------------------ windows

    def _window(self, run: _Run, window: DateRange) -> None:
        self._save(run, run.status.progressed(run.status.cursor.start(window)))
        size = self.settings.page_size
        seen: set[str] = set()
        offset = 0
        for _ in range(self.settings.max_pages_per_window):
            page = self._page(run, window, offset)
            if page.refs and window.end < run.horizon:
                self._observe(run, Capability.HISTORICAL_DATA)
            for ref in page.refs:
                if ref.provider_activity_id in seen:
                    continue
                seen.add(ref.provider_activity_id)
                run.counts.listed += 1
                if self._known(run, ref):
                    run.counts.known += 1
                    continue
                self._activity(run, ref)
                cursor = run.status.cursor.processed_id(ref.provider_activity_id)
                self._save(run, run.status.progressed(cursor))
            if len(page.refs) < size:
                break
            offset += size
        else:
            raise ProviderError(IntegrationReason.PROVIDER_PROTOCOL_ERROR, "PAGINATION")
        through = min(window.end, run.earliest_today - timedelta(days=1))
        self._save(run, run.status.progressed(run.status.cursor.complete(through=through)))

    def _page(self, run: _Run, window: DateRange, offset: int) -> ActivityPage:
        page = self._retrier.call(
            lambda: run.provider.get_activities(window, offset, self.settings.page_size)
        )
        run.counts.pages += 1
        payload = page.payload
        self._raw.store(
            run.scope,
            payload.data,
            origin=provider_origin(payload.provider),
            category=payload.category,
            detected_type=DetectedType.JSON,
            malware_scan=MalwareScan.NOT_APPLICABLE,
            provider=payload.provider,
            request_window=payload.window,
        )
        return page

    def _known(self, run: _Run, ref: ActivityRef) -> bool:
        """Processed in this window, already linked, or its record was rejected before."""
        activity_id = ref.provider_activity_id
        if activity_id in run.status.cursor.processed:
            return True
        name = run.provider.provider
        if self._activities.find_by_provider_record(run.scope, name, activity_id) is not None:
            return True
        return any(
            record.detected_type is DetectedType.JSON
            and record.outcome is IngestionOutcome.REJECTED
            for record in self._records.for_provider_object(run.scope, name, activity_id)
        )

    def _activity(self, run: _Run, ref: ActivityRef) -> None:
        # The file goes first: an activity linked to its provider record then implies that
        # both payloads were handled, even if a sync is interrupted in between.
        fit_download = run.status.capabilities.value(Capability.FIT_DOWNLOAD)
        if fit_download is not CapabilityValue.UNSUPPORTED and not self._file_settled(run, ref):
            original = self._fetch(run, ref, run.provider.get_activity_file, ZIP_MEDIA_TYPE)
            if original is None:
                run.counts.no_file += 1
            elif isinstance(original, RawPayload):
                result = self._ingestion.import_provider_payload(run.scope, original)
                run.counts.record(result)
                if result.record.succeeded:
                    self._observe(run, Capability.FIT_DOWNLOAD)
        details = self._fetch(run, ref, run.provider.get_activity_details, JSON_MEDIA_TYPE)
        if details is None:
            run.counts.gone += 1
        elif isinstance(details, RawPayload):
            run.counts.record(self._ingestion.import_provider_payload(run.scope, details))

    def _fetch(
        self,
        run: _Run,
        ref: ActivityRef,
        call: Callable[[ActivityRef], RawPayload | None],
        media_type: str,
    ) -> RawPayload | _Skipped | None:
        """One activity payload; one that exceeds the size cap is recorded and skipped."""
        try:
            return self._retrier.call(lambda: call(ref))
        except ProviderError as error:
            if error.detail != OVERSIZED:
                raise
        result = self._ingestion.reject_provider_payload(
            run.scope,
            provider=run.provider.provider,
            provider_object_id=ref.provider_activity_id,
            media_type=media_type,
            rejection=FileRejectedError(RejectionReason.TOO_LARGE, "RESPONSE_LIMIT"),
        )
        run.counts.record(result)
        return _SKIPPED

    def _file_settled(self, run: _Run, ref: ActivityRef) -> bool:
        """The original file was imported or rejected before: never downloaded again."""
        records = self._records.for_provider_object(
            run.scope, run.provider.provider, ref.provider_activity_id
        )
        return any(
            record.detected_type is DetectedType.ZIP
            and record.outcome is not IngestionOutcome.FAILED
            for record in records
        )

    # ------------------------------------------------------------------ state

    def _observe(self, run: _Run, capability: Capability) -> None:
        state = run.status.capabilities.state(capability)
        supported = CapabilityValue.SUPPORTED
        if state.basis is CapabilityBasis.OBSERVED and state.value is supported:
            return
        observed = run.status.capabilities.observe(
            capability, supported, at=self._clock(), source=OBSERVED_SOURCE
        )
        self._save(run, run.status.with_capabilities(observed))
        self._events.emit(
            EventName.INTEGRATION_CAPABILITY_OBSERVED,
            {
                "provider": run.provider.provider,
                "capability": capability.value,
                "value": supported.value,
            },
        )

    def _save(self, run: _Run, changed: IntegrationStatus) -> None:
        run.status = self._statuses.update(run.scope, changed, expected_version=run.status.version)

    def _failed(self, run: _Run, error: ProviderError, requested: DateRange) -> SyncReport:
        now = self._clock()
        longest = self._retrier.policy.max_delay_seconds
        not_before = None
        if error.retry_after is not None and error.retry_after > longest:
            not_before = now + timedelta(seconds=error.retry_after)  # spec 7: never sooner
        elif error.code is IntegrationReason.PROVIDER_RATE_LIMITED:
            not_before = now + timedelta(seconds=max(error.retry_after or 0.0, longest))
        self._save(run, run.status.failed(now, error.code, error.detail, not_before=not_before))
        self._conditions.activate(run.scope, Condition.INTEGRATION_ERROR, reason=error.code.value)
        fields_ = {"provider": run.provider.provider, "reason": error.code.value}
        if error.detail is not None:
            fields_["detail"] = error.detail
        self._events.emit(EventName.INTEGRATION_SYNC_FAILED, fields_)
        return SyncReport(SyncOutcome.PENDING, run.status, run.counts, requested)
