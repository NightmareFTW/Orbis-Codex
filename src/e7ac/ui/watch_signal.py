"""Visual signal of `e7 roster watch` (M9a, SPEC D55): a small always-on-top badge at the top of the game window.

It says when to hold still, when the screen was captured (move on to the next hero: reading happens in the
background) and how the last reading ended. The badge never touches the game: it is our own window, excluded from
screen capture (`SetWindowDisplayAffinity`, Windows 10 2004+) so it never appears in the pictures that are read, and
click-through. Without that exclusion it is placed outside the game window, or not shown. Needs Windows and tkinter
(bundled with the Python the launcher installs); otherwise the console is the only signal.
"""

from __future__ import annotations

import queue
import sys
import threading
from typing import Any, Final, Protocol

from e7ac.vision.window import Rect

WIDTH: Final = 300
HEIGHT: Final = 58
GREY, AMBER, GREEN, RED = "#5a5a5a", "#e0a020", "#2fa84f", "#d04040"
BACKGROUND: Final = "#1e1e1e"


class WatchSignal(Protocol):
    def place(self, area: Rect) -> None: ...
    def settling(self) -> None: ...
    def captured(self, waiting: int) -> None: ...
    def result(self, name: str, ok: bool) -> None: ...
    def close(self) -> None: ...


class NoSignal:
    """Console only."""

    def place(self, area: Rect) -> None: ...
    def settling(self) -> None: ...
    def captured(self, waiting: int) -> None: ...
    def result(self, name: str, ok: bool) -> None: ...
    def close(self) -> None: ...


def open_signal(beep: bool) -> tuple[WatchSignal, str]:
    """The badge, or the console only with the reason."""
    if sys.platform == "win32":
        try:
            import tkinter  # noqa: F401
        except ImportError:
            return NoSignal(), "tkinter is missing from this Python: console only"
        badge = TkBadge(beep)
        if not badge.ready.wait(5):
            return NoSignal(), "the visual signal did not start: console only"
        return badge, ""
    else:
        return NoSignal(), "the visual signal needs Windows: console only"


class TkBadge:
    """The badge, run by its own thread (tkinter calls stay on that thread; the others only post messages)."""

    def __init__(self, beep: bool) -> None:
        self.beep = beep
        self.ready = threading.Event()
        self.excluded = False
        self._messages: queue.Queue[tuple[str, Any]] = queue.Queue()
        threading.Thread(target=self._run, name="e7-watch-signal", daemon=True).start()

    def place(self, area: Rect) -> None:
        self._messages.put(("place", area))

    def settling(self) -> None:
        self._messages.put(("top", ("Hold still...", AMBER)))

    def captured(self, waiting: int) -> None:
        more = f" ({waiting} being read)" if waiting > 1 else ""
        self._messages.put(("top", (f"Captured - next hero{more}", GREEN)))
        self._sound(ok=True)

    def result(self, name: str, ok: bool) -> None:
        text = f"{name}: stored" if ok else f"{name}: not stored - see the console"
        self._messages.put(("bottom", (text, GREEN if ok else RED)))
        if not ok:
            self._sound(ok=False)

    def close(self) -> None:
        self._messages.put(("close", None))

    def _sound(self, ok: bool) -> None:
        if self.beep and sys.platform == "win32":
            import winsound

            winsound.MessageBeep(winsound.MB_OK if ok else winsound.MB_ICONHAND)

    def _run(self) -> None:
        import tkinter as tk

        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.92)
        box = tk.Frame(root, bg=BACKGROUND, highlightthickness=3, highlightbackground=GREY)
        box.pack(fill="both", expand=True)
        top = tk.Label(box, text="Watching the game...", fg="white", bg=BACKGROUND, font=("Segoe UI", 12, "bold"))
        bottom = tk.Label(box, text="", fg="#bbbbbb", bg=BACKGROUND, font=("Segoe UI", 10))
        top.pack(fill="x", padx=8, pady=(4, 0))
        bottom.pack(fill="x", padx=8)
        root.withdraw()
        root.update_idletasks()
        hwnd = int(root.wm_frame(), 16)
        self.excluded = _exclude_from_capture(hwnd)
        _click_through(hwnd)
        placed: list[Rect] = []

        def apply(kind: str, value: Any) -> bool:
            if kind == "close":
                root.destroy()
                return False
            if kind == "place" and (not placed or placed[-1] != value):
                placed[:] = [value]
                spot = _spot(value, self.excluded, root.winfo_screenheight())
                if spot is None:
                    root.withdraw()
                else:
                    root.geometry(f"{WIDTH}x{HEIGHT}+{spot[0]}+{spot[1]}")
                    root.deiconify()
            elif kind == "top":
                text, colour = value
                top.config(text=text, fg=colour)
                box.config(highlightbackground=colour)
            elif kind == "bottom":
                text, colour = value
                bottom.config(text=text, fg=colour)
            return True

        def poll() -> None:
            while True:
                try:
                    kind, value = self._messages.get_nowait()
                except queue.Empty:
                    break
                if not apply(kind, value):
                    return
            root.after(50, poll)

        root.after(50, poll)
        self.ready.set()
        root.mainloop()


def _spot(area: Rect, excluded: bool, screen_height: int) -> tuple[int, int] | None:
    """Top centre of the game when the badge is invisible to capture; else just above or below the game window."""
    x = area.left + max(0, (area.width - WIDTH) // 2)
    if excluded:
        return x, area.top + 8
    if area.top - HEIGHT - 6 >= 0:
        return x, area.top - HEIGHT - 6
    if area.top + area.height + 6 + HEIGHT <= screen_height:
        return x, area.top + area.height + 6
    return None


def _exclude_from_capture(hwnd: int) -> bool:
    """Hide our own badge from screen capture (WDA_EXCLUDEFROMCAPTURE); False on older Windows."""
    if sys.platform == "win32":
        return _exclude_win32(hwnd)
    else:
        return False


def _click_through(hwnd: int) -> None:
    """Clicks go through our badge to whatever is under it; it never takes the focus."""
    if sys.platform == "win32":
        _click_through_win32(hwnd)


if sys.platform == "win32":

    def _exclude_win32(hwnd: int) -> bool:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
        return bool(user32.SetWindowDisplayAffinity(hwnd, 0x11))

    def _click_through_win32(hwnd: int) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        user32.SetWindowLongW.restype = ctypes.c_long
        gwl_exstyle = -20
        flags = 0x80000 | 0x20 | 0x80 | 0x08000000  # layered, transparent (click-through), tool window, no-activate
        user32.SetWindowLongW(hwnd, gwl_exstyle, user32.GetWindowLongW(hwnd, gwl_exstyle) | flags)
