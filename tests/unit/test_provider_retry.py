"""Provider port types and the retry policy (INT-GAR-001, NFR-REL-002, INT-GAR-010)."""

from datetime import date

import pytest

from coach.application.events import EventName
from coach.application.providers import (
    ActivityRef,
    ProviderError,
    RawPayload,
)
from coach.application.retry import Retrier, RetryPolicy
from coach.domain.common.errors import DomainValidationError
from coach.domain.common.time import DateRange
from coach.domain.integration.status import IntegrationReason
from coach.infrastructure.memory import RecordingEventSink

UNAVAILABLE = IntegrationReason.PROVIDER_UNAVAILABLE
LIMITED = IntegrationReason.PROVIDER_RATE_LIMITED
AUTH = IntegrationReason.PROVIDER_AUTH_FAILED


class Script:
    """Raises the scripted errors in order, then returns ``"ok"``."""

    def __init__(self, *errors: ProviderError) -> None:
        self.errors = list(errors)
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "ok"


def retrier(**settings: float) -> tuple[Retrier, list[float], RecordingEventSink]:
    waits: list[float] = []
    events = RecordingEventSink()
    policy = RetryPolicy(**settings)  # type: ignore[arg-type]
    return Retrier(policy, events=events, sleep=waits.append, jitter=lambda: 1.0), waits, events


# ---------------------------------------------------------------- port types


@pytest.mark.req("INT-GAR-001")
def test_provider_errors_carry_codes_only() -> None:
    error = ProviderError(LIMITED, "HTTP_429", retry_after=30)
    assert (error.code, error.detail, error.retry_after, error.transient) == (
        LIMITED,
        "HTTP_429",
        30,
        True,
    )
    assert str(error) == "PROVIDER_RATE_LIMITED (HTTP_429)"
    assert not ProviderError(AUTH, "HTTP_401").transient
    assert not ProviderError(IntegrationReason.PROVIDER_PROTOCOL_ERROR).transient
    with pytest.raises(DomainValidationError):
        ProviderError(UNAVAILABLE, "body: <html>")
    with pytest.raises(DomainValidationError):
        ProviderError(UNAVAILABLE, retry_after=-1)
    with pytest.raises(DomainValidationError):
        ProviderError(UNAVAILABLE, retry_after=float("nan"))


@pytest.mark.req("INT-GAR-001", "INT-GAR-009")
def test_raw_payloads_validate_their_metadata_and_hide_their_bytes() -> None:
    window = DateRange(date(2026, 10, 1), date(2026, 10, 7))
    payload = RawPayload("garmin", "GARMIN_ACTIVITY_LIST", "application/json", b"[]", window=window)
    assert "b'[]'" not in repr(payload)
    for bad in (
        {"provider": "Garmin"},
        {"category": "list"},
        {"media_type": "json"},
        {"provider_object_id": "1/../2"},
    ):
        values = {
            "provider": "garmin",
            "category": "GARMIN_ACTIVITY",
            "media_type": "application/json",
            "data": b"{}",
            **bad,
        }
        with pytest.raises(DomainValidationError):
            RawPayload(**values)  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        ActivityRef("")
    assert ActivityRef("21004587311").provider_activity_id == "21004587311"


# ---------------------------------------------------------------- retry policy


@pytest.mark.req("NFR-REL-002")
def test_backoff_doubles_up_to_the_maximum_with_jitter() -> None:
    policy = RetryPolicy()
    assert [policy.delay(n, retry_after=None, jitter=1.0) for n in range(1, 6)] == [
        1.0,
        2.0,
        4.0,
        8.0,
        None,
    ]
    assert policy.delay(1, retry_after=None, jitter=0.0) == 0.5
    capped = RetryPolicy(max_attempts=10)
    assert capped.delay(8, retry_after=None, jitter=1.0) == 60.0


@pytest.mark.req("NFR-REL-002", "INT-GAR-010")
def test_retry_after_is_a_minimum_and_a_long_one_stops_retrying() -> None:
    policy = RetryPolicy()
    assert policy.delay(1, retry_after=10, jitter=1.0) == 10
    assert policy.delay(4, retry_after=3, jitter=1.0) == 8.0
    assert policy.delay(1, retry_after=61, jitter=1.0) is None


def test_retry_settings_are_positive() -> None:
    with pytest.raises(DomainValidationError):
        RetryPolicy(max_attempts=0)
    with pytest.raises(DomainValidationError):
        RetryPolicy(factor=0.5)


@pytest.mark.req("NFR-REL-002")
def test_transient_failures_are_retried_and_reported() -> None:
    run, waits, events = retrier()
    operation = Script(
        ProviderError(UNAVAILABLE, "HTTP_503"), ProviderError(LIMITED, retry_after=5)
    )
    assert run.call(operation) == "ok"
    assert (operation.calls, waits) == (3, [1.0, 5.0])
    assert events.events == [
        (EventName.INTEGRATION_RETRY, {"reason": "PROVIDER_UNAVAILABLE", "attempt": "1"}),
        (EventName.INTEGRATION_RETRY, {"reason": "PROVIDER_RATE_LIMITED", "attempt": "2"}),
    ]


@pytest.mark.req("NFR-REL-002")
def test_non_transient_failures_are_not_retried() -> None:
    run, waits, _ = retrier()
    operation = Script(ProviderError(AUTH, "HTTP_401"))
    with pytest.raises(ProviderError) as failed:
        run.call(operation)
    assert (failed.value.code, operation.calls, waits) == (AUTH, 1, [])


@pytest.mark.req("NFR-REL-002")
def test_attempts_are_bounded() -> None:
    run, waits, _ = retrier()
    operation = Script(*(ProviderError(UNAVAILABLE) for _ in range(9)))
    with pytest.raises(ProviderError):
        run.call(operation)
    assert operation.calls == 5
    assert waits == [1.0, 2.0, 4.0, 8.0]


@pytest.mark.req("INT-GAR-010")
def test_a_long_retry_after_fails_at_once() -> None:
    run, waits, _ = retrier()
    operation = Script(ProviderError(LIMITED, "HTTP_429", retry_after=3600))
    with pytest.raises(ProviderError) as failed:
        run.call(operation)
    assert (failed.value.retry_after, operation.calls, waits) == (3600, 1, [])
