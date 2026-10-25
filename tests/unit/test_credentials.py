"""Credential storage, rotation and expiry tracking (SEC-SEC-001, SEC-SEC-004)."""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest

from coach.application.contracts import CREDENTIAL_RECORD_CODEC
from coach.application.credentials import (
    CredentialKind,
    CredentialRecord,
    CredentialService,
    ExpiryState,
    credential_handle,
    expiry_state,
)
from coach.application.errors import NotFoundError
from coach.application.events import EventName
from coach.application.ownership import OwnerScope
from coach.application.secrets import SecretValue
from coach.domain.common.errors import DomainValidationError
from coach.infrastructure.memory import (
    InMemoryAuditLog,
    InMemoryCredentialProvider,
    InMemoryRepository,
    RecordingEventSink,
)
from samples import ATHLETE
from schemas import validation_errors

SCOPE = OwnerScope(ATHLETE)
NOW = datetime(2026, 10, 25, 10, 0, tzinfo=UTC)
KEY = SecretValue("synthetic-garmin-key-value-for-tests-0001")  # pragma: allowlist secret
NEW_KEY = SecretValue("synthetic-garmin-key-value-for-tests-0002")  # pragma: allowlist secret
GARMIN = CredentialKind.GARMIN_PERSONAL_API_KEY


@pytest.fixture
def setup() -> tuple[
    CredentialService, InMemoryCredentialProvider, InMemoryAuditLog, RecordingEventSink
]:
    provider, audit, events = InMemoryCredentialProvider(), InMemoryAuditLog(), RecordingEventSink()
    counter = iter(range(1, 100))
    service = CredentialService(
        provider,
        InMemoryRepository[CredentialRecord](),
        audit=audit,
        events=events,
        clock=lambda: NOW,
        ids=lambda: UUID(int=next(counter)),
    )
    return service, provider, audit, events


Setup = tuple[CredentialService, InMemoryCredentialProvider, InMemoryAuditLog, RecordingEventSink]


@pytest.mark.req("SEC-SEC-001", "SEC-SEC-003")
def test_secret_goes_to_the_store_and_only_metadata_to_the_record(setup: Setup) -> None:
    service, provider, audit, events = setup
    record = service.store(SCOPE, GARMIN, KEY, expires_on=date(2027, 1, 23))
    assert provider.get(credential_handle(GARMIN, ATHLETE)) == KEY
    assert service.secret(SCOPE, GARMIN) == KEY
    document = CREDENTIAL_RECORD_CODEC.dump(record)
    assert validation_errors("credential-record", document) == []
    assert KEY.reveal() not in str(document)
    assert events.names() == [EventName.CREDENTIAL_STORED]
    (audited,) = audit.all_records()
    assert (audited.document["action"], audited.document["target"]["type"]) == (
        "credential.stored",
        "GARMIN_PERSONAL_API_KEY",
    )
    assert KEY.reveal() not in str(audited.document)


@pytest.mark.req("SEC-SEC-004")
def test_storing_again_is_a_rotation(setup: Setup) -> None:
    service, _provider, audit, events = setup
    first = service.store(SCOPE, GARMIN, KEY)
    second = service.store(SCOPE, GARMIN, NEW_KEY, expires_on=date(2027, 1, 23))
    assert second.credential_id == first.credential_id
    assert second.rotated_at == NOW
    assert second.version == first.version + 1
    assert service.secret(SCOPE, GARMIN) == NEW_KEY
    assert events.names() == [EventName.CREDENTIAL_STORED, EventName.CREDENTIAL_ROTATED]
    assert [record.document["action"] for record in audit.all_records()] == [
        "credential.stored",
        "credential.rotated",
    ]


@pytest.mark.req("SEC-SEC-004")
def test_local_token_rotation_returns_a_new_token(setup: Setup) -> None:
    service = setup[0]
    first = service.rotate_local_token(SCOPE)
    second = service.rotate_local_token(SCOPE)
    assert first != second
    assert service.secret(SCOPE, CredentialKind.LOCAL_API_TOKEN) == second


def test_missing_secret_is_not_found(setup: Setup) -> None:
    with pytest.raises(NotFoundError):
        setup[0].secret(SCOPE, GARMIN)


@pytest.mark.req("SEC-SEC-004")
def test_expiry_in_the_past_is_rejected(setup: Setup) -> None:
    with pytest.raises(DomainValidationError):
        setup[0].store(SCOPE, GARMIN, KEY, expires_on=date(2026, 10, 24))


@pytest.mark.req("SEC-SEC-004")
@pytest.mark.parametrize(
    ("expires_on", "state"),
    [
        (None, ExpiryState.NO_EXPIRY),
        (date(2026, 11, 2), ExpiryState.VALID),
        (date(2026, 11, 1), ExpiryState.EXPIRING),
        (date(2026, 10, 25), ExpiryState.EXPIRING),
        (date(2026, 10, 24), ExpiryState.EXPIRED),
    ],
)
def test_expiry_states(expires_on: date | None, state: ExpiryState) -> None:
    assert expiry_state(expires_on, today=date(2026, 10, 25)) is state


@pytest.mark.req("SEC-SEC-004")
def test_status_lists_every_credential_with_its_state(setup: Setup) -> None:
    service = setup[0]
    service.store(SCOPE, GARMIN, KEY, expires_on=NOW.date() + timedelta(days=3))
    service.rotate_local_token(SCOPE)
    status = {item.kind: item.state for item in service.status(SCOPE)}
    assert status == {
        GARMIN: ExpiryState.EXPIRING,
        CredentialKind.LOCAL_API_TOKEN: ExpiryState.NO_EXPIRY,
    }


@pytest.mark.req("SEC-SEC-001")
def test_handles_are_derived_never_free_form() -> None:
    assert credential_handle(GARMIN, ATHLETE) == f"garmin_personal_api_key/{ATHLETE}"
    with pytest.raises(DomainValidationError):
        CredentialRecord(
            credential_id=UUID(int=1),
            athlete_id=ATHLETE,
            kind=GARMIN,
            created_at=NOW,
            rotated_at=NOW - timedelta(days=1),
        )
