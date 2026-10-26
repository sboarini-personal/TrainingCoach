"""Garmin Personal adapter: token broker, requests, error mapping, budget (garmin spec 3, 7, 11)."""

from __future__ import annotations

import base64
from datetime import date, timedelta
from email.utils import format_datetime
from uuid import UUID

import pytest

from coach.application.credentials import CredentialKind, CredentialService
from coach.application.ownership import OwnerScope
from coach.application.providers import (
    ActivityRef,
    ProviderError,
    ProviderNotConfiguredError,
)
from coach.application.secrets import SecretValue
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.time import DateRange
from coach.domain.integration.capabilities import Capability, CapabilityValue
from coach.domain.integration.status import IntegrationReason
from coach.infrastructure.memory import (
    InMemoryAuditLog,
    InMemoryCredentialProvider,
    InMemoryRepository,
    RecordingEventSink,
)
from coach.integrations.garmin.http import retry_after
from coach.integrations.garmin.provider import (
    SERVERS,
    GarminPersonalProvider,
    credential_key,
    parse_activity_list,
)
from coach.integrations.garmin.throttle import RequestBudget
from coach.integrations.garmin.token import RENEW_MARGIN, TOKEN_PATH, jwt_expiry
from coach.integrations.garmin.transport import TransportFailure
from garmin_samples import ACTIVITY_ID, SYNTHETIC_KEY, jwt, list_page, token_for
from garmin_world import BASE, FakeTime, GarminWorld, response
from samples import ATHLETE

WINDOW = DateRange(date(2026, 10, 14), date(2026, 10, 20))
REF = ActivityRef(str(ACTIVITY_ID))
_R = IntegrationReason


def unsigned(claims: bytes, *, suffix: bytes = b"") -> bytes:
    """A token-shaped value built at run time from its claims (no literal tokens in code)."""
    header = base64.urlsafe_b64encode(b'{"alg": "RS256"}').rstrip(b"=")
    payload = base64.urlsafe_b64encode(claims).rstrip(b"=")
    return header + b"." + payload + suffix + b".c2ln"


def failure(call: object) -> tuple[IntegrationReason, str | None, float | None]:
    assert callable(call)
    with pytest.raises(ProviderError) as failed:
        call()
    return failed.value.code, failed.value.detail, failed.value.retry_after


# ---------------------------------------------------------------- token broker


@pytest.mark.req("INT-GAR-006", "SEC-SEC-001")
def test_the_key_is_exchanged_at_the_documented_endpoint_and_the_token_kept_in_memory() -> None:
    world = GarminWorld()
    token = world.broker.token()
    (sent,) = world.transport.sent
    assert (sent.method, sent.url) == ("POST", BASE + TOKEN_PATH)
    assert sent.headers == {"Accept": "*/*", "Authorization": f"ApiKey {SYNTHETIC_KEY}"}
    assert isinstance(token, SecretValue)
    assert str(token) == "***"
    assert world.broker.token() is token
    assert len(world.transport.sent) == 1


@pytest.mark.req("INT-GAR-006")
def test_the_token_is_renewed_when_less_than_an_hour_remains() -> None:
    world = GarminWorld()
    first = world.broker.token()
    world.time.advance(hours=22, minutes=59)
    assert world.broker.token() is first
    world.time.advance(minutes=2)
    assert world.broker.token() is not first
    assert world.transport.paths() == [TOKEN_PATH, TOKEN_PATH]
    assert timedelta(hours=1) == RENEW_MARGIN
    world.broker.invalidate()
    world.broker.token()
    assert len(world.transport.sent) == 3


@pytest.mark.req("INT-GAR-006")
@pytest.mark.parametrize(
    "body",
    [
        b"not a token",
        b"a.b",
        b"a..c",
        b"\xff\xfe.x.y",
        jwt(expires=FakeTime().now - timedelta(seconds=1)).encode(),
        unsigned(b'{"sub": "x"}'),  # no exp
        unsigned(b'{"exp": "soon"}'),  # exp is text
        unsigned(b"[]"),  # claims are not an object
        unsigned(b'{"exp": 1}', suffix=b"\r\nX-Injected: 1"),  # header injection
        unsigned(b'{"exp": 100000000000000000000}'),  # exp too large
    ],
)
def test_a_response_that_is_not_a_valid_jwt_is_a_protocol_error(body: bytes) -> None:
    world = GarminWorld()
    world.transport.script(TOKEN_PATH, response(200, body))
    assert failure(world.broker.token) == (_R.PROVIDER_PROTOCOL_ERROR, "TOKEN_FORMAT", None)


@pytest.mark.req("INT-GAR-006", "INT-GAR-008")
@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (response(401), (_R.PROVIDER_AUTH_FAILED, "KEY_REJECTED", None)),
        (response(403), (_R.PROVIDER_AUTH_FAILED, "KEY_REJECTED", None)),
        (response(503), (_R.PROVIDER_UNAVAILABLE, "HTTP_5XX", None)),
        (response(429, retry_after="30"), (_R.PROVIDER_RATE_LIMITED, "HTTP_429", 30.0)),
        (TransportFailure.TIMEOUT, (_R.PROVIDER_UNAVAILABLE, "TIMEOUT", None)),
        (TransportFailure.NETWORK, (_R.PROVIDER_UNAVAILABLE, "NETWORK", None)),
    ],
)
def test_token_endpoint_failures_are_typed(
    reply: object, expected: tuple[IntegrationReason, str, float | None]
) -> None:
    world = GarminWorld()
    world.transport.script(TOKEN_PATH, reply)  # type: ignore[arg-type]
    assert failure(world.broker.token) == expected


@pytest.mark.req("INT-GAR-006")
def test_a_short_lived_token_is_used_for_half_its_life() -> None:
    world = GarminWorld()
    world.transport.script(TOKEN_PATH, response(200, token_for(world.time.now, hours=0.5).encode()))
    first = world.broker.token()
    world.time.advance(minutes=14)
    assert world.broker.token() is first
    world.time.advance(minutes=2)
    assert world.broker.token() is not first


def test_jwt_expiry_reads_only_the_exp_claim() -> None:
    moment = FakeTime().now + timedelta(hours=24)
    assert jwt_expiry(jwt(expires=moment)) == moment.replace(microsecond=0)


# ---------------------------------------------------------------- key source


def credentials() -> tuple[CredentialService, FakeTime]:
    time = FakeTime()
    counter = iter(range(1, 100))
    service = CredentialService(
        InMemoryCredentialProvider(),
        InMemoryRepository(),
        audit=InMemoryAuditLog(),
        events=RecordingEventSink(),
        clock=time.clock,
        ids=lambda: UUID(int=next(counter)),
    )
    return service, time


@pytest.mark.req("INT-GAR-006", "SEC-SEC-004")
def test_the_key_comes_from_the_secret_store_and_an_expired_key_is_not_sent() -> None:
    service, time = credentials()
    scope = OwnerScope(ATHLETE)
    source = credential_key(service, scope)
    with pytest.raises(ProviderNotConfiguredError):
        source()
    service.store(
        scope,
        CredentialKind.GARMIN_PERSONAL_API_KEY,
        SecretValue(SYNTHETIC_KEY),
        expires_on=time.now.date() + timedelta(days=30),
    )
    assert source().reveal() == SYNTHETIC_KEY
    for bad in ("two words", "line\nbreak", "acentuada-á"):
        service.store(scope, CredentialKind.GARMIN_PERSONAL_API_KEY, SecretValue(bad))
        assert failure(source) == (_R.PROVIDER_AUTH_FAILED, "KEY_FORMAT", None)
    service.store(
        scope,
        CredentialKind.GARMIN_PERSONAL_API_KEY,
        SecretValue(SYNTHETIC_KEY),
        expires_on=time.now.date() + timedelta(days=30),
    )
    time.advance(days=31)
    assert failure(source) == (_R.PROVIDER_AUTH_FAILED, "KEY_EXPIRED", None)


# ---------------------------------------------------------------- requests


@pytest.mark.req("INT-GAR-005", "INT-GAR-007")
def test_a_window_is_listed_with_documented_parameters_and_a_bearer_token() -> None:
    world = GarminWorld()
    world.transport.activities["2026-10-15"] = [11, 12, 13]
    page = world.provider.get_activities(WINDOW, 1, 20)
    assert [ref.provider_activity_id for ref in page.refs] == ["12", "13"]
    (listing,) = world.requests_to("/activitylist-service/")
    assert listing.url.startswith(BASE + "/activitylist-service/activities/search/activities?")
    assert listing.query == {
        "start": ["1"],
        "limit": ["20"],
        "startDate": ["2026-10-14"],
        "endDate": ["2026-10-20"],
        "sortOrder": ["asc"],
    }
    assert listing.headers["Accept"] == "application/json"
    assert listing.headers["Authorization"].startswith("Bearer ")
    assert (page.payload.category, page.payload.window, page.payload.media_type) == (
        "GARMIN_ACTIVITY_LIST",
        WINDOW,
        "application/json",
    )
    assert page.payload.data == list_page(12, 13)


@pytest.mark.req("INT-GAR-005", "INT-GAR-009")
def test_details_and_files_are_raw_payloads_named_by_the_activity() -> None:
    world = GarminWorld()
    details = world.provider.get_activity_details(REF)
    original = world.provider.get_activity_file(REF)
    assert details is not None
    assert original is not None
    assert (details.category, details.media_type, details.provider_object_id) == (
        "GARMIN_ACTIVITY",
        "application/json",
        str(ACTIVITY_ID),
    )
    assert (original.category, original.media_type, original.provider_object_id) == (
        "GARMIN_ACTIVITY_FILE",
        "application/x-zip-compressed",
        str(ACTIVITY_ID),
    )
    assert world.transport.paths()[1:] == [
        f"/activity-service/activity/{ACTIVITY_ID}",
        f"/download-service/files/activity/{ACTIVITY_ID}",
    ]


@pytest.mark.req("INT-GAR-005")
def test_a_missing_activity_or_file_is_none() -> None:
    world = GarminWorld()
    world.transport.script(f"/activity-service/activity/{ACTIVITY_ID}", response(404))
    world.transport.script(f"/download-service/files/activity/{ACTIVITY_ID}", response(404))
    assert world.provider.get_activity_details(REF) is None
    assert world.provider.get_activity_file(REF) is None
    world.transport.script(f"/download-service/files/activity/{ACTIVITY_ID}", response(200))
    assert world.provider.get_activity_file(REF) is None


@pytest.mark.req("INT-GAR-006")
def test_a_401_renews_the_token_once() -> None:
    world = GarminWorld()
    path = f"/activity-service/activity/{ACTIVITY_ID}"
    world.transport.script(path, response(401))
    assert world.provider.get_activity_details(REF) is not None
    assert world.transport.paths() == [TOKEN_PATH, path, TOKEN_PATH, path]
    world.transport.script(path, response(401), response(401))
    assert failure(lambda: world.provider.get_activity_details(REF)) == (
        _R.PROVIDER_AUTH_FAILED,
        "HTTP_401",
        None,
    )


@pytest.mark.req("INT-GAR-008", "INT-GAR-010", "NFR-REL-002")
@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (response(429, retry_after="120"), (_R.PROVIDER_RATE_LIMITED, "HTTP_429", 120.0)),
        (response(429), (_R.PROVIDER_RATE_LIMITED, "HTTP_429", None)),
        (response(503, retry_after="5"), (_R.PROVIDER_UNAVAILABLE, "HTTP_5XX", 5.0)),
        (response(500), (_R.PROVIDER_UNAVAILABLE, "HTTP_5XX", None)),
        (response(408), (_R.PROVIDER_UNAVAILABLE, "HTTP_408", None)),
        (response(403), (_R.PROVIDER_AUTH_FAILED, "HTTP_403", None)),
        (response(400), (_R.PROVIDER_PROTOCOL_ERROR, "HTTP_4XX", None)),
        (
            response(302, location="https://elsewhere.example"),
            (
                _R.PROVIDER_PROTOCOL_ERROR,
                "REDIRECT",
                None,
            ),
        ),
        (TransportFailure.TIMEOUT, (_R.PROVIDER_UNAVAILABLE, "TIMEOUT", None)),
        (TransportFailure.TOO_LARGE, (_R.PROVIDER_PROTOCOL_ERROR, "RESPONSE_TOO_LARGE", None)),
    ],
)
def test_data_call_failures_are_typed(
    reply: object, expected: tuple[IntegrationReason, str, float | None]
) -> None:
    world = GarminWorld()
    world.transport.script(f"/activity-service/activity/{ACTIVITY_ID}", reply)  # type: ignore[arg-type]
    assert failure(lambda: world.provider.get_activity_details(REF)) == expected


@pytest.mark.req("INT-GAR-010")
def test_retry_after_accepts_seconds_and_http_dates() -> None:
    now = FakeTime().now
    assert retry_after("17", now=now) == 17.0
    assert retry_after(format_datetime(now + timedelta(seconds=90), usegmt=True), now=now) == 90.0
    assert retry_after(format_datetime(now - timedelta(seconds=90), usegmt=True), now=now) == 0.0
    assert retry_after("soon", now=now) is None
    assert retry_after("²", now=now) is None  # a digit to str.isdigit, not to float
    assert retry_after(None, now=now) is None
    assert retry_after("99999999", now=now) == 7 * 24 * 3600.0


@pytest.mark.req("INT-GAR-007")
@pytest.mark.parametrize(
    "body",
    [
        b"{}",
        b"[{}]",
        b'[{"activityId": "12"}]',
        b'[{"activityId": true}]',
        b'[{"activityId": 0}]',
        b'[{"activityId": 12.5}]',
        b'[{"activityId": 99999999999999999999}]',
        b"[NaN]",
        b"not json",
    ],
)
def test_a_list_page_must_hold_integer_activity_ids(body: bytes) -> None:
    with pytest.raises(ProviderError) as failed:
        parse_activity_list(body)
    assert (failed.value.code, failed.value.detail) == (_R.PROVIDER_PROTOCOL_ERROR, "LIST_FORMAT")
    assert parse_activity_list(b"[]") == ()


@pytest.mark.req("INT-GAR-005", "SEC-SEC-003")
def test_only_documented_servers_and_numeric_ids_are_used() -> None:
    world = GarminWorld()
    with pytest.raises(ValueError, match="documented"):
        GarminPersonalProvider(
            broker=world.broker,
            transport=world.transport,
            budget=world.budget,
            clock=world.time.clock,
            base_url="https://attacker.example",
        )
    assert set(SERVERS.values()) == {
        "https://personal.connectapi.garmin.com",
        "https://personal.connectapi.garmin.cn",
    }
    with pytest.raises(DomainValidationError):
        ActivityRef("1;rm")
    assert failure(lambda: world.provider.get_activity_details(ActivityRef("abc-1"))) == (
        _R.PROVIDER_PROTOCOL_ERROR,
        "ACTIVITY_ID",
        None,
    )
    world.provider.get_activity_details(REF)
    for sent in world.transport.sent:
        assert SYNTHETIC_KEY not in sent.url
        assert "Bearer" not in sent.url


@pytest.mark.req("INT-GAR-005", "INT-GAR-003")
def test_the_adapter_reports_the_configured_personal_capabilities() -> None:
    capabilities = GarminWorld().provider.get_capabilities()
    assert capabilities.value(Capability.TRAINING_PUBLISH) is CapabilityValue.UNSUPPORTED
    assert capabilities.value(Capability.HISTORICAL_DATA) is CapabilityValue.UNKNOWN
    assert capabilities.supports(Capability.ACTIVITY_READ)


@pytest.mark.req("INT-GAR-010")
def test_the_request_budget_allows_a_burst_then_one_per_second() -> None:
    time = FakeTime()
    budget = RequestBudget(monotonic=time.monotonic, sleep=time.sleep)
    for _ in range(5):
        budget.acquire()
    assert time.slept == []
    budget.acquire()
    budget.acquire()
    assert time.slept == pytest.approx([1.0, 1.0])
    time.advance(seconds=10)
    for _ in range(5):
        budget.acquire()
    assert len(time.slept) == 2
    with pytest.raises(DomainValidationError):
        RequestBudget(rate_per_second=0)


@pytest.mark.req("INT-GAR-010")
def test_every_request_spends_the_budget() -> None:
    time = FakeTime()
    world = GarminWorld(budget=RequestBudget(burst=1, monotonic=time.monotonic, sleep=time.sleep))
    world.provider.get_activity_details(REF)
    assert time.slept == pytest.approx([1.0])  # token exchange, then the details call
    assert world.transport.paths() == [TOKEN_PATH, f"/activity-service/activity/{ACTIVITY_ID}"]
    assert token_for(time.now)
