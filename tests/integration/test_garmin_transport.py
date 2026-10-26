"""The urllib transport against a local HTTP server: redirects, caps, timeouts (ADR-026)."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from coach.application.secrets import SecretValue
from coach.integrations.garmin.transport import (
    HttpRequest,
    HttpResponse,
    TransportError,
    TransportFailure,
    UrllibTransport,
)


class Handler(BaseHTTPRequestHandler):
    seen: ClassVar[list[tuple[str, str | None]]] = []

    def log_message(self, *args: object) -> None:  # keep test output quiet
        return

    def _reply(self, status: int, body: bytes = b"", **headers: str) -> None:
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name.replace("_", "-"), value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        Handler.seen.append((self.path, self.headers.get("Authorization")))
        if self.path == "/ok":
            self._reply(200, b'{"ok": true}', Content_Type="application/json", X_Trace="t1")
        elif self.path == "/redirect":
            self._reply(302, Location="/stolen")
        elif self.path == "/busy":
            self._reply(503, b"busy", Retry_After="7")
        elif self.path == "/big":
            self._reply(200, b"x" * 2048)
        elif self.path == "/slow":
            time.sleep(1.5)
            self._reply(200, b"late")
        elif self.path == "/cut":
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b"x" * 10)  # then the connection closes
        else:
            self._reply(404)


@pytest.fixture
def server() -> Iterator[str]:
    Handler.seen = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def local() -> UrllibTransport:
    return UrllibTransport(require_https=False, proxies={})


def get(base: str, path: str, *, timeout: float = 5.0, max_bytes: int = 1024) -> HttpResponse:
    return local().send(
        HttpRequest("GET", base + path, {"Authorization": SecretValue("Bearer synthetic")}),
        timeout=timeout,
        max_bytes=max_bytes,
    )


@pytest.mark.req("INT-GAR-005", "SEC-SEC-003")
def test_a_response_has_lower_case_headers_and_the_secret_is_sent_not_shown(server: str) -> None:
    response = get(server, "/ok")
    assert response.status == 200
    assert response.header("X-Trace") == "t1"
    assert response.body == b'{"ok": true}'
    assert Handler.seen == [("/ok", "Bearer synthetic")]
    request = HttpRequest("GET", server + "/ok", {"Authorization": SecretValue("Bearer synthetic")})
    assert "synthetic" not in repr(request)
    assert "ok" not in repr(response)


@pytest.mark.req("INT-GAR-006", "SEC-SEC-003")
def test_redirects_are_returned_never_followed(server: str) -> None:
    response = get(server, "/redirect")
    assert response.status == 302
    assert [path for path, _ in Handler.seen] == ["/redirect"]


@pytest.mark.req("INT-GAR-010")
def test_error_statuses_are_responses_with_their_headers(server: str) -> None:
    response = get(server, "/busy")
    assert (response.status, response.header("retry-after")) == (503, "7")


@pytest.mark.req("INT-GAR-009", "SEC-UPL-003")
def test_bodies_over_the_cap_are_refused(server: str) -> None:
    with pytest.raises(TransportError) as failed:
        get(server, "/big", max_bytes=1024)
    assert failed.value.failure is TransportFailure.TOO_LARGE
    assert get(server, "/big", max_bytes=2048).status == 200


@pytest.mark.req("NFR-REL-002")
def test_timeouts_and_refused_connections_are_typed(server: str) -> None:
    with pytest.raises(TransportError) as slow:
        get(server, "/slow", timeout=0.3)
    assert slow.value.failure is TransportFailure.TIMEOUT
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with pytest.raises(TransportError) as refused:
        get(f"http://127.0.0.1:{port}", "/ok")
    assert refused.value.failure is TransportFailure.NETWORK
    assert str(refused.value) == "NETWORK"


@pytest.mark.req("NFR-REL-002", "INT-GAR-009")
def test_a_response_cut_off_before_its_announced_length_is_a_network_failure(
    server: str,
) -> None:
    with pytest.raises(TransportError) as cut:
        get(server, "/cut")
    assert cut.value.failure is TransportFailure.NETWORK


@pytest.mark.req("SEC-SEC-003", "INT-GAR-006")
def test_a_header_value_with_line_breaks_is_refused_without_quoting_it(server: str) -> None:
    planted = "Bearer planted-value\r\nX-Injected: 1"  # pragma: allowlist secret
    with pytest.raises(TransportError) as refused:
        local().send(
            HttpRequest("GET", server + "/ok", {"Authorization": SecretValue(planted)}),
            timeout=5,
            max_bytes=1024,
        )
    assert refused.value.failure is TransportFailure.REQUEST_INVALID
    assert "planted" not in str(refused.value)
    assert refused.value.__cause__ is None
    assert Handler.seen == []


@pytest.mark.req("SEC-SEC-003")
def test_only_https_is_sent_by_default() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        UrllibTransport().send(HttpRequest("GET", "http://127.0.0.1/ok"), timeout=1, max_bytes=1)
    with pytest.raises(ValueError, match="HTTPS"):
        local().send(HttpRequest("GET", "file:///etc/passwd"), timeout=1, max_bytes=1)
