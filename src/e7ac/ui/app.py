"""`e7 app`: the main window."""

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence

from PySide6.QtCore import QMessageLogContext, QtMsgType, qInstallMessageHandler
from PySide6.QtWidgets import QApplication

from e7ac import crashlog
from e7ac.paths import AppPaths, default_paths
from e7ac.ui import icon
from e7ac.ui.context import AppContext
from e7ac.ui.shell import MainWindow
from e7ac.ui.theme import stylesheet
from e7ac.ui.watch_controller import WatchController

WINDOW_SIZE = (1120, 740)

_QT_LEVELS = {
    QtMsgType.QtDebugMsg: logging.DEBUG,
    QtMsgType.QtInfoMsg: logging.INFO,
    QtMsgType.QtWarningMsg: logging.WARNING,
    QtMsgType.QtCriticalMsg: logging.ERROR,
    QtMsgType.QtFatalMsg: logging.CRITICAL,
}


def build_window(paths: AppPaths | None = None, previous: crashlog.PreviousRun | None = None) -> MainWindow:
    """The window with its context (a QApplication must exist). `previous`: the run before this one did not end
    normally (`crashlog.install`)."""
    paths = paths or default_paths()
    paths.ensure()
    return MainWindow(AppContext(paths, WatchController(), previous))


def qt_message(kind: QtMsgType, _context: QMessageLogContext, message: str) -> None:
    """Qt's own warnings and errors go to `app.log` too (they are what comes just before a native crash)."""
    level = _QT_LEVELS.get(kind, logging.WARNING)
    crashlog.note(f"Qt: {message}", level)
    if level >= logging.WARNING and sys.stderr is not None:
        print(message, file=sys.stderr)


def run(argv: Sequence[str] | None = None) -> int:
    paths = default_paths()
    paths.ensure()
    previous = crashlog.install(paths.logs_dir)
    qInstallMessageHandler(qt_message)
    app = QApplication.instance() or QApplication(list(argv if argv is not None else sys.argv))
    if isinstance(app, QApplication):
        app.setApplicationName("Orbis Codex")
        app.setStyleSheet(stylesheet())
        icon.install(app, paths)
    window = build_window(paths, previous)
    window.setWindowIcon(icon.app_icon(paths))  # the window itself too, not only the application default
    window.resize(*WINDOW_SIZE)
    window.show()
    code = app.exec()
    crashlog.shutdown()
    return code
