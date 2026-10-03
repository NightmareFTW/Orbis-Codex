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
    ]


def _check_python() -> CheckResult:
    version = ".".join(str(part) for part in sys.version_info[:3])
    detail = f"{version} (base interpreter: {sys.base_prefix})"
    if sys.version_info[:2] >= MIN_PYTHON:
        return CheckResult("python", CheckStatus.OK, detail)
    return CheckResult("python", CheckStatus.FAIL, f"{detail}; need >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}")


def _check_os() -> CheckResult:
    system = platform.system()
    if system == "Windows":
        return CheckResult("os", CheckStatus.OK, f"Windows {platform.release()}")
    return CheckResult("os", CheckStatus.WARN, f"{system}: core/CLI features work; capture and overlay need Windows")


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
        f"display={settings.display_mode.value}, resolution={settings.resolution or 'auto'}",
    )
