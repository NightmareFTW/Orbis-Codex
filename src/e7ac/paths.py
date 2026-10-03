"""Local data locations (local-first: everything lives under one per-user folder).

Default home: `%LOCALAPPDATA%\\OrbisCodex` on Windows (platformdirs user data dir), overridable with the
`E7AC_HOME` environment variable (used by tests and portable installs).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import platformdirs

APP_DIR_NAME = "OrbisCodex"
HOME_ENV_VAR = "E7AC_HOME"


@dataclass(frozen=True, slots=True)
class AppPaths:
    """All on-disk locations used by the app, derived from a single home directory."""

    home: Path

    @property
    def settings_file(self) -> Path:
        return self.home / "settings.json"

    @property
    def database(self) -> Path:
        return self.home / "e7ac.sqlite3"

    @property
    def cache_dir(self) -> Path:
        return self.home / "cache"

    @property
    def captures_dir(self) -> Path:
        return self.home / "captures"

    @property
    def logs_dir(self) -> Path:
        return self.home / "logs"

    def directories(self) -> tuple[Path, ...]:
        return (self.home, self.cache_dir, self.captures_dir, self.logs_dir)

    def ensure(self) -> None:
        """Create every directory (idempotent)."""
        for directory in self.directories():
            directory.mkdir(parents=True, exist_ok=True)


def default_home() -> Path:
    override = os.environ.get(HOME_ENV_VAR, "").strip()
    if override:
        return Path(override).expanduser()
    return Path(platformdirs.user_data_dir(APP_DIR_NAME, appauthor=False, roaming=False))


def default_paths() -> AppPaths:
    return AppPaths(home=default_home())
