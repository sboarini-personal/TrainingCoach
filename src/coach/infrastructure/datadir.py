"""Where athlete data lives (PRIV-STO-001, ADR-014, ADR-035; spec/security/privacy.spec.md
section 5).

By default the data directory is local and not synchronised. The athlete may place it in a
folder synchronised to cloud storage (athlete's decision D-5, ADR-035): ``coach init`` then
names the risks once (an organisational tenant may retain or expose the data and defeat
deletion; SQLite may be damaged by sync conflicts when two computers open it). Secrets never
live there (SEC-SEC-001). A data directory inside a git working tree is always refused: athlete
data never goes in a repository.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

APP_FOLDER: Final = "CoachDeportivo"
SYNCED_ROOT_VARIABLES: Final = ("OneDrive", "OneDriveCommercial", "OneDriveConsumer")
SYNCED_FOLDER_NAMES: Final = frozenset({"dropbox", "google drive", "my drive", "iclouddrive"})

_security_log = logging.getLogger("coach.security")


class DataDirError(ValueError):
    """The data directory cannot hold athlete data."""


@dataclass(frozen=True, slots=True)
class DataLayout:
    root: Path
    database: Path
    raw: Path
    quarantine: Path


def _is_within(path: Path, root: Path) -> bool:
    return Path(os.path.normcase(path)).is_relative_to(os.path.normcase(root))


def synced_folder_reason(path: Path, env: Mapping[str, str]) -> str | None:
    """Why ``path`` looks synchronised to cloud storage, or ``None``."""
    for variable in SYNCED_ROOT_VARIABLES:
        root = env.get(variable)
        if root and _is_within(path, Path(root).resolve()):
            return f"it is inside the folder synchronised by {variable}"
    for part in path.parts:
        folded = part.casefold()
        if folded.startswith("onedrive") or folded in SYNCED_FOLDER_NAMES:
            return f"its folder {part!r} looks synchronised to cloud storage"
    return None


def synced_folder_notice(path: Path, reason: str) -> str:
    """What the athlete is told once, at ``coach init`` (ADR-035)."""
    return (
        f"notice: the data directory {path} is in a synchronised folder ({reason}). Your health "
        "and training data will be copied to that cloud; with a work account, its retention and "
        "eDiscovery policies may reach it and keep it after you delete it. Do not use the data "
        "from two computers at the same time: sync conflicts can damage the database. Secrets "
        "stay in the OS secret store."
    )


def repository_of(path: Path) -> Path | None:
    """The git working tree that contains ``path``, if any."""
    for folder in (path, *path.parents):
        if (folder / ".git").exists():
            return folder
    return None


def resolve_data_dir(
    env: Mapping[str, str], *, windows: bool | None = None, home: Path | None = None
) -> Path:
    """Resolve the data directory; refuse one inside a git repository."""
    is_windows = os.name == "nt" if windows is None else windows
    explicit = env.get("COACH_DATA_DIR")
    if explicit:
        candidate = Path(explicit).expanduser()
    elif is_windows and env.get("LOCALAPPDATA"):
        candidate = Path(env["LOCALAPPDATA"]) / APP_FOLDER
    else:
        base = env.get("XDG_DATA_HOME") or str((home or Path.home()) / ".local" / "share")
        candidate = Path(base) / APP_FOLDER
    resolved = candidate.resolve()
    repository = repository_of(resolved)
    if repository is not None:
        raise DataDirError(
            f"refusing data directory {resolved}: it is inside the repository {repository}; "
            "athlete data never goes in a repository"
        )
    return resolved


def log_synced_folder(path: Path, env: Mapping[str, str]) -> str | None:
    """The notice for a synchronised data directory, also logged as a security event."""
    reason = synced_folder_reason(path, env)
    if reason is None:
        return None
    _security_log.warning("security.synced_data_dir reason=%s", reason)
    return synced_folder_notice(path, reason)


def ensure_layout(root: Path) -> DataLayout:
    raw = root / "raw"
    raw.mkdir(mode=0o700, parents=True, exist_ok=True)
    quarantine = root / "quarantine"
    quarantine.mkdir(mode=0o700, parents=True, exist_ok=True)
    return DataLayout(root=root, database=root / "coach.sqlite3", raw=raw, quarantine=quarantine)
