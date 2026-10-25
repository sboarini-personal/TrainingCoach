"""Parser sandbox: a separate, isolated interpreter with timeout and limits (SEC-UPL-004).

The worker gets the request on standard input only after the limits are in place (a Job
Object on Windows; the worker's own ``RLIMIT_AS`` on POSIX). Its response is read with an
output-size limit; standard error is discarded. Timeouts, crashes and breaches become typed
rejections (spec/security/uploads.spec.md section 4).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import sysconfig
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

import coach
from coach.application.files import FileRejectedError
from coach.domain.ingestion.record import RejectionReason

_BOOTSTRAP: Final = (
    "import sys;sys.path[0:0]=sys.argv[1:];"
    "from coach.integrations.files.worker import main;raise SystemExit(main())"
)
_CHUNK: Final = 64 * 1024
_KILL_GRACE_SECONDS: Final = 5.0


def interpreter() -> str:
    """The real interpreter: on Windows a venv's python.exe is a launcher with a child."""
    base = vars(sys).get("_base_executable")
    return base if isinstance(base, str) and base else sys.executable


def worker_command() -> list[str]:
    """Isolated interpreter running the worker from this code base and its dependencies."""
    source_root = str(Path(coach.__file__).resolve().parents[1])
    site_packages = sysconfig.get_paths()["purelib"]
    return [interpreter(), "-I", "-X", "utf8", "-c", _BOOTSTRAP, source_root, site_packages]


def minimal_environment() -> dict[str, str]:
    """No application variables reach the parser; Windows needs SYSTEMROOT to start."""
    if sys.platform == "win32":
        return {"SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows")}
    return {}


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_ulonglong)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _EXTENDED_LIMIT_INFORMATION: Final = 9
    _LIMIT_ACTIVE_PROCESS: Final = 0x0008
    _LIMIT_PROCESS_MEMORY: Final = 0x0100
    _LIMIT_DIE_ON_UNHANDLED_EXCEPTION: Final = 0x0400
    _LIMIT_KILL_ON_JOB_CLOSE: Final = 0x2000
    _PROCESS_SET_QUOTA: Final = 0x0100
    _PROCESS_TERMINATE: Final = 0x0001

    def _confine(pid: int, memory_bytes: int) -> int | None:
        """Put the process in a Job Object: memory limit, one process, kill on close."""
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.OpenProcess.restype = wintypes.HANDLE
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = (
            _LIMIT_ACTIVE_PROCESS
            | _LIMIT_PROCESS_MEMORY
            | _LIMIT_DIE_ON_UNHANDLED_EXCEPTION
            | _LIMIT_KILL_ON_JOB_CLOSE
        )
        limits.BasicLimitInformation.ActiveProcessLimit = 1
        limits.ProcessMemoryLimit = memory_bytes
        process = kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        confined = bool(process) and bool(
            kernel32.SetInformationJobObject(
                wintypes.HANDLE(job),
                _EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            )
        )
        confined = confined and bool(
            kernel32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(process))
        )
        if process:
            kernel32.CloseHandle(wintypes.HANDLE(process))
        if not confined:
            kernel32.CloseHandle(wintypes.HANDLE(job))
            return None
        return int(job)

    def _release(job: int | None) -> None:
        if job is not None:
            ctypes.WinDLL("kernel32").CloseHandle(wintypes.HANDLE(job))

else:

    def _confine(pid: int, memory_bytes: int) -> int | None:
        return None  # POSIX: the worker sets RLIMIT_AS before reading the file

    def _release(job: int | None) -> None:
        return None


class _Reader(threading.Thread):
    def __init__(self, process: subprocess.Popen[bytes], limit: int) -> None:
        super().__init__(daemon=True)
        self._process = process
        self._limit = limit
        self.data = bytearray()
        self.overflow = False

    def run(self) -> None:
        stream = self._process.stdout
        if stream is None:  # pragma: no cover - always piped
            return
        while chunk := stream.read(_CHUNK):
            self.data.extend(chunk)
            if len(self.data) > self._limit:
                self.overflow = True
                self._process.kill()
                return


class ParserSandbox:
    def __init__(
        self,
        working_dir: Path,
        *,
        command: Sequence[str] | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self._working_dir = working_dir
        self._command = list(command) if command is not None else worker_command()
        self._environment = dict(environment) if environment is not None else minimal_environment()
        self.memory_limit_enforced: bool | None = None

    def run(
        self,
        request: Mapping[str, Any],
        *,
        timeout_seconds: float,
        memory_bytes: int,
        max_output_bytes: int,
    ) -> dict[str, Any]:
        """The worker's JSON response; raises FileRejectedError on any failure."""
        process = subprocess.Popen(  # noqa: S603 - fixed interpreter and arguments
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=self._working_dir,
            env=self._environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        job = _confine(process.pid, memory_bytes)
        if sys.platform == "win32":
            self.memory_limit_enforced = job is not None
        reader = _Reader(process, max_output_bytes)
        reader.start()
        try:
            self._send(process, request)
            try:
                process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                raise FileRejectedError(RejectionReason.PARSE_TIMEOUT) from None
            reader.join(timeout=_KILL_GRACE_SECONDS)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=_KILL_GRACE_SECONDS)
            _release(job)
        if reader.overflow:
            raise FileRejectedError(RejectionReason.LIMIT_EXCEEDED, "PARSER_OUTPUT")
        if process.returncode != 0 or not reader.data:
            raise FileRejectedError(RejectionReason.PARSE_FAILED, "PARSER_CRASHED")
        try:
            response = json.loads(bytes(reader.data))
        except (ValueError, RecursionError):
            raise FileRejectedError(RejectionReason.SCHEMA_INVALID, "PARSER_RESPONSE") from None
        if not isinstance(response, dict):
            raise FileRejectedError(RejectionReason.SCHEMA_INVALID, "PARSER_RESPONSE")
        return response

    @staticmethod
    def _send(process: subprocess.Popen[bytes], request: Mapping[str, Any]) -> None:
        stdin = process.stdin
        if stdin is None:  # pragma: no cover - always piped
            return
        try:
            stdin.write(json.dumps(dict(request)).encode("utf-8"))
            stdin.close()
        except (BrokenPipeError, OSError):
            pass  # the worker died; its exit status reports it
