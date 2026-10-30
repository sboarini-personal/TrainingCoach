"""Daily recovery synchronisation over the FitnessProvider port (recovery spec 5; P11).

For each recovery metric whose consent is granted (FR-REC-005; a metric without it is
neither fetched nor stored), the provider's daily payloads are requested in windows the
provider allows, stored byte-exact as RAW (INT-GAR-009), decoded by the provider's daily
normaliser and kept as immutable observations, one per day, metric and RAW source, so a
repeated sync stores nothing new. The first sync reaches back over the baseline window (28
days, recovery spec 3); later syncs from the last day known, less two days whose values may
still settle. A provider failure after the retry policy ends the sync PENDING and activates
INTEGRATION_ERROR; nothing is fabricated to fill the gap.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum
from functools import partial
from typing import Final
from zoneinfo import ZoneInfo

from coach.application.coaching import CoachingConditions
from coach.application.consent import ConsentGate
from coach.application.errors import ConcurrencyConflictError
from coach.application.events import EventName
from coach.application.ownership import OwnerScope
from coach.application.ports import (
    BlobStore,
    Clock,
    EventSink,
    IdFactory,
    OwnedRepository,
    RawSourceRepository,
)
from coach.application.providers import (
    DailyRecords,
    FitnessProvider,
    ProviderError,
    ProviderNotConfiguredError,
)
from coach.application.raw_storage import RawStorage
from coach.application.retry import Retrier
from coach.application.sync import CapabilityUnavailableError, SyncOutcome
from coach.domain.coaching.state import Condition
from coach.domain.common.time import DateRange
from coach.domain.integration.capabilities import Feature
from coach.domain.raw.source import DetectedType, MalwareScan, provider_origin
from coach.domain.recovery.daily import (
    METRICS,
    RecoveryMetric,
    RecoveryObservation,
    observation_id,
)

BACKFILL_DAYS: Final = 28
SETTLING_DAYS: Final = 2
MAX_DAYS: Final = 365
FORMAT_ERROR: Final = "DAILY_FORMAT"


class MetricOutcome(StrEnum):
    SYNCED = "SYNCED"
    NO_CONSENT = "NO_CONSENT"


@dataclass(slots=True)
class MetricSync:
    metric: RecoveryMetric
    outcome: MetricOutcome
    requested: DateRange | None = None
    requests: int = 0
    stored: int = 0  # new observations
    rejected: int = 0  # payloads the normaliser could not read


@dataclass(frozen=True, slots=True)
class RecoverySyncReport:
    outcome: SyncOutcome
    metrics: tuple[MetricSync, ...] = field(default=())
    error: str | None = None  # the failure's code, when PENDING


class RecoverySyncService:
    def __init__(
        self,
        *,
        providers: Callable[[OwnerScope], FitnessProvider],
        records: Mapping[str, DailyRecords],
        observations: OwnedRepository[RecoveryObservation],
        blobs: BlobStore,
        raw_sources: RawSourceRepository,
        consents: ConsentGate,
        conditions: CoachingConditions,
        retrier: Retrier,
        timezone_of: Callable[[OwnerScope], ZoneInfo],
        events: EventSink,
        clock: Clock,
        ids: IdFactory,
    ) -> None:
        self._providers = providers
        self._records = records
        self._observations = observations
        self._raw = RawStorage(blobs, raw_sources, clock=clock, ids=ids)
        self._consents = consents
        self._conditions = conditions
        self._retrier = retrier
        self._timezone_of = timezone_of
        self._events = events
        self._clock = clock

    def sync(self, scope: OwnerScope) -> RecoverySyncReport:
        provider = self._providers(scope)
        missing = provider.get_capabilities().missing_for(Feature.RECOVERY_SYNC)
        if missing:
            raise CapabilityUnavailableError(missing)
        granted = self._consents.granted(scope)
        today = self._clock().astimezone(self._timezone_of(scope)).date()
        done: list[MetricSync] = []
        try:
            for metric in RecoveryMetric:
                if METRICS[metric].consent not in granted:
                    done.append(MetricSync(metric, MetricOutcome.NO_CONSENT))
                    continue
                done.append(self._metric(scope, provider, metric, today))
        except ProviderNotConfiguredError:
            return RecoverySyncReport(SyncOutcome.NOT_CONFIGURED, tuple(done))
        except ProviderError as error:
            self._conditions.activate(scope, Condition.INTEGRATION_ERROR, reason=error.code.value)
            fields = {"provider": provider.provider, "reason": error.code.value, "kind": "recovery"}
            self._events.emit(EventName.INTEGRATION_SYNC_FAILED, fields)
            return RecoverySyncReport(SyncOutcome.PENDING, tuple(done), error.code.value)
        stored = sum(item.stored for item in done)
        self._events.emit(
            EventName.RECOVERY_SYNC_SUCCEEDED,
            {"provider": provider.provider, "stored": str(stored)},
        )
        return RecoverySyncReport(SyncOutcome.SYNCED, tuple(done))

    def _metric(
        self, scope: OwnerScope, provider: FitnessProvider, metric: RecoveryMetric, today: date
    ) -> MetricSync:
        known = {item.day for item in self._observations.list_all(scope) if item.metric is metric}
        settled = today - timedelta(days=SETTLING_DAYS)
        start = today - timedelta(days=BACKFILL_DAYS - 1)
        if known:
            start = max(min(max(known), settled), today - timedelta(days=MAX_DAYS - 1))
        requested = DateRange(start, today)
        report = MetricSync(metric, MetricOutcome.SYNCED, requested)
        records = self._records[provider.provider]
        for window in requested.split(provider.daily_window_days(metric)):
            if window.days == 1 and window.start in known and window.start < settled:
                continue  # a single settled day already stored (one request per day)
            payload = self._retrier.call(partial(provider.get_daily, metric, window))
            report.requests += 1
            if payload is None:
                continue
            raw = self._raw.store(
                scope,
                payload.data,
                origin=provider_origin(payload.provider),
                category=payload.category,
                detected_type=DetectedType.JSON,
                malware_scan=MalwareScan.NOT_APPLICABLE,
                provider=payload.provider,
                request_window=window,
            ).source
            try:
                values = records.decode(metric, payload)
            except ProviderError as error:
                if error.detail != FORMAT_ERROR:
                    raise
                report.rejected += 1
                continue
            for value in values:
                observation = RecoveryObservation(
                    observation_id=observation_id(
                        scope.athlete_id, value.day, metric, raw.raw_source_id
                    ),
                    athlete_id=scope.athlete_id,
                    day=value.day,
                    metric=metric,
                    value=value.value,
                    provider=payload.provider,
                    raw_source_id=raw.raw_source_id,
                    source_path=value.source_path,
                    received_at=raw.received_at,
                )
                try:
                    self._observations.add(scope, observation)
                except ConcurrencyConflictError:
                    continue  # the same payload, stored before
                report.stored += 1
        return report
