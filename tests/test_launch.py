"""Starting the window with no console, and the desktop shortcut (SPEC D86)."""

from __future__ import annotations

import base64
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from e7ac import shortcut
from e7ac.paths import AppPaths
from e7ac.ui import launch

ROOT = Path(__file__).resolve().parents[1]


def test_the_shortcut_script_survives_any_path() -> None:
    target = Path("C:/Users/O'Brien/Orbis/.venv/Scripts/pythonw.exe")
    text = shortcut.script(target, Path("C:/Users/O'Brien/AppData/Local/OrbisCodex"), Path("C:/x/orbis.ico"))
    assert f"$s.TargetPath = '{str(target).replace(chr(39), chr(39) * 2)}'" in text  # the quote is doubled
    assert "O''Brien" in text and "$s.Arguments = '-m e7ac.ui'" in text
    assert f"$s.IconLocation = '{Path('C:/x/orbis.ico')},0'" in text
    assert "GetFolderPath('Desktop')" in text and "'Orbis Codex.lnk'" in text  # wherever Windows keeps the Desktop
    assert text.rstrip().endswith("Write-Output $link")
    assert base64.b64decode(shortcut.encoded(text)).decode("utf-16-le") == text  # what -EncodedCommand reads


def test_a_shortcut_is_made_by_powershell_and_every_failure_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shortcut, "on_windows", lambda: False)
    with pytest.raises(shortcut.ShortcutError, match="Windows only"):
        shortcut.create_desktop_shortcut(tmp_path, tmp_path / "orbis.ico")
    pythonw = tmp_path / "pythonw.exe"
    monkeypatch.setattr(shortcut, "on_windows", lambda: True)
    monkeypatch.setattr(shortcut, "gui_python", lambda: pythonw)
    with pytest.raises(shortcut.ShortcutError, match="no windowless Python"):
        shortcut.create_desktop_shortcut(tmp_path, tmp_path / "orbis.ico")
    pythonw.write_bytes(b"")
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        assert kwargs["capture_output"] and kwargs["timeout"] == 30
        return subprocess.CompletedProcess(command, 0, "\nC:\\Users\\me\\Desktop\\Orbis Codex.lnk\r\n", "")

    monkeypatch.setattr(subprocess, "run", run)
    made = shortcut.create_desktop_shortcut(tmp_path, tmp_path / "orbis.ico")
    assert str(made) == "C:\\Users\\me\\Desktop\\Orbis Codex.lnk"  # the last line PowerShell wrote
    (command,) = calls
    assert command[:4] == ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand"]
    script = base64.b64decode(command[4]).decode("utf-16-le")
    assert str(pythonw) in script and "-m e7ac.ui" in script and "orbis.ico,0" in script

    failing = subprocess.CompletedProcess(["x"], 1, "", "Exception calling Save\nat line 3")
    monkeypatch.setattr(subprocess, "run", lambda command, **kw: failing)
    with pytest.raises(shortcut.ShortcutError, match="Exception calling Save"):
        shortcut.create_desktop_shortcut(tmp_path, tmp_path / "orbis.ico")
    silent = subprocess.CompletedProcess(["x"], 0, "", "")
    monkeypatch.setattr(subprocess, "run", lambda command, **kw: silent)
    with pytest.raises(shortcut.ShortcutError, match="did not make the shortcut"):
        shortcut.create_desktop_shortcut(tmp_path, tmp_path / "orbis.ico")

    def missing(command: list[str], **kw: Any) -> None:
        raise FileNotFoundError("powershell.exe")

    monkeypatch.setattr(subprocess, "run", missing)
    with pytest.raises(shortcut.ShortcutError, match="could not be run"):
        shortcut.create_desktop_shortcut(tmp_path, tmp_path / "orbis.ico")


def test_the_launchers_the_shortcut_and_the_entry_point_agree() -> None:
    entry = ROOT / "src" / "e7ac" / "ui" / "__main__.py"
    assert entry.is_file() and "launch import main" in entry.read_text(encoding="utf-8")
    assert shortcut.ARGUMENTS == "-m e7ac.ui"  # python -m e7ac.ui  ->  ui/__main__.py
    orbis = (ROOT / "Orbis.cmd").read_text(encoding="utf-8")
    codex = (ROOT / "OrbisCodex.cmd").read_text(encoding="utf-8")
    # the double-click opens the window detached and its console closes: it starts the windowless Python, not `e7 app`
    assert 'OrbisCodex.cmd" window' in orbis and "--console" in orbis and 'OrbisCodex.cmd" app' in orbis
    assert 'pythonw.exe" -m e7ac.ui' in codex and "sync --frozen --no-dev" in codex and 'start ""' in codex
    assert "if errorlevel" not in orbis.replace('"if errorlevel 1"', "")  # negative exit codes are not missed
    assert 'if not "%ORBIS_RC%"=="0"' in orbis
    assert "goto window" in codex and ":window" in codex


def test_a_window_that_cannot_start_says_so_where_it_will_be_seen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    import e7ac.ui.app as window_app

    paths = AppPaths(tmp_path / "home")
    monkeypatch.setattr(launch, "default_paths", lambda: paths)
    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(launch, "show_error", lambda title, message: shown.append((title, message)))
    monkeypatch.setattr(window_app, "run", lambda: 7)
    assert launch.main() == 7 and not shown  # a window that ran: nothing to say

    def broken() -> int:
        raise ImportError("No module named 'PySide6'")

    monkeypatch.setattr(window_app, "run", broken)
    assert launch.main() == 1
    ((title, message),) = shown
    assert title == "Orbis Codex" and "No module named 'PySide6'" in message and "Orbis.cmd" in message
    log = (paths.logs_dir / "app.log").read_text(encoding="utf-8")
    assert "the window could not start" in log and "ImportError" in log  # and the log keeps the whole trace


def test_without_a_console_what_the_program_prints_goes_to_a_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = AppPaths(tmp_path / "home")
    launch.own_streams(paths)  # there is a console: nothing changes
    assert not (paths.logs_dir / "console.log").exists()
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    launch.own_streams(paths)
    assert sys.stdout is not None and sys.stderr is sys.stdout
    print("a message nobody could have seen")
    sys.stdout.flush()
    assert "a message nobody could have seen" in (paths.logs_dir / "console.log").read_text(encoding="utf-8")
    sys.stdout.close()
    # a log that grew too big starts again, one that did not keeps what it has
    log = paths.logs_dir / "console.log"
    log.write_text("x" * (launch.CONSOLE_LOG_MAX + 1), encoding="utf-8")
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    launch.own_streams(paths)
    assert sys.stdout is not None
    sys.stdout.close()
    assert log.stat().st_size == 0


def test_the_shortcut_command_writes_the_icon_file_and_refuses_off_windows(
    isolated_home: AppPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from typer.testing import CliRunner

    from e7ac.cli.app import app

    monkeypatch.setattr(shortcut, "on_windows", lambda: False)
    result = CliRunner().invoke(app, ["shortcut"])
    assert result.exit_code == 2 and "Windows only" in result.stderr
    assert (isolated_home.home / "orbis.ico").is_file()  # the icon file is there for the day it can be used
    monkeypatch.setattr(shortcut, "create_desktop_shortcut", lambda working_dir, ico: Path("C:/D/Orbis Codex.lnk"))
    done = CliRunner().invoke(app, ["shortcut"])
    assert done.exit_code == 0 and "Orbis Codex.lnk" in done.stdout
