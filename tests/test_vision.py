from __future__ import annotations

import re
import struct
import zlib
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from e7ac.cli import capture as capture_cli
from e7ac.cli.app import app
from e7ac.paths import AppPaths
from e7ac.settings import ClientKind
from e7ac.vision import _win32
from e7ac.vision.capture import CaptureError, Frame, MssBackend, capture_name, frame_warnings, save_png
from e7ac.vision.window import PROFILES, Rect, WindowError, WindowInfo, select_game_window

runner = CliRunner()
SRC = Path(__file__).resolve().parents[1] / "src" / "e7ac"
STOVE = PROFILES[ClientKind.STOVE_PC]


def window(
    hwnd: int = 1,
    title: str = "Epic Seven",
    class_name: str = "GLFW30",
    exe: str | None = "EpicSeven.exe",
    size: tuple[int, int] = (64, 36),
    minimized: bool = False,
) -> WindowInfo:
    return WindowInfo(hwnd, title, class_name, exe, Rect(10, 20, *size), minimized)


def frame_for(win: WindowInfo, pixel: Callable[[int, int], tuple[int, int, int]]) -> Frame:
    w, h = win.client.width, win.client.height
    data = bytearray()
    for y in range(h):
        for x in range(w):
            data.extend(pixel(x, y))
    return Frame(width=w, height=h, rgb=bytes(data), backend="fake", window=win)


def noise(x: int, y: int) -> tuple[int, int, int]:
    return ((x * 37 + y * 11) % 256, (x * 7) % 256, (y * 13) % 256)


# ------------------------------------------------------------------------------------------------ safety guard


FORBIDDEN_CALLS = (
    "OpenProcess",
    "ReadProcessMemory",
    "WriteProcessMemory",
    "VirtualAllocEx",
    "CreateRemoteThread",
    "SendInput",
    "keybd_event",
    "mouse_event",
    "SetWindowsHookEx",
    "SetWindowsHookExW",
    "PostMessageW",
    "SendMessageW",
)
FORBIDDEN_IMPORTS = ("scapy", "pcap", "pyshark", "pyautogui", "pynput", "keyboard", "mouse", "pymem", "frida")


def test_no_source_file_can_touch_the_game() -> None:
    """CLAUDE.md hard rule: no process handles/memory, no input injection, no keyboard hooks, no packet capture."""
    offenders = []
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for name in FORBIDDEN_CALLS:
            if re.search(rf"\b{name}\s*\(|[\"']{name}[\"']", text):
                offenders.append(f"{path.relative_to(SRC)}: {name}")
        for module in FORBIDDEN_IMPORTS:
            if re.search(rf"^\s*(import|from)\s+{module}\b", text, re.MULTILINE):
                offenders.append(f"{path.relative_to(SRC)}: import {module}")
    assert offenders == []


# ------------------------------------------------------------------------------------------------ window selection


def test_stove_profile_recognises_the_game_but_not_lookalikes() -> None:
    assert STOVE.matches(window())
    assert STOVE.matches(window(title=""))  # the client sometimes has an empty title
    assert STOVE.matches(window(title="에픽세븐"))
    assert STOVE.matches(window(class_name="Other", exe="epicseven.EXE"))
    assert not STOVE.matches(window(title="Blender", exe="blender.exe"))  # another GLFW application
    browser = window(title="Epic Seven - Wiki - Chrome", class_name="Chrome_WidgetWin_1", exe="chrome.exe")
    assert not STOVE.matches(browser)
    assert not STOVE.matches(window(title="Epic Seven Guide", exe="EpicSeven.exe"))


def test_selection_never_guesses() -> None:
    with pytest.raises(WindowError, match="--list-windows"):
        select_game_window([window(title="Notepad", class_name="Notepad", exe="notepad.exe")], STOVE)
    with pytest.raises(WindowError, match="several windows"):
        select_game_window([window(hwnd=1), window(hwnd=2, title="")], STOVE)
    assert select_game_window([window(hwnd=1), window(hwnd=2, title="")], STOVE, hwnd=2).hwnd == 2
    with pytest.raises(WindowError, match="no visible window with handle 9"):
        select_game_window([window()], STOVE, hwnd=9)
    with pytest.raises(WindowError, match="minimised"):
        select_game_window([window(minimized=True)], STOVE)
    with pytest.raises(WindowError, match="no visible client area"):
        select_game_window([window(size=(0, 0))], STOVE)


@pytest.mark.parametrize(
    ("text", "modifiers", "vk"),
    [
        ("ctrl+shift+s", _win32.MOD_CONTROL | _win32.MOD_SHIFT, ord("S")),
        ("F9", 0, 0x78),
        ("alt + 1", _win32.MOD_ALT, ord("1")),
        ("win+f24", _win32.MOD_WIN, 0x87),
    ],
)
def test_hotkey_parsing(text: str, modifiers: int, vk: int) -> None:
    hotkey = _win32.parse_hotkey(text)
    assert (hotkey.modifiers, hotkey.vk) == (modifiers, vk)


@pytest.mark.parametrize("text", ["", "ctrl+", "hyper+s", "ctrl+enter", "f25", "ctrl+é"])
def test_bad_hotkeys_are_refused(text: str) -> None:
    with pytest.raises(ValueError):
        _win32.parse_hotkey(text)


# ------------------------------------------------------------------------------------------------ frames and PNG


def test_frame_size_must_match_its_data() -> None:
    with pytest.raises(CaptureError, match="expected"):
        Frame(width=2, height=2, rgb=b"\x00" * 11, backend="fake", window=window())


def test_blank_or_scaled_frames_are_reported() -> None:
    assert frame_warnings(frame_for(window(), noise)) == []
    black = frame_warnings(frame_for(window(), lambda x, y: (0, 0, 0)))
    assert len(black) == 1 and "blank" in black[0]
    small = frame_for(window(size=(32, 18)), noise)
    scaled = Frame(width=32, height=18, rgb=small.rgb, backend="fake", window=window(size=(64, 36)))
    assert any("display scaling" in w for w in frame_warnings(scaled))


def _png_pixels(path: Path) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, width, height = 8, b"", 0, 0
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        kind, body = data[pos + 4 : pos + 8], data[pos + 8 : pos + 8 + length]
        if kind == b"IHDR":
            width, height, depth, colour = struct.unpack(">IIBB", body[:10])
            assert (depth, colour) == (8, 2)  # 8-bit RGB
        elif kind == b"IDAT":
            idat += body
        pos += 12 + length
    raw = zlib.decompress(idat)
    rows = [raw[r * (width * 3 + 1) : (r + 1) * (width * 3 + 1)] for r in range(height)]
    assert all(row[0] == 0 for row in rows)  # filter type "none"
    return width, height, b"".join(row[1:] for row in rows)


def test_png_is_lossless(tmp_path: Path) -> None:
    frame = frame_for(window(size=(17, 9)), noise)
    path = save_png(frame, tmp_path / "sub" / "shot.png")
    assert _png_pixels(path) == (17, 9, frame.rgb)


def test_capture_names_are_safe_and_unique() -> None:
    when = datetime(2026, 10, 4, 10, 30, 0, tzinfo=UTC)
    taken = {"20261004-103000-hero_info.png"}
    assert capture_name(when, "hero_info", taken.__contains__) == "20261004-103000-hero_info-2.png"
    assert capture_name(when, "../../evil name", lambda _: False) == "20261004-103000-______evil_name.png"
    assert capture_name(when, None, lambda _: False) == "20261004-103000-screen.png"


# ------------------------------------------------------------------------------------------------ CLI


class FakeBackend:
    name = "fake"

    def __init__(self, pixel: Callable[[int, int], tuple[int, int, int]] = noise) -> None:
        self.pixel = pixel
        self.grabs: list[int] = []

    def grab(self, win: WindowInfo) -> Frame:
        self.grabs.append(win.hwnd)
        return frame_for(win, self.pixel)


@pytest.fixture
def fake_screen(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[list[WindowInfo], FakeBackend]]:
    windows = [window(), window(hwnd=7, title="Notepad", class_name="Notepad", exe="notepad.exe")]
    backend = FakeBackend()
    monkeypatch.setattr(capture_cli, "window_lister", lambda: list(windows))
    monkeypatch.setattr(capture_cli, "backend_factory", lambda: backend)
    monkeypatch.setattr(capture_cli, "prepare_process", lambda: None)
    yield windows, backend


def test_one_shot_capture_saves_a_png_in_the_captures_folder(
    fake_screen: tuple[list[WindowInfo], FakeBackend], isolated_home: AppPaths
) -> None:
    result = runner.invoke(app, ["capture", "hero_info"])
    assert result.exit_code == 0, result.output
    saved = list(isolated_home.captures_dir.glob("*-hero_info.png"))
    assert len(saved) == 1 and f"Saved {saved[0]}" in result.stdout and "64x36" in result.stdout
    assert _png_pixels(saved[0])[:2] == (64, 36)


def test_capture_errors_are_explained(
    fake_screen: tuple[list[WindowInfo], FakeBackend], isolated_home: AppPaths
) -> None:
    windows, backend = fake_screen
    windows[0] = window(minimized=True)
    minimised = runner.invoke(app, ["capture"])
    assert minimised.exit_code == 1 and "minimised" in minimised.stderr
    windows[0] = window(title="Notepad", class_name="Notepad", exe="notepad.exe")
    missing = runner.invoke(app, ["capture"])
    assert missing.exit_code == 1 and "--list-windows" in missing.stderr
    assert backend.grabs == [] and not isolated_home.captures_dir.exists()


def test_blank_capture_is_saved_but_flagged(
    fake_screen: tuple[list[WindowInfo], FakeBackend], isolated_home: AppPaths
) -> None:
    fake_screen[1].pixel = lambda x, y: (0, 0, 0)
    result = runner.invoke(app, ["capture"])
    assert result.exit_code == 0 and "blank" in result.stderr


def test_list_windows_marks_the_game(fake_screen: tuple[list[WindowInfo], FakeBackend]) -> None:
    result = runner.invoke(app, ["capture", "--list-windows"])
    lines = result.stdout.splitlines()
    assert result.exit_code == 0 and lines[1].startswith("*") and "Epic Seven" in lines[1]
    assert lines[2].startswith(" ") and "Notepad" in lines[2]


def test_hotkey_mode_captures_on_each_press(
    fake_screen: tuple[list[WindowInfo], FakeBackend], isolated_home: AppPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[_win32.Hotkey] = []

    def listener(hotkey: _win32.Hotkey, on_press: Callable[[], None]) -> None:
        seen.append(hotkey)
        on_press()
        on_press()
        raise KeyboardInterrupt

    monkeypatch.setattr(capture_cli, "hotkey_listener", listener)
    result = runner.invoke(app, ["capture", "gear", "--hotkey", "ctrl+shift+s"])
    assert result.exit_code == 0, result.output
    assert seen[0].text == "ctrl+shift+s" and "2 capture(s) saved" in result.stdout
    assert len(list(isolated_home.captures_dir.glob("*-gear*.png"))) == 2
    bad = runner.invoke(app, ["capture", "--hotkey", "ctrl+enter"])
    assert bad.exit_code == 2 and "unsupported key" in bad.stderr


def test_hotkey_already_taken_is_a_clean_error(
    fake_screen: tuple[list[WindowInfo], FakeBackend], monkeypatch: pytest.MonkeyPatch
) -> None:
    def taken(hotkey: _win32.Hotkey, on_press: Callable[[], None]) -> None:
        raise WindowError("cannot register the hotkey f9: another program already uses it (try another)")

    monkeypatch.setattr(capture_cli, "hotkey_listener", taken)
    result = runner.invoke(app, ["capture", "--hotkey", "F9"])
    assert result.exit_code == 2 and "already uses it" in result.stderr


# ------------------------------------------------------------------------------------------------ real Windows


@pytest.mark.windows
def test_windows_lists_real_windows() -> None:
    _win32.set_dpi_aware()
    windows = _win32.list_windows()
    assert isinstance(windows, list)
    assert all(w.client.width >= 0 and w.client.height >= 0 and w.hwnd > 0 for w in windows)


@pytest.mark.windows
def test_windows_mss_grabs_the_screen() -> None:
    _win32.set_dpi_aware()
    frame = MssBackend().grab(WindowInfo(1, "desktop", "test", None, Rect(0, 0, 40, 30)))
    assert (frame.width, frame.height, len(frame.rgb)) == (40, 30, 40 * 30 * 3)
