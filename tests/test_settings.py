from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from e7ac.domain.world import World
from e7ac.paths import AppPaths
from e7ac.settings import (
    EDITABLE_KEYS,
    ClientKind,
    DisplayMode,
    Settings,
    SettingsError,
    allowed_values,
    load_settings,
    save_settings,
    with_value,
)


def test_missing_file_gives_defaults(isolated_home: AppPaths) -> None:
    settings = load_settings(isolated_home.settings_file)
    assert settings == Settings()
    assert settings.client is ClientKind.STOVE_PC
    assert settings.world is World.GLOBAL
    assert settings.display_mode is DisplayMode.BORDERLESS
    assert settings.resolution is None


def test_corrupt_json_is_an_error_not_a_reset(isolated_home: AppPaths) -> None:
    isolated_home.home.mkdir(parents=True)
    isolated_home.settings_file.write_text("{not json", encoding="utf-8")
    with pytest.raises(SettingsError, match="cannot read settings file"):
        load_settings(isolated_home.settings_file)
    assert isolated_home.settings_file.read_text(encoding="utf-8") == "{not json"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("[]", "must contain a JSON object"),
        ('{"client": "playstation"}', "client"),
        ('{"colour": "blue"}', "colour"),
        ('{"schema_version": 99}', "schema_version"),
        ('{"resolution": "big"}', "resolution"),
    ],
)
def test_invalid_content_is_reported(isolated_home: AppPaths, content: str, message: str) -> None:
    isolated_home.home.mkdir(parents=True)
    isolated_home.settings_file.write_text(content, encoding="utf-8")
    with pytest.raises(SettingsError, match=message):
        load_settings(isolated_home.settings_file)


def test_save_is_atomic_and_leaves_no_temp_file(isolated_home: AppPaths) -> None:
    save_settings(Settings(world=World.EUROPE), isolated_home.settings_file)
    assert load_settings(isolated_home.settings_file).world is World.EUROPE
    assert [p.name for p in isolated_home.home.iterdir()] == ["settings.json"]
    assert json.loads(isolated_home.settings_file.read_text(encoding="utf-8"))["world"] == "world_eu"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1920x1080", "1920x1080"),
        (" 2560 X 1440 ", "2560x1440"),
        ("3840\u00d72160", "3840x2160"),
        ("auto", None),
        ("", None),
    ],
)
def test_resolution_normalisation(raw: str, expected: str | None) -> None:
    assert with_value(Settings(), "resolution", raw).resolution == expected


@pytest.mark.parametrize("raw", ["1920", "1920x", "x1080", "99x99", "100000x1080", "1920x1080x2", "-1920x1080"])
def test_resolution_rejects_garbage(raw: str) -> None:
    with pytest.raises(SettingsError, match="resolution"):
        with_value(Settings(), "resolution", raw)


def test_unknown_key_lists_valid_keys() -> None:
    with pytest.raises(SettingsError, match="Valid keys: client"):
        with_value(Settings(), "region", "world_eu")


def test_schema_version_is_not_editable() -> None:
    assert "schema_version" not in EDITABLE_KEYS
    with pytest.raises(SettingsError, match="unknown setting"):
        with_value(Settings(), "schema_version", "2")


def test_invalid_enum_value_lists_allowed_values() -> None:
    with pytest.raises(SettingsError, match="Allowed values: world_global, world_eu"):
        with_value(Settings(), "world", "world_mars")


def test_allowed_values() -> None:
    assert allowed_values("client") == ["stove_pc", "steam", "google_play_games", "emulator"]
    assert allowed_values("resolution") is None


resolutions = st.one_of(
    st.none(),
    st.builds(lambda w, h: f"{w}x{h}", st.integers(320, 16384), st.integers(240, 16384)),
)


@given(
    client=st.sampled_from(ClientKind),
    world=st.sampled_from(World),
    display=st.sampled_from(DisplayMode),
    resolution=resolutions,
)
def test_roundtrip_through_file(
    tmp_path_factory: pytest.TempPathFactory,
    client: ClientKind,
    world: World,
    display: DisplayMode,
    resolution: str | None,
) -> None:
    path: Path = tmp_path_factory.mktemp("rt") / "settings.json"
    original = Settings(client=client, world=world, display_mode=display, resolution=resolution)
    save_settings(original, path)
    assert load_settings(path) == original


@given(key=st.sampled_from([k for k in EDITABLE_KEYS if allowed_values(k)]), data=st.data())
def test_with_value_accepts_every_allowed_value(key: str, data: st.DataObject) -> None:
    choices = allowed_values(key)
    assert choices is not None
    value = data.draw(st.sampled_from(choices))
    updated = with_value(Settings(), key, value)
    assert updated.model_dump(mode="json")[key] == value


def test_bom_file_from_notepad_is_accepted(isolated_home: AppPaths) -> None:
    isolated_home.home.mkdir(parents=True)
    isolated_home.settings_file.write_text('﻿{"world": "world_eu"}', encoding="utf-8")
    assert load_settings(isolated_home.settings_file).world is World.EUROPE


def test_failed_save_keeps_previous_file_and_no_temp(isolated_home: AppPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    save_settings(Settings(world=World.EUROPE), isolated_home.settings_file)

    def refuse(self: Path, target: Path) -> Path:
        raise PermissionError("locked by another process")

    monkeypatch.setattr(Path, "replace", refuse)
    with pytest.raises(PermissionError):
        save_settings(Settings(world=World.JAPAN), isolated_home.settings_file)
    monkeypatch.undo()
    assert load_settings(isolated_home.settings_file).world is World.EUROPE
    assert [p.name for p in isolated_home.home.iterdir()] == ["settings.json"]
