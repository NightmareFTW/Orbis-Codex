"""Home: what the app has (catalog, roster, what the Arena watch learned), whether it is ready, and the one-click
catalog sync."""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout

from e7ac.appdata import open_engine
from e7ac.arena.sightings import SightingStore, sightings_path
from e7ac.catalog.store import current_snapshot
from e7ac.doctor import CheckResult, CheckStatus, run_checks
from e7ac.roster.store import list_owned
from e7ac.storage.db import session_scope
from e7ac.ui.context import AppContext
from e7ac.ui.pages.base import Page, card, heading
from e7ac.ui.tasks import Task
from e7ac.ui.theme import status_style

_STATUS_KIND = {CheckStatus.OK: "ok", CheckStatus.WARN: "warn", CheckStatus.FAIL: "bad"}


class _Checks(QObject):
    """`run_checks` on a thread (it starts the OCR engine and looks for the game window)."""

    done = Signal(list)

    def run(self, context: AppContext) -> None:
        threading.Thread(
            target=lambda: self.done.emit(run_checks(context.paths)), name="e7-checks", daemon=True
        ).start()


class HomePage(Page):
    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        layout.addWidget(
            heading("Orbis Codex", "Everything stays on this PC. The game is only ever looked at, never touched.")
        )

        data, data_layout = card("What the app has")
        self._facts = QGridLayout()
        self._facts.setHorizontalSpacing(24)
        self._facts.setColumnStretch(1, 1)
        data_layout.addLayout(self._facts)
        row = QHBoxLayout()
        sync = QPushButton("Sync catalog")
        sync.setObjectName("primary")
        sync.clicked.connect(self._sync)
        self._sync_button = sync
        folder = QPushButton("Open data folder")
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(context.paths.home))))
        row.addWidget(sync)
        row.addWidget(folder)
        row.addStretch()
        data_layout.addLayout(row)
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(500)
        self._log.setFixedHeight(110)
        self._log.hide()
        data_layout.addWidget(self._log)
        layout.addWidget(data)

        checks, checks_layout = card("Is everything ready?")
        self._checks = QGridLayout()
        self._checks.setHorizontalSpacing(16)
        self._checks.setColumnStretch(1, 1)
        checks_layout.addLayout(self._checks)
        again = QPushButton("Check again")
        again.clicked.connect(self._run_checks)
        checks_layout.addWidget(again, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(checks)
        layout.addStretch()

        self._task = Task()
        self._task.line.connect(self._log.appendPlainText)
        self._task.finished.connect(self._synced)
        self._runner = _Checks()
        self._runner.done.connect(self._show_checks)
        context.watch.state.connect(lambda _state: self._sync_button.setEnabled(not context.watch.running))

    def shown(self) -> None:
        self._show_facts()
        self._run_checks()

    def _show_facts(self) -> None:
        paths = self.context.paths
        catalog, roster = "no catalog yet: press Sync catalog", "-"
        if paths.database.is_file():
            with session_scope(open_engine(paths)) as session:
                snapshot = current_snapshot(session)
                if snapshot is not None:
                    catalog = (
                        f"{snapshot.entity_count} entries, {snapshot.world}, "
                        f"synced {snapshot.created_at:%Y-%m-%d %H:%M} UTC"
                    )
                roster = f"{len(list_owned(session))} heroes"
        sightings, skipped = SightingStore(sightings_path(paths.home)).read()
        seen = f"{len(sightings)} opponent teams recorded" + (f" ({skipped} unreadable lines)" if skipped else "")
        for i, (name, value) in enumerate((("Catalog", catalog), ("Roster", roster), ("Arena", seen))):
            self._set_row(self._facts, i, name, value)

    def _set_row(self, grid: QGridLayout, row: int, name: str, value: str, kind: str = "") -> None:
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

    def _sync(self) -> None:
        from e7ac.cli import catalog as catalog_cli

        self._log.clear()
        self._log.show()
        self._sync_button.setEnabled(False)
        self._log.appendPlainText("Syncing the catalog (politely: one request per second, cached)...")
        if not self._task.start(lambda: catalog_cli.sync(source=None, world=None, refresh=False, offline=False)):
            self._sync_button.setEnabled(True)

    def _synced(self, problem: str) -> None:
        self._sync_button.setEnabled(not self.context.watch.running)
        self._log.appendPlainText(f"Failed: {problem}" if problem else "Done.")
        self.context.heroes(reload=True)
        self._show_facts()
        self._run_checks()

    def _run_checks(self) -> None:
        self._runner.run(self.context)

    def _show_checks(self, results: list[CheckResult]) -> None:
        for i, result in enumerate(results):
            self._set_row(
                self._checks, i, f"{result.status.value:<4} {result.name}", result.detail, _STATUS_KIND[result.status]
            )
