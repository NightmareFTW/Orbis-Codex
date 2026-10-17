"""The main window: a sidebar of pages (from the registry in `ui.pages`) and the page shown. Pages are built on their
first visit; the window knows nothing of what a page does."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QStackedWidget, QWidget

from e7ac import __version__
from e7ac.ui.context import AppContext
from e7ac.ui.pages import PAGES, PageSpec
from e7ac.ui.pages.base import Page
from e7ac.ui.watch_controller import ARENA, COLLECT, RUNNING, STOPPED, STOPPING

NAV_WIDTH = 190


class MainWindow(QMainWindow):
    def __init__(self, context: AppContext, pages: Sequence[PageSpec] = PAGES) -> None:
        super().__init__()
        self.context = context
        self.setWindowTitle(f"Orbis Codex {__version__}")
        self._specs = list(pages)
        self._built: dict[str, Page] = {}
        self._row_of: dict[str, int] = {}

        self._nav = QListWidget()
        self._nav.setObjectName("nav")
        self._nav.setFixedWidth(NAV_WIDTH)
        self._stack = QStackedWidget()
        for index, spec in enumerate(self._specs):
            if spec.group:
                header = QListWidgetItem(spec.group.upper())
                header.setFlags(Qt.ItemFlag.NoItemFlags)
                self._nav.addItem(header)
            item = QListWidgetItem(spec.title)
            item.setData(Qt.ItemDataRole.UserRole, index)
            self._nav.addItem(item)
            self._row_of[spec.key] = self._nav.count() - 1
            self._stack.addWidget(QWidget())  # replaced by the page on its first visit
        self._nav.currentItemChanged.connect(self._changed)

        central = QWidget()
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self._nav)
        row.addWidget(self._stack, 1)
        self.setCentralWidget(central)

        self._watching = QLabel()
        self.statusBar().addPermanentWidget(self._watching)
        context.watch.state.connect(self._watch_state)
        self._watch_state(STOPPED)
        if self._specs:
            self.show_page(self._specs[0].key)

    def show_page(self, key: str) -> None:
        self._nav.setCurrentRow(self._row_of[key])

    def page(self, key: str) -> Page | None:
        """The page, once it has been opened (None before its first visit)."""
        return self._built.get(key)

    def _changed(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        index = int(current.data(Qt.ItemDataRole.UserRole))
        spec = self._specs[index]
        if spec.key not in self._built:
            page = spec.build(self.context)
            self._built[spec.key] = page
            placeholder = self._stack.widget(index)
            if placeholder is not None:
                self._stack.removeWidget(placeholder)
                placeholder.deleteLater()
            self._stack.insertWidget(index, page)
        self._stack.setCurrentIndex(index)
        self._built[spec.key].shown()

    def _watch_state(self, state: str) -> None:
        mode = {ARENA: "Arena watch", COLLECT: "Collecting Arena opponents"}.get(self.context.watch.mode, "Watch")
        text = {STOPPED: "Not watching the game", RUNNING: f"{mode}: running", STOPPING: f"{mode}: stopping..."}[state]
        self._watching.setText(text)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt's name)
        """Closing the window stops the watch first (its thread ends within a second)."""
        self.context.watch.stop()
        self.context.watch.wait(5.0)
        super().closeEvent(event)
