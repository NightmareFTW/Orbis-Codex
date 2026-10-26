"""Skill reader (dev tool, SPEC D91): hold a skill icon in the game and the program reads the popup the game shows (the
description at the hero's enhancement level, the soulburn variant, the enhancement steps and one panel for each effect
the text names), so nothing has to be typed or screenshotted. The table lists the heroes whose skills are still to be
read, the ones that matter most first; what was read feeds the simulation at once and `Export` writes one text file to
send."""

from __future__ import annotations

import sys
import threading
from typing import Any

from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QCompleter,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from e7ac.arena.met import met_heroes
from e7ac.cli.skills import describe
from e7ac.skills.export import export_text
from e7ac.skills.store import SkillStore, skills_dir
from e7ac.skills.worklist import SkillRow, progress, worklist
from e7ac.ui.context import AppContext
from e7ac.ui.pages.base import Page, card, heading
from e7ac.ui.skill_controller import RUNNING, STOPPED, STOPPING
from e7ac.ui.theme import status_style
from e7ac.ui.widgets import SteadyComboBox

HEADERS = ("#", "Hero", "Code", "Skills", "Why")
LIST_SIZE = 400


class _Tiers(QObject):
    """The RTA tier list's order of the heroes (epic7db, cached a week), fetched once on a thread."""

    ready = Signal(list)

    def run(self, context: AppContext) -> None:
        threading.Thread(target=self._work, args=(context,), name="e7-tiers", daemon=True).start()

    def _work(self, context: AppContext) -> None:
        from e7ac.cli import catalog as catalog_cli
        from e7ac.sources.epic7db import fetch_tier_order, slug

        try:
            by_slug = {slug(h.name): code for code, h in context.heroes().items() if h.name}
            with catalog_cli.http_factory(context.paths, False) as http:
                self.ready.emit([by_slug[s] for s in fetch_tier_order(http) if s in by_slug])
        except Exception:  # the order is only a hint: the list still works without it
            self.ready.emit([])


class SkillsPage(Page):
    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self._skills = context.skills
        self._store = SkillStore(skills_dir(context.paths.home))
        self._rows: list[SkillRow] = []
        self._tier_order: list[str] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(
            heading(
                "Skill reader",
                "Press Start, open a hero's Skill tab in the game and hold each skill icon for about two seconds: "
                "the program reads the popup (numbers, enhancement steps, effect descriptions). Three skills a hero. "
                "Name the hero below when its name over the art is read badly; change it when you open the next hero.",
            )
        )
        controls = QHBoxLayout()
        self._start = QPushButton("Start reading")
        self._start.setObjectName("go")
        self._start.clicked.connect(self._toggle)
        self._overlay = QCheckBox("Badge on the game")
        self._overlay.setChecked(sys.platform == "win32")
        self._overlay.setEnabled(sys.platform == "win32")
        self._hero = SteadyComboBox()
        self._hero.setEditable(True)
        self._hero.setInsertPolicy(SteadyComboBox.InsertPolicy.NoInsert)
        self._hero.setMinimumWidth(240)
        line = QLineEdit()
        line.setPlaceholderText("Hero open in the game (empty: read its name)")
        line.setClearButtonEnabled(True)
        self._hero.setLineEdit(line)
        completer = QCompleter(self._hero.model(), self._hero)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._hero.setCompleter(completer)
        self._chosen = ""
        """The hero named in the box: a plain string, because the reader asks for it from its own thread."""
        self._hero.editTextChanged.connect(self._hero_named)
        self._hide = QCheckBox("Hide complete heroes")
        self._hide.setChecked(True)
        self._hide.toggled.connect(self._fill)
        export = QPushButton("Export file")
        export.setToolTip("Write everything read as one text file, to send.")
        export.clicked.connect(self._export)
        copy = QPushButton("Copy export")
        copy.setToolTip("Everything read as text on the clipboard, to paste in the chat.")
        copy.clicked.connect(self._copy_export)
        for widget in (self._start, self._hero, self._overlay, self._hide):
            controls.addWidget(widget)
        controls.addStretch()
        controls.addWidget(export)
        controls.addWidget(copy)
        layout.addLayout(controls)

        summary, summary_layout = card()
        self._summary = QLabel()
        self._summary.setWordWrap(True)
        self._status = QLabel()
        self._status.setObjectName("muted")
        self._status.setWordWrap(True)
        summary_layout.addWidget(self._summary)
        summary_layout.addWidget(self._status)
        layout.addWidget(summary)

        self._table = QTableWidget(0, len(HEADERS))
        self._table.setHorizontalHeaderLabels(list(HEADERS))
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setIconSize(QSize(32, 32))
        self._table.verticalHeader().setDefaultSectionSize(40)
        self._table.verticalHeader().hide()
        self._table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._table, 3)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(300)
        self._log.setFixedHeight(100)
        layout.addWidget(self._log)

        self._tiers = _Tiers()
        self._tiers.ready.connect(self._tiers_ready)
        self._skills.state.connect(self._state)
        self._skills.line.connect(self._log.appendPlainText)
        self._skills.said.connect(self._said)
        self._skills.read.connect(self._read)
        self._skills.failed.connect(lambda why: self._set_status(f"The reader stopped: {why}", "bad"))
        self._state(STOPPED)

    def shown(self) -> None:
        if not self._tier_order:
            self._tiers.run(self.context)
        self.reload()

    def reload(self) -> None:
        heroes = {code: h.name for code, h in self.context.heroes().items() if h.name}
        self._rows = worklist(heroes, met_heroes(self.context.paths.home), self._tier_order, self._store)
        complete, started, skills, total = progress(self._rows)
        effects = len(self._store.effects())
        self._fill_heroes()
        self._summary.setText(
            f"{complete} of {total} heroes complete  |  {started} started  |  {skills} skills read  |  "
            f"{effects} effects described"
        )
        self._fill()

    def _hero_named(self, text: str) -> None:
        self._chosen = text.strip()

    def _fill_heroes(self) -> None:
        """The heroes still to read, in the order of the table, to pick from (typing narrows the list)."""
        typed = self._hero.currentText()
        self._hero.blockSignals(True)
        self._hero.clear()
        self._hero.addItems([r.name for r in self._rows if not r.complete][:LIST_SIZE])
        self._hero.setEditText(typed)
        self._hero.blockSignals(False)

    def _fill(self) -> None:
        rows = [r for r in self._rows if not (self._hide.isChecked() and r.complete)][:LIST_SIZE]
        table = self._table
        table.setRowCount(len(rows))
        for number, row in enumerate(rows):
            cells = [
                QTableWidgetItem(str(number + 1)),
                QTableWidgetItem(QIcon(self.context.portraits.pixmap(row.code, 32)), row.name),
                QTableWidgetItem(row.code),
                QTableWidgetItem(row.marks),
                QTableWidgetItem(row.why),
            ]
            for column, cell in enumerate(cells):
                table.setItem(number, column, cell)
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)

    def _tiers_ready(self, order: list[str]) -> None:
        self._tier_order = order
        self.reload()

    def _toggle(self) -> None:
        if self._skills.running:
            self._skills.stop()
            return
        self._log.clear()
        if not self._skills.start(overlay=self._overlay.isChecked(), chosen=lambda: self._chosen or None):
            self._set_status("The Arena watch is running: stop it there first.", "warn")
            return
        self._set_status("Starting: reading the catalog...", "")

    def _read(self, event: Any) -> None:
        self._set_status(describe(event), "warn" if event.problem else "ok")
        self.reload()

    def _said(self, top: str, bottom: str, _colour: str) -> None:
        if self._skills.running:
            self._status.setText(f"{top}  -  {bottom}")

    def _state(self, state: str) -> None:
        self._start.setText({STOPPED: "Start reading", RUNNING: "Stop", STOPPING: "Stopping..."}[state])
        self._start.setObjectName("danger" if state == RUNNING else "go")
        self._start.style().unpolish(self._start)
        self._start.style().polish(self._start)
        self._start.setEnabled(state != STOPPING)
        self._overlay.setEnabled(state == STOPPED and sys.platform == "win32")

    def _export(self) -> None:
        text, target = self._export_text(), skills_dir(self.context.paths.home) / "skills_export.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        QGuiApplication.clipboard().setText(str(target))
        self._set_status(f"Written {target} (the path is on the clipboard): send the file.", "ok")

    def _copy_export(self) -> None:
        QGuiApplication.clipboard().setText(self._export_text())
        self._set_status("Copied: paste it in the chat.", "ok")

    def _export_text(self) -> str:
        names = {code: h.name or code for code, h in self.context.heroes().items()}
        return export_text(self._store, names)

    def _set_status(self, text: str, kind: str) -> None:
        self._status.setText(text)
        self._status.setStyleSheet(status_style(kind) if kind else "")
