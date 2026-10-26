"""Garmin adapter wiring for tests: a scripted HTTP transport and fake time. No network."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from coach.application.secrets import SecretValue
from coach.integrations.garmin.provider import SERVERS, GarminPersonalProvider
from coach.integrations.garmin.throttle import RequestBudget
from coach.integrations.garmin.token import TOKEN_PATH, TokenBroker
from coach.integrations.garmin.transport import (
    HttpRequest,
    HttpResponse,
    TransportError,
    TransportFailure,
)
from garmin_samples import SYNTHETIC_KEY, details_bytes, download, list_page, token_for

BASE = SERVERS["global"]
type Reply = HttpResponse | TransportFailure | Callable[[HttpRequest], HttpResponse]


class FakeTime:
    """Wall clock and monotonic clock that only move when told to (or when slept)."""

    def __init__(self, start: datetime = datetime(2026, 10, 26, 20, 0, tzinfo=UTC)) -> None:
        self.now = start
        self.slept: list[float] = []

    def clock(self) -> datetime:
        return self.now

    def monotonic(self) -> float:
        return self.now.timestamp()

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += timedelta(seconds=seconds)

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


@dataclass
class Sent:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    url: str


@dataclass
class ScriptedTransport:
    """Answers by path: a queue of scripted replies first, then the default route."""

    time: FakeTime
    routes: dict[str, list[Reply]] = field(default_factory=dict)
    sent: list[Sent] = field(default_factory=list)
    activities: dict[str, list[int]] = field(default_factory=dict)  # local date -> IDs

    def script(self, path: str, *replies: Reply) -> None:
        self.routes.setdefault(path, []).extend(replies)

    def send(self, request: HttpRequest, *, timeout: float, max_bytes: int) -> HttpResponse:
        parts = urlsplit(request.url)
        headers = {
            name: value.reveal() if isinstance(value, SecretValue) else value
            for name, value in request.headers.items()
        }
        self.sent.append(
            Sent(request.method, parts.path, parse_qs(parts.query), headers, request.url)
        )
        queue = self.routes.get(parts.path)
        reply: Reply = queue.pop(0) if queue else self._default(parts.path, parts.query)
        if isinstance(reply, TransportFailure):
            raise TransportError(reply)
        response = reply(request) if callable(reply) else reply
        if len(response.body) > max_bytes:
            raise TransportError(TransportFailure.TOO_LARGE)
        return response

    def _default(self, path: str, query: str) -> HttpResponse:
        if path == TOKEN_PATH:
            return HttpResponse(200, {}, token_for(self.time.now).encode())
        if path.startswith("/activitylist-service/"):
            params = parse_qs(query)
            first, last = params["startDate"][0], params["endDate"][0]
            ids = [
                activity_id
                for day, listed in sorted(self.activities.items())
                if first <= day <= last
                for activity_id in listed
            ]
            start, limit = int(params["start"][0]), int(params["limit"][0])
            return HttpResponse(200, {}, list_page(*ids[start : start + limit]))
        if path.startswith("/activity-service/activity/"):
            return HttpResponse(200, {}, details_bytes(int(path.rsplit("/", 1)[1])))
        if path.startswith("/download-service/files/activity/"):
            return HttpResponse(200, {}, download(int(path.rsplit("/", 1)[1])))
        return HttpResponse(404)

    def paths(self) -> list[str]:
        return [item.path for item in self.sent]


class GarminWorld:
    def __init__(
        self,
        *,
        key: Callable[[], SecretValue] | None = None,
        budget: RequestBudget | None = None,
    ) -> None:
        self.time = FakeTime()
        self.transport = ScriptedTransport(self.time)
        self.budget = budget or RequestBudget(
            rate_per_second=1000, burst=1000, monotonic=self.time.monotonic, sleep=self.time.sleep
        )
        self.broker = TokenBroker(
            base_url=BASE,
            key_source=key or (lambda: SecretValue(SYNTHETIC_KEY)),
            transport=self.transport,
            clock=self.time.clock,
            budget=self.budget,
        )
        self.provider = GarminPersonalProvider(
            broker=self.broker,
            transport=self.transport,
            budget=self.budget,
            clock=self.time.clock,
        )

    def requests_to(self, prefix: str) -> list[Sent]:
        return [item for item in self.transport.sent if item.path.startswith(prefix)]


def response(status: int, body: bytes = b"", **headers: str) -> HttpResponse:
    return HttpResponse(
        status, {name.replace("_", "-"): value for name, value in headers.items()}, body
    )
