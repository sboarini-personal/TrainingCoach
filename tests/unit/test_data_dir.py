"""Data directory resolution, synchronised folders and the repository guard (PRIV-STO-001,
ADR-014, ADR-035)."""

import logging
from pathlib import Path

import pytest

from coach.infrastructure.datadir import (
    DataDirError,
    ensure_layout,
    log_synced_folder,
    resolve_data_dir,
    synced_folder_reason,
)

pytestmark = [pytest.mark.req("PRIV-STO-001"), pytest.mark.acceptance("AT-STO-001")]


def test_windows_default_is_local_app_data(tmp_path: Path) -> None:
    resolved = resolve_data_dir({"LOCALAPPDATA": str(tmp_path / "Local")}, windows=True)
    assert resolved == (tmp_path / "Local" / "CoachDeportivo").resolve()
    assert synced_folder_reason(resolved, {}) is None  # local by default


def test_posix_default_is_xdg_data_home(tmp_path: Path) -> None:
    resolved = resolve_data_dir({"XDG_DATA_HOME": str(tmp_path / "share")}, windows=False)
    assert resolved == (tmp_path / "share" / "CoachDeportivo").resolve()
    fallback = resolve_data_dir({}, windows=False, home=tmp_path)
    assert fallback == (tmp_path / ".local" / "share" / "CoachDeportivo").resolve()


def test_explicit_directory_wins(tmp_path: Path) -> None:
    env = {"COACH_DATA_DIR": str(tmp_path / "coach-data"), "LOCALAPPDATA": str(tmp_path)}
    assert resolve_data_dir(env, windows=True) == (tmp_path / "coach-data").resolve()


def test_a_directory_inside_a_synced_root_is_accepted_and_named(tmp_path: Path) -> None:
    synced = tmp_path / "CorporateSync"
    env = {"OneDriveCommercial": str(synced), "COACH_DATA_DIR": str(synced / "Docs" / "coach")}
    resolved = resolve_data_dir(env, windows=True)  # the athlete's choice (D-5, ADR-035)
    assert resolved == (synced / "Docs" / "coach").resolve()
    assert "OneDriveCommercial" in (synced_folder_reason(resolved, env) or "")


@pytest.mark.parametrize(
    "component", ["OneDrive - Example Org", "onedrive", "Dropbox", "Google Drive"]
)
def test_synced_looking_components_are_accepted_with_a_logged_notice(
    tmp_path: Path, component: str, caplog: pytest.LogCaptureFixture
) -> None:
    env = {"COACH_DATA_DIR": str(tmp_path / component / "coach")}
    resolved = resolve_data_dir(env, windows=True)
    with caplog.at_level(logging.WARNING, logger="coach.security"):
        notice = log_synced_folder(resolved, env)
    assert notice is not None
    assert "retention and eDiscovery" in notice
    assert "two computers" in notice
    assert "security.synced_data_dir" in caplog.text
    assert log_synced_folder(tmp_path / "local", {}) is None


def test_a_directory_inside_a_repository_is_refused(tmp_path: Path) -> None:
    (tmp_path / "project" / ".git").mkdir(parents=True)
    env = {"COACH_DATA_DIR": str(tmp_path / "project" / "data")}
    with pytest.raises(DataDirError, match="inside the repository"):
        resolve_data_dir(env, windows=True)


def test_layout_is_created(tmp_path: Path) -> None:
    layout = ensure_layout(tmp_path / "data")
    assert layout.root.is_dir()
    assert layout.raw.is_dir()
    assert layout.database == tmp_path / "data" / "coach.sqlite3"
