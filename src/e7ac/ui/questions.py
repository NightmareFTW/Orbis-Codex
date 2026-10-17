"""The question card (SPEC D83): "who is this?" for a hero the watch could not name. It shows the picture, offers the
heroes it most likely is as portraits to pick with one click, and only when none of them is right asks for the name
(typed, with the catalog's names to complete it). Used by the Arena and the Arena stats pages."""

from __future__ import annotations

import textwrap
from collections import deque

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCompleter,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from e7ac.arena.questions import FaceQuestion
from e7ac.ui.art import round_from_bgr
from e7ac.ui.context import AppContext
from e7ac.ui.theme import status_style

PICTURE = 112
CHOICE = 60
MAX_CHOICES = 6


def _two_lines(name: str, width: int = 13) -> str:
    """A hero's name on at most two short lines (the rest cut with "..."; the tooltip has it all)."""
    lines = textwrap.wrap(name, width) or [name]
    if len(lines) > 2:
        lines = [lines[0], textwrap.shorten(" ".join(lines[1:]), width, placeholder="...")]
    return "\n".join(line if len(line) <= width + 3 else line[: width + 3] for line in lines)


class QuestionCard(QFrame):
    answered = Signal(object, str)
    """(the question, a hero's code; empty when skipped)."""

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.setObjectName("ask")
        self._context = context
        self._waiting: deque[FaceQuestion] = deque()
        self._current: FaceQuestion | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(8)

        self._title = QLabel()
        self._title.setStyleSheet("font-size: 12pt; font-weight: 600; color: #ecd29a; background: transparent;")
        self._more = QLabel()
        self._more.setObjectName("muted")
        head = QHBoxLayout()
        head.addWidget(self._title, 1)
        head.addWidget(self._more)
        outer.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(14)
        self._picture = QLabel()
        self._picture.setFixedSize(PICTURE, PICTURE)
        body.addWidget(self._picture)
        text = QVBoxLayout()
        self._detail = QLabel()
        self._detail.setWordWrap(True)
        self._hint = QLabel("Is it one of these? Click it.")
        self._hint.setObjectName("muted")
        self._choices = QHBoxLayout()
        self._choices.setSpacing(8)
        text.addWidget(self._detail)
        text.addWidget(self._hint)
        text.addLayout(self._choices)
        text.addStretch()
        body.addLayout(text, 1)
        outer.addLayout(body)

        self._typing = QWidget()
        typing = QHBoxLayout(self._typing)
        typing.setContentsMargins(0, 0, 0, 0)
        self._name = QLineEdit()
        self._name.setPlaceholderText("None of them: type the hero's name (the list completes it), then Enter")
        self._name.returnPressed.connect(self._typed)
        names = QCompleter(sorted(h.name for h in context.heroes().values() if h.name), self)
        names.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        names.setFilterMode(Qt.MatchFlag.MatchContains)
        self._name.setCompleter(names)
        give = QPushButton("Name it")
        give.setObjectName("go")
        give.clicked.connect(self._typed)
        typing.addWidget(self._name, 1)
        typing.addWidget(give)
        outer.addWidget(self._typing)
        self._problem = QLabel()
        outer.addWidget(self._problem)

        buttons = QHBoxLayout()
        self._none = QPushButton("None of these")
        self._none.clicked.connect(self._show_typing)
        skip = QPushButton("Skip")
        skip.clicked.connect(lambda: self._answer(""))
        self._skip_all = QPushButton("Skip all")
        self._skip_all.clicked.connect(self._drop_all)
        buttons.addWidget(self._none)
        buttons.addStretch()
        buttons.addWidget(skip)
        buttons.addWidget(self._skip_all)
        outer.addLayout(buttons)
        self.hide()

    @property
    def current(self) -> FaceQuestion | None:
        return self._current

    def choices(self) -> list[QToolButton]:
        """The heroes offered to pick, as buttons, in order."""
        found = (self._choices.itemAt(i) for i in range(self._choices.count()))
        return [w for item in found if item is not None and isinstance(w := item.widget(), QToolButton)]

    def pending(self) -> int:
        """Questions waiting, the one shown included."""
        return len(self._waiting) + (self._current is not None)

    def add(self, questions: list[FaceQuestion]) -> None:
        for question in questions:
            self._waiting.append(question)
        if self._current is None:
            self._next()
        else:
            self._refresh_count()
        if any(q.kind != "list" for q in questions):
            QApplication.alert(self.window())  # flash the taskbar: the watch is waiting for this one

    def clear(self) -> None:
        """Forget everything asked (the watch stopped): nothing is answered."""
        self._waiting.clear()
        self._current = None
        self.hide()

    def _next(self) -> None:
        if not self._waiting:
            self._current = None
            self.hide()
            return
        self._current = question = self._waiting.popleft()
        blocking = question.kind != "list"
        self.setProperty("urgent", blocking)
        self.style().unpolish(self)
        self.style().polish(self)
        self._title.setText(("Who is this? " if blocking else "Teach me: ") + question.title)
        self._detail.setText(question.detail + ("  -  the watch is waiting for you" if blocking else ""))
        if question.crop is not None:
            self._picture.setPixmap(round_from_bgr(question.crop, PICTURE))
        else:
            self._picture.setPixmap(self._context.portraits.pixmap(None, PICTURE))
        self._fill_choices(question)
        self._name.clear()
        self._problem.clear()
        self._refresh_count()
        self.show()

    def _refresh_count(self) -> None:
        left = len(self._waiting)
        self._more.setText(f"+{left} more" if left else "")
        self._skip_all.setVisible(left > 0)

    def _fill_choices(self, question: FaceQuestion) -> None:
        while self._choices.count():
            item = self._choices.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        shown = [code for code in question.suggestions if code in self._context.heroes()][:MAX_CHOICES]
        for code in shown:
            button = QToolButton()
            button.setIcon(QIcon(self._context.portraits.pixmap(code, CHOICE)))
            button.setIconSize(QSize(CHOICE, CHOICE))
            name = self._context.hero_name(code)
            button.setText(_two_lines(name))
            button.setToolTip(f"{name} ({code})")
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setObjectName("choice")
            button.setFixedSize(CHOICE + 44, CHOICE + 46)
            button.clicked.connect(lambda _checked=False, c=code: self._answer(c))
            self._choices.addWidget(button)
        self._choices.addStretch()
        self._hint.setVisible(bool(shown))
        self._none.setVisible(bool(shown))
        self._typing.setVisible(not shown)  # nothing to offer: straight to the name

    def _show_typing(self) -> None:
        self._typing.show()
        self._name.setFocus(Qt.FocusReason.OtherFocusReason)

    def _typed(self) -> None:
        text = self._name.text().strip()
        if not text:
            return
        code = self._code_of(text)
        if code is None:
            self._problem.setText(f"{text!r} is not a hero of the catalog (newer heroes: sync the catalog on Home).")
            self._problem.setStyleSheet(status_style("bad"))
            return
        self._answer(code)

    def _code_of(self, text: str) -> str | None:
        heroes = self._context.heroes()
        lowered = text.lower()
        if lowered in heroes:
            return lowered
        found = [code for code, hero in heroes.items() if (hero.name or "").lower() == lowered]
        return found[0] if len(found) == 1 else None

    def _answer(self, code: str) -> None:
        question = self._current
        if question is None:
            return
        self._next()
        self.answered.emit(question, code)

    def _drop_all(self) -> None:
        """Skip this question and every one waiting."""
        shown, waiting = self._current, list(self._waiting)
        self._waiting.clear()
        self._current = None
        self.hide()
        for question in ([shown] if shown is not None else []) + waiting:
            self.answered.emit(question, "")
