"""What a page of the window is."""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from e7ac.ui.context import AppContext


class Page(QWidget):
    """One page of the window. The window builds it on the first visit and calls `shown` on every visit; the page
    keeps to its own widgets and talks to the rest of the app through `context` only."""

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context

    def shown(self) -> None:
        """The page came to the front (refresh what may have changed)."""


def card(title: str = "") -> tuple[QFrame, QVBoxLayout]:
    """A framed block of a page, with an optional heading, and its layout to fill."""
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 12, 16, 14)
    layout.setSpacing(8)
    if title:
        heading = QLabel(title)
        heading.setStyleSheet("font-weight: 600; background: transparent;")
        layout.addWidget(heading)
    return frame, layout


def heading(text: str, hint: str = "") -> QWidget:
    """The title of a page and, under it, a line saying what it is for."""
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 4)
    layout.setSpacing(2)
    title = QLabel(text)
    title.setObjectName("title")
    layout.addWidget(title)
    if hint:
        sub = QLabel(hint)
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        layout.addWidget(sub)
    return box
