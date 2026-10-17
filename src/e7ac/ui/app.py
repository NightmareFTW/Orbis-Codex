"""`e7 app`: the main window."""

from __future__ import annotations

import sys
from collections.abc import Sequence

from PySide6.QtWidgets import QApplication

from e7ac.paths import AppPaths, default_paths
from e7ac.ui.context import AppContext
from e7ac.ui.shell import MainWindow
from e7ac.ui.theme import stylesheet
from e7ac.ui.watch_controller import WatchController

WINDOW_SIZE = (1120, 740)


def build_window(paths: AppPaths | None = None) -> MainWindow:
    """The window with its context (a QApplication must exist)."""
    paths = paths or default_paths()
    paths.ensure()
    return MainWindow(AppContext(paths, WatchController()))


def run(argv: Sequence[str] | None = None) -> int:
    app = QApplication.instance() or QApplication(list(argv if argv is not None else sys.argv))
    if isinstance(app, QApplication):
        app.setApplicationName("Orbis Codex")
        app.setStyleSheet(stylesheet())
    window = build_window()
    window.resize(*WINDOW_SIZE)
    window.show()
    return app.exec()
