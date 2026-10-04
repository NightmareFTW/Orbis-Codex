"""Environment self-check (`e7 doctor`): reports problems instead of failing later in obscure ways."""

from __future__ import annotations

import platform
import sys
import tempfile
from dataclasses import dataclass
from enum import StrEnum

from e7ac import __version__
from e7ac.paths import AppPaths
from e7ac.settings import SettingsError, load_settings
from e7ac.vision import _win32
from e7ac.vision.window import PROFILES, WindowError, select_game_window

MIN_PYTHON = (3, 12)


class CheckStatus(StrEnum):
    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    status: CheckStatus
    detail: str


def run_checks(paths: AppPaths) -> list[CheckResult]:
    return [
        CheckResult("version", CheckStatus.OK, f"e7ac {__version__}"),
        _check_python(),
        _check_os(),
        _check_home(paths),
        _check_settings(paths),
        *_check_game_window(paths),
    ]


def _check_python() -> CheckResult:
    version = ".".join(str(part) for part in sys.version_info[:3])
    detail = f"{version} (base interpreter: {sys.base_prefix})"
    if sys.version_info[:2] >= MIN_PYTHON:
        return CheckResult("python", CheckStatus.OK, detail)
    return CheckResult("python", CheckStatus.FAIL, f"{detail}; need >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}")


WINDOWS_11_BUILD = 22000
"""Windows 11 still reports version '10.0'; its build numbers start at 22000."""


def _check_os() -> CheckResult:
    system = platform.system()
    if system == "Windows":
        return CheckResult("os", CheckStatus.OK, windows_name(platform.version()))
    return CheckResult("os", CheckStatus.WARN, f"{system}: core/CLI features work; capture and overlay need Windows")


def windows_name(version: str) -> str:
    """'10.0.26100' -> 'Windows 11 (build 26100)'."""
    parts = version.split(".")
    build = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else None
    if build is None:
        return f"Windows (version {version})"
    return f"Windows {11 if build >= WINDOWS_11_BUILD else 10} (build {build})"


def _check_home(paths: AppPaths) -> CheckResult:
    try:
        paths.ensure()
        with tempfile.TemporaryFile(dir=paths.home):
            pass
    except OSError as exc:
        return CheckResult("data dir", CheckStatus.FAIL, f"{paths.home} is not writable: {exc}")
    return CheckResult("data dir", CheckStatus.OK, str(paths.home))


def _check_settings(paths: AppPaths) -> CheckResult:
    try:
        settings = load_settings(paths.settings_file)
    except SettingsError as exc:
        return CheckResult("settings", CheckStatus.FAIL, str(exc))
    source = "file" if paths.settings_file.exists() else "defaults"
    return CheckResult(
        "settings",
        CheckStatus.OK,
        f"{source}: client={settings.client.value}, world={settings.world.value}, "
        f"display={settings.display_mode.value}, resolution={settings.resolution or 'auto'}, "
        f"game language={settings.game_language.value}",
    )


def _check_game_window(paths: AppPaths) -> list[CheckResult]:
    """Windows only: is the game window visible for capture? (Read-only: window titles/classes only.)"""
    if sys.platform == "win32":
        return [_game_window_result(paths)]
    else:
        return []


def _game_window_result(paths: AppPaths) -> CheckResult:
    try:
        client = load_settings(paths.settings_file).client
        _win32.set_dpi_aware()
        game = select_game_window(_win32.list_windows(), PROFILES[client])
    except (SettingsError, WindowError) as exc:
        return CheckResult("game window", CheckStatus.WARN, f"{exc}")
    size = f"{game.client.width}x{game.client.height}"
    return CheckResult("game window", CheckStatus.OK, f"{game.title or '(no title)'} {size} ({game.exe or '?'})")
