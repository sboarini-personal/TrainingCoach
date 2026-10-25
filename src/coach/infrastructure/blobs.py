"""Content-addressed RAW blob store, partitioned per athlete (FR-ACT-002, SEC-UPL-005).

Layout: ``<root>/<athlete_id>/<sha256[0:2]>/<sha256>``. Files are written atomically, made
read-only, and verified against their hash when read. Only validated digests and the
athlete's UUID ever form a path; uploaded filenames never do.
"""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from pathlib import Path

from coach.application.errors import NotFoundError
from coach.application.ownership import OwnerScope
from coach.domain.raw.source import require_sha256

_READ_ONLY = stat.S_IRUSR | stat.S_IRGRP


class BlobIntegrityError(RuntimeError):
    """A stored blob no longer matches its content hash."""


class FileBlobStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def put(self, scope: OwnerScope, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        path = self._path(scope, digest)
        if path.exists():
            return digest
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(dir=path.parent, prefix=".incoming-")
        temporary = Path(name)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(_READ_ONLY)
            try:
                temporary.replace(path)
            except PermissionError:
                if not path.exists():
                    raise
        finally:
            if temporary.exists():
                temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
                temporary.unlink(missing_ok=True)
        return digest

    def get(self, scope: OwnerScope, sha256: str) -> bytes:
        path = self._path(scope, sha256)
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            raise NotFoundError from None
        if hashlib.sha256(data).hexdigest() != sha256:
            raise BlobIntegrityError(f"blob sha256:{sha256[:12]} does not match its content")
        return data

    def exists(self, scope: OwnerScope, sha256: str) -> bool:
        return self._path(scope, sha256).is_file()

    def _path(self, scope: OwnerScope, sha256: str) -> Path:
        digest = require_sha256(sha256)
        return self._root / str(scope.athlete_id) / digest[:2] / digest
