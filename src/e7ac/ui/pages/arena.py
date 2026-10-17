"""Arena: the watch with predictions. Leave it running and play: the VS screen and the Attack Guide (or the
Opponents list) are read, the chance to win is shown here and on the small badge over the game."""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from e7ac.arena.compare import OpponentOdds
from e7ac.arena.questions import FaceQuestion
from e7ac.ui import prefs
from e7ac.ui.context import AppContext
from e7ac.ui.opponents import OpponentsCard
from e7ac.ui.pages.base import Page, card, heading
from e7ac.ui.questions import QuestionCard
from e7ac.ui.watch_controller import ARENA, COLLECT, RUNNING, STOPPED, STOPPING
from e7ac.ui.watch_signal import LIGHT
from e7ac.ui.widgets import BadgeSizeBox, HeroRow


class ArenaPage(Page):
    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self._watch = context.watch
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        layout.addWidget(
            heading(
                "Arena",
                "Start, then play: open a match (the VS screen), tap the magnifier, or open the Opponents list. "
                "Your team is read from the VS screen; the opponent from the Attack Guide faces.",
            )
        )

        result, result_layout = card()
        match = QHBoxLayout()
        self._yours = HeroRow(context, size=60)
        self._theirs = HeroRow(context, size=60)
        middle = QVBoxLayout()
        self._top = QLabel("Not watching")
        self._top.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._top.setStyleSheet("font-size: 22pt; font-weight: 600; background: transparent;")
        self._bottom = QLabel("Press Start")
        self._bottom.setObjectName("muted")
        self._bottom.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._bottom.setWordWrap(True)
        middle.addWidget(self._top)
        middle.addWidget(self._bottom)
        match.addWidget(self._yours)
        match.addLayout(middle, 1)
        match.addWidget(self._theirs)
        result_layout.addLayout(match)
        layout.addWidget(result)
        self._opponents = OpponentsCard(context)
        layout.addWidget(self._opponents)

        controls = QHBoxLayout()
        self._start = QPushButton("Start")
        self._start.setObjectName("go")
        self._start.clicked.connect(self._toggle)
        self._again = QPushButton("Check again")
        self._again.clicked.connect(self._watch.check_again)
        self._picture = QPushButton("Save picture")
        self._picture.clicked.connect(self._watch.save_picture)
        self._keep = QPushButton("Keep picture")
        self._keep.setToolTip("Save the picture the watch is looking at on this PC, to find out why it reads nothing.")
        self._keep.clicked.connect(self._watch.keep_picture)
        self._overlay = QCheckBox("Badge on the game")
        settings, _problem = prefs.load(context.paths)
        self._overlay.setChecked(settings.badge_overlay and sys.platform == "win32")
        self._overlay.setEnabled(sys.platform == "win32")
        self._overlay.setToolTip("A small badge over the game window: what to do next, and the result (Windows).")
        self._offline = QCheckBox("Offline")
        self._offline.setToolTip("Use cached data only (no downloads).")
        self._offline.setChecked(settings.offline)
        self._overlay.toggled.connect(self._remember_overlay)
        self._offline.toggled.connect(self._remember_offline)
        self._size = BadgeSizeBox(context, lambda message: self._say("Settings", message, "#e0a020"))
        for widget in (self._start, self._again, self._picture, self._keep):
            controls.addWidget(widget)
        controls.addSpacing(12)
        controls.addWidget(self._overlay)
        controls.addWidget(self._size)
        controls.addWidget(self._offline)
        controls.addStretch()
        layout.addLayout(controls)

        self._sees = QLabel()
        self._sees.setObjectName("muted")
        self._sees.setWordWrap(True)
        layout.addWidget(self._sees)
        self._card = QuestionCard(context)
        self._card.answered.connect(self._watch.respond)
        layout.addWidget(self._card)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(2000)
        layout.addWidget(self._log, 1)

        self._watch.said.connect(self._say)
        self._watch.line.connect(self._on_line)
        self._watch.state.connect(self._state)
        self._watch.question.connect(self._asked)
        self._watch.unknown_faces.connect(self._unknown)
        self._watch.sees.connect(self._saw)
        self._watch.opponents.connect(self._compared)
        self._watch.teams.connect(self._teams)
        self._watch.failed.connect(self._failed)
        self._state(STOPPED)

    def shown(self) -> None:
        """What the Settings page may have changed since the last visit."""
        settings, _problem = prefs.load(self.context.paths)
        if not self._watch.running:
            self._overlay.setChecked(settings.badge_overlay and sys.platform == "win32")
            self._offline.setChecked(settings.offline)
        self._size.show_saved()

    def _remember_overlay(self, on: bool) -> None:
        self._remember("badge_overlay", on)

    def _remember_offline(self, on: bool) -> None:
        self._remember("offline", on)

    def _remember(self, key: str, on: bool) -> None:
        problem = prefs.store(self.context.paths, key, "true" if on else "false")
        if problem:
            self._say("Settings", f"not saved: {problem}", "#e0a020")

    def _compared(self, rows: list[OpponentOdds]) -> None:
        if self._watch.mode == ARENA:
            self._opponents.set_rows(rows)

    def _saw(self, text: str) -> None:
        if self._watch.mode == ARENA:
            self._sees.setText(f"The watch sees: {text}")

    def _failed(self, reason: str) -> None:
        self._say("The watch stopped", reason, "#d04040")

    def _toggle(self) -> None:
        if self._watch.running:
            self._watch.stop()
            return
        self._log.clear()
        self._opponents.clear()
        self._watch.start(
            offline=self._offline.isChecked(),
            collect_only=False,
            overlay=self._overlay.isChecked(),
            compare_runs=prefs.load(self.context.paths)[0].compare_runs,
        )

    def _on_line(self, line: str) -> None:
        if self._watch.mode == ARENA:
            self._log.appendPlainText(line)

    def _say(self, top: str, bottom: str, colour: str) -> None:
        if self._watch.mode != ARENA and self._watch.running:
            return
        self._top.setText(top)
        self._top.setStyleSheet(f"font-size: 22pt; font-weight: 600; background: transparent; color: {colour};")
        self._bottom.setText(bottom)

    def _state(self, state: str) -> None:
        mine = self._watch.mode == ARENA or state == STOPPED
        busy_elsewhere = self._watch.running and self._watch.mode == COLLECT
        self._start.setText({STOPPED: "Start", RUNNING: "Stop", STOPPING: "Stopping..."}[state] if mine else "Start")
        self._start.setObjectName("danger" if state == RUNNING and mine else "go")
        self._start.style().unpolish(self._start)
        self._start.style().polish(self._start)
        self._start.setEnabled(state != STOPPING and not busy_elsewhere)
        self._again.setEnabled(state == RUNNING and mine)
        self._picture.setEnabled(state == RUNNING and mine)
        self._keep.setEnabled(state == RUNNING and mine)
        if state == STOPPED:
            self._sees.clear()
        self._overlay.setEnabled(state == STOPPED and sys.platform == "win32")
        self._offline.setEnabled(state == STOPPED)
        if state == STOPPED:
            self._card.clear()
            self._opponents.clear()
            self._say("Not watching", "Press Start", LIGHT)
            self._yours.set_codes([])
            self._theirs.set_codes([])
        elif busy_elsewhere:
            self._bottom.setText("The Arena stats page is collecting: stop it there first.")

    def _teams(self, yours: list[str], theirs: list[str]) -> None:
        if self._watch.mode == ARENA:
            self._yours.set_codes(yours)
            self._theirs.set_codes(theirs)

    def _asked(self, question: FaceQuestion) -> None:
        if self._watch.mode == ARENA:
            self._card.add([question])

    def _unknown(self, questions: list[FaceQuestion]) -> None:
        if self._watch.mode == ARENA:
            self._card.add(questions)
