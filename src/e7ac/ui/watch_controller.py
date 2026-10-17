"""The Arena watch, driven from the window (SPEC D81): `run_watch` on a thread, what it prints and says sent to Qt
signals. One watch at a time, shared by the Arena page (predictions) and the Dev page (collecting opponents)."""

from __future__ import annotations

import queue
import threading
from typing import Final

from PySide6.QtCore import QObject, Signal

from e7ac.ui.tasks import run_captured
from e7ac.ui.watch_signal import LIGHT, NoSignal, WatchSignal
from e7ac.vision.window import Rect

STOPPED: Final = "stopped"
RUNNING: Final = "running"
STOPPING: Final = "stopping"
ARENA: Final = "arena"
COLLECT: Final = "collect"


class WatchController(QObject):
    line = Signal(str)
    """One line the watch printed."""
    said = Signal(str, str, str)
    """What the badge says: top line, bottom line, colour."""
    state = Signal(str)
    """"stopped", "running" or "stopping"."""
    question = Signal(int)
    """An opponent hero (face 1-4) needs a name: `answer` it."""
    recorded = Signal(int)
    """Opponent teams just recorded on the sightings file."""
    failed = Signal(str)
    """The watch ended for a reason other than a stop."""

    def __init__(self) -> None:
        super().__init__()
        self.mode = ""
        self._state = STOPPED
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._retry, self._share = threading.Event(), threading.Event()
        self._answers: queue.Queue[str] = queue.Queue()

    @property
    def running(self) -> bool:
        return self._state != STOPPED

    def start(self, *, offline: bool, collect_only: bool, overlay: bool) -> bool:
        """False when a watch is already running."""
        if self.running:
            return False
        from e7ac.cli import arena as arena_cli

        self.mode = COLLECT if collect_only else ARENA
        self._stop.clear()
        self._retry.clear()
        self._share.clear()
        self._answers = queue.Queue()
        options = arena_cli.WatchOptions(
            offline=offline,
            collect_only=collect_only,
            ask=self.question.emit,
            recorded=self.recorded.emit,
        )
        self._set_state(RUNNING)

        def job() -> None:
            badge: WatchSignal = NoSignal()
            if overlay:
                badge, why = arena_cli.signal_factory(self._retry.set, self._share.set)
                if why:
                    self.line.emit(f"note: {why}")
            relay = _Relay(self, badge)
            relay.say("Arena: starting", "reading the catalog and the hero icons", LIGHT)
            reason = run_captured(
                lambda: arena_cli.run_watch(options, relay, self._answers, self._retry, self._share, self._stop),
                self.line.emit,
            )
            relay.close()
            self._set_state(STOPPED)
            if reason and not self._stop.is_set():
                self.failed.emit(reason)

        self._thread = threading.Thread(target=job, name="e7-watch", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        if self.running:
            self._set_state(STOPPING)
            self._stop.set()

    def wait(self, seconds: float) -> bool:
        """True once the watch has ended (for the window closing)."""
        if self._thread is not None:
            self._thread.join(seconds)
        return not self.running

    def check_again(self) -> None:
        self._retry.set()

    def save_picture(self) -> None:
        self._share.set()

    def answer(self, name: str) -> None:
        """The name typed for the face asked about (empty: skip it)."""
        self._answers.put(name.strip())

    def _set_state(self, value: str) -> None:
        self._state = value
        self.state.emit(value)


class _Relay:
    """The badge the watch talks to: the window first, and the small badge on the game too when there is one."""

    def __init__(self, controller: WatchController, overlay: WatchSignal) -> None:
        self._controller = controller
        self._overlay = overlay

    def place(self, area: Rect) -> None:
        self._overlay.place(area)

    def settling(self) -> None:
        self._overlay.settling()

    def captured(self, waiting: int) -> None:
        self._overlay.captured(waiting)

    def result(self, name: str, outcome: str, waiting: int) -> None:
        self._overlay.result(name, outcome, waiting)

    def say(self, top: str, bottom: str, colour: str) -> None:
        self._controller.said.emit(top, bottom, colour)
        self._overlay.say(top, bottom, colour)

    def capturable(self, on: bool) -> bool:
        return self._overlay.capturable(on)

    def close(self) -> None:
        self._overlay.close()
