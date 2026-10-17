"""The Arena watch, driven from the window (SPEC D81): `run_watch` on a thread, what it prints and says sent to Qt
signals. One watch at a time, shared by the Arena page (predictions) and the Dev page (collecting opponents)."""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from typing import Any, Final

from PySide6.QtCore import QObject, Signal

from e7ac.arena.compare import SIMULATIONS
from e7ac.arena.questions import FaceQuestion
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
    question = Signal(object)
    """A `FaceQuestion` to answer now: a hero of the VS screen (the watch waits) or an Attack Guide face; `respond`."""
    unknown_faces = Signal(list)
    """`FaceQuestion`s for faces of the Opponents list nobody could name: taught at leisure, the watch goes on."""
    recorded = Signal(int)
    """Opponent teams just recorded on the sightings file."""
    teams = Signal(list, list)
    """Your team and the opponent's (hero codes) behind the last prediction."""
    opponents = Signal(list)
    """The chance to win against each opponent of the Opponents list (`arena.compare.OpponentOdds`), best one marked."""
    sees = Signal(str)
    """What the watch makes of the game picture now, in words."""
    failed = Signal(str)
    """The watch ended for a reason other than a stop."""

    def __init__(self) -> None:
        super().__init__()
        self.mode = ""
        self._state = STOPPED
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._retry, self._share = threading.Event(), threading.Event()
        self._snapshot = threading.Event()
        self._answers: queue.Queue[str] = queue.Queue()
        self._choices: queue.Queue[str | None] = queue.Queue()
        self._taught: queue.Queue[tuple[FaceQuestion, str]] = queue.Queue()
        self._relay: Relay | None = None
        self.rival: Any = None
        """The other thing that watches the game (the skill reader): never both at once."""

    @property
    def running(self) -> bool:
        return self._state != STOPPED

    @property
    def current(self) -> str:
        return self._state

    def start(self, *, offline: bool, collect_only: bool, overlay: bool, compare_runs: int = SIMULATIONS) -> bool:
        """False when a watch is already running (this one, or the skill reader: both capture the game)."""
        if self.running or (self.rival is not None and self.rival.running):
            return False
        from e7ac.cli import arena as arena_cli

        self.mode = COLLECT if collect_only else ARENA
        self._stop.clear()
        self._retry.clear()
        self._share.clear()
        self._snapshot.clear()
        self._answers = queue.Queue()
        self._choices = queue.Queue()
        self._taught = queue.Queue()
        options = arena_cli.WatchOptions(
            offline=offline,
            collect_only=collect_only,
            ask_face=self.question.emit,
            choose=self._choose,
            unknown=self.unknown_faces.emit,
            taught=self._taught,
            status=self.sees.emit,
            snapshot=self._snapshot,
            recorded=self.recorded.emit,
            teams=self.teams.emit,
            opponents=self.opponents.emit,
            compare_runs=compare_runs,
        )
        self._set_state(RUNNING)

        def job() -> None:
            badge: WatchSignal = NoSignal()
            if overlay:
                badge, why = arena_cli.signal_factory(self._retry.set, self._share.set)
                if why:
                    self.line.emit(f"note: {why}")
            relay = Relay(self.said.emit, badge)
            self._relay = relay
            relay.say("Arena: starting", "reading the catalog and the hero icons", LIGHT)
            reason = run_captured(
                lambda: arena_cli.run_watch(options, relay, self._answers, self._retry, self._share, self._stop),
                self.line.emit,
            )
            relay.close()
            self._relay = None
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

    def set_badge_size(self, size: str) -> None:
        """The badge over the game, at once, when one is showing."""
        relay = self._relay
        if relay is not None:
            relay.resize(size)

    def keep_picture(self) -> None:
        """Keep the picture the watch is looking at (local), to find out why it reads nothing."""
        self._snapshot.set()

    def check_again(self) -> None:
        self._retry.set()

    def save_picture(self) -> None:
        self._share.set()

    def respond(self, question: FaceQuestion, answer: str) -> None:
        """The answer to a question (a hero's code or exact name; empty: skip it)."""
        answer = answer.strip()
        if question.kind == "slot":
            self._choices.put(answer or None)
        elif question.kind == "guide":
            self._answers.put(answer)
        elif answer:
            self._taught.put((question, answer))

    def _choose(self, question: FaceQuestion) -> str | None:
        """Called by the watch for a hero of the VS screen: shows the question, waits for the answer (or the stop)."""
        self.question.emit(question)
        while not self._stop.is_set():
            try:
                return self._choices.get(timeout=0.2)
            except queue.Empty:
                continue
        return None

    def _set_state(self, value: str) -> None:
        self._state = value
        self.state.emit(value)


class Relay:
    """The badge a watch talks to: the window first (`said`), and the small badge on the game too when there is one."""

    def __init__(self, said: Callable[[str, str, str], None], overlay: WatchSignal) -> None:
        self._said = said
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
        self._said(top, bottom, colour)
        self._overlay.say(top, bottom, colour)

    def capturable(self, on: bool) -> bool:
        return self._overlay.capturable(on)

    def resize(self, size: str) -> None:
        self._overlay.resize(size)

    def close(self) -> None:
        self._overlay.close()
