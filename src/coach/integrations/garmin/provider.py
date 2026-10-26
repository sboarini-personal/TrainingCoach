"""Garmin Personal tier adapter: implements ``FitnessProvider`` (INT-GAR-005; ADR-012, 026).

Read-only and pull-only. It calls exactly the documented activity search, details and
download endpoints (garmin spec section 1) through the thin transport, spending the request
budget and renewing the token once after a 401. It returns RAW payloads and nothing else:
no storage, no normalisation, no retries (the application owns those).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from typing import Any, Final, NoReturn
from urllib.parse import urlencode

from coach.application.credentials import CredentialKind, CredentialService, ExpiryState
from coach.application.errors import NotFoundError
from coach.application.ownership import OwnerScope
from coach.application.ports import Clock
from coach.application.providers import (
    JSON_MEDIA_TYPE,
    ZIP_MEDIA_TYPE,
    ActivityPage,
    ActivityRef,
    ProviderError,
    ProviderNotConfiguredError,
    RawPayload,
)
from coach.application.secrets import SecretValue
from coach.domain.common.time import DateRange
from coach.domain.ingestion.limits import IngestionLimits
from coach.domain.integration.capabilities import ProviderCapabilities
from coach.domain.integration.status import IntegrationReason
from coach.integrations.garmin.capabilities import PROVIDER, personal_capabilities
from coach.integrations.garmin.http import provider_error
from coach.integrations.garmin.throttle import RequestBudget
from coach.integrations.garmin.token import TokenBroker
from coach.integrations.garmin.transport import (
    DEFAULT_TIMEOUT_SECONDS,
    HttpRequest,
    HttpResponse,
    HttpTransport,
    TransportError,
)

# The servers the Swagger document lists; no other host is ever contacted.
SERVERS: Final = {
    "global": "https://personal.connectapi.garmin.com",
    "china": "https://personal.connectapi.garmin.cn",
}
SEARCH_PATH: Final = "/activitylist-service/activities/search/activities"
DETAILS_PATH: Final = "/activity-service/activity/{activity_id}"
DOWNLOAD_PATH: Final = "/download-service/files/activity/{activity_id}"
LIST_CATEGORY: Final = "GARMIN_ACTIVITY_LIST"
DETAILS_CATEGORY: Final = "GARMIN_ACTIVITY"
FILE_CATEGORY: Final = "GARMIN_ACTIVITY_FILE"
JSON_LIMIT_BYTES: Final = IngestionLimits().json_bytes
_ACTIVITY_ID: Final = re.compile(r"^[1-9][0-9]{0,18}$")
_KEY: Final = re.compile(r"[\x21-\x7e]{1,4096}")  # visible ASCII, no whitespace
_PROTOCOL: Final = IntegrationReason.PROVIDER_PROTOCOL_ERROR


def _list_format() -> NoReturn:
    raise ProviderError(_PROTOCOL, "LIST_FORMAT")


def _no_constants(name: str) -> NoReturn:
    raise ValueError(name)


def parse_activity_list(body: bytes) -> tuple[ActivityRef, ...]:
    """IDs of a search page: an array of objects with a positive integer ``activityId``."""
    try:
        items = json.loads(body.decode("utf-8"), parse_constant=_no_constants)
    except (UnicodeDecodeError, ValueError, RecursionError):
        _list_format()
    if not isinstance(items, list):
        _list_format()
    refs = []
    for item in items:
        activity_id = item.get("activityId") if isinstance(item, dict) else None
        if isinstance(activity_id, bool) or not isinstance(activity_id, int) or activity_id < 1:
            _list_format()
        if not _ACTIVITY_ID.match(str(activity_id)):
            _list_format()
        refs.append(ActivityRef(str(activity_id)))
    return tuple(refs)


def credential_key(credentials: CredentialService, scope: OwnerScope) -> Callable[[], SecretValue]:
    """The stored Garmin key, refused before any request when its recorded expiry passed."""

    def source() -> SecretValue:
        kind = CredentialKind.GARMIN_PERSONAL_API_KEY
        status = next((item for item in credentials.status(scope) if item.kind is kind), None)
        if status is None:
            raise ProviderNotConfiguredError(PROVIDER)
        if status.state is ExpiryState.EXPIRED:
            raise ProviderError(IntegrationReason.PROVIDER_AUTH_FAILED, "KEY_EXPIRED")
        try:
            key = credentials.secret(scope, kind)
        except NotFoundError:
            raise ProviderNotConfiguredError(PROVIDER) from None
        if not _KEY.fullmatch(key.reveal()):
            # A key with spaces, line breaks or non-ASCII characters cannot be a header value.
            raise ProviderError(IntegrationReason.PROVIDER_AUTH_FAILED, "KEY_FORMAT")
        return key

    return source


class GarminPersonalProvider:
    def __init__(
        self,
        *,
        broker: TokenBroker,
        transport: HttpTransport,
        budget: RequestBudget,
        clock: Clock,
        base_url: str = SERVERS["global"],
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        file_limit_bytes: int = IngestionLimits().zip_bytes,
    ) -> None:
        if base_url not in SERVERS.values():
            raise ValueError("only the documented Garmin servers are allowed")
        self._broker = broker
        self._transport = transport
        self._budget = budget
        self._clock = clock
        self._base_url = base_url
        self._timeout = timeout
        self._file_limit = file_limit_bytes

    @property
    def provider(self) -> str:
        return PROVIDER

    def get_capabilities(self) -> ProviderCapabilities:
        return personal_capabilities()

    def get_activities(self, window: DateRange, offset: int, limit: int) -> ActivityPage:
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("offset is not negative and the page size is 1 to 100")
        params = {
            "start": offset,
            "limit": limit,
            "startDate": window.start.isoformat(),
            "endDate": window.end.isoformat(),
            "sortOrder": "asc",
        }
        response = self._get(SEARCH_PATH, params, accept=JSON_MEDIA_TYPE, limit=JSON_LIMIT_BYTES)
        if response is None:
            raise ProviderError(_PROTOCOL, "HTTP_4XX")
        payload = RawPayload(PROVIDER, LIST_CATEGORY, JSON_MEDIA_TYPE, response.body, window=window)
        return ActivityPage(parse_activity_list(response.body), payload)

    def get_activity_details(self, ref: ActivityRef) -> RawPayload | None:
        path = DETAILS_PATH.format(activity_id=self._activity_id(ref))
        response = self._get(path, {}, accept=JSON_MEDIA_TYPE, limit=JSON_LIMIT_BYTES)
        if response is None:
            return None
        return RawPayload(
            PROVIDER,
            DETAILS_CATEGORY,
            JSON_MEDIA_TYPE,
            response.body,
            provider_object_id=ref.provider_activity_id,
        )

    def get_activity_file(self, ref: ActivityRef) -> RawPayload | None:
        path = DOWNLOAD_PATH.format(activity_id=self._activity_id(ref))
        response = self._get(path, {}, accept="*/*", limit=self._file_limit)
        if response is None or not response.body:
            return None
        return RawPayload(
            PROVIDER,
            FILE_CATEGORY,
            ZIP_MEDIA_TYPE,
            response.body,
            provider_object_id=ref.provider_activity_id,
        )

    # ------------------------------------------------------------------ HTTP

    @staticmethod
    def _activity_id(ref: ActivityRef) -> str:
        if not _ACTIVITY_ID.match(ref.provider_activity_id):
            raise ProviderError(_PROTOCOL, "ACTIVITY_ID")
        return ref.provider_activity_id

    def _get(
        self, path: str, params: Mapping[str, Any], *, accept: str, limit: int
    ) -> HttpResponse | None:
        """The response, or ``None`` for HTTP 404; raises ``ProviderError`` otherwise."""
        query = f"?{urlencode(params)}" if params else ""
        url = f"{self._base_url}{path}{query}"
        response = self._send(url, accept=accept, limit=limit)
        if response.status == 401:
            # Revoked or expired early: one fresh token, then the failure stands.
            self._broker.invalidate()
            response = self._send(url, accept=accept, limit=limit)
        if 200 <= response.status < 300:
            return response
        if response.status == 404:
            return None
        raise provider_error(response, now=self._clock())

    def _send(self, url: str, *, accept: str, limit: int) -> HttpResponse:
        token = self._broker.token()
        self._budget.acquire()
        request = HttpRequest(
            "GET",
            url,
            {"Accept": accept, "Authorization": SecretValue(f"Bearer {token.reveal()}")},
        )
        try:
            return self._transport.send(request, timeout=self._timeout, max_bytes=limit)
        except TransportError as error:
            raise provider_error(error) from None
