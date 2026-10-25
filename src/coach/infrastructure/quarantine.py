"""Upload quarantine and malware-scan hook (INT-FIL-002 stages 5 and 6; SEC-UPL-005/006).

Quarantined files get random names in their own directory outside the blob store; the
uploaded filename never reaches the file system. Rejected and processed files are deleted
immediately (retention 0 days).
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Final

from coach.domain.raw.source import MalwareScan

_NAME_BYTES: Final = 16


class FileQuarantine:
    def __init__(self, root: Path) -> None:
        self._root = root

    def put(self, data: bytes) -> Path:
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self._root / secrets.token_hex(_NAME_BYTES)
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        return path

    def discard(self, path: Path) -> None:
        if path.parent.resolve() != self._root.resolve():
            raise ValueError("not a quarantined file")
        path.unlink(missing_ok=True)


class UnavailableMalwareScanner:
    """V1 ships no scanner adapter (A-11): uploads are flagged NOT_SCANNED, never 'clean'."""

    def scan(self, path: Path) -> MalwareScan:
        return MalwareScan.NOT_SCANNED
