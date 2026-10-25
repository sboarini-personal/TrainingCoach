"""Local token authentication, lockout and authorization chain (AT-AUTH-001)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from coach.application.authentication import (
    AuthenticationError,
    AuthenticationService,
    FailedAuthLockout,
    LocalTokenAuthenticator,
    RateLimitedError,
)
from coach.application.authorization import AuthorizationError, AuthorizationService
from coach.application.credentials import CredentialKind, credential_handle, generate_local_token
from coach.application.events import EventName
from coach.application.identity import Identity, Principal
from coach.application.ownership import OwnerScope
from coach.application.secrets import SecretValue
from coach.infrastructure.memory import (
    InMemoryAuditLog,
    InMemoryCredentialProvider,
    InMemoryIdentityDirectory,
    RecordingEventSink,
)
from samples import ATHLETE

USER = UUID("0193b2a0-0000-7000-8000-0000000000a1")
START = datetime(2026, 10, 25, 10, 0, tzinfo=UTC)
TOKEN = SecretValue("synthetic-local-token-value-for-tests-0001")  # pragma: allowlist secret
WRONG = SecretValue("synthetic-wrong-token-value-for-tests-0002")  # pragma: allowlist secret


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def world() -> tuple[
    AuthenticationService,
    AuthorizationService,
    InMemoryCredentialProvider,
    InMemoryAuditLog,
    RecordingEventSink,
    Clock,
]:
    identities = InMemoryIdentityDirectory()
    identities.register_owner(Identity(user_id=USER, athlete_id=ATHLETE, created_at=START))
    credentials = InMemoryCredentialProvider()
    credentials.put(credential_handle(CredentialKind.LOCAL_API_TOKEN, ATHLETE), TOKEN)
    audit, events, clock = InMemoryAuditLog(), RecordingEventSink(), Clock()
    authentication = AuthenticationService(
        LocalTokenAuthenticator(credentials, identities),
        FailedAuthLockout(),
        audit=audit,
        events=events,
        clock=clock,
    )
    authorization = AuthorizationService(identities, audit=audit, events=events, clock=clock)
    return authentication, authorization, credentials, audit, events, clock


World = tuple[
    AuthenticationService,
    AuthorizationService,
    InMemoryCredentialProvider,
    InMemoryAuditLog,
    RecordingEventSink,
    Clock,
]


@pytest.mark.req("SEC-AUTH-001", "SEC-AUTHZ-001")
@pytest.mark.acceptance("AT-AUTH-001")
def test_correct_token_yields_the_owner_scope(world: World) -> None:
    authentication, authorization, *_ = world
    principal = authentication.authenticate("cli", TOKEN)
    assert principal == Principal(USER)
    assert authorization.scope_for(principal) == OwnerScope(ATHLETE)


@pytest.mark.req("SEC-AUTH-001")
@pytest.mark.acceptance("AT-AUTH-001")
@pytest.mark.parametrize("presented", [None, WRONG], ids=["missing", "wrong"])
def test_missing_or_wrong_token_fails_generically(
    world: World, presented: SecretValue | None
) -> None:
    authentication, _, _, audit, events, _ = world
    with pytest.raises(AuthenticationError) as error:
        authentication.authenticate("cli", presented)
    assert str(error.value) == "authentication failed"
    assert events.names() == [EventName.AUTHN_FAILED]
    (record,) = audit.all_records()
    assert (record.document["action"], record.document["outcome"]) == (
        "auth.authenticate",
        "FAILED",
    )


@pytest.mark.req("SEC-AUTH-001", "SEC-SEC-004")
@pytest.mark.acceptance("AT-AUTH-001")
def test_rotated_token_stops_working_immediately(world: World) -> None:
    authentication, _, credentials, *_ = world
    new_token = generate_local_token()
    credentials.put(credential_handle(CredentialKind.LOCAL_API_TOKEN, ATHLETE), new_token)
    with pytest.raises(AuthenticationError):
        authentication.authenticate("cli", TOKEN)
    assert authentication.authenticate("cli", new_token) == Principal(USER)


def test_generated_tokens_have_256_bits_and_differ() -> None:
    first, second = generate_local_token(), generate_local_token()
    assert len(first.reveal()) == 43
    assert first != second


@pytest.mark.req("SEC-AUTH-001")
def test_without_an_owner_or_token_nothing_authenticates() -> None:
    identities = InMemoryIdentityDirectory()
    credentials = InMemoryCredentialProvider()
    authenticator = LocalTokenAuthenticator(credentials, identities)
    assert authenticator.verify(TOKEN) is None
    identities.register_owner(Identity(user_id=USER, athlete_id=ATHLETE, created_at=START))
    assert authenticator.verify(TOKEN) is None


# ------------------------------------------------------------------ lockout


@pytest.mark.req("SEC-AUTH-003")
@pytest.mark.acceptance("AT-AUTH-001")
def test_five_failures_lock_the_client_without_checking_the_token(world: World) -> None:
    authentication, _, _, audit, events, clock = world
    for _ in range(5):
        with pytest.raises(AuthenticationError):
            authentication.authenticate("client-a", WRONG)
        clock.advance(5)
    with pytest.raises(RateLimitedError) as limited:
        authentication.authenticate("client-a", TOKEN)
    assert 0 < limited.value.retry_after_seconds <= 60
    assert events.names()[-1] is EventName.RATE_LIMITED
    assert len(audit.all_records()) == 5
    assert authentication.authenticate("client-b", TOKEN) == Principal(USER)


@pytest.mark.req("SEC-AUTH-003")
@pytest.mark.acceptance("AT-AUTH-001")
def test_lockouts_double_up_to_one_hour() -> None:
    lockout = FailedAuthLockout()
    now = START
    durations = []
    for _ in range(8):
        for _ in range(5):
            locked_until = lockout.record_failure("c", now)
        assert locked_until is not None
        durations.append((locked_until - now).total_seconds())
        now = locked_until
    assert durations == [60, 120, 240, 480, 960, 1920, 3600, 3600]


@pytest.mark.req("SEC-AUTH-003")
def test_failures_outside_the_window_do_not_count() -> None:
    lockout = FailedAuthLockout()
    for minute in range(5):
        assert lockout.record_failure("c", START + timedelta(seconds=61 * minute)) is None
    assert lockout.locked_until("c", START + timedelta(minutes=10)) is None


@pytest.mark.req("SEC-AUTH-003")
def test_success_resets_failures_and_level(world: World) -> None:
    authentication, _, _, _, _, clock = world
    for _ in range(4):
        with pytest.raises(AuthenticationError):
            authentication.authenticate("c", WRONG)
    authentication.authenticate("c", TOKEN)
    with pytest.raises(AuthenticationError):
        authentication.authenticate("c", WRONG)
    clock.advance(1)
    assert authentication.authenticate("c", TOKEN) == Principal(USER)


@pytest.mark.req("SEC-AUTH-003")
def test_client_identifiers_are_codes(world: World) -> None:
    authentication = world[0]
    with pytest.raises(ValueError, match="client"):
        authentication.authenticate("client with spaces", TOKEN)


# ------------------------------------------------------------------ authorization


@pytest.mark.req("SEC-AUTHZ-001")
@pytest.mark.acceptance("AT-AUTH-001")
def test_principal_without_link_is_denied_and_audited(world: World) -> None:
    _, authorization, _, audit, events, _ = world
    with pytest.raises(AuthorizationError):
        authorization.scope_for(Principal(UUID(int=99)))
    assert events.names() == [EventName.AUTHZ_DENIED]
    (record,) = audit.all_records()
    assert (record.document["action"], record.document["outcome"]) == ("auth.authorize", "DENIED")


def test_only_one_owner_per_installation() -> None:
    identities = InMemoryIdentityDirectory()
    identities.register_owner(Identity(user_id=USER, athlete_id=ATHLETE, created_at=START))
    with pytest.raises(ValueError, match="owner"):
        identities.register_owner(
            Identity(user_id=UUID(int=5), athlete_id=UUID(int=6), created_at=START)
        )
