"""AT-IDOR-001 at the application level: services act only within the caller's owner scope.

Two athletes share one database. Every service call for athlete A is checked never to read or
change athlete B's consents, credentials or audit trail.
"""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from coach.application.consent import ConsentGate, ConsentService
from coach.application.contracts import CONSENT_LEDGER_CODEC, CREDENTIAL_RECORD_CODEC
from coach.application.credentials import CredentialKind, CredentialService
from coach.application.errors import NotFoundError
from coach.application.ownership import OwnerScope
from coach.application.secrets import SecretValue
from coach.domain.consent.ledger import ConsentType
from coach.infrastructure.memory import InMemoryCredentialProvider, RecordingEventSink
from coach.infrastructure.sqlite.aggregates import SqliteAggregateRepository
from coach.infrastructure.sqlite.audit import SqliteAuditLog
from coach.infrastructure.sqlite.database import SqliteDatabase
from samples import ATHLETE, OTHER_ATHLETE

pytestmark = [
    pytest.mark.req("SEC-IDOR-001", "SEC-AUTHZ-001"),
    pytest.mark.acceptance("AT-IDOR-001"),
]

NOW = datetime(2026, 10, 25, 10, 0, tzinfo=UTC)
A, B = OwnerScope(ATHLETE), OwnerScope(OTHER_ATHLETE)
B_KEY = SecretValue("synthetic-key-of-athlete-b-0001")  # pragma: allowlist secret


@pytest.fixture
def services(
    tmp_path: Path,
) -> tuple[ConsentService, ConsentGate, CredentialService, SqliteAuditLog]:
    database = SqliteDatabase.open(tmp_path / "coach.sqlite3")
    audit, events = SqliteAuditLog(database), RecordingEventSink()
    counter = iter(range(1, 1000))
    ledgers = SqliteAggregateRepository(database, CONSENT_LEDGER_CODEC)
    consents = ConsentService(
        ledgers,
        policy_version="1.0",
        audit=audit,
        events=events,
        clock=lambda: NOW,
        ids=lambda: UUID(int=next(counter)),
    )
    credentials = CredentialService(
        InMemoryCredentialProvider(),
        SqliteAggregateRepository(database, CREDENTIAL_RECORD_CODEC),
        audit=audit,
        events=events,
        clock=lambda: NOW,
        ids=lambda: UUID(int=next(counter)),
    )
    consents.grant(B, ConsentType.HRV, purpose="Use HRV as a recovery signal.")
    credentials.store(B, CredentialKind.GARMIN_PERSONAL_API_KEY, B_KEY)
    return consents, ConsentGate(ledgers, policy_version="1.0"), credentials, audit


Services = tuple[ConsentService, ConsentGate, CredentialService, SqliteAuditLog]


def test_consents_of_another_athlete_are_invisible_and_untouched(services: Services) -> None:
    consents, gate, _, _ = services
    assert gate.granted(A) == frozenset()
    consents.grant(A, ConsentType.SLEEP, purpose="Use sleep as a recovery signal.")
    assert gate.granted(B) == {ConsentType.HRV}
    assert gate.granted(A) == {ConsentType.SLEEP}


def test_credentials_of_another_athlete_cannot_be_read(services: Services) -> None:
    _, _, credentials, _ = services
    with pytest.raises(NotFoundError):
        credentials.secret(A, CredentialKind.GARMIN_PERSONAL_API_KEY)
    assert credentials.status(A) == []
    assert credentials.secret(B, CredentialKind.GARMIN_PERSONAL_API_KEY) == B_KEY


def test_audit_trail_of_another_athlete_is_not_returned(services: Services) -> None:
    _, _, _, audit = services
    assert audit.events_for(A) == []
    assert {record.athlete_id for record in audit.events_for(B)} == {OTHER_ATHLETE}
