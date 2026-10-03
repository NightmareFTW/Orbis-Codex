from __future__ import annotations

import os
from pathlib import Path

import pytest

from e7ac.paths import APP_DIR_NAME, HOME_ENV_VAR, AppPaths, default_home, default_paths


def test_env_override_wins(isolated_home: AppPaths) -> None:
    assert default_paths() == isolated_home


def test_default_home_without_override_uses_app_dir_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(HOME_ENV_VAR, raising=False)
    assert default_home().name == APP_DIR_NAME


def test_blank_override_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(HOME_ENV_VAR, "   ")
    assert default_home().name == APP_DIR_NAME


def test_layout_is_under_home(isolated_home: AppPaths) -> None:
    for path in (
        isolated_home.settings_file,
        isolated_home.database,
        isolated_home.cache_dir,
        isolated_home.captures_dir,
        isolated_home.logs_dir,
    ):
        assert path.parent == isolated_home.home


def test_ensure_is_idempotent(isolated_home: AppPaths) -> None:
    isolated_home.ensure()
    isolated_home.ensure()
    assert all(Path(d).is_dir() for d in isolated_home.directories())


@pytest.mark.windows
def test_windows_default_home_is_localappdata(monkeypatch: pytest.MonkeyPatch) -> None:
    """The documented location (README/SPEC): %LOCALAPPDATA%\\OrbisCodex, not roaming, no author sub-folder."""
    monkeypatch.delenv(HOME_ENV_VAR, raising=False)
    assert default_home() == Path(os.environ["LOCALAPPDATA"]) / APP_DIR_NAME
