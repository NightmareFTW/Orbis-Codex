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

from e7ac.arena.questions import FaceQuestion
from e7ac.ui.context import AppContext
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

        controls = QHBoxLayout()
        self._start = QPushButton("Start")
        self._start.setObjectName("go")
        self._start.clicked.connect(self._toggle)
        self._again = QPushButton("Check again")
        self._again.clicked.connect(self._watch.check_again)
        self._picture = QPushButton("Save picture")
        self._picture.clicked.connect(self._watch.save_picture)
        self._overlay = QCheckBox("Badge on the game")
        self._overlay.setChecked(sys.platform == "win32")
        self._overlay.setEnabled(sys.platform == "win32")
        self._overlay.setToolTip("A small badge over the game window: what to do next, and the result (Windows).")
        self._offline = QCheckBox("Offline")
        self._offline.setToolTip("Use cached data only (no downloads).")
        self._size = BadgeSizeBox(context, lambda message: self._say("Settings", message, "#e0a020"))
        for widget in (self._start, self._again, self._picture):
            controls.addWidget(widget)
        controls.addSpacing(12)
        controls.addWidget(self._overlay)
        controls.addWidget(self._size)
        controls.addWidget(self._offline)
        controls.addStretch()
        layout.addLayout(controls)

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
        self._watch.teams.connect(self._teams)
        self._watch.failed.connect(self._failed)
        self._state(STOPPED)

    def _failed(self, reason: str) -> None:
        self._say("The watch stopped", reason, "#d04040")

    def _toggle(self) -> None:
        if self._watch.running:
            self._watch.stop()
            return
        self._log.clear()
        self._watch.start(offline=self._offline.isChecked(), collect_only=False, overlay=self._overlay.isChecked())

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
        self._overlay.setEnabled(state == STOPPED and sys.platform == "win32")
        self._offline.setEnabled(state == STOPPED)
        if state == STOPPED:
            self._card.clear()
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
