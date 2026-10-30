"""Composition root: wires infrastructure adapters into application services."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final
from zoneinfo import ZoneInfo

from coach.application.activities import ActivityService, ActivityStores
from coach.application.activity_metrics import ActivityMetricsEngine
from coach.application.analytics import AnalyticsService
from coach.application.authentication import (
    AuthenticationService,
    FailedAuthLockout,
    LocalTokenAuthenticator,
)
from coach.application.authorization import AuthorizationService
from coach.application.coaching import CoachingConditions
from coach.application.consent import ConsentGate, ConsentService
from coach.application.contracts import (
    COACHING_STATE_CODEC,
    CONSENT_LEDGER_CODEC,
    CREDENTIAL_RECORD_CODEC,
    DATA_QUALITY_ISSUE_CODEC,
    INTEGRATION_STATUS_CODEC,
    LAPS_CODEC,
    LENGTHS_CODEC,
    PROFILE_CODEC,
    TIME_SERIES_CODEC,
    DocumentCodec,
)
from coach.application.credentials import CredentialService
from coach.application.derived_metrics import DerivedMetricService
from coach.application.errors import ApplicationError, NotFoundError
from coach.application.ids import new_id
from coach.application.ingestion import IngestionService
from coach.application.ownership import OwnerScope
from coach.application.ports import Clock, CredentialProvider
from coach.application.providers import FitnessProvider
from coach.application.retry import Retrier, RetryPolicy
from coach.application.screening import SecretScreen
from coach.application.sync import SyncService
from coach.domain.activity.fields import StreamKind
from coach.domain.activity.issues import DataQualityIssue
from coach.domain.athlete.profile import AthleteProfile
from coach.domain.athlete.settings import AnalysisSettings
from coach.domain.common.time import parse_timezone
from coach.domain.integration.status import IntegrationStatus
from coach.infrastructure.blobs import BlobIntegrityError, FileBlobStore
from coach.infrastructure.datadir import DataLayout
from coach.infrastructure.events import LoggingEventSink
from coach.infrastructure.quarantine import FileQuarantine, UnavailableMalwareScanner
from coach.infrastructure.sqlite.activities import (
    SqliteActivityRepository,
    SqliteObservationRepository,
    SqliteOverrideRepository,
)
from coach.infrastructure.sqlite.aggregates import SqliteAggregateRepository
from coach.infrastructure.sqlite.audit import SqliteAuditLog
from coach.infrastructure.sqlite.database import SqliteDatabase
from coach.infrastructure.sqlite.derived_values import SqliteDerivedValueRepository
from coach.infrastructure.sqlite.identities import SqliteIdentityDirectory
from coach.infrastructure.sqlite.ingestion import SqliteIngestionRecordRepository
from coach.infrastructure.sqlite.raw_sources import SqliteRawSourceRepository
from coach.integrations.files.formats import LocalFileFormats
from coach.integrations.files.sandbox import ParserSandbox
from coach.integrations.garmin.normalise import GarminActivityRecords
from coach.integrations.garmin.provider import (
    SERVERS,
    GarminPersonalProvider,
    credential_key,
)
from coach.integrations.garmin.throttle import RequestBudget
from coach.integrations.garmin.token import TokenBroker
from coach.integrations.garmin.transport import HttpTransport, UrllibTransport
from coach.metrics.catalog import CATALOG

PRIVACY_POLICY_VERSION: Final = "1.0"


class TimezoneRequiredError(ApplicationError):
    """The athlete's timezone is needed to estimate training days and is not set."""

    def __init__(self) -> None:
        super().__init__("athlete timezone not set")


def analysis_settings(
    profiles: SqliteAggregateRepository[AthleteProfile], scope: OwnerScope
) -> AnalysisSettings:
    """Zones, reliable power sensors and FTP from the profile (metrics spec 10); none without
    a profile."""
    try:
        return profiles.get(scope, scope.athlete_id).analysis_settings
    except NotFoundError:
        return AnalysisSettings()


def athlete_timezone(
    profiles: SqliteAggregateRepository[AthleteProfile], scope: OwnerScope
) -> ZoneInfo:
    """The profile timezone (activities spec section 2); never guessed."""
    try:
        profile = profiles.get(scope, scope.athlete_id)
    except NotFoundError:
        raise TimezoneRequiredError from None
    if profile.timezone is None or profile.timezone.value is None:
        raise TimezoneRequiredError
    return parse_timezone(profile.timezone.value)


@dataclass(frozen=True, slots=True)
class Runtime:
    layout: DataLayout
    database: SqliteDatabase
    provider: CredentialProvider
    identities: SqliteIdentityDirectory
    audit: SqliteAuditLog
    credentials: CredentialService
    consents: ConsentService
    authentication: AuthenticationService
    authorization: AuthorizationService
    profiles: SqliteAggregateRepository[AthleteProfile]
    activity_store: SqliteActivityRepository
    issue_store: SqliteAggregateRepository[DataQualityIssue]
    ingestion_records: SqliteIngestionRecordRepository
    activities: ActivityService
    ingestion: IngestionService
    analytics: AnalyticsService
    integrations: SqliteAggregateRepository[IntegrationStatus]
    conditions: CoachingConditions
    sync: SyncService
    clock: Clock


def garmin_providers(
    credentials: CredentialService, transport: HttpTransport, clock: Clock
) -> Callable[[OwnerScope], FitnessProvider]:
    """One Garmin adapter per sync: its token lives in memory for that run only."""
    budget = RequestBudget()

    def build(scope: OwnerScope) -> FitnessProvider:
        broker = TokenBroker(
            base_url=SERVERS["global"],
            key_source=credential_key(credentials, scope),
            transport=transport,
            clock=clock,
            budget=budget,
        )
        return GarminPersonalProvider(
            broker=broker, transport=transport, budget=budget, clock=clock
        )

    return build


def build_runtime(
    layout: DataLayout,
    provider: CredentialProvider,
    clock: Clock,
    *,
    transport: HttpTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Runtime:
    database = SqliteDatabase.open(layout.database)
    identities = SqliteIdentityDirectory(database)
    audit = SqliteAuditLog(database)
    events = LoggingEventSink()
    credentials = CredentialService(
        provider,
        SqliteAggregateRepository(database, CREDENTIAL_RECORD_CODEC),
        audit=audit,
        events=events,
        clock=clock,
        ids=new_id,
    )
    consents = ConsentService(
        SqliteAggregateRepository(database, CONSENT_LEDGER_CODEC),
        policy_version=PRIVACY_POLICY_VERSION,
        audit=audit,
        events=events,
        clock=clock,
        ids=new_id,
    )
    authentication = AuthenticationService(
        LocalTokenAuthenticator(provider, identities),
        FailedAuthLockout(),
        audit=audit,
        events=events,
        clock=clock,
    )
    authorization = AuthorizationService(identities, audit=audit, events=events, clock=clock)
    profiles = SqliteAggregateRepository(database, PROFILE_CODEC)
    activity_store = SqliteActivityRepository(database)
    issue_store = SqliteAggregateRepository(database, DATA_QUALITY_ISSUE_CODEC)
    raw_sources = SqliteRawSourceRepository(database)
    blobs = FileBlobStore(layout.raw)
    gate = ConsentGate(consents.ledgers, policy_version=PRIVACY_POLICY_VERSION)
    screen = SecretScreen(events)
    derived = DerivedMetricService(
        CATALOG, SqliteDerivedValueRepository(database), clock=clock, ids=new_id
    )

    codecs: dict[StreamKind, DocumentCodec[Any]] = {
        StreamKind.TIME_SERIES: TIME_SERIES_CODEC,
        StreamKind.LAPS: LAPS_CODEC,
        StreamKind.LENGTHS: LENGTHS_CODEC,
    }

    def read_stream(scope: OwnerScope, kind: StreamKind, digest: str) -> object | None:
        """A stored stream; ``None`` when its blob is missing or damaged (analyses UNKNOWN)."""
        codec = codecs.get(kind)
        if codec is None:
            return None
        try:
            content: object = codec.load(json.loads(blobs.get(scope, digest)))
        except (NotFoundError, BlobIntegrityError, ValueError, KeyError, TypeError):
            return None
        return content

    engine = ActivityMetricsEngine(
        derived,
        athlete=lambda scope: analysis_settings(profiles, scope),
        streams=read_stream,
    )
    overrides = SqliteOverrideRepository(database)
    activities = ActivityService(
        ActivityStores(
            activity_store,
            SqliteObservationRepository(database),
            overrides,
            issue_store,
            raw_sources,
        ),
        consents=gate,
        metrics=derived,
        timezone_of=lambda scope: athlete_timezone(profiles, scope),
        audit=audit,
        events=events,
        clock=clock,
        ids=new_id,
        screen=screen,
        engine=engine,
        batch=database.batch,
    )
    analytics = AnalyticsService(
        activities=activities,
        store=activity_store,
        overrides=overrides,
        metrics=derived,
        consents=gate,
        batch=database.batch,
    )
    ingestion_records = SqliteIngestionRecordRepository(database)
    ingestion = IngestionService(
        formats=LocalFileFormats(ParserSandbox(layout.quarantine)),
        quarantine=FileQuarantine(layout.quarantine),
        scanner=UnavailableMalwareScanner(),
        blobs=blobs,
        raw_sources=raw_sources,
        records=ingestion_records,
        activities=activities,
        issues=issue_store,
        consents=gate,
        screen=screen,
        events=events,
        clock=clock,
        ids=new_id,
        provider_records={"garmin": GarminActivityRecords()},
    )
    integrations = SqliteAggregateRepository(database, INTEGRATION_STATUS_CODEC)
    conditions = CoachingConditions(
        SqliteAggregateRepository(database, COACHING_STATE_CODEC),
        audit=audit,
        events=events,
        clock=clock,
    )
    sync = SyncService(
        providers=garmin_providers(credentials, transport or UrllibTransport(), clock),
        statuses=integrations,
        blobs=blobs,
        raw_sources=raw_sources,
        ingestion=ingestion,
        activities=activity_store,
        records=ingestion_records,
        consents=gate,
        conditions=conditions,
        retrier=Retrier(RetryPolicy(), events=events, sleep=sleep),
        events=events,
        clock=clock,
        ids=new_id,
    )
    return Runtime(
        layout=layout,
        database=database,
        provider=provider,
        identities=identities,
        audit=audit,
        credentials=credentials,
        consents=consents,
        authentication=authentication,
        authorization=authorization,
        profiles=profiles,
        activity_store=activity_store,
        issue_store=issue_store,
        ingestion_records=ingestion_records,
        activities=activities,
        ingestion=ingestion,
        analytics=analytics,
        integrations=integrations,
        conditions=conditions,
        sync=sync,
        clock=clock,
    )
