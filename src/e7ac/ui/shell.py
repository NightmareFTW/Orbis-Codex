"""The main window: a sidebar (the app's name, the pages from the registry in `ui.pages`, a featured hero) and the page
shown. Pages are built on their first visit; the window knows nothing of what a page does. A page with a `group` sits
inside a folder of the sidebar that opens and closes when clicked (SPEC D82)."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from e7ac import __version__
from e7ac.ui.context import AppContext
from e7ac.ui.pages import PAGES, PageSpec
from e7ac.ui.pages.base import Page
from e7ac.ui.watch_controller import ARENA, COLLECT, RUNNING, STOPPED, STOPPING
from e7ac.ui.widgets import FeaturedHero, Ornament

NAV_WIDTH = 210
PAGE = Qt.ItemDataRole.UserRole
CLOSED, OPEN = "▸", "▾"
"""The folder's arrows (right: closed, down: open)."""


class MainWindow(QMainWindow):
    def __init__(self, context: AppContext, pages: Sequence[PageSpec] = PAGES) -> None:
        super().__init__()
        self.context = context
        self.setWindowTitle(f"Orbis Codex {__version__}")
        self._specs = list(pages)
        self._built: dict[str, Page] = {}
        self._items: dict[str, QTreeWidgetItem] = {}
        self._folders: dict[str, QTreeWidgetItem] = {}
        self._shown = ""

        self._nav = QTreeWidget()
        self._nav.setObjectName("nav")
        self._nav.setHeaderHidden(True)
        self._nav.setRootIsDecorated(False)
        self._nav.setIndentation(18)
        self._nav.setExpandsOnDoubleClick(False)
        self._nav.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._stack = QStackedWidget()
        self._stack.setObjectName("stack")
        for index, spec in enumerate(self._specs):
            parent = self._folder(spec.group) if spec.group else None
            item = QTreeWidgetItem([spec.title])
            item.setData(0, PAGE, index)
            (parent.addChild if parent is not None else self._nav.addTopLevelItem)(item)
            self._items[spec.key] = item
            self._stack.addWidget(QWidget())  # replaced by the page on its first visit
        self._nav.currentItemChanged.connect(self._changed)
        self._nav.itemClicked.connect(self._clicked)

        side = QWidget()
        side.setObjectName("side")
        side.setFixedWidth(NAV_WIDTH)
        column = QVBoxLayout(side)
        column.setContentsMargins(0, 18, 0, 0)
        column.setSpacing(0)
        brand = QLabel("ORBIS CODEX")
        brand.setObjectName("brand")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tagline = QLabel("ARENA COMPANION")
        tagline.setObjectName("tagline")
        tagline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._featured = FeaturedHero(context)
        for widget in (brand, tagline):
            column.addWidget(widget)
        column.addSpacing(8)
        ornament = Ornament()
        ornament.setContentsMargins(10, 0, 10, 0)
        column.addWidget(ornament)
        column.addSpacing(6)
        column.addWidget(self._nav, 1)
        column.addWidget(self._featured)

        central = QWidget()
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(side)
        row.addWidget(self._stack, 1)
        self.setCentralWidget(central)

        self._watching = QLabel()
        self.statusBar().addPermanentWidget(self._watching)
        context.watch.state.connect(self._watch_state)
        self._watch_state(STOPPED)
        if self._specs:
            self.show_page(self._specs[0].key)

    def show_page(self, key: str) -> None:
        item = self._items[key]
        parent = item.parent()
        if parent is not None:
            self._open(parent, True)
        self._nav.setCurrentItem(item)

    def page(self, key: str) -> Page | None:
        """The page, once it has been opened (None before its first visit)."""
        return self._built.get(key)

    def folder_open(self, group: str) -> bool:
        return self._folders[group].isExpanded()

    def _folder(self, group: str) -> QTreeWidgetItem:
        if group not in self._folders:
            folder = QTreeWidgetItem([f"{CLOSED}  {group}"])
            folder.setData(0, PAGE, -1)
            font = folder.font(0)
            font.setBold(True)
            folder.setFont(0, font)
            self._nav.addTopLevelItem(folder)
            self._folders[group] = folder
        return self._folders[group]

    def _open(self, folder: QTreeWidgetItem, opened: bool) -> None:
        group = next(name for name, item in self._folders.items() if item is folder)
        folder.setExpanded(opened)
        folder.setText(0, f"{OPEN if opened else CLOSED}  {group}")

    def _clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        """A click on a folder opens or closes it; the page shown stays."""
        if int(item.data(0, PAGE)) == -1:
            self._open(item, not item.isExpanded())

    def _changed(self, current: QTreeWidgetItem | None, previous: QTreeWidgetItem | None) -> None:
        if current is None:
            return
        index = int(current.data(0, PAGE))
        if index == -1:  # a folder is not a page: keep the highlight on the page shown
            self._nav.blockSignals(True)
            if previous is not None and int(previous.data(0, PAGE)) != -1:
                self._nav.setCurrentItem(previous)
            self._nav.blockSignals(False)
            return
        spec = self._specs[index]
        if spec.key not in self._built:
            page = spec.build(self.context)
            self._built[spec.key] = page
            placeholder = self._stack.widget(index)
            if placeholder is not None:
                self._stack.removeWidget(placeholder)
                placeholder.deleteLater()
            self._stack.insertWidget(index, page)
        self._shown = spec.key
        self._stack.setCurrentIndex(index)
        self._built[spec.key].shown()
        self._featured.refresh()

    def _watch_state(self, state: str) -> None:
        mode = {ARENA: "Arena watch", COLLECT: "Collecting Arena opponents"}.get(self.context.watch.mode, "Watch")
        text = {STOPPED: "Not watching the game", RUNNING: f"{mode}: running", STOPPING: f"{mode}: stopping..."}[state]
        self._watching.setText(text)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt's name)
        """Closing the window stops the watch first (its thread ends within a second)."""
        self.context.watch.stop()
        self.context.watch.wait(5.0)
        super().closeEvent(event)
