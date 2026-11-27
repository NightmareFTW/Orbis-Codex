"""The Hero page (SPEC D133): everything about one hero, opened by a click on its portrait (Home's team). It shows only
the hero that was clicked; from the sidebar, with none chosen, it says how to choose one."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from e7ac.ui.context import AppContext
from e7ac.ui.hero_sheet import Section, Sheet, build_sheet
from e7ac.ui.pages.base import Page, card, heading

LABEL_WIDTH = 170


class HeroPage(Page):
    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self.code: str | None = None
        """The hero shown now."""
        self.sheet: Sheet | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 12)
        outer.addWidget(
            heading("Hero", "Everything the app has about one hero. Click a portrait of your team on Home.")
        )
        self._hint = QLabel("No hero chosen: click one of the portraits of your team on Home.")
        self._hint.setObjectName("muted")
        outer.addWidget(self._hint)
        self._top = QWidget()
        top = QHBoxLayout(self._top)
        top.setContentsMargins(0, 0, 0, 0)
        self._portrait = QLabel()
        self._name = QLabel()
        self._name.setObjectName("title")
        self._tags = QLabel()
        self._tags.setObjectName("muted")
        names = QVBoxLayout()
        names.addWidget(self._name)
        names.addWidget(self._tags)
        top.addWidget(self._portrait)
        top.addLayout(names, 1)
        outer.addWidget(self._top)
        self._body = QWidget()
        self._column = QVBoxLayout(self._body)
        self._column.setContentsMargins(0, 0, 0, 0)
        self._column.setSpacing(12)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(self._body)
        outer.addWidget(scroll, 1)
        self._top.hide()

    def show_hero(self, code: str) -> None:
        """Show this hero (and only this one)."""
        self.code = code
        self.sheet = build_sheet(self.context.paths, self.context.heroes(), code)
        self._hint.hide()
        self._top.show()
        self._portrait.setPixmap(self.context.portraits.pixmap(code, 84))
        self._name.setText(self.sheet.name)
        self._tags.setText("  ·  ".join(self.sheet.tags) + f"   ({code})")
        while self._column.count():
            item = self._column.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        for section in self.sheet.sections:
            self._column.addWidget(_card(section))
        self._column.addStretch()

    def shown(self) -> None:
        if self.code is not None:
            self.show_hero(self.code)  # the roster or the skills may have changed since


def _card(section: Section) -> QWidget:
    frame, layout = card(section.title)
    if not section.rows and not section.paragraphs:
        note = QLabel(section.empty)
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        return frame
    for label, value in section.rows:
        row = QHBoxLayout()
        name = QLabel(label)
        name.setObjectName("muted")
        name.setFixedWidth(LABEL_WIDTH)
        name.setAlignment(Qt.AlignmentFlag.AlignTop)
        text = QLabel(value)
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(name)
        row.addWidget(text, 1)
        layout.addLayout(row)
    for paragraph in section.paragraphs:
        text = QLabel(paragraph)
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(text)
    return frame
