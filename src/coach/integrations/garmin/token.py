"""Garmin Personal API token broker (INT-GAR-006, ADR-015; garmin spec section 3).

Exchanges the athlete's API key for an access token at the documented token endpoint and
keeps the token **in memory only**, as a ``SecretValue``. The token is a JWT the client
cannot verify: its ``exp`` claim is read only to schedule renewal, which happens when less
than one hour remains (the official client's margin) or after a 401. Neither the key nor the
token is ever logged, stored, put in a URL or in an error.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Final

from coach.application.ports import Clock
from coach.application.providers import ProviderError
from coach.application.secrets import SecretValue
from coach.domain.integration.status import IntegrationReason
from coach.integrations.garmin.http import provider_error
from coach.integrations.garmin.throttle import RequestBudget
from coach.integrations.garmin.transport import (
    DEFAULT_TIMEOUT_SECONDS,
    HttpRequest,
    HttpTransport,
    TransportError,
)

TOKEN_PATH: Final = "/personalapitoken-service/personal-api/personal-api-key/token"  # noqa: S105 - a path
RENEW_MARGIN: Final = timedelta(hours=1)
MAX_TOKEN_BYTES: Final = 64 * 1024
_PROTOCOL: Final = IntegrationReason.PROVIDER_PROTOCOL_ERROR
_BASE64URL: Final = re.compile(r"[A-Za-z0-9_-]{1,8192}")


def _token_format() -> ProviderError:
    return ProviderError(_PROTOCOL, "TOKEN_FORMAT")


def jwt_expiry(token: str) -> datetime:
    """The ``exp`` claim of a three-part base64url JWT, without verifying its signature."""
    parts = token.split(".")
    if len(parts) != 3 or not all(_BASE64URL.fullmatch(part) for part in parts):
        raise _token_format()
    try:
        payload = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        claims = json.loads(payload)
    except (binascii.Error, ValueError):
        raise _token_format() from None
    expiry = claims.get("exp") if isinstance(claims, dict) else None
    if isinstance(expiry, bool) or not isinstance(expiry, int | float):
        raise _token_format()
    try:
        if not math.isfinite(expiry):
            raise _token_format()
        return datetime.fromtimestamp(expiry, UTC)
    except (OverflowError, OSError, ValueError):
        raise _token_format() from None


class TokenBroker:
    def __init__(
        self,
        *,
        base_url: str,
        key_source: Callable[[], SecretValue],
        transport: HttpTransport,
        clock: Clock,
        budget: RequestBudget | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._url = base_url.rstrip("/") + TOKEN_PATH
        self._budget = budget
        self._key_source = key_source
        self._transport = transport
        self._clock = clock
        self._timeout = timeout
        self._token: SecretValue | None = None
        self._expires_at: datetime | None = None
        self._renew_at: datetime | None = None

    @property
    def expires_at(self) -> datetime | None:
        return self._expires_at

    def token(self) -> SecretValue:
        renew_at = self._renew_at
        if self._token is not None and renew_at is not None and self._clock() < renew_at:
            return self._token
        return self._exchange()

    def invalidate(self) -> None:
        self._token = None
        self._expires_at = None
        self._renew_at = None

    def _exchange(self) -> SecretValue:
        self.invalidate()
        key = self._key_source()
        request = HttpRequest(
            "POST",
            self._url,
            {"Accept": "*/*", "Authorization": SecretValue(f"ApiKey {key.reveal()}")},
        )
        if self._budget is not None:
            self._budget.acquire()
        try:
            response = self._transport.send(
                request, timeout=self._timeout, max_bytes=MAX_TOKEN_BYTES
            )
        except TransportError as error:
            raise provider_error(error) from None
        if response.status in {401, 403}:
            raise ProviderError(IntegrationReason.PROVIDER_AUTH_FAILED, "KEY_REJECTED")
        if not 200 <= response.status < 300:
            raise provider_error(response, now=self._clock())
        try:
            text = response.body.decode("ascii").strip()
        except UnicodeDecodeError:
            raise _token_format() from None
        expires_at = jwt_expiry(text)
        now = self._clock()
        if expires_at <= now:
            raise _token_format()
        # A token shorter-lived than the margin (key about to expire) is used for half its life
        # instead of being exchanged again before every request.
        margin = min(RENEW_MARGIN, (expires_at - now) / 2)
        self._token, self._expires_at = SecretValue(text), expires_at
        self._renew_at = expires_at - margin
        return self._token
