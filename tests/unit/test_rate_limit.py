"""Per-principal token-bucket rate limiting (SEC-AUTH-003)."""

from datetime import UTC, datetime, timedelta

import pytest

from coach.application.authentication import RateLimitedError
from coach.application.rate_limit import TokenBucketRateLimiter

pytestmark = pytest.mark.req("SEC-AUTH-003")

START = datetime(2026, 10, 25, 10, 0, tzinfo=UTC)


def test_budget_is_enforced_per_key() -> None:
    limiter = TokenBucketRateLimiter(per_minute=10)
    for _ in range(10):
        limiter.acquire("athlete:upload", START)
    with pytest.raises(RateLimitedError) as limited:
        limiter.acquire("athlete:upload", START)
    assert limited.value.retry_after_seconds == 6
    limiter.acquire("athlete:api", START)


def test_tokens_refill_continuously() -> None:
    limiter = TokenBucketRateLimiter(per_minute=120)
    for _ in range(120):
        limiter.acquire("k", START)
    with pytest.raises(RateLimitedError):
        limiter.acquire("k", START)
    limiter.acquire("k", START + timedelta(seconds=0.5))
    with pytest.raises(RateLimitedError):
        limiter.acquire("k", START + timedelta(seconds=0.5))


def test_idle_buckets_do_not_exceed_capacity() -> None:
    limiter = TokenBucketRateLimiter(per_minute=2)
    later = START + timedelta(hours=1)
    limiter.acquire("k", later)
    limiter.acquire("k", later)
    with pytest.raises(RateLimitedError):
        limiter.acquire("k", later)


def test_budget_must_be_positive() -> None:
    with pytest.raises(ValueError, match="per_minute"):
        TokenBucketRateLimiter(per_minute=0)
