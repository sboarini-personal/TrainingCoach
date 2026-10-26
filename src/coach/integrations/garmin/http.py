"""HTTP outcomes mapped to provider errors (garmin spec section 11). Codes only, no bodies."""

from __future__ import annotations

import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Final, overload

from coach.application.providers import ProviderError
from coach.domain.integration.status import IntegrationReason
from coach.integrations.garmin.transport import (
    HttpResponse,
    TransportError,
    TransportFailure,
)

_R = IntegrationReason
MAX_RETRY_AFTER_SECONDS: Final = 7 * 24 * 3600.0
_SECONDS: Final = re.compile(r"[0-9]{1,12}")


def retry_after(value: str | None, *, now: datetime) -> float | None:
    """Seconds from ``Retry-After`` (delta seconds or an HTTP date); ``None`` if unusable."""
    if value is None:
        return None
    text = value.strip()
    if _SECONDS.fullmatch(text):
        return min(float(text), MAX_RETRY_AFTER_SECONDS)
    try:
        moment = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        return None
    return min(max((moment - now).total_seconds(), 0.0), MAX_RETRY_AFTER_SECONDS)


@overload
def provider_error(outcome: TransportError) -> ProviderError: ...


@overload
def provider_error(outcome: HttpResponse, *, now: datetime) -> ProviderError: ...


def provider_error(
    outcome: TransportError | HttpResponse, *, now: datetime | None = None
) -> ProviderError:
    if isinstance(outcome, TransportError):
        if outcome.failure in {TransportFailure.TOO_LARGE, TransportFailure.REQUEST_INVALID}:
            return ProviderError(_R.PROVIDER_PROTOCOL_ERROR, outcome.failure.value)
        return ProviderError(_R.PROVIDER_UNAVAILABLE, outcome.failure.value)
    status = outcome.status
    wait = None if now is None else retry_after(outcome.header("retry-after"), now=now)
    if status == 429:
        return ProviderError(_R.PROVIDER_RATE_LIMITED, "HTTP_429", retry_after=wait)
    if status == 408 or 500 <= status <= 599:
        detail = "HTTP_408" if status == 408 else "HTTP_5XX"
        return ProviderError(_R.PROVIDER_UNAVAILABLE, detail, retry_after=wait)
    if status in {401, 403}:
        return ProviderError(_R.PROVIDER_AUTH_FAILED, f"HTTP_{status}")
    if 300 <= status <= 399:
        return ProviderError(_R.PROVIDER_PROTOCOL_ERROR, "REDIRECT")
    return ProviderError(_R.PROVIDER_PROTOCOL_ERROR, "HTTP_4XX" if status >= 400 else "HTTP_STATUS")
