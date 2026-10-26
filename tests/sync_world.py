"""Sync service wiring for tests: the ingestion world plus a provider, statuses and retries."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from activity_world import SCOPE
from coach.application.coaching import CoachingConditions
from coach.application.providers import FitnessProvider
from coach.application.retry import Retrier, RetryPolicy
from coach.application.sync import SyncReport, SyncService, SyncSettings
from coach.domain.coaching.state import CoachingState
from coach.domain.integration.status import IntegrationStatus
from coach.infrastructure.memory import InMemoryRepository
from coach.integrations.garmin.normalise import GarminActivityRecords
from fake_provider import FakeProvider
from ingestion_world import IngestionWorld

SYNC_NOW = datetime(2026, 10, 26, 20, 0, tzinfo=UTC)


class SyncWorld(IngestionWorld):
    def __init__(
        self,
        root: Path,
        *,
        provider: FitnessProvider | None = None,
        settings: SyncSettings | None = None,
        consent: bool = True,
    ) -> None:
        super().__init__(
            root, consent=consent, provider_records={"garmin": GarminActivityRecords()}
        )
        self.clock.now = SYNC_NOW
        self.provider = provider if provider is not None else FakeProvider()
        self.statuses = InMemoryRepository[IntegrationStatus]()
        self.states = InMemoryRepository[CoachingState]()
        self.conditions = CoachingConditions(
            self.states, audit=self.audit, events=self.events, clock=self.clock
        )
        self.waits: list[float] = []

        def sleep(seconds: float) -> None:
            self.waits.append(seconds)
            self.clock.advance(seconds=seconds)

        self.retrier = Retrier(RetryPolicy(), events=self.events, sleep=sleep, jitter=lambda: 1.0)
        self.sync = SyncService(
            providers=lambda _scope: self.provider,
            statuses=self.statuses,
            blobs=self.blobs,
            raw_sources=self.raw,
            ingestion=self.ingestion,
            activities=self.activities,
            records=self.records,
            consents=self.consents_gate,
            conditions=self.conditions,
            retrier=self.retrier,
            events=self.events,
            clock=self.clock,
            ids=self.ids,
            settings=settings,
        )

    def run(self, **options: object) -> SyncReport:
        return self.sync.sync(SCOPE, **options)  # type: ignore[arg-type]

    @property
    def fake(self) -> FakeProvider:
        assert isinstance(self.provider, FakeProvider)
        return self.provider

    def status(self) -> IntegrationStatus:
        status = self.sync.status(SCOPE, "garmin")
        assert status is not None
        return status

    def conditions_now(self) -> frozenset[object]:
        return frozenset(self.conditions.current(SCOPE).conditions)
