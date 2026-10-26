"""Thin HTTPS transport for the Garmin adapter (garmin spec section 11, ADR-026).

Standard library only. TLS with certificate verification, proxies from the environment, a
timeout on every request, response bodies read under a cap, and **no redirects**: a 3xx is
returned as is, so the Authorization header never reaches another host. Header values that
are secrets stay ``SecretValue`` until the moment they are written to the socket; requests
and responses never render their headers or bodies. Every failure becomes a ``TransportError``
carrying a code only: no library message (which could quote a header value) gets out.
"""

from __future__ import annotations

import http.client
import re
import ssl
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from email.message import Message
from enum import StrEnum
from typing import Final, Protocol
from urllib.parse import urlsplit

from coach.application.secrets import SecretValue

DEFAULT_TIMEOUT_SECONDS: Final = 30.0
_DIGITS: Final = re.compile(r"[0-9]{1,18}")
_HEADER_VALUE: Final = re.compile(r"[\x20-\x7e]*")  # printable ASCII: no CR, LF or NUL


class TransportFailure(StrEnum):
    TIMEOUT = "TIMEOUT"
    NETWORK = "NETWORK"
    TOO_LARGE = "RESPONSE_TOO_LARGE"
    REQUEST_INVALID = "REQUEST_INVALID"


class TransportError(Exception):
    """The request did not produce a response; carries a code only."""

    def __init__(self, failure: TransportFailure) -> None:
        super().__init__(failure.value)
        self.failure = failure


@dataclass(frozen=True, slots=True)
class HttpRequest:
    method: str
    url: str
    headers: Mapping[str, str | SecretValue] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    body: bytes = field(default=b"", repr=False)

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())


class HttpTransport(Protocol):
    def send(self, request: HttpRequest, *, timeout: float, max_bytes: int) -> HttpResponse: ...


class _Readable(Protocol):
    def read(self, amount: int, /) -> bytes: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def _headers(message: Message | None) -> dict[str, str]:
    if message is None:
        return {}
    return {name.lower(): value for name, value in message.items()}


def _read(stream: _Readable, headers: Mapping[str, str], max_bytes: int) -> bytes:
    """The body under the cap; a body shorter than its announced length was cut off."""
    body = stream.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise TransportError(TransportFailure.TOO_LARGE)
    declared = headers.get("content-length", "").strip()
    if _DIGITS.fullmatch(declared) and int(declared) != len(body):
        raise TransportError(TransportFailure.NETWORK)
    return bytes(body)


def _header_value(value: str | SecretValue) -> str:
    text = value.reveal() if isinstance(value, SecretValue) else value
    if not _HEADER_VALUE.fullmatch(text):
        # Never let the HTTP library quote a refused value (it may be a credential).
        raise TransportError(TransportFailure.REQUEST_INVALID)
    return text


class UrllibTransport:
    def __init__(
        self,
        *,
        require_https: bool = True,
        proxies: Mapping[str, str] | None = None,
    ) -> None:
        handlers: list[urllib.request.BaseHandler] = [
            _NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        ]
        if proxies is not None:
            handlers.append(urllib.request.ProxyHandler(dict(proxies)))
        self._opener = urllib.request.build_opener(*handlers)
        self._require_https = require_https

    def send(self, request: HttpRequest, *, timeout: float, max_bytes: int) -> HttpResponse:
        scheme = urlsplit(request.url).scheme
        if scheme != "https" and (self._require_https or scheme != "http"):
            raise ValueError("only HTTPS requests are sent")
        prepared = urllib.request.Request(  # noqa: S310 - scheme checked above
            request.url,
            method=request.method,
            headers={name: _header_value(value) for name, value in request.headers.items()},
        )
        try:
            return self._exchange(prepared, timeout=timeout, max_bytes=max_bytes)
        except TransportError:
            raise
        except TimeoutError:
            raise TransportError(TransportFailure.TIMEOUT) from None
        except urllib.error.URLError as error:
            failure = (
                TransportFailure.TIMEOUT
                if isinstance(error.reason, TimeoutError)
                else TransportFailure.NETWORK
            )
            raise TransportError(failure) from None
        except (http.client.HTTPException, OSError):
            raise TransportError(TransportFailure.NETWORK) from None
        except (ValueError, UnicodeError):
            raise TransportError(TransportFailure.REQUEST_INVALID) from None

    def _exchange(
        self, prepared: urllib.request.Request, *, timeout: float, max_bytes: int
    ) -> HttpResponse:
        try:
            with self._opener.open(prepared, timeout=timeout) as response:
                headers = _headers(response.headers)
                return HttpResponse(response.status, headers, _read(response, headers, max_bytes))
        except urllib.error.HTTPError as error:
            with error:
                headers = _headers(error.headers)
                return HttpResponse(error.code, headers, _read(error, headers, max_bytes))
