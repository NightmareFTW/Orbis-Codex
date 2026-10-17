"""Arena stats (dev tool, SPEC D81): which heroes the Arena shows most right now. Start collecting, open the
Opponents list in the game and refresh it as often as you like: every opponent team shown is recorded (hero codes
only), and the table says who is most common, which of them have a hand-refined kit and what is not simulated yet
for the rest: the list to refine next."""

from __future__ import annotations

import sys
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from e7ac.appdata import open_engine
from e7ac.arena.sightings import Popularity, SightingStore, popularity, sightings_path
from e7ac.predict.overrides import KitError, load_overrides
from e7ac.predict.worklist import WorkRow, kit_amounts, worklist
from e7ac.storage.db import session_scope
from e7ac.ui.context import AppContext
from e7ac.ui.pages.base import Page, card, heading
from e7ac.ui.theme import status_style
from e7ac.ui.watch_controller import COLLECT, RUNNING, STOPPED, STOPPING
from e7ac.ui.widgets import BadgeSizeBox

PERIODS = (("All time", None), ("Last 24 hours", timedelta(hours=24)), ("Last 7 days", timedelta(days=7)))
KIT_HEROES = 30
"""Heroes whose kit status is read (each is one cached epic7db page: about a second the first time)."""
HEADERS = ("#", "Hero", "Code", "Teams", "Share", "Kit", "Not simulated")


def _count(n: int, one: str, many: str = "") -> str:
    return f"{n} {one}" if n == 1 else f"{n} {many or one + 's'}"


class _Number(QTableWidgetItem):
    """A cell that sorts by its number, not by its text."""

    def __init__(self, text: str, value: float) -> None:
        super().__init__(text)
        self._value = value
        self.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def __lt__(self, other: QTableWidgetItem) -> bool:
        return self._value < other._value if isinstance(other, _Number) else super().__lt__(other)


class _Kits(QObject):
    """Reads the kit status of the most seen heroes on a thread (the pages come from the epic7db cache or the net)."""

    row = Signal(object)
    progress = Signal(int, int)
    done = Signal(str)

    def run(self, context: AppContext, counts: list[tuple[str, int]], offline: bool) -> None:
        threading.Thread(target=self._work, args=(context, counts, offline), name="e7-kits", daemon=True).start()

    def _work(self, context: AppContext, counts: list[tuple[str, int]], offline: bool) -> None:
        from e7ac.cli import catalog as catalog_cli

        try:
            overrides = load_overrides()
            heroes = context.heroes()
            with catalog_cli.http_factory(context.paths, offline) as http:
                for number, (code, seen) in enumerate(counts, 1):
                    with session_scope(open_engine(context.paths)) as session:
                        amounts = {code: kit_amounts(session, code)}
                    self.row.emit(worklist([(code, seen)], heroes, amounts, overrides, http)[0])
                    self.progress.emit(number, len(counts))
        except KitError as exc:
            self.done.emit(f"a hand-written kit is broken: {exc}")
        except Exception as exc:  # reported in the window, never a crash
            self.done.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.done.emit("")


class DevPage(Page):
    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self._watch = context.watch
        self._kit_rows: dict[str, WorkRow] = {}
        self._popularity: Popularity | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(
            heading(
                "Arena stats",
                "Press Start, open the Opponents list in the game and refresh it as often as you like: every team "
                "shown is recorded. Only hero codes are kept, never a player name.",
            )
        )

        controls = QHBoxLayout()
        self._start = QPushButton("Start collecting")
        self._start.setObjectName("go")
        self._start.clicked.connect(self._toggle)
        self._now = QPushButton("Read now")
        self._now.setToolTip("Read the Opponents list on the screen again, even if it did not change.")
        self._now.clicked.connect(self._watch.check_again)
        self._now.setEnabled(False)
        self._overlay = QCheckBox("Badge on the game")
        self._overlay.setChecked(sys.platform == "win32")
        self._overlay.setEnabled(sys.platform == "win32")
        self._offline = QCheckBox("Offline")
        self._size = BadgeSizeBox(context, lambda message: self._status.setText(message))
        self._period = QComboBox()
        for label, _ in PERIODS:
            self._period.addItem(label)
        self._period.currentIndexChanged.connect(self.reload)
        for widget in (self._start, self._now, self._overlay, self._size, self._offline):
            controls.addWidget(widget)
        controls.addStretch()
        controls.addWidget(QLabel("Show"))
        controls.addWidget(self._period)
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

        actions = QHBoxLayout()
        self._kits = QPushButton(f"Read kit status (top {KIT_HEROES})")
        self._kits.clicked.connect(self._read_kits)
        copy = QPushButton("Copy list")
        copy.setToolTip("The table as text, to paste in the chat.")
        copy.clicked.connect(self._copy)
        actions.addWidget(self._kits)
        actions.addWidget(copy)
        actions.addStretch()
        layout.addLayout(actions)

        self._table = QTableWidget(0, len(HEADERS))
        self._table.setHorizontalHeaderLabels(list(HEADERS))
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setSortIndicator(0, Qt.SortOrder.AscendingOrder)  # the rank, best first
        self._table.setSortingEnabled(True)
        self._table.setIconSize(QSize(32, 32))
        self._table.verticalHeader().setDefaultSectionSize(40)
        self._table.verticalHeader().hide()
        self._table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._table, 3)

        self._pairs = QLabel()
        self._pairs.setObjectName("muted")
        self._pairs.setWordWrap(True)
        layout.addWidget(self._pairs)
        self._mix = QLabel()
        self._mix.setObjectName("muted")
        self._mix.setWordWrap(True)
        layout.addWidget(self._mix)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(300)
        self._log.setFixedHeight(90)
        layout.addWidget(self._log)

        self._kit_loader = _Kits()
        self._kit_loader.row.connect(self._kit_row)
        self._kit_loader.progress.connect(lambda n, total: self._kits.setText(f"Reading kits {n}/{total}..."))
        self._kit_loader.done.connect(self._kits_done)
        self._refresh_soon = QTimer(self)
        self._refresh_soon.setSingleShot(True)
        self._refresh_soon.setInterval(300)
        self._refresh_soon.timeout.connect(self.reload)
        self._watch.recorded.connect(lambda _n: self._refresh_soon.start())
        self._watch.state.connect(self._state)
        self._watch.line.connect(self._on_line)
        self._watch.failed.connect(lambda reason: self._status.setText(f"The watch stopped: {reason}"))
        self._watch.said.connect(self._said)
        self._state(STOPPED)

    def shown(self) -> None:
        self.reload()

    def reload(self) -> None:
        sightings, skipped = SightingStore(sightings_path(self.context.paths.home)).read()
        _, span = PERIODS[self._period.currentIndex()]
        stats = popularity(sightings, datetime.now(UTC) - span if span else None)
        self._popularity = stats
        unknown = f"{stats.unknown_faces / stats.faces:.0%}" if stats.faces else "-"
        self._summary.setText(
            f"{_count(stats.teams, 'team')} in {_count(stats.refreshes, 'refresh', 'refreshes')}  |  "
            f"{_count(len(stats.heroes), 'different hero')}  |  "
            f"faces not recognised: {unknown}" + (f"  |  {skipped} unreadable lines in the file" if skipped else "")
        )
        self._fill()
        self._mix.setText(
            "Elements: "
            + (", ".join(f"{name} {n}" for name, n in stats.elements) or "-")
            + "   |   Classes: "
            + (", ".join(f"{name} {n}" for name, n in stats.classes) or "-")
        )
        self._pairs.setText(
            "Most common pairs: "
            + (
                ", ".join(
                    f"{self.context.hero_name(a)} + {self.context.hero_name(b)} ({n})" for (a, b), n in stats.pairs
                )
                or "-"
            )
        )

    def _fill(self) -> None:
        stats = self._popularity
        table = self._table
        table.setSortingEnabled(False)
        table.setRowCount(len(stats.heroes) if stats else 0)
        for row, hero in enumerate(stats.heroes if stats else ()):
            work = self._kit_rows.get(hero.code)
            kit = "-" if work is None else ("hand-refined" if work.refined else "automatic")
            cells: list[QTableWidgetItem] = [
                _Number(str(row + 1), row + 1),
                QTableWidgetItem(
                    QIcon(self.context.portraits.pixmap(hero.code, 32)), self.context.hero_name(hero.code)
                ),
                QTableWidgetItem(hero.code),
                _Number(str(hero.teams), hero.teams),
                _Number(f"{hero.share:.0%}", hero.share),
                QTableWidgetItem(kit),
                QTableWidgetItem("" if work is None else ", ".join(work.unmodelled) or "nothing"),
            ]
            for column, cell in enumerate(cells):
                table.setItem(row, column, cell)
        table.setSortingEnabled(True)
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)

    def _toggle(self) -> None:
        if self._watch.running:
            self._watch.stop()
            return
        self._log.clear()
        self._status.setText("Starting: reading the catalog and the hero icons...")
        self._watch.start(offline=self._offline.isChecked(), collect_only=True, overlay=self._overlay.isChecked())

    def _on_line(self, line: str) -> None:
        if self._watch.mode == COLLECT:
            self._log.appendPlainText(line)

    def _said(self, top: str, bottom: str, colour: str) -> None:
        if self._watch.mode == COLLECT and self._watch.running:
            self._status.setText(f"{top}  -  {bottom}")
            self._status.setStyleSheet(f"color: {colour}; background: transparent;")

    def _state(self, state: str) -> None:
        mine = self._watch.mode == COLLECT or state == STOPPED
        busy_elsewhere = self._watch.running and not mine
        self._start.setText(
            {STOPPED: "Start collecting", RUNNING: "Stop", STOPPING: "Stopping..."}[state]
            if mine
            else "Start collecting"
        )
        self._start.setObjectName("danger" if state == RUNNING and mine else "go")
        self._start.style().unpolish(self._start)
        self._start.style().polish(self._start)
        self._start.setEnabled(state != STOPPING and not busy_elsewhere)
        self._overlay.setEnabled(state == STOPPED and sys.platform == "win32")
        self._offline.setEnabled(state == STOPPED)
        self._now.setEnabled(state == RUNNING and mine)
        if busy_elsewhere:
            self._status.setText("The Arena page is watching: stop it there first.")
            self._status.setStyleSheet(status_style("warn"))
        elif state == STOPPED:
            self._status.setStyleSheet("")

    def _read_kits(self) -> None:
        stats = self._popularity
        if stats is None or not stats.heroes:
            self._status.setText("Nothing to read yet: collect some opponents first.")
            return
        self._kits.setEnabled(False)
        counts = [(h.code, h.teams) for h in stats.heroes[:KIT_HEROES]]
        self._kit_loader.run(self.context, counts, self._offline.isChecked())

    def _kit_row(self, row: Any) -> None:
        self._kit_rows[row.code] = row
        self._fill()

    def _kits_done(self, problem: str) -> None:
        self._kits.setEnabled(True)
        self._kits.setText(f"Read kit status (top {KIT_HEROES})")
        if problem:
            self._status.setText(f"Kit status: {problem}")
            self._status.setStyleSheet(status_style("bad"))

    def text_list(self) -> str:
        """The table as the lines `e7 kit todo` prints."""
        stats = self._popularity
        lines = []
        for hero in stats.heroes if stats else ():
            work = self._kit_rows.get(hero.code)
            mark = "?" if work is None else "refined" if work.refined else "auto"
            missing = "" if work is None else ", ".join(work.unmodelled) or "-"
            name = self.context.hero_name(hero.code)
            lines.append(f"  {hero.teams:>3}x  {name:<28} {hero.code:<7} [{mark}]  not simulated: {missing}")
        return "\n".join(lines)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.text_list())
        self._status.setText("Copied: paste it in the chat.")
        self._status.setStyleSheet("")
