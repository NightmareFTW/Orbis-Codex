"""Small pieces of the window that more than one page uses: the gold ornament, a row of hero portraits, the featured
hero and the badge size choice."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import ClassVar

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPaintEvent, QPen, QPolygonF, QWheelEvent
from PySide6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout, QLabel, QSpinBox, QVBoxLayout, QWidget

from e7ac.settings import BadgeSize
from e7ac.ui import prefs
from e7ac.ui.context import AppContext
from e7ac.ui.theme import TOKENS, status_style


class Ornament(QWidget):
    """The game's divider: a thin gold line fading out to the right, with three small diamonds at its left."""

    def __init__(self, height: int = 14) -> None:
        super().__init__()
        self.setFixedHeight(height)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt's name)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        mid = h / 2
        fade = QLinearGradient(0, 0, w, 0)
        clear, gold = QColor(TOKENS["gold_dim"]), QColor(TOKENS["gold"])
        clear.setAlpha(0)
        fade.setColorAt(0.0, gold)
        fade.setColorAt(0.85, clear)
        painter.setPen(QPen(fade, 1))
        painter.drawLine(QPointF(0, mid), QPointF(w, mid))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(TOKENS["gold_bright"]))
        for x, r in ((18.0, 3.0), (30.0, 2.0), (42.0, 2.0)):
            painter.drawPolygon(
                QPolygonF([QPointF(x, mid - r), QPointF(x + r, mid), QPointF(x, mid + r), QPointF(x - r, mid)])
            )
        painter.end()


class HeroRow(QWidget):
    """A row of round portraits (a team, a banner). Each shows the hero's name when the mouse rests on it."""

    def __init__(self, context: AppContext, size: int = 56, spacing: int = 6) -> None:
        super().__init__()
        self._context = context
        self._size = size
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(spacing)
        self._layout = layout
        self.codes: list[str | None] = []
        """The heroes shown now."""
        layout.addStretch()

    def set_codes(self, codes: Sequence[str | None]) -> None:
        self.codes = list(codes)
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        for code in codes:
            label = QLabel()
            label.setPixmap(self._context.portraits.pixmap(code, self._size))
            label.setFixedSize(self._size, self._size)
            label.setToolTip(self._context.hero_name(code) if code else "not recognised")
            self._layout.insertWidget(self._layout.count() - 1, label)


class FeaturedHero(QWidget):
    """One hero's portrait with a caption, at the foot of the sidebar: another hero every day."""

    def __init__(self, context: AppContext, size: int = 84) -> None:
        super().__init__()
        self._context = context
        self._size = size
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 14)
        layout.setSpacing(4)
        self._portrait = QLabel()
        self._portrait.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name = QLabel()
        self._name.setObjectName("gold")
        self._name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name.setWordWrap(True)
        self._caption = QLabel("Hero of the day")
        self._caption.setObjectName("tagline")
        self._caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for widget in (self._portrait, self._name, self._caption):
            layout.addWidget(widget)
        self.refresh()

    def refresh(self) -> None:
        code = self._context.hero_of_the_day()
        self.setVisible(code is not None)
        if code is not None:
            self._portrait.setPixmap(self._context.portraits.pixmap(code, self._size))
            self._name.setText(self._context.hero_name(code))


class SteadyComboBox(QComboBox):
    """A choice that the mouse wheel changes only after it was clicked: a wheel turned over a page that scrolls would
    otherwise move the choice under the pointer, and a choice that saves itself (the Settings page) would keep the
    change without anybody noticing (SPEC D95: the client became "emulator" and no window was found any more)."""

    def __init__(self) -> None:
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 (Qt's name)
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()  # the page under it scrolls instead


class SteadySpinBox(QSpinBox):
    """A number that the mouse wheel changes only after it was clicked (see `SteadyComboBox`)."""

    def __init__(self) -> None:
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 (Qt's name)
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class BadgeSizeBox(SteadyComboBox):
    """The size of the small badge over the game: saved in the settings, and applied at once when it is showing."""

    LABELS: ClassVar[dict[BadgeSize, str]] = {
        BadgeSize.NORMAL: "Badge: normal",
        BadgeSize.LARGE: "Badge: large",
        BadgeSize.HUGE: "Badge: huge",
    }

    def __init__(self, context: AppContext, on_error: Callable[[str], None] | None = None) -> None:
        super().__init__()
        self._context = context
        self._on_error = on_error
        for size, label in self.LABELS.items():
            self.addItem(label, size.value)
        current = prefs.load(context.paths)[0].badge_size
        self.setCurrentIndex(list(self.LABELS).index(current))
        self.setToolTip("How big the small badge over the game is (what to do next, and the result).")
        self.currentIndexChanged.connect(self._changed)

    def _changed(self) -> None:
        size = str(self.currentData())
        problem = prefs.store(self._context.paths, "badge_size", size)
        if problem and self._on_error is not None:
            self._on_error(f"the badge size was not saved: {problem}")
        self._context.watch.set_badge_size(size)

    def show_saved(self) -> None:
        """Follow the saved size (another page may have changed it)."""
        current = prefs.load(self._context.paths)[0].badge_size
        self.blockSignals(True)
        self.setCurrentIndex(list(self.LABELS).index(current))
        self.blockSignals(False)


def set_row(grid: QGridLayout, row: int, name: str, value: str, kind: str = "") -> None:
    """A two-column row (name, value) of a grid of facts; the name is coloured by `kind` (ok / warn / bad)."""
    for column, text in enumerate((name, value)):
        item = grid.itemAtPosition(row, column)
        label = item.widget() if item is not None else None
        if not isinstance(label, QLabel):
            label = QLabel()
            label.setWordWrap(True)
            grid.addWidget(label, row, column)
        label.setText(text)
        label.setStyleSheet(status_style(kind) if kind and column == 0 else "background: transparent;")
        if column == 0:
            label.setMinimumWidth(110)
