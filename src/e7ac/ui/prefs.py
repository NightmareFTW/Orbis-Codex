"""Reading and writing the user's settings from the window: one place for what every page needs (SPEC D85).

A settings file that cannot be read is never replaced by defaults behind the user's back: `load` returns the defaults to
work with and says why, `store` refuses to write over it."""

from __future__ import annotations

from e7ac.paths import AppPaths
from e7ac.settings import Settings, SettingsError, load_settings, save_settings, with_value


def load(paths: AppPaths) -> tuple[Settings, str]:
    """The settings, and "" - or the defaults and why the file could not be read."""
    try:
        return load_settings(paths.settings_file), ""
    except SettingsError as exc:
        return Settings(), str(exc)


def store(paths: AppPaths, key: str, value: str) -> str:
    """Save one setting: "" when saved (a value that is the saved one already writes nothing), else why it was not."""
    try:
        current = load_settings(paths.settings_file)
        updated = with_value(current, key, value)
        if updated != current:
            save_settings(updated, paths.settings_file)
    except (SettingsError, OSError) as exc:
        return str(exc)
    return ""
