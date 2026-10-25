"""Token-bucket request budgets per principal and endpoint class (SEC-AUTH-003)."""

from __future__ import annotations

import math
import threading
from datetime import datetime

from coach.application.errors import RateLimitedError

SECONDS_PER_MINUTE = 60.0


class TokenBucketRateLimiter:
    """Capacity equals the per-minute budget; tokens refill continuously."""

    def __init__(self, *, per_minute: int) -> None:
        if per_minute <= 0:
            raise ValueError("per_minute must be positive")
        self._per_minute = per_minute
        self._capacity = float(per_minute)
        self._buckets: dict[str, tuple[float, datetime]] = {}
        self._lock = threading.Lock()

    def acquire(self, key: str, now: datetime) -> None:
        with self._lock:
            tokens, updated = self._buckets.get(key, (self._capacity, now))
            elapsed = max((now - updated).total_seconds(), 0.0)
            tokens = min(self._capacity, tokens + elapsed * self._per_minute / SECONDS_PER_MINUTE)
            if tokens < 1.0:
                self._buckets[key] = (tokens, now)
                raise RateLimitedError(
                    math.ceil((1.0 - tokens) * SECONDS_PER_MINUTE / self._per_minute)
                )
            self._buckets[key] = (tokens - 1.0, now)
