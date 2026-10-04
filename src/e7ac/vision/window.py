"""Which window is the game: client profiles and window selection (platform-independent logic).

The window identifiers below come from community sources (docs/DATA_SOURCES.md "Game client windows") and are only a
starting point: `e7 capture --list-windows` shows what is really running, and `--hwnd` picks a window explicitly.
Selection never guesses: no match or several matches is an error that lists the candidates.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from e7ac.settings import ClientKind


class WindowError(Exception):
    """The game window could not be found unambiguously, or is not capturable right now."""


@dataclass(frozen=True, slots=True)
class Rect:
    """Screen rectangle in physical pixels (the process is made per-monitor DPI aware first)."""

    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class WindowInfo:
    hwnd: int
    title: str
    class_name: str
    exe: str | None
    """Executable name from the system process list (no handle to the process is ever opened)."""
    client: Rect
    """Client area (the game picture, without borders/title bar) in screen coordinates."""
    minimized: bool = False


@dataclass(frozen=True, slots=True)
class ClientProfile:
    """How to recognise one game client's window. Any of `exe_names` / `class_names` must match; titles narrow down."""

    kind: ClientKind
    exe_names: frozenset[str] = frozenset()
    class_names: frozenset[str] = frozenset()
    title_patterns: tuple[str, ...] = ()
    allow_empty_title: bool = True
    note: str = ""
    _compiled: tuple[re.Pattern[str], ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_compiled", tuple(re.compile(p, re.IGNORECASE) for p in self.title_patterns))

    def matches(self, window: WindowInfo) -> bool:
        exe_ok = window.exe is not None and window.exe.casefold() in {e.casefold() for e in self.exe_names}
        class_ok = window.class_name in self.class_names
        if not (exe_ok or class_ok or (not self.exe_names and not self.class_names)):
            return False
        if not self._compiled:
            return True
        if not window.title:
            return self.allow_empty_title and (exe_ok or class_ok)
        return any(p.search(window.title) for p in self._compiled)


_E7_TITLES = (r"^epic\s*seven$", r"^에픽세븐$")
PROFILES: dict[ClientKind, ClientProfile] = {
    ClientKind.STOVE_PC: ClientProfile(
        ClientKind.STOVE_PC,
        exe_names=frozenset({"EpicSeven.exe"}),
        class_names=frozenset({"GLFW30"}),
        title_patterns=_E7_TITLES,
        note="EpicSeven.exe / class GLFW30 / title 'Epic Seven' (community-sourced, to confirm on the user's PC)",
    ),
    ClientKind.STEAM: ClientProfile(
        ClientKind.STEAM,
        exe_names=frozenset({"EpicSeven.exe"}),
        class_names=frozenset({"GLFW30"}),
        title_patterns=_E7_TITLES,
        note="assumed identical to the Stove PC client until the Steam release can be checked",
    ),
    ClientKind.GOOGLE_PLAY_GAMES: ClientProfile(
        ClientKind.GOOGLE_PLAY_GAMES,
        title_patterns=(r"^epic\s*seven$",),
        allow_empty_title=False,
        note="matched by window title only (identifiers unknown)",
    ),
    ClientKind.EMULATOR: ClientProfile(
        ClientKind.EMULATOR,
        exe_names=frozenset({"HD-Player.exe", "dnplayer.exe", "MuMuPlayer.exe", "MuMuNxDevice.exe", "Nox.exe"}),
        note="common emulator executables (community knowledge); use --hwnd if yours is not listed",
    ),
}


def select_game_window(windows: Sequence[WindowInfo], profile: ClientProfile, hwnd: int | None = None) -> WindowInfo:
    """The one window to capture. Explicit `hwnd` wins; otherwise exactly one profile match is required."""
    if hwnd is not None:
        chosen = next((w for w in windows if w.hwnd == hwnd), None)
        if chosen is None:
            raise WindowError(f"no visible window with handle {hwnd} (see: e7 capture --list-windows)")
    else:
        found = [w for w in windows if profile.matches(w)]
        if not found:
            raise WindowError(
                f"the game window was not found for client '{profile.kind.value}' ({profile.note}). Is the game "
                "running? See the open windows with: e7 capture --list-windows, then pick one with --hwnd"
            )
        if len(found) > 1:
            listed = "; ".join(f"{w.hwnd}: {w.title or '(no title)'} [{w.class_name}, {w.exe}]" for w in found)
            raise WindowError(f"several windows look like the game - choose one with --hwnd: {listed}")
        chosen = found[0]
    if chosen.minimized:
        raise WindowError("the game window is minimised: restore it first (a minimised window has no picture)")
    if chosen.client.width < 2 or chosen.client.height < 2:
        raise WindowError(f"the game window has no visible client area ({chosen.client.width}x{chosen.client.height})")
    return chosen
