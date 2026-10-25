"""Data directory resolution and the synced-folder guard (PRIV-STO-001, ADR-014)."""

import logging
from pathlib import Path

import pytest

from coach.infrastructure.datadir import SyncedDataDirError, ensure_layout, resolve_data_dir

pytestmark = [pytest.mark.req("PRIV-STO-001"), pytest.mark.acceptance("AT-STO-001")]


def test_windows_default_is_local_app_data(tmp_path: Path) -> None:
    resolved = resolve_data_dir({"LOCALAPPDATA": str(tmp_path / "Local")}, windows=True)
    assert resolved == (tmp_path / "Local" / "CoachDeportivo").resolve()


def test_posix_default_is_xdg_data_home(tmp_path: Path) -> None:
    resolved = resolve_data_dir({"XDG_DATA_HOME": str(tmp_path / "share")}, windows=False)
    assert resolved == (tmp_path / "share" / "CoachDeportivo").resolve()
    fallback = resolve_data_dir({}, windows=False, home=tmp_path)
    assert fallback == (tmp_path / ".local" / "share" / "CoachDeportivo").resolve()


def test_explicit_directory_wins(tmp_path: Path) -> None:
    env = {"COACH_DATA_DIR": str(tmp_path / "coach-data"), "LOCALAPPDATA": str(tmp_path)}
    assert resolve_data_dir(env, windows=True) == (tmp_path / "coach-data").resolve()


def test_directory_inside_a_synced_root_is_refused(tmp_path: Path) -> None:
    synced = tmp_path / "CorporateSync"
    env = {"OneDriveCommercial": str(synced), "COACH_DATA_DIR": str(synced / "Docs" / "coach")}
    with pytest.raises(SyncedDataDirError, match="OneDriveCommercial"):
        resolve_data_dir(env, windows=True)


@pytest.mark.parametrize(
    "component", ["OneDrive - Example Org", "onedrive", "Dropbox", "Google Drive"]
)
def test_synced_looking_path_components_are_refused(tmp_path: Path, component: str) -> None:
    env = {"COACH_DATA_DIR": str(tmp_path / component / "coach")}
    with pytest.raises(SyncedDataDirError):
        resolve_data_dir(env, windows=True)


def test_override_is_explicit_and_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    env = {
        "COACH_DATA_DIR": str(tmp_path / "Dropbox" / "coach"),
        "COACH_ALLOW_SYNCED_DATA_DIR": "1",
    }
    with caplog.at_level(logging.WARNING, logger="coach.security"):
        assert resolve_data_dir(env, windows=True) == (tmp_path / "Dropbox" / "coach").resolve()
    assert "security.synced_data_dir_override" in caplog.text
    with pytest.raises(SyncedDataDirError):
        resolve_data_dir({**env, "COACH_ALLOW_SYNCED_DATA_DIR": "yes"}, windows=True)


def test_layout_is_created(tmp_path: Path) -> None:
    layout = ensure_layout(tmp_path / "data")
    assert layout.root.is_dir()
    assert layout.raw.is_dir()
    assert layout.database == tmp_path / "data" / "coach.sqlite3"
