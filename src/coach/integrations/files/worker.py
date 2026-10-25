"""Sandbox worker: parses one quarantined file and prints one JSON document (SEC-UPL-004).

Started by ``coach.integrations.files.sandbox`` in an isolated interpreter. It reads the
request from standard input only after the parent has applied the process limits, applies the
POSIX memory limit itself, and never writes anything but the response to standard output.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Final

from coach.integrations.files.csv_parser import parse_csv
from coach.integrations.files.fit_parser import parse_fit
from coach.integrations.files.gpx_parser import parse_gpx
from coach.integrations.files.parsing import ParserLimits, ParserReason, ParserRejection
from coach.integrations.files.tcx_parser import parse_tcx

PROTOCOL: Final = 1
MAX_REQUEST_BYTES: Final = 64 * 1024
PARSERS: Final[Mapping[str, Callable[[Path, ParserLimits], dict[str, Any]]]] = {
    "FIT": parse_fit,
    "TCX": parse_tcx,
    "GPX": parse_gpx,
    "CSV": parse_csv,
}
_LIMIT_KEYS: Final = ("records", "activities", "xmlDepth", "xmlElements", "memoryBytes")


def _rejection(reason: ParserReason, detail: str) -> dict[str, Any]:
    return {"ok": False, "reason": reason.value, "detail": detail}


def _request(
    raw: bytes,
) -> tuple[Callable[[Path, ParserLimits], dict[str, Any]], Path, ParserLimits, int]:
    request = json.loads(raw)
    if not isinstance(request, dict) or request.get("protocol") != PROTOCOL:
        raise ValueError("bad request")
    parser = PARSERS[request["format"]]
    path = Path(request["path"])
    limits = request["limits"]
    values = [limits[key] for key in _LIMIT_KEYS]
    if not all(
        isinstance(value, int) and not isinstance(value, bool) and value > 0 for value in values
    ):
        raise ValueError("bad limits")
    parser_limits = ParserLimits(
        records=limits["records"],
        activities=limits["activities"],
        xml_depth=limits["xmlDepth"],
        xml_elements=limits["xmlElements"],
    )
    return parser, path, parser_limits, limits["memoryBytes"]


def _limit_memory(memory_bytes: int) -> None:
    if sys.platform != "win32":  # Windows: the parent's Job Object enforces the limit
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))


def _no_memory_limit(memory_bytes: int) -> None:
    return None


def handle(raw: bytes, *, limit_memory: Callable[[int], None] = _no_memory_limit) -> dict[str, Any]:
    """Parse one request. Only the sandboxed ``main`` limits its own process memory;
    in-process callers (tests) must never limit the calling process."""
    try:
        parser, path, limits, memory_bytes = _request(raw)
    except (ValueError, KeyError, TypeError):
        return _rejection(ParserReason.PARSE_FAILED, "BAD_REQUEST")
    limit_memory(memory_bytes)
    try:
        return {"ok": True, "output": parser(path, limits)}
    except ParserRejection as rejection:
        return _rejection(rejection.reason, rejection.detail)
    except MemoryError:
        return _rejection(ParserReason.LIMIT_EXCEEDED, "MEMORY")
    except RecursionError:
        return _rejection(ParserReason.LIMIT_EXCEEDED, "RECURSION")
    except Exception:
        return _rejection(ParserReason.PARSE_FAILED, "PARSER_ERROR")


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        response = _rejection(ParserReason.PARSE_FAILED, "BAD_REQUEST")
    else:
        response = handle(raw, limit_memory=_limit_memory)
    sys.stdout.buffer.write(
        json.dumps(response, allow_nan=False, separators=(",", ":")).encode("utf-8")
    )
    sys.stdout.buffer.flush()
    return 0
