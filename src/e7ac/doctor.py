"""Environment self-check (`e7 doctor`): reports problems instead of failing later in obscure ways."""

from __future__ import annotations

import platform
import sys
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from e7ac import __version__
from e7ac.paths import AppPaths
from e7ac.settings import SettingsError, load_settings
from e7ac.sources.assets import load_set_icons
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
        _check_ocr(),
        _check_set_icons(paths),
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


def _check_ocr() -> CheckResult:
    """The OCR engine loads (onnxruntime needs the Microsoft Visual C++ runtime on Windows)."""
    try:
        import cv2
        import onnxruntime
        import rapidocr  # noqa: F401
    except Exception as exc:  # any import failure (missing DLL, broken install) must be reported, not raised
        return CheckResult(
            "ocr",
            CheckStatus.FAIL,
            f"the OCR engine does not load ({type(exc).__name__}: {exc}). On Windows, install the Microsoft "
            "Visual C++ Redistributable (x64), then run this again",
        )
    versions = f"RapidOCR, onnxruntime {onnxruntime.__version__}, OpenCV {cv2.__version__}"
    return CheckResult("ocr", CheckStatus.OK, versions)


SET_ICONS_SHOWN: Final = 6
"""How many missing set codes the check names before abbreviating."""


def _check_set_icons(paths: AppPaths) -> CheckResult:
    """Every set of the current catalog has its icon cached (needed to read gear sets on Hero Info, M7)."""
    name = "set icons"
    if not paths.database.is_file():
        return CheckResult(name, CheckStatus.WARN, "no catalog yet: run e7 catalog sync")
    try:
        codes = _catalog_set_codes(paths)
    except Exception as exc:  # a doctor reports problems, it never raises (old schema, locked or damaged file...)
        detail = f"cannot read the catalog ({type(exc).__name__}): run e7 catalog sync"
        return CheckResult(name, CheckStatus.WARN, detail)
    if not codes:
        return CheckResult(name, CheckStatus.WARN, "the current catalog has no sets: run e7 catalog sync")
    cached = load_set_icons(paths.cache_dir, codes)
    detail = f"{len(cached)}/{len(codes)} cached"
    missing = sorted(set(codes) - cached.keys())
    if not missing:
        return CheckResult(name, CheckStatus.OK, detail)
    listed = ", ".join(missing[:SET_ICONS_SHOWN]) + (", ..." if len(missing) > SET_ICONS_SHOWN else "")
    return CheckResult(name, CheckStatus.WARN, f"{detail}, missing {listed}: run e7 catalog sync")


def _catalog_set_codes(paths: AppPaths) -> list[str]:
    """Set codes of the current snapshot, read without migrating the database (the doctor changes nothing)."""
    from e7ac.catalog.facts import EntityType
    from e7ac.catalog.store import current_snapshot, load_entities
    from e7ac.storage.db import make_engine, session_scope

    engine = make_engine(paths.database)
    try:
        with session_scope(engine) as session:
            snapshot = current_snapshot(session)
            if snapshot is None:
                return []
            return [entity.entity_id for entity in load_entities(session, snapshot.id, EntityType.SET)]
    finally:
        engine.dispose()


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
