"""A desktop shortcut that opens the window with no console (SPEC D86).

The shortcut starts the environment's `pythonw.exe` (the Python that has no console window) on the window's entry point,
and wears the program's icon, which the shell reads from `orbis.ico` in the data folder. It is made by Windows' own
PowerShell (a command line that is passed encoded, so no path can break its quoting); nothing else is installed.
"""

from __future__ import annotations

import base64
import subprocess
import sys
from pathlib import Path
from typing import Final

NAME: Final = "Orbis Codex.lnk"
ARGUMENTS: Final = "-m e7ac.ui"
"""`python -m e7ac.ui`: the window's entry point (`ui/__main__.py`)."""
NO_WINDOW: Final = 0x08000000
"""`CREATE_NO_WINDOW`: PowerShell works without flashing a console."""


class ShortcutError(Exception):
    """The shortcut could not be made, and why."""


def on_windows() -> bool:
    """A function, so that tests (and the type checker) can treat the platform as a value."""
    return sys.platform == "win32"


def gui_python() -> Path:
    """The `pythonw.exe` next to the Python that runs this program (the same environment, no console)."""
    return Path(sys.executable).with_name("pythonw.exe")


def _quoted(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def script(target: Path, working_dir: Path, icon: Path, name: str = NAME, arguments: str = ARGUMENTS) -> str:
    """The PowerShell that makes the shortcut on the user's Desktop (wherever Windows keeps it: OneDrive too) and says
    where it is."""
    return "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            "$link = Join-Path ([Environment]::GetFolderPath('Desktop')) " + _quoted(name),
            "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($link)",
            f"$s.TargetPath = {_quoted(str(target))}",
            f"$s.Arguments = {_quoted(arguments)}",
            f"$s.WorkingDirectory = {_quoted(str(working_dir))}",
            f"$s.IconLocation = {_quoted(str(icon) + ',0')}",
            "$s.Description = 'Orbis Codex: the Epic Seven Arena companion'",
            "$s.Save()",
            "Write-Output $link",
        ]
    )


def encoded(text: str) -> str:
    """What PowerShell's `-EncodedCommand` takes: the text in UTF-16, as base 64."""
    return base64.b64encode(text.encode("utf-16-le")).decode("ascii")


def create_desktop_shortcut(working_dir: Path, icon: Path) -> Path:
    """Make (or replace) the shortcut on the Desktop; where it is. `ShortcutError` when it cannot be made."""
    if not on_windows():
        raise ShortcutError("Shortcuts are made on Windows only.")
    target = gui_python()
    if not target.is_file():
        raise ShortcutError(f"{target} does not exist: this installation has no windowless Python.")
    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-EncodedCommand",
        encoded(script(target, working_dir, icon)),
    ]
    try:
        done = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", NO_WINDOW),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ShortcutError(f"PowerShell could not be run: {exc}") from exc
    lines = [line.strip() for line in done.stdout.splitlines() if line.strip()]
    if done.returncode != 0 or not lines:
        reason = next(
            (line.strip() for line in done.stderr.splitlines() if line.strip()), f"exit code {done.returncode}"
        )
        raise ShortcutError(f"PowerShell did not make the shortcut: {reason}")
    return Path(lines[-1])
