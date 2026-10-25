"""Composition root: wires infrastructure adapters into application services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from zoneinfo import ZoneInfo

from coach.application.activities import ActivityService, ActivityStores
from coach.application.authentication import (
    AuthenticationService,
    FailedAuthLockout,
    LocalTokenAuthenticator,
)
from coach.application.authorization import AuthorizationService
from coach.application.consent import ConsentGate, ConsentService
from coach.application.contracts import (
    CONSENT_LEDGER_CODEC,
    CREDENTIAL_RECORD_CODEC,
    DATA_QUALITY_ISSUE_CODEC,
    PROFILE_CODEC,
)
from coach.application.credentials import CredentialService
from coach.application.derived_metrics import DerivedMetricService
from coach.application.errors import ApplicationError, NotFoundError
from coach.application.ids import new_id
from coach.application.ingestion import IngestionService
from coach.application.ownership import OwnerScope
from coach.application.ports import Clock, CredentialProvider
from coach.application.screening import SecretScreen
from coach.domain.activity.issues import DataQualityIssue
from coach.domain.athlete.profile import AthleteProfile
from coach.domain.common.time import parse_timezone
from coach.infrastructure.blobs import FileBlobStore
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
from coach.metrics.catalog import CATALOG

PRIVACY_POLICY_VERSION: Final = "1.0"


class TimezoneRequiredError(ApplicationError):
    """The athlete's timezone is needed to estimate training days and is not set."""

    def __init__(self) -> None:
        super().__init__("athlete timezone not set")


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
    clock: Clock


def build_runtime(layout: DataLayout, provider: CredentialProvider, clock: Clock) -> Runtime:
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
    activities = ActivityService(
        ActivityStores(
            activity_store,
            SqliteObservationRepository(database),
            SqliteOverrideRepository(database),
            issue_store,
            raw_sources,
        ),
        consents=gate,
        metrics=DerivedMetricService(
            CATALOG, SqliteDerivedValueRepository(database), clock=clock, ids=new_id
        ),
        timezone_of=lambda scope: athlete_timezone(profiles, scope),
        audit=audit,
        events=events,
        clock=clock,
        ids=new_id,
        screen=screen,
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
        clock=clock,
    )
