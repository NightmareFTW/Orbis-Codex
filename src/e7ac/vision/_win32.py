"""Windows-only helpers (ctypes): DPI awareness, read-only window list, global hotkey.

Read-only by construction: window titles/classes/rectangles (user32, dwmapi) and executable names from a Toolhelp32
process snapshot. We never call OpenProcess on the game (anti-cheat software watches handles to the game process),
never read memory, never send input. The hotkey uses RegisterHotKey - no keyboard hook.
"""

from __future__ import annotations

import ctypes
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from e7ac.vision.window import Rect, WindowError, WindowInfo

MOD_ALT: Final = 0x0001
MOD_CONTROL: Final = 0x0002
MOD_SHIFT: Final = 0x0004
MOD_WIN: Final = 0x0008
MOD_NOREPEAT: Final = 0x4000
_MODIFIERS: Final = {"alt": MOD_ALT, "ctrl": MOD_CONTROL, "control": MOD_CONTROL, "shift": MOD_SHIFT, "win": MOD_WIN}


@dataclass(frozen=True, slots=True)
class Hotkey:
    modifiers: int
    vk: int
    text: str


def parse_hotkey(text: str) -> Hotkey:
    """'ctrl+shift+s', 'F9', 'alt+1' -> RegisterHotKey arguments (letters, digits and F1-F24 only)."""
    parts = [p.strip().casefold() for p in text.split("+") if p.strip()]
    if not parts:
        raise ValueError("empty hotkey")
    *mods, key = parts
    modifiers = 0
    for mod in mods:
        if mod not in _MODIFIERS:
            raise ValueError(f"unknown modifier {mod!r} (use ctrl, shift, alt, win)")
        modifiers |= _MODIFIERS[mod]
    if len(key) == 1 and (key.isascii() and key.isalnum()):
        vk = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    else:
        raise ValueError(f"unsupported key {key!r} (use a letter, a digit or F1-F24)")
    return Hotkey(modifiers, vk, "+".join([*mods, key]))


def set_dpi_aware() -> None:
    """Per-monitor DPI awareness, so window rectangles and captures use the same physical pixels."""
    if sys.platform == "win32":
        _set_dpi_aware()


def list_windows() -> list[WindowInfo]:
    """Visible, non-cloaked top-level windows with their client rectangles (screen coordinates)."""
    if sys.platform == "win32":
        return _list_windows()
    else:
        raise WindowError("finding the game window needs Windows")


def listen_hotkey(hotkey: Hotkey, on_press: Callable[[], None], *, poll: float = 0.05) -> None:
    """Call `on_press` each time the global hotkey is pressed, until KeyboardInterrupt (Ctrl+C in the console).

    RegisterHotKey delivers WM_HOTKEY to this thread's message queue; polling keeps Ctrl+C responsive."""
    if sys.platform == "win32":
        _listen_hotkey(hotkey, on_press, poll)
    else:
        raise WindowError("global hotkeys need Windows")


if sys.platform == "win32":
    from ctypes import wintypes

    def _set_dpi_aware() -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        set_context = getattr(user32, "SetProcessDpiAwarenessContext", None)
        if set_context is not None:
            set_context.argtypes = [ctypes.c_void_p]
            set_context.restype = ctypes.c_int
            # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2; fails harmlessly when already set
            set_context(ctypes.c_void_p(-4))

    def _list_windows() -> list[WindowInfo]:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        dwmapi = ctypes.WinDLL("dwmapi")
        hwnd_t = wintypes.HWND
        user32.IsWindowVisible.argtypes = [hwnd_t]
        user32.IsIconic.argtypes = [hwnd_t]
        user32.GetWindowTextLengthW.argtypes = [hwnd_t]
        user32.GetWindowTextW.argtypes = [hwnd_t, wintypes.LPWSTR, ctypes.c_int]
        user32.GetClassNameW.argtypes = [hwnd_t, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowThreadProcessId.argtypes = [hwnd_t, ctypes.POINTER(wintypes.DWORD)]
        user32.GetClientRect.argtypes = [hwnd_t, ctypes.POINTER(wintypes.RECT)]
        user32.ClientToScreen.argtypes = [hwnd_t, ctypes.POINTER(wintypes.POINT)]
        dwmapi.DwmGetWindowAttribute.argtypes = [hwnd_t, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        exe_by_pid = _process_names()
        windows: list[WindowInfo] = []

        def visit(hwnd: int | None, _lparam: int) -> bool:
            if not hwnd or not user32.IsWindowVisible(hwnd):
                return True
            cloaked = wintypes.DWORD(0)
            dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))  # DWMWA_CLOAKED
            if cloaked.value:
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, length + 1)
            class_name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_name, 256)
            pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            rect = wintypes.RECT()
            origin = wintypes.POINT(0, 0)
            user32.GetClientRect(hwnd, ctypes.byref(rect))
            user32.ClientToScreen(hwnd, ctypes.byref(origin))
            windows.append(
                WindowInfo(
                    hwnd=int(hwnd),
                    title=title.value,
                    class_name=class_name.value,
                    exe=exe_by_pid.get(pid.value),
                    client=Rect(origin.x, origin.y, rect.right - rect.left, rect.bottom - rect.top),
                    minimized=bool(user32.IsIconic(hwnd)),
                )
            )
            return True

        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(callback_type(visit), 0)
        return windows

    def _process_names() -> dict[int, str]:
        """pid -> executable name from a Toolhelp32 snapshot (system process list; opens no process handle)."""

        class ProcessEntry(ctypes.Structure):
            _fields_ = (
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_wchar * 260),
            )

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
        if not snapshot or snapshot == wintypes.HANDLE(-1).value:
            return {}
        names: dict[int, str] = {}
        try:
            entry = ProcessEntry()
            entry.dwSize = ctypes.sizeof(ProcessEntry)
            ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                names[int(entry.th32ProcessID)] = entry.szExeFile
                ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
        return names

    def _listen_hotkey(hotkey: Hotkey, on_press: Callable[[], None], poll: float) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.PeekMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
            wintypes.UINT,
        ]
        hotkey_id = 0x0E7A  # application hotkey ids must be within 0x0000-0xBFFF
        if not user32.RegisterHotKey(None, hotkey_id, hotkey.modifiers | MOD_NOREPEAT, hotkey.vk):
            raise WindowError(
                f"cannot register the hotkey {hotkey.text}: another program already uses it (try another)"
            )
        try:
            message = wintypes.MSG()
            while True:
                while user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0x0001):  # PM_REMOVE
                    if message.message == 0x0312 and message.wParam == hotkey_id:  # WM_HOTKEY
                        on_press()
                time.sleep(poll)
        finally:
            user32.UnregisterHotKey(None, hotkey_id)
