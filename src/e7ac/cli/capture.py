"""`e7 capture`: save the game picture as PNG (one shot, or on a global hotkey while you browse the game)."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from e7ac.paths import default_paths
from e7ac.settings import ClientKind, SettingsError, load_settings
from e7ac.vision import _win32
from e7ac.vision.capture import (
    CaptureBackend,
    CaptureError,
    MssBackend,
    capture_name,
    describe,
    frame_warnings,
    save_png,
)
from e7ac.vision.window import PROFILES, WindowError, WindowInfo, select_game_window

# Factories replaced by tests (no Windows needed there).
window_lister: Callable[[], list[WindowInfo]] = _win32.list_windows
backend_factory: Callable[[], CaptureBackend] = MssBackend
hotkey_listener: Callable[[_win32.Hotkey, Callable[[], None]], None] = _win32.listen_hotkey
prepare_process: Callable[[], None] = _win32.set_dpi_aware


def _fail(message: str) -> typer.Exit:
    typer.echo(f"Error: {message}", err=True)
    return typer.Exit(code=2)


def capture(
    label: Annotated[
        str | None, typer.Argument(help="Optional label for the file name, e.g. hero_info, gear_detail.")
    ] = None,
    list_windows: Annotated[bool, typer.Option("--list-windows", help="Show the open windows and exit.")] = False,
    hwnd: Annotated[int | None, typer.Option(help="Capture this window (handle from --list-windows).")] = None,
    hotkey: Annotated[
        str | None, typer.Option(help="Stay running and capture each time this key is pressed, e.g. ctrl+shift+s.")
    ] = None,
    delay: Annotated[float, typer.Option(min=0, max=60, help="Seconds to wait before a one-shot capture.")] = 0.0,
    out: Annotated[Path | None, typer.Option(help="Folder for the PNG files (default: the captures folder).")] = None,
) -> None:
    """Save the game picture as PNG. Read-only: it only copies screen pixels and never touches the game.

    Captures stay on your PC (default folder: see `e7 paths`); they are never uploaded."""
    paths = default_paths()
    try:
        client = load_settings(paths.settings_file).client
    except SettingsError as exc:
        raise _fail(str(exc)) from exc
    prepare_process()
    if list_windows:
        _print_windows(client)
        return
    folder = out or paths.captures_dir
    backend = backend_factory()
    if hotkey is None:
        if delay:
            typer.echo(f"Capturing in {delay:g} s - switch to the game now...")
            time.sleep(delay)
        if not _capture_once(client, hwnd, backend, folder, label):
            raise typer.Exit(code=1)
        return
    try:
        key = _win32.parse_hotkey(hotkey)
    except ValueError as exc:
        raise _fail(f"--hotkey {hotkey!r}: {exc}") from exc
    typer.echo(f"Press {key.text} in the game to capture (saved to {folder}). Press Ctrl+C here to stop.")
    count = 0

    def on_press() -> None:
        nonlocal count
        if _capture_once(client, hwnd, backend, folder, label):
            count += 1

    try:
        hotkey_listener(key, on_press)
    except WindowError as exc:
        raise _fail(str(exc)) from exc
    except KeyboardInterrupt:
        pass
    typer.echo(f"Stopped. {count} capture(s) saved.")


def _capture_once(
    client: ClientKind, hwnd: int | None, backend: CaptureBackend, folder: Path, label: str | None
) -> bool:
    try:
        window = select_game_window(window_lister(), PROFILES[client], hwnd)
        frame = backend.grab(window)
    except (WindowError, CaptureError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        return False
    name = capture_name(frame.captured_at, label, lambda n: (folder / n).exists())
    try:
        path = save_png(frame, folder / name)
    except OSError as exc:
        typer.echo(f"Error: cannot write {folder / name}: {exc.strerror or exc}", err=True)
        return False
    typer.echo(f"Saved {path} ({frame.width}x{frame.height}, {frame.backend}) - {describe(window)}")
    for warning in frame_warnings(frame):
        typer.echo(f"  warning: {warning}", err=True)
    return True


def _print_windows(client: ClientKind) -> None:
    try:
        windows = window_lister()
    except WindowError as exc:
        raise _fail(str(exc)) from exc
    profile = PROFILES[client]
    typer.echo(f"Visible windows ({len(windows)}); '*' = looks like the game for client '{client.value}':")
    for window in sorted(windows, key=lambda w: (not profile.matches(w), w.title.casefold())):
        mark = "*" if profile.matches(window) else " "
        state = " (minimised)" if window.minimized else ""
        typer.echo(f"{mark} {window.hwnd:>10}  {describe(window)}{state}")
    typer.echo(f"Started at {datetime.now(UTC).strftime('%H:%M:%S UTC')}. Use --hwnd <number> to pick a window.")
