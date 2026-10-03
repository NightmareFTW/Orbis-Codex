from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from e7ac import __version__
from e7ac.cli.app import app
from e7ac.paths import HOME_ENV_VAR, AppPaths

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"e7 (Orbis Codex) {__version__}"


def test_no_arguments_shows_help() -> None:
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "Usage" in result.stdout
    assert "config" in result.stdout


def test_config_show_defaults_without_creating_a_file(isolated_home: AppPaths) -> None:
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0, result.output
    shown = json.loads(result.stdout)
    assert shown["client"] == "stove_pc"
    assert shown["world"] == "world_global"
    assert shown["display_mode"] == "borderless"
    assert shown["resolution"] is None
    assert not isolated_home.settings_file.exists()


def test_config_set_persists(isolated_home: AppPaths) -> None:
    assert runner.invoke(app, ["config", "set", "client", "steam"]).exit_code == 0
    result = runner.invoke(app, ["config", "set", "resolution", "2560x1440"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "resolution = 2560x1440"
    shown = json.loads(runner.invoke(app, ["config", "show"]).stdout)
    assert shown["client"] == "steam"
    assert shown["resolution"] == "2560x1440"
    assert isolated_home.settings_file.exists()


def test_config_set_auto_resolution_prints_auto() -> None:
    result = runner.invoke(app, ["config", "set", "resolution", "auto"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "resolution = auto"


def test_config_set_rejects_bad_value_without_writing(isolated_home: AppPaths) -> None:
    result = runner.invoke(app, ["config", "set", "world", "world_mars"])
    assert result.exit_code == 2
    assert "Allowed values" in result.stderr
    assert not isolated_home.settings_file.exists()


def test_config_set_rejects_unknown_key() -> None:
    result = runner.invoke(app, ["config", "set", "region", "world_eu"])
    assert result.exit_code == 2
    assert "Valid keys" in result.stderr


def test_corrupt_settings_file_is_reported_not_reset(isolated_home: AppPaths) -> None:
    isolated_home.home.mkdir(parents=True)
    isolated_home.settings_file.write_text("{oops", encoding="utf-8")
    result = runner.invoke(app, ["config", "set", "client", "steam"])
    assert result.exit_code == 2
    assert "cannot read settings file" in result.stderr
    assert isolated_home.settings_file.read_text(encoding="utf-8") == "{oops"


def test_config_options_lists_keys() -> None:
    result = runner.invoke(app, ["config", "options"])
    assert result.exit_code == 0
    assert "world" in result.stdout and "world_jpn" in result.stdout
    assert "WIDTHxHEIGHT" in result.stdout


def test_paths_lists_home(isolated_home: AppPaths) -> None:
    result = runner.invoke(app, ["paths"])
    assert result.exit_code == 0
    assert str(isolated_home.home) in result.stdout


def test_doctor_ok_on_clean_home(isolated_home: AppPaths) -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "[OK  ] python" in result.stdout
    assert "[OK  ] settings" in result.stdout
    assert isolated_home.home.is_dir()


def test_doctor_fails_on_corrupt_settings(isolated_home: AppPaths) -> None:
    isolated_home.home.mkdir(parents=True)
    isolated_home.settings_file.write_text('{"client": "playstation"}', encoding="utf-8")
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "[FAIL] settings" in result.stdout


def test_doctor_shows_base_interpreter() -> None:
    result = runner.invoke(app, ["doctor"])
    assert "base interpreter:" in result.stdout


def test_doctor_fails_when_data_dir_unusable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv(HOME_ENV_VAR, str(blocker / "home"))
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "[FAIL] data dir" in result.stdout


def test_config_set_reports_write_errors_without_traceback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv(HOME_ENV_VAR, str(blocker / "home"))
    result = runner.invoke(app, ["config", "set", "world", "world_eu"])
    assert result.exit_code == 2
    assert "cannot write settings file" in result.stderr
    assert "Traceback" not in result.output
