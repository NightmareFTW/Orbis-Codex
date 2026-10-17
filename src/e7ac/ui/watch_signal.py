"""Visual signal of `e7 roster watch` (M9a, SPEC D55): a small always-on-top badge at the top of the game window.

It says when to hold still, when the screen was captured (move on to the next hero: reading happens in the
background) and how each reading ended. Both lines change together: on a capture the result line of the previous
hero gives way to "Reading..." at once, so it never looks as if only the previous hero was stored (user,
2026-10-12). The badge never touches the game: it is our own window, excluded from screen capture
(`SetWindowDisplayAffinity`, Windows 10 2004+) so it never appears in the pictures that are read, and click-through.
Without that exclusion it is placed outside the game window, or not shown. Needs Windows and tkinter (bundled with
the Python the launcher installs); otherwise the console is the only signal.
"""

from __future__ import annotations

import queue
import sys
import threading
from collections.abc import Callable
from typing import Any, Final, Protocol

from e7ac.vision.window import Rect

WIDTH: Final = 300
HEIGHT: Final = 58
"""The badge at "normal" size on a 96-dpi screen, in pixels; fonts below in pixels too."""
FONT_TITLE, FONT_LINE, FONT_BUTTON = 16, 13, 11
SIZES: Final = {"normal": 1.0, "large": 1.6, "huge": 2.2}
"""Badge size name -> factor over the "normal" size (SPEC D82: "normal" was unreadable on a high-resolution screen)."""
DEFAULT_SIZE: Final = "large"
GREY, AMBER, GREEN, RED, LIGHT = "#5a5a5a", "#e0a020", "#2fa84f", "#d04040", "#bbbbbb"
BACKGROUND: Final = "#1e1e1e"
RESULTS: Final = {  # outcome -> text, colour (outcomes without a hero name: "not_hero", "incomplete")
    "stored": ("stored", GREEN),
    "doubtful": ("stored, but check the console", AMBER),
    "failed": ("not stored - see the console", RED),
    "not_hero": ("not a hero screen", LIGHT),
    "incomplete": ("incomplete screen, ignored", LIGHT),
}


class WatchSignal(Protocol):
    def place(self, area: Rect) -> None: ...
    def settling(self) -> None: ...
    def captured(self, waiting: int) -> None: ...
    def result(self, name: str, outcome: str, waiting: int) -> None: ...
    def say(self, top: str, bottom: str, colour: str) -> None: ...
    def capturable(self, on: bool) -> bool: ...
    def resize(self, size: str) -> None: ...
    def close(self) -> None: ...


class NoSignal:
    """Console only."""

    def place(self, area: Rect) -> None: ...
    def settling(self) -> None: ...
    def captured(self, waiting: int) -> None: ...
    def result(self, name: str, outcome: str, waiting: int) -> None: ...
    def say(self, top: str, bottom: str, colour: str) -> None: ...
    def capturable(self, on: bool) -> bool:
        return False

    def resize(self, size: str) -> None: ...

    def close(self) -> None: ...


def metrics(size: str, dpi_scale: float) -> tuple[int, int, int, int, int]:
    """(width, height, title font, line font, button font) in pixels for a size name on a screen of this scale."""
    factor = SIZES.get(size, SIZES[DEFAULT_SIZE]) * max(1.0, min(dpi_scale, 4.0))
    return (
        round(WIDTH * factor),
        round(HEIGHT * factor),
        round(FONT_TITLE * factor),
        round(FONT_LINE * factor),
        round(FONT_BUTTON * factor),
    )


def open_signal(
    beep: bool, buttons: list[tuple[str, Callable[[], None]]] | None = None, size: str = DEFAULT_SIZE
) -> tuple[WatchSignal, str]:
    """The badge, or the console only with the reason. `buttons`: labels and what a click on each does (the badge then
    takes clicks, on its own area only; it still never takes the focus from the game). `size`: a key of `SIZES`."""
    if sys.platform == "win32":
        try:
            import tkinter  # noqa: F401
        except ImportError:
            return NoSignal(), "tkinter is missing from this Python: console only"
        badge = TkBadge(beep, buttons or [], size)
        if not badge.ready.wait(5):
            return NoSignal(), "the visual signal did not start: console only"
        return badge, ""
    else:
        return NoSignal(), "the visual signal needs Windows: console only"


class TkBadge:
    """The badge, run by its own thread (tkinter calls stay on that thread; the others only post messages)."""

    def __init__(
        self, beep: bool, buttons: list[tuple[str, Callable[[], None]]] | None = None, size: str = DEFAULT_SIZE
    ) -> None:
        self.beep = beep
        self.buttons = buttons or []
        self.size = size
        self._affinity = threading.Event()
        self.ready = threading.Event()
        self.excluded = False
        self._messages: queue.Queue[tuple[str, Any]] = queue.Queue()
        threading.Thread(target=self._run, name="e7-watch-signal", daemon=True).start()

    def place(self, area: Rect) -> None:
        self._messages.put(("place", area))

    def settling(self) -> None:
        self._messages.put(("top", ("Hold still...", AMBER)))

    def captured(self, waiting: int) -> None:
        """`waiting`: captured screens not read yet, this one included."""
        self._messages.put(("lines", (("Captured - next hero", GREEN), (reading_line(waiting), LIGHT))))
        self._sound(ok=True)

    def result(self, name: str, outcome: str, waiting: int) -> None:
        """`outcome`: "stored", "doubtful" (stored, a check failed) or "failed" (`cli.roster.ScreenOutcome`), or
        "not_hero" / "incomplete" for a screen that was ignored (no name); `waiting`: captured screens still to read
        after this one."""
        self._messages.put(("bottom", result_line(name, outcome, waiting)))
        if outcome == "failed":
            self._sound(ok=False)

    def say(self, top: str, bottom: str, colour: str) -> None:
        """Any two lines (`e7 arena watch`: what to do next, then the result)."""
        self._messages.put(("lines", ((top, colour), (bottom, LIGHT))))

    def capturable(self, on: bool) -> bool:
        """Let screen captures see the badge (on) or hide it again (off), e.g. for a picture to share; True once
        applied. Only for a badge that is hidden from capture in the first place."""
        if not self.excluded:
            return False
        self._affinity.clear()
        self._messages.put(("affinity", on))
        return self._affinity.wait(1.0)

    def resize(self, size: str) -> None:
        """Another size, at once (the window's size choice)."""
        self._messages.put(("size", size))

    def close(self) -> None:
        self._messages.put(("close", None))

    def _sound(self, ok: bool) -> None:
        if self.beep and sys.platform == "win32":
            import winsound

            winsound.MessageBeep(winsound.MB_OK if ok else winsound.MB_ICONHAND)

    def _run(self) -> None:
        import tkinter as tk

        _make_dpi_aware()  # before any window: sizes and places are then physical pixels, like the capture's
        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.92)
        dpi_scale = float(root.winfo_fpixels("1i")) / 96.0
        size = [self.size]
        box = tk.Frame(root, bg=BACKGROUND, highlightthickness=3, highlightbackground=GREY)
        box.pack(fill="both", expand=True)
        top = tk.Label(box, text="Watching the game...", fg="white", bg=BACKGROUND, anchor="w")
        bottom = tk.Label(box, text="", fg="#bbbbbb", bg=BACKGROUND, anchor="w")
        presses: list[tk.Label] = []
        if self.buttons:
            column = tk.Frame(box, bg=BACKGROUND)
            column.pack(side="right", fill="y", padx=(0, 4), pady=3)
            for label, action in self.buttons:
                press = tk.Label(column, text=label, fg="white", bg=GREY, cursor="hand2", padx=6)
                press.pack(fill="both", expand=True, pady=1)
                press.bind("<Button-1>", _on_click(action))
                presses.append(press)
        top.pack(fill="x", padx=8, pady=(4, 0))
        bottom.pack(fill="x", padx=8)

        def fit() -> tuple[int, int]:
            """Fonts and size for the current size name (sizes in pixels: negative font sizes)."""
            width, height, title, line, button = metrics(size[0], dpi_scale)
            top.config(font=("Segoe UI", -title, "bold"))
            bottom.config(font=("Segoe UI", -line))
            for press in presses:
                press.config(font=("Segoe UI", -button, "bold"))
            return width, height

        fitted = [fit()]
        root.withdraw()
        root.update_idletasks()
        hwnd = int(root.wm_frame(), 16)
        self.excluded = _exclude_from_capture(hwnd)
        _click_through(hwnd, clickable=bool(self.buttons))
        placed: list[Rect] = []

        def apply(kind: str, value: Any) -> bool:
            if kind == "close":
                root.destroy()
                return False
            if kind == "place" and (not placed or placed[-1] != value):
                placed[:] = [value]
                spot = _spot(value, self.excluded, root.winfo_screenheight(), *fitted[0])
                if spot is None:
                    root.withdraw()
                else:
                    root.geometry(f"{fitted[0][0]}x{fitted[0][1]}+{spot[0]}+{spot[1]}")
                    root.deiconify()
            elif kind == "size":
                size[0] = value
                fitted[0] = fit()
                if placed:
                    apply("place_again", placed[-1])
            elif kind == "place_again":
                placed.clear()
                apply("place", value)
            elif kind == "top":
                text, colour = value
                top.config(text=text, fg=colour)
                box.config(highlightbackground=colour)
            elif kind == "bottom":
                text, colour = value
                bottom.config(text=text, fg=colour)
            elif kind == "affinity":
                _set_affinity(hwnd, excluded=not value)
                root.update_idletasks()
                self._affinity.set()
            elif kind == "lines":  # both in one update: they never disagree on screen
                apply("top", value[0])
                apply("bottom", value[1])
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


def _on_click(action: Callable[[], None]) -> Callable[[Any], None]:
    def handler(_event: Any) -> None:
        action()

    return handler


def reading_line(waiting: int) -> str:
    return "Reading..." if waiting <= 1 else f"Reading... ({waiting} waiting)"


def result_line(name: str, outcome: str, waiting: int) -> tuple[str, str]:
    """The result line: text and colour. While other captures wait, it says so: this result is not the last hero's."""
    text, colour = RESULTS.get(outcome, RESULTS["failed"])
    more = f" - {waiting} more being read" if waiting else ""
    head = f"{name}: {text}" if name else text[:1].upper() + text[1:]
    return f"{head}{more}", colour


def _spot(
    area: Rect, excluded: bool, screen_height: int, width: int = WIDTH, height: int = HEIGHT
) -> tuple[int, int] | None:
    """Top centre of the game when the badge is invisible to capture; else just above or below the game window."""
    x = area.left + max(0, (area.width - width) // 2)
    if excluded:
        return x, area.top + 8
    if area.top - height - 6 >= 0:
        return x, area.top - height - 6
    if area.top + area.height + 6 + height <= screen_height:
        return x, area.top + area.height + 6
    return None


def _make_dpi_aware() -> None:
    """Per-monitor DPI awareness for this process (a no-op where it is already set or not on Windows)."""
    if sys.platform == "win32":
        from e7ac.vision._win32 import set_dpi_aware

        set_dpi_aware()


def _exclude_from_capture(hwnd: int) -> bool:
    """Hide our own badge from screen capture (WDA_EXCLUDEFROMCAPTURE); False on older Windows."""
    if sys.platform == "win32":
        return _exclude_win32(hwnd)
    else:
        return False


def _set_affinity(hwnd: int, excluded: bool) -> None:
    if sys.platform == "win32":
        _affinity_win32(hwnd, 0x11 if excluded else 0x0)


def _click_through(hwnd: int, clickable: bool = False) -> None:
    """Clicks go through our badge to whatever is under it (unless it has a button); it never takes the focus."""
    if sys.platform == "win32":
        _click_through_win32(hwnd, clickable)


if sys.platform == "win32":

    def _exclude_win32(hwnd: int) -> bool:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
        return bool(user32.SetWindowDisplayAffinity(hwnd, 0x11))

    def _affinity_win32(hwnd: int, affinity: int) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
        user32.SetWindowDisplayAffinity(hwnd, affinity)

    def _click_through_win32(hwnd: int, clickable: bool) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        user32.SetWindowLongW.restype = ctypes.c_long
        gwl_exstyle = -20
        flags = 0x80000 | 0x80 | 0x08000000  # layered, tool window, no-activate
        if not clickable:
            flags |= 0x20  # transparent: clicks go through
        user32.SetWindowLongW(hwnd, gwl_exstyle, user32.GetWindowLongW(hwnd, gwl_exstyle) | flags)
