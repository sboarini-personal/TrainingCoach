"""In-memory wiring of the activity service, shared by the activity service tests."""

from __future__ import annotations

import itertools
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from coach.application.activities import ActivityService, ActivityStores, IngestResult
from coach.application.activity_metrics import ActivityMetricsEngine
from coach.application.consent import ConsentGate, ConsentService
from coach.application.derived_metrics import DerivedMetricService
from coach.application.ownership import OwnerScope
from coach.application.screening import SecretScreen
from coach.domain.activity.activity import Activity
from coach.domain.activity.fields import PowerSensor, StreamKind
from coach.domain.activity.issues import DataQualityIssue
from coach.domain.activity.observation import Observation, SourceKind
from coach.domain.activity.segments import Laps, Lengths
from coach.domain.activity.streams import TimeSeries
from coach.domain.athlete.experience import FtpValue
from coach.domain.athlete.physiology import HeartRateZoneModel, HeartRateZones
from coach.domain.athlete.settings import AnalysisSettings
from coach.domain.common.sport import Sport
from coach.domain.consent.ledger import ConsentLedger, ConsentType
from coach.domain.raw.source import DetectedType, MalwareScan, RawSource, SourceOrigin
from coach.infrastructure.memory import (
    InMemoryActivityRepository,
    InMemoryAuditLog,
    InMemoryDerivedValueRepository,
    InMemoryObservationRepository,
    InMemoryOverrideRepository,
    InMemoryRawSourceRepository,
    InMemoryRepository,
    RecordingEventSink,
)
from coach.metrics.catalog import CATALOG
from coach.metrics.volume import WeeklyVolume, weekly_volume
from samples import ATHLETE, BUENOS_AIRES, NOW

SCOPE = OwnerScope(ATHLETE)
_DETECTED = {
    SourceKind.FIT_FILE: DetectedType.FIT,
    SourceKind.OTHER_FILE: DetectedType.GPX,
    SourceKind.PROVIDER_RECORD: DetectedType.JSON,
    SourceKind.ATHLETE_ENTRY: DetectedType.JSON,
}


class MutableClock:
    def __init__(self, start: datetime = NOW) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


class World:
    def __init__(self, *, consent: bool = True, timezone: ZoneInfo = BUENOS_AIRES) -> None:
        self.raw = InMemoryRawSourceRepository()
        self.activities = InMemoryActivityRepository()
        self.observations = InMemoryObservationRepository()
        self.overrides = InMemoryOverrideRepository()
        self.issues = InMemoryRepository[DataQualityIssue]()
        self.stores = ActivityStores(
            self.activities, self.observations, self.overrides, self.issues, self.raw
        )
        self.derived = InMemoryDerivedValueRepository()
        self.clock = MutableClock()
        counter = itertools.count(1)
        self.ids = lambda: UUID(int=next(counter))
        self.ledgers = InMemoryRepository[ConsentLedger]()
        self.audit = InMemoryAuditLog()
        self.events = RecordingEventSink()
        self.consents = ConsentService(
            self.ledgers,
            policy_version="1.0",
            audit=self.audit,
            events=self.events,
            clock=self.clock,
            ids=self.ids,
        )
        if consent:
            self.grant(ATHLETE)
        self.metrics = DerivedMetricService(CATALOG, self.derived, clock=self.clock, ids=self.ids)
        # Zone models and time series blobs the metrics engine reads (metrics spec 9.2).
        self.zone_models: list[HeartRateZoneModel] = []
        self.birth_date: date | None = None  # for age-predicted models (ADR-028)
        self.reliable_power: set[PowerSensor] = set()  # declared reliable devices (P9)
        self.ftp: FtpValue | None = None
        self.pool_length: float | None = None  # the athlete's usual pool (resources)
        self.series: dict[str, TimeSeries] = {}
        self.series_reads: list[str] = []
        self.segments: dict[str, Laps | Lengths] = {}  # LAPS and LENGTHS blobs (P9)
        self.engine = ActivityMetricsEngine(
            self.metrics,
            athlete=lambda _scope: AnalysisSettings(
                HeartRateZones(tuple(self.zone_models), self.birth_date),
                frozenset(self.reliable_power),
                self.ftp,
                self.pool_length,
            ),
            streams=self.read_stream,
        )
        self.consents_gate = ConsentGate(self.ledgers, policy_version="1.0")
        self.service = ActivityService(
            self.stores,
            consents=self.consents_gate,
            metrics=self.metrics,
            timezone_of=lambda _: timezone,
            audit=self.audit,
            events=self.events,
            clock=self.clock,
            ids=self.ids,
            screen=SecretScreen(self.events),
            engine=self.engine,
        )

    def read_series(self, _scope: OwnerScope, digest: str) -> TimeSeries | None:
        self.series_reads.append(digest)
        return self.series.get(digest)  # a missing blob reads as None, like the runtime

    def read_stream(self, scope: OwnerScope, kind: StreamKind, digest: str) -> object | None:
        if kind is StreamKind.TIME_SERIES:
            return self.read_series(scope, digest)
        return self.segments.get(digest)

    def grant(self, athlete_id: UUID) -> None:
        self.consents.grant(
            OwnerScope(athlete_id), ConsentType.ACTIVITIES, purpose="Analyse my activities."
        )

    def register(self, observation: Observation) -> Observation:
        uploaded = observation.origin is SourceOrigin.FILE_UPLOAD
        raw = RawSource(
            raw_source_id=observation.raw_source_id,
            athlete_id=observation.athlete_id,
            origin=observation.origin,
            category="ACTIVITY",
            received_at=observation.received_at,
            sha256=observation.raw_sha256,
            byte_size=1,
            detected_type=_DETECTED[observation.kind],
            malware_scan=MalwareScan.NOT_SCANNED if uploaded else MalwareScan.NOT_APPLICABLE,
            provider=observation.provider,
            provider_object_id=observation.provider_activity_id,
        )
        self.raw.add(OwnerScope(observation.athlete_id), raw)
        return observation

    def ingest(self, observation: Observation) -> IngestResult:
        self.register(observation)
        return self.service.ingest(OwnerScope(observation.athlete_id), observation)

    def live(self) -> list[Activity]:
        return [item for item in self.activities.list_all(SCOPE) if item.is_live]

    def effective_activities(self) -> list[Activity]:
        return [
            self.service.effective(SCOPE, item.activity_id).activity
            for item in self.activities.list_all(SCOPE)
        ]

    def volume(self) -> dict[tuple[str, Sport], WeeklyVolume]:
        return dict(weekly_volume(self.effective_activities()))

    def open_issues(self) -> list[DataQualityIssue]:
        return self.service.issues(SCOPE)
