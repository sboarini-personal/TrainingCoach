"""Composition root: wires infrastructure adapters into application services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from coach.application.authentication import (
    AuthenticationService,
    FailedAuthLockout,
    LocalTokenAuthenticator,
)
from coach.application.authorization import AuthorizationService
from coach.application.consent import ConsentService
from coach.application.contracts import CONSENT_LEDGER_CODEC, CREDENTIAL_RECORD_CODEC
from coach.application.credentials import CredentialService
from coach.application.ids import new_id
from coach.application.ports import Clock, CredentialProvider
from coach.infrastructure.datadir import DataLayout
from coach.infrastructure.events import LoggingEventSink
from coach.infrastructure.sqlite.aggregates import SqliteAggregateRepository
from coach.infrastructure.sqlite.audit import SqliteAuditLog
from coach.infrastructure.sqlite.database import SqliteDatabase
from coach.infrastructure.sqlite.identities import SqliteIdentityDirectory

PRIVACY_POLICY_VERSION: Final = "1.0"


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
        clock=clock,
    )
