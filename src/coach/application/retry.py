"""Retry strategy for provider calls (NFR-REL-002; garmin spec section 7).

Transient failures are retried with exponential backoff and jitter: base 1 s, factor 2, at
most 5 attempts, at most 60 s between attempts. ``Retry-After`` is the minimum wait; when it
is longer than the maximum delay the call fails at once, so the sync is postponed instead of
blocking. Every retry emits an event; nothing is swallowed.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass, fields

from coach.application.events import EventName
from coach.application.ports import EventSink
from coach.application.providers import ProviderError
from coach.domain.common.errors import DomainValidationError


@dataclass(frozen=True, slots=True, kw_only=True)
class RetryPolicy:
    base_seconds: float = 1.0
    factor: float = 2.0
    max_attempts: int = 5
    max_delay_seconds: float = 60.0

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if isinstance(value, bool) or not value > 0:
                raise DomainValidationError(f"retry setting {item.name} must be positive")
        if self.factor < 1:
            raise DomainValidationError("the backoff factor is at least 1")

    def delay(self, attempt: int, *, retry_after: float | None, jitter: float) -> float | None:
        """Seconds to wait after failed ``attempt`` (1-based); ``None``: do not retry."""
        if attempt >= self.max_attempts:
            return None
        if retry_after is not None and retry_after > self.max_delay_seconds:
            return None
        backoff = min(self.max_delay_seconds, self.base_seconds * self.factor ** (attempt - 1))
        jittered = backoff * (0.5 + 0.5 * min(max(jitter, 0.0), 1.0))
        return max(jittered, retry_after or 0.0)


class Retrier:
    def __init__(
        self,
        policy: RetryPolicy,
        *,
        events: EventSink,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self.policy = policy
        self._events = events
        self._sleep = sleep
        self._jitter = jitter

    def call[T](self, operation: Callable[[], T]) -> T:
        attempt = 1
        while True:
            try:
                return operation()
            except ProviderError as error:
                if not error.transient:
                    raise
                wait = self.policy.delay(
                    attempt, retry_after=error.retry_after, jitter=self._jitter()
                )
                if wait is None:
                    raise
                self._events.emit(
                    EventName.INTEGRATION_RETRY,
                    {"reason": error.code.value, "attempt": str(attempt)},
                )
                self._sleep(wait)
                attempt += 1
