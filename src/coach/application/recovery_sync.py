"""Daily recovery synchronisation over the FitnessProvider port (recovery spec 5; P11).

For each recovery metric whose consent is granted (FR-REC-005; a metric without it is
neither fetched nor stored), the provider's daily payloads are requested in windows the
provider allows and decoded in memory by the provider's daily normaliser. A provider payload
carries more than its metric, data of other consent categories included, so it is never
stored: what is stored as RAW is a minimised document of the values read, with the request
window (contract ``recovery-payload``, ADR-032). A new value for a day and metric becomes an
immutable observation; a value equal to the one in force is not stored again.

The first sync reaches back over the baseline window (28 days, recovery spec 3); later ones
start at the last day requested for the metric (kept in the integration status), less two
days whose values may still settle, so days without data are not asked again. A payload the
normaliser cannot read, a response over the size cap or another HTTP 4xx is counted as
unreadable and skipped. Any other provider failure after the retry policy ends the sync
PENDING: the integration status records it, with the postponement a rate limit asks for, and
INTEGRATION_ERROR is activated; nothing is fabricated to fill the gap.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
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
    DailyValue,
    FitnessProvider,
    ProviderError,
    ProviderNotConfiguredError,
    RawPayload,
)
from coach.application.raw_storage import RawStorage
from coach.application.retry import Retrier
from coach.application.sync import (
    SUCCESS_REASON,
    CapabilityUnavailableError,
    SyncOutcome,
    current_status,
    postponement,
)
from coach.domain.coaching.state import Condition
from coach.domain.common.time import DateRange
from coach.domain.integration.capabilities import Feature
from coach.domain.integration.status import IntegrationReason, IntegrationStatus
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
MINIMISATION_VERSION: Final = "1.0.0"
MINIMISED_SUFFIX: Final = "_MINIMISED"
# Protocol failures of one response: counted as unreadable, the sync goes on.
SKIPPED_DETAILS: Final = frozenset({"DAILY_FORMAT", "RESPONSE_TOO_LARGE", "HTTP_4XX"})


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
    rejected: int = 0  # responses that could not be read


@dataclass(frozen=True, slots=True)
class RecoverySyncReport:
    outcome: SyncOutcome
    metrics: tuple[MetricSync, ...] = field(default=())
    error: str | None = None  # the failure's code, when PENDING
    not_before: datetime | None = None  # when PENDING after a rate limit


def minimised_document(
    metric: RecoveryMetric, payload: RawPayload, values: Sequence[DailyValue]
) -> bytes:
    """The RAW kept for a daily payload: the values read and the request, nothing else.

    Canonical JSON (sorted keys, no spaces), so the same values for the same request are the
    same RAW (content-addressed)."""
    document = {
        "schemaVersion": "1.0.0",
        "minimisation": MINIMISATION_VERSION,
        "provider": payload.provider,
        "category": payload.category,
        "metric": metric.value,
        "window": None
        if payload.window is None
        else {"start": payload.window.start.isoformat(), "end": payload.window.end.isoformat()},
        "values": [
            {"day": value.day.isoformat(), "value": value.value, "sourcePath": value.source_path}
            for value in sorted(values, key=lambda item: item.day)
        ],
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _ordered(*items: MetricSync) -> tuple[MetricSync, ...]:
    order = list(RecoveryMetric)
    return tuple(sorted(items, key=lambda item: order.index(item.metric)))


class RecoverySyncService:
    def __init__(
        self,
        *,
        providers: Callable[[OwnerScope], FitnessProvider],
        records: Mapping[str, DailyRecords],
        observations: OwnedRepository[RecoveryObservation],
        statuses: OwnedRepository[IntegrationStatus],
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
        self._statuses = statuses
        self._raw = RawStorage(blobs, raw_sources, clock=clock, ids=ids)
        self._consents = consents
        self._conditions = conditions
        self._retrier = retrier
        self._timezone_of = timezone_of
        self._events = events
        self._clock = clock
        self._ids = ids

    def sync(self, scope: OwnerScope) -> RecoverySyncReport:
        provider = self._providers(scope)
        granted = self._consents.granted(scope)
        consented = [metric for metric in RecoveryMetric if METRICS[metric].consent in granted]
        skipped = tuple(
            MetricSync(metric, MetricOutcome.NO_CONSENT)
            for metric in RecoveryMetric
            if metric not in consented
        )
        if not consented:
            return RecoverySyncReport(SyncOutcome.SYNCED, skipped)
        status = current_status(self._statuses, scope, provider, self._ids)
        missing = status.capabilities.missing_for(Feature.RECOVERY_SYNC)
        if missing:
            raise CapabilityUnavailableError(missing)
        now = self._clock()
        if status.postponed(now):
            code = None if status.reason is None else status.reason.value
            return RecoverySyncReport(SyncOutcome.PENDING, skipped, code, status.not_before)
        today = now.astimezone(self._timezone_of(scope)).date()
        status = self._save(scope, status, status.started(now))
        done: list[MetricSync] = []
        try:
            for metric in consented:
                report, status = self._metric(scope, provider, status, metric, today)
                done.append(report)
        except ProviderNotConfiguredError:
            return RecoverySyncReport(SyncOutcome.NOT_CONFIGURED, _ordered(*done, *skipped))
        except ProviderError as error:
            return self._failed(scope, provider, status, error, _ordered(*done, *skipped))
        self._save(scope, status, status.succeeded(self._clock()))
        self._conditions.clear(scope, Condition.INTEGRATION_ERROR, reason=SUCCESS_REASON)
        stored = sum(item.stored for item in done)
        self._events.emit(
            EventName.RECOVERY_SYNC_SUCCEEDED,
            {"provider": provider.provider, "stored": str(stored)},
        )
        return RecoverySyncReport(SyncOutcome.SYNCED, _ordered(*done, *skipped))

    def _metric(
        self,
        scope: OwnerScope,
        provider: FitnessProvider,
        status: IntegrationStatus,
        metric: RecoveryMetric,
        today: date,
    ) -> tuple[MetricSync, IntegrationStatus]:
        through = status.daily(metric.value)
        start = today - timedelta(days=BACKFILL_DAYS - 1)
        if through is not None:
            start = max(
                min(through, today) - timedelta(days=SETTLING_DAYS),
                today - timedelta(days=MAX_DAYS - 1),
            )
        requested = DateRange(start, today)
        report = MetricSync(metric, MetricOutcome.SYNCED, requested)
        current = {
            item.day: item
            for item in sorted(
                (item for item in self._observations.list_all(scope) if item.metric is metric),
                key=lambda entry: (entry.received_at, str(entry.observation_id)),
            )
        }
        for window in requested.split(provider.daily_window_days(metric)):
            report.requests += 1
            try:
                self._window(scope, provider, metric, window, current, report)
            except ProviderError as error:
                skippable = error.code is IntegrationReason.PROVIDER_PROTOCOL_ERROR
                if not skippable or error.detail not in SKIPPED_DETAILS:
                    raise
                report.rejected += 1
            # saved after every window: an interrupted backfill resumes where it stopped
            status = self._save(scope, status, status.with_daily(metric.value, window.end))
        return report, status

    def _window(
        self,
        scope: OwnerScope,
        provider: FitnessProvider,
        metric: RecoveryMetric,
        window: DateRange,
        current: dict[date, RecoveryObservation],
        report: MetricSync,
    ) -> None:
        payload = self._retrier.call(partial(provider.get_daily, metric, window))
        if payload is None:
            return
        received_at = self._clock()
        values = self._records[provider.provider].decode(metric, payload)
        fresh = [
            value
            for value in values
            if value.day not in current or current[value.day].value != value.value
        ]
        if not fresh:
            return
        raw = self._raw.store(
            scope,
            minimised_document(metric, payload, values),
            origin=provider_origin(payload.provider),
            category=payload.category + MINIMISED_SUFFIX,
            detected_type=DetectedType.JSON,
            malware_scan=MalwareScan.NOT_APPLICABLE,
            provider=payload.provider,
            request_window=window,
        ).source
        for value in fresh:
            observation = RecoveryObservation(
                observation_id=observation_id(
                    scope.athlete_id, value.day, metric, raw.raw_source_id, received_at
                ),
                athlete_id=scope.athlete_id,
                day=value.day,
                metric=metric,
                value=value.value,
                provider=payload.provider,
                raw_source_id=raw.raw_source_id,
                source_path=value.source_path,
                received_at=received_at,
            )
            try:
                current[value.day] = self._observations.add(scope, observation)
            except ConcurrencyConflictError:
                continue  # stored by a concurrent sync
            report.stored += 1

    def _save(
        self, scope: OwnerScope, status: IntegrationStatus, changed: IntegrationStatus
    ) -> IntegrationStatus:
        if changed == status:
            return status
        return self._statuses.update(scope, changed, expected_version=status.version)

    def _failed(
        self,
        scope: OwnerScope,
        provider: FitnessProvider,
        status: IntegrationStatus,
        error: ProviderError,
        done: tuple[MetricSync, ...],
    ) -> RecoverySyncReport:
        now = self._clock()
        not_before = postponement(error, now=now, longest=self._retrier.policy.max_delay_seconds)
        latest = self._statuses.get(scope, status.integration_id)  # cursors saved meanwhile
        self._save(
            scope, latest, latest.failed(now, error.code, error.detail, not_before=not_before)
        )
        self._conditions.activate(scope, Condition.INTEGRATION_ERROR, reason=error.code.value)
        fields = {"provider": provider.provider, "reason": error.code.value, "kind": "recovery"}
        if error.detail is not None:
            fields["detail"] = error.detail
        self._events.emit(EventName.INTEGRATION_SYNC_FAILED, fields)
        return RecoverySyncReport(SyncOutcome.PENDING, done, error.code.value, not_before)
