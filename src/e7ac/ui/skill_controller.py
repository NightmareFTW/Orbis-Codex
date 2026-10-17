"""The skill reader, driven from the window (SPEC D91): `run_skill_watch` on a thread, what it prints and says sent to
Qt signals. Never at the same time as the Arena watch (both capture the game window)."""

from __future__ import annotations

import threading
from typing import Any, Final

from PySide6.QtCore import QObject, Signal

from e7ac.ui.tasks import run_captured
from e7ac.ui.watch_controller import Relay
from e7ac.ui.watch_signal import LIGHT, NoSignal, WatchSignal

STOPPED: Final = "stopped"
RUNNING: Final = "running"
STOPPING: Final = "stopping"


class SkillController(QObject):
    line = Signal(str)
    """One line the reader printed."""
    said = Signal(str, str, str)
    """What the badge says: top line, bottom line, colour."""
    state = Signal(str)
    read = Signal(object)
    """A `cli.skills.ReadEvent`: a popup was stored (or refused, with its reason)."""
    failed = Signal(str)
    """The reader ended for a reason other than a stop."""

    def __init__(self) -> None:
        super().__init__()
        self._state = STOPPED
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.rival: Any = None
        """The Arena watch: never both at once."""

    @property
    def running(self) -> bool:
        return self._state != STOPPED

    def start(self, *, overlay: bool) -> bool:
        """False when something is already watching the game."""
        if self.running or (self.rival is not None and self.rival.running):
            return False
        from e7ac.cli import arena as arena_cli
        from e7ac.cli import skills as skills_cli

        self._stop.clear()
        options = skills_cli.SkillWatchOptions(on_read=self.read.emit)
        self._set_state(RUNNING)

        def job() -> None:
            badge: WatchSignal = NoSignal()
            if overlay:
                badge, why = arena_cli.signal_factory(lambda: None, lambda: None)
                if why:
                    self.line.emit(f"note: {why}")
            relay = Relay(self.said.emit, badge)
            relay.say("Skills: starting", "reading the catalog", LIGHT)
            reason = run_captured(lambda: skills_cli.run_skill_watch(options, relay, self._stop), self.line.emit)
            relay.close()
            self._set_state(STOPPED)
            if reason and not self._stop.is_set():
                self.failed.emit(reason)

        self._thread = threading.Thread(target=job, name="e7-skill-watch", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        if self.running:
            self._set_state(STOPPING)
            self._stop.set()

    def wait(self, seconds: float) -> bool:
        if self._thread is not None:
            self._thread.join(seconds)
        return not self.running

    def _set_state(self, value: str) -> None:
        self._state = value
        self.state.emit(value)
