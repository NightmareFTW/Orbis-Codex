"""User settings persisted as JSON in the app home (`settings.json`).

Rules: a missing file means defaults; a corrupt or invalid file is an error (never silently reset);
writes are atomic so a crash cannot leave a half-written file.
"""

from __future__ import annotations

import json
import re
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from e7ac.domain.world import World
from e7ac.fileio import write_text_atomic

SETTINGS_SCHEMA_VERSION: Final = 1
_RESOLUTION_RE: Final = re.compile(r"^(\d{3,5})x(\d{3,5})$")


class SettingsError(Exception):
    """The settings file or a requested change is invalid."""


class ClientKind(StrEnum):
    """Which Epic Seven client is captured. Window matching per client arrives with client profiles (M9)."""

    STOVE_PC = "stove_pc"
    STEAM = "steam"
    GOOGLE_PLAY_GAMES = "google_play_games"
    EMULATOR = "emulator"


class DisplayMode(StrEnum):
    BORDERLESS = "borderless"
    WINDOWED = "windowed"
    FULLSCREEN = "fullscreen"


class Settings(BaseModel):
    """User-editable settings. Field names are the keys accepted by `e7 config set`."""

    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=False)

    schema_version: int = SETTINGS_SCHEMA_VERSION
    client: ClientKind = ClientKind.STOVE_PC
    world: World = World.GLOBAL
    display_mode: DisplayMode = DisplayMode.BORDERLESS
    resolution: str | None = None
    """`WIDTHxHEIGHT` of the game window, or None to auto-detect it from the window (default)."""

    @field_validator("schema_version")
    @classmethod
    def _known_schema(cls, value: int) -> int:
        if value != SETTINGS_SCHEMA_VERSION:
            raise ValueError(f"unsupported settings schema_version {value} (expected {SETTINGS_SCHEMA_VERSION})")
        return value

    @field_validator("resolution", mode="before")
    @classmethod
    def _parse_resolution(cls, value: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("resolution must be 'auto' or WIDTHxHEIGHT, e.g. 1920x1080")
        text = value.strip().lower().replace("\u00d7", "x").replace(" ", "")  # accept the multiplication sign
        if text in ("", "auto"):
            return None
        match = _RESOLUTION_RE.match(text)
        if not match:
            raise ValueError("resolution must be 'auto' or WIDTHxHEIGHT, e.g. 1920x1080")
        width, height = int(match.group(1)), int(match.group(2))
        if not (320 <= width <= 16384 and 240 <= height <= 16384):
            raise ValueError(f"resolution {width}x{height} is out of range")
        return f"{width}x{height}"


EDITABLE_KEYS: Final[tuple[str, ...]] = tuple(name for name in Settings.model_fields if name != "schema_version")


def allowed_values(key: str) -> list[str] | None:
    """Allowed values for an enum-typed key (None when the key is free-form)."""
    annotation = Settings.model_fields[key].annotation
    if isinstance(annotation, type) and issubclass(annotation, StrEnum):
        return [member.value for member in annotation]
    return None


def load_settings(path: Path) -> Settings:
    if not path.exists():
        return Settings()
    try:
        # utf-8-sig: accept files saved "with BOM" by Notepad or Windows PowerShell.
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SettingsError(f"cannot read settings file {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise SettingsError(f"settings file {path} must contain a JSON object")
    try:
        return Settings.model_validate(raw)
    except ValidationError as exc:
        raise SettingsError(f"invalid settings file {path}:\n{_format_errors(exc)}") from exc


def save_settings(settings: Settings, path: Path) -> None:
    """Write atomically: unique temp file in the same directory, flushed to disk, then replace the target.

    On failure the previous file is left untouched and the temp file is removed; the OSError propagates.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, json.dumps(settings.model_dump(mode="json"), indent=2, sort_keys=True) + "\n")


def with_value(settings: Settings, key: str, value: str) -> Settings:
    """Return a copy of `settings` with `key` set from its string form, validated like the file."""
    if key not in EDITABLE_KEYS:
        raise SettingsError(f"unknown setting '{key}'. Valid keys: {', '.join(EDITABLE_KEYS)}")
    data = settings.model_dump(mode="json")
    data[key] = value
    try:
        return Settings.model_validate(data)
    except ValidationError as exc:
        choices = allowed_values(key)
        hint = f"\nAllowed values: {', '.join(choices)}" if choices else ""
        raise SettingsError(f"invalid value for '{key}': {value!r}\n{_format_errors(exc)}{hint}") from exc


def _format_errors(exc: ValidationError) -> str:
    lines = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        lines.append(f"  - {location}: {error['msg']}")
    return "\n".join(lines)
