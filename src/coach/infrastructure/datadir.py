"""Where athlete data lives (PRIV-STO-001, ADR-014; spec/security/privacy.spec.md section 5).

Athlete data must not land in a folder synchronised to cloud storage: an organisational
tenant could retain or expose it and deletion could not be guaranteed.
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
OVERRIDE_VARIABLE: Final = "COACH_ALLOW_SYNCED_DATA_DIR"

_security_log = logging.getLogger("coach.security")


class SyncedDataDirError(ValueError):
    """The data directory is inside a synchronised folder."""


@dataclass(frozen=True, slots=True)
class DataLayout:
    root: Path
    database: Path
    raw: Path


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


def resolve_data_dir(
    env: Mapping[str, str], *, windows: bool | None = None, home: Path | None = None
) -> Path:
    """Resolve the data directory; refuse synchronised folders unless explicitly overridden."""
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
    reason = synced_folder_reason(resolved, env)
    if reason is not None:
        if env.get(OVERRIDE_VARIABLE) != "1":
            raise SyncedDataDirError(
                f"refusing data directory {resolved}: {reason}. Athlete data must stay out of "
                f"synchronised folders; set {OVERRIDE_VARIABLE}=1 only if you accept the risk."
            )
        _security_log.warning("security.synced_data_dir_override reason=%s", reason)
    return resolved


def ensure_layout(root: Path) -> DataLayout:
    raw = root / "raw"
    raw.mkdir(mode=0o700, parents=True, exist_ok=True)
    return DataLayout(root=root, database=root / "coach.sqlite3", raw=raw)
