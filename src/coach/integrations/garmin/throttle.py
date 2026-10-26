"""Client-side request budget (INT-GAR-010; garmin spec section 7).

Garmin documents no rate limits, so the adapter spends a token bucket: by default one
request per second with bursts of five. Waiting is explicit and injectable for tests.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from coach.domain.common.errors import DomainValidationError


class RequestBudget:
    def __init__(
        self,
        *,
        rate_per_second: float = 1.0,
        burst: int = 5,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not rate_per_second > 0 or burst < 1:
            raise DomainValidationError("a request budget has a positive rate and burst")
        self._rate = rate_per_second
        self._burst = float(burst)
        self._monotonic = monotonic
        self._sleep = sleep
        self._tokens = float(burst)
        self._last = monotonic()

    def acquire(self) -> None:
        """Take one request from the budget, waiting until one is available."""
        now = self._monotonic()
        self._tokens = min(self._burst, self._tokens + (now - self._last) * self._rate)
        self._last = now
        if self._tokens < 1:
            self._sleep((1 - self._tokens) / self._rate)
            self._last = self._monotonic()
            self._tokens = 1.0
        self._tokens -= 1
