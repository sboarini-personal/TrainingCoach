"""The real parser sandbox: isolated process, timeout, memory, output and child limits.

SEC-UPL-004 (spec/security/uploads.spec.md section 4). Test commands use the same base
interpreter as the worker; the venv launcher on Windows would itself be a child process.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from coach.application.files import FileRejectedError
from coach.domain.ingestion.limits import IngestionLimits
from coach.domain.ingestion.record import RejectionReason
from coach.domain.raw.source import DetectedType
from coach.integrations.files.formats import LocalFileFormats
from coach.integrations.files.sandbox import ParserSandbox, interpreter, worker_command
from file_samples import csv_file, fit_file, gpx_file, tcx_file
from samples import NOW

MIB = 1024 * 1024
WINDOWS = sys.platform == "win32"


def _python(tmp_path: Path, code: str) -> ParserSandbox:
    return ParserSandbox(tmp_path, command=[interpreter(), "-I", "-c", code])


def _run(
    sandbox: ParserSandbox, *, timeout: float = 30, memory: int = 256 * MIB, output: int = MIB
) -> dict[str, Any]:
    return sandbox.run(
        {"protocol": 1}, timeout_seconds=timeout, memory_bytes=memory, max_output_bytes=output
    )


def _rejected(sandbox: ParserSandbox, **options: Any) -> FileRejectedError:
    with pytest.raises(FileRejectedError) as raised:
        _run(sandbox, **options)
    return raised.value


@pytest.mark.req("SEC-UPL-004", "INT-FIL-001")
@pytest.mark.parametrize(
    ("fmt", "data"),
    [
        (DetectedType.FIT, fit_file()),
        (DetectedType.TCX, tcx_file()),
        (DetectedType.GPX, gpx_file()),
        (DetectedType.CSV, csv_file()),
    ],
)
def test_every_format_parses_in_the_sandbox(tmp_path: Path, fmt: DetectedType, data: bytes) -> None:
    path = tmp_path / "quarantined"
    path.write_bytes(data)
    sandbox = ParserSandbox(tmp_path)
    sessions = LocalFileFormats(sandbox).decode(path, fmt, IngestionLimits(), received_at=NOW)
    assert len(sessions) == 1
    if WINDOWS:
        assert sandbox.memory_limit_enforced is True


@pytest.mark.req("SEC-UPL-004")
def test_worker_runs_isolated_on_the_base_interpreter() -> None:
    command = worker_command()
    assert command[0] == interpreter()
    assert command[1:4] == ["-I", "-X", "utf8"]


@pytest.mark.req("SEC-UPL-004", "SEC-SEC-002")
def test_parser_gets_a_minimal_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COACH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("COACH_SECRET_PROBE", "should-not-leak")
    code = (
        "import json, os, sys; sys.stdin.buffer.read(); "
        "print(json.dumps({'keys': sorted(os.environ)}))"
    )
    keys = _run(_python(tmp_path, code))["keys"]
    assert not any(key.upper().startswith("COACH") for key in keys)
    assert set(keys) <= {"SYSTEMROOT", "LC_CTYPE"}


@pytest.mark.req("SEC-UPL-004", "INT-FIL-003")
@pytest.mark.acceptance("AT-UPL-001")
def test_a_parser_over_the_timeout_is_stopped(tmp_path: Path) -> None:
    rejection = _rejected(_python(tmp_path, "import time; time.sleep(30)"), timeout=1)
    assert rejection.reason is RejectionReason.PARSE_TIMEOUT


@pytest.mark.req("SEC-UPL-004")
@pytest.mark.parametrize(
    ("code", "reason", "detail"),
    [
        (
            "import sys; sys.stdin.buffer.read(); sys.exit(3)",
            RejectionReason.PARSE_FAILED,
            "PARSER_CRASHED",
        ),
        (
            "import sys; sys.stdin.buffer.read(); print('not json')",
            RejectionReason.SCHEMA_INVALID,
            "PARSER_RESPONSE",
        ),
        (
            "import sys; sys.stdin.buffer.read(); print('[1, 2]')",
            RejectionReason.SCHEMA_INVALID,
            "PARSER_RESPONSE",
        ),
        (
            "import sys; sys.stdin.buffer.read(); sys.stdout.write('x' * 3_000_000)",
            RejectionReason.LIMIT_EXCEEDED,
            "PARSER_OUTPUT",
        ),
    ],
)
def test_crashes_bad_output_and_oversized_output(
    tmp_path: Path, code: str, reason: RejectionReason, detail: str
) -> None:
    rejection = _rejected(_python(tmp_path, code))
    assert (rejection.reason, rejection.detail) == (reason, detail)


@pytest.mark.req("SEC-UPL-004")
@pytest.mark.skipif(not WINDOWS, reason="Job Object limits are Windows-only; POSIX uses RLIMIT_AS")
def test_windows_job_limits_memory_and_child_processes(tmp_path: Path) -> None:
    hog = (
        "import sys, json; sys.stdin.buffer.read(); x = bytearray(600 * 1024 * 1024); "
        "print(json.dumps({'allocated': True}))"
    )
    assert _run(_python(tmp_path, hog), memory=1024 * MIB) == {"allocated": True}
    assert _rejected(_python(tmp_path, hog), memory=128 * MIB).detail == "PARSER_CRASHED"
    spawner = (
        "import sys, json, subprocess; sys.stdin.buffer.read(); "
        "subprocess.run([sys.executable, '-I', '-c', 'pass'], check=True); "
        "print(json.dumps({'spawned': True}))"
    )
    control = [interpreter(), "-I", "-c", spawner]
    outside = subprocess.run(control, input=b"", capture_output=True, check=False)  # noqa: S603
    assert json.loads(outside.stdout) == {"spawned": True}  # control: allowed outside the job
    assert _rejected(_python(tmp_path, spawner)).detail == "PARSER_CRASHED"


@pytest.mark.req("SEC-UPL-004", "INT-FIL-003")
@pytest.mark.skipif(WINDOWS, reason="POSIX memory limit is set by the worker itself")
def test_posix_worker_limits_its_own_memory(tmp_path: Path) -> None:
    path = tmp_path / "quarantined"
    path.write_bytes(fit_file())
    limits = IngestionLimits(parser_memory_bytes=8 * MIB)
    with pytest.raises(FileRejectedError) as raised:
        LocalFileFormats(ParserSandbox(tmp_path)).decode(
            path, DetectedType.FIT, limits, received_at=NOW
        )
    assert raised.value.reason in {RejectionReason.LIMIT_EXCEEDED, RejectionReason.PARSE_FAILED}


@pytest.mark.req("SEC-UPL-004")
def test_the_worker_refuses_malformed_requests(tmp_path: Path) -> None:
    response = ParserSandbox(tmp_path).run(
        {"protocol": 99}, timeout_seconds=30, memory_bytes=256 * MIB, max_output_bytes=MIB
    )
    assert response == {"ok": False, "reason": "PARSE_FAILED", "detail": "BAD_REQUEST"}
