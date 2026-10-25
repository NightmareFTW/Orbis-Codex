"""Settings (SPEC D85): whether everything is ready (the self-check), the game and Arena watch options, the folders
and the diagnostics. Every change is saved at once; a settings file that cannot be read is shown, never replaced."""

from __future__ import annotations

import platform
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Final

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from e7ac import __version__, crashlog, shortcut
from e7ac.doctor import CheckResult, CheckStatus, run_checks
from e7ac.settings import COMPARE_RUNS_MAX, COMPARE_RUNS_MIN, Settings, allowed_values
from e7ac.ui import icon, prefs
from e7ac.ui.context import AppContext
from e7ac.ui.pages.base import Page, card, heading
from e7ac.ui.theme import status_style
from e7ac.ui.widgets import SteadyComboBox, SteadySpinBox, set_row

_STATUS_KIND = {CheckStatus.OK: "ok", CheckStatus.WARN: "warn", CheckStatus.FAIL: "bad"}
CHOICE_LABELS: Final[dict[str, dict[str, str]]] = {
    "client": {
        "stove_pc": "Stove (PC)",
        "steam": "Steam",
        "google_play_games": "Google Play Games",
        "emulator": "Emulator",
    },
    "world": {
        "world_global": "Global",
        "world_eu": "Europe",
        "world_asia": "Asia",
        "world_kor": "Korea",
        "world_jpn": "Japan",
    },
    "display_mode": {"borderless": "Borderless", "windowed": "Windowed", "fullscreen": "Fullscreen"},
    "game_language": {"en": "English", "pt": "Portuguese"},
    "badge_size": {"normal": "Normal", "large": "Large", "huge": "Huge"},
}
KEPT_PICTURES: Final = ("arena", "debug")
"""Under the captures folder: the pictures "Keep picture" saved."""
PROBLEM_LINES: Final = 14


class _Checks(QObject):
    """`run_checks` on a thread (it starts the OCR engine and looks for the game window)."""

    done = Signal(list)

    def run(self, context: AppContext) -> None:
        threading.Thread(target=self._work, args=(context,), name="e7-checks", daemon=True).start()

    def _work(self, context: AppContext) -> None:
        self.done.emit(run_checks(context.paths))


class SettingsPage(Page):
    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self._editors: dict[str, QWidget] = {}
        self._loading = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; } QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        layout.addWidget(heading("Settings", "Saved at once, on this PC only. Nothing here touches the game."))
        self._note = QLabel()
        self._note.setWordWrap(True)
        layout.addWidget(self._note)

        layout.addWidget(self._build_checks())
        layout.addWidget(self._build_game())
        layout.addWidget(self._build_arena())
        layout.addWidget(self._build_files())
        layout.addWidget(self._build_diagnostics())
        layout.addWidget(self._build_about())
        layout.addStretch()

        self._runner = _Checks()
        self._runner.done.connect(self._show_checks)

    # --- building ---------------------------------------------------------------------------------------------

    def _build_checks(self) -> QWidget:
        frame, layout = card("Is everything ready?")
        self._checks = QGridLayout()
        self._checks.setHorizontalSpacing(16)
        self._checks.setColumnStretch(1, 1)
        layout.addLayout(self._checks)
        again = QPushButton("Check again")
        again.clicked.connect(self._run_checks)
        layout.addWidget(again, alignment=Qt.AlignmentFlag.AlignLeft)
        return frame

    def _build_game(self) -> QWidget:
        frame, layout = card("Game")
        grid = self._grid(layout)
        self._choice(grid, 0, "client", "Client", "Where you play. Only the Stove PC client is read for now.")
        self._choice(grid, 1, "world", "Region", "Whose Arena statistics are used (Stove, by server).")
        self._choice(grid, 2, "display_mode", "Display mode", "Not used yet: the game window is found by itself.")
        self._text(grid, 3, "resolution", "Resolution", "auto | 1920x1080", "auto reads it from the game window.")
        self._choice(grid, 4, "game_language", "Game language", "The language of the game's texts, to read them.")
        return frame

    def _build_arena(self) -> QWidget:
        frame, layout = card("Arena watch")
        grid = self._grid(layout)
        self._flag(grid, 0, "badge_overlay", "Badge over the game", "The small badge with what to do and the result.")
        self._choice(grid, 1, "badge_size", "Badge size", "Applies at once, also while the watch runs.")
        self._flag(grid, 2, "offline", "Offline", "Cached data only: nothing is downloaded while watching.")
        spin = SteadySpinBox()
        spin.setRange(COMPARE_RUNS_MIN, COMPARE_RUNS_MAX)
        spin.setSingleStep(500)
        spin.valueChanged.connect(lambda value: self._save("compare_runs", str(value)))
        self._editors["compare_runs"] = spin
        self._row(
            grid,
            3,
            "Battles per opponent",
            spin,
            "Simulated when the three opponents are compared: more is steadier, slower.",
        )
        self._choice(
            grid,
            4,
            "opponent_profile",
            "Opponent build",
            "The game never shows an opponent's gear: mixed (the three Stove builds, with a range), one of them, or "
            "the Fribbels library average.",
        )
        if sys.platform != "win32":
            self._editors["badge_overlay"].setEnabled(False)
        return frame

    def _build_files(self) -> QWidget:
        frame, layout = card("Files and folders")
        self._paths = QLabel()
        self._paths.setObjectName("muted")
        self._paths.setWordWrap(True)
        self._paths.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._paths)
        row = QHBoxLayout()
        for text, target in (
            ("Open data folder", lambda: self.context.paths.home),
            ("Open captures", lambda: self.context.paths.captures_dir),
            ("Open logs", lambda: self.context.paths.logs_dir),
        ):
            button = QPushButton(text)
            button.clicked.connect(self._opener(target))
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        kept = QHBoxLayout()
        self._kept = QLabel()
        self._kept.setObjectName("muted")
        self._kept.setWordWrap(True)
        self._forget = QPushButton("Delete kept pictures")
        self._forget.clicked.connect(self._ask_forget)
        kept.addWidget(self._kept, 1)
        kept.addWidget(self._forget)
        layout.addLayout(kept)
        return frame

    def _build_diagnostics(self) -> QWidget:
        frame, layout = card("Diagnostics")
        self._last_run = QLabel()
        self._last_run.setWordWrap(True)
        layout.addWidget(self._last_run)
        self._problems = QPlainTextEdit()
        self._problems.setReadOnly(True)
        self._problems.setFixedHeight(130)
        layout.addWidget(self._problems)
        row = QHBoxLayout()
        copy = QPushButton("Copy report")
        copy.setToolTip("Version, system, settings and the problems above, to paste into a message.")
        copy.clicked.connect(self._copy_report)
        logs = QPushButton("Open logs")
        logs.clicked.connect(self._opener(lambda: self.context.paths.logs_dir))
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._show_diagnostics)
        for button in (copy, logs, refresh):
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        return frame

    def _build_about(self) -> QWidget:
        frame, layout = card("About")
        top = QHBoxLayout()
        self._icon_preview = QLabel()
        self._icon_preview.setFixedSize(64, 64)
        about = QLabel(
            f"Orbis Codex {__version__}. Read-only towards the game: it only looks at pictures of the screen, "
            "and never reads its memory, listens to its network or sends it a key or a click."
        )
        about.setObjectName("muted")
        about.setWordWrap(True)
        top.addWidget(self._icon_preview)
        top.addWidget(about, 1)
        layout.addLayout(top)
        row = QHBoxLayout()
        self._icon_source = QLabel()
        self._icon_source.setObjectName("muted")
        pick = QPushButton("Choose icon picture...")
        pick.setToolTip(
            "Any picture; the middle square is used (for example the face of a skin). Kept on this PC only."
        )
        pick.clicked.connect(self._pick_icon)
        self._icon_default = QPushButton("Celine's portrait")
        self._icon_default.clicked.connect(self.use_default_icon)
        row.addWidget(self._icon_source, 1)
        row.addWidget(pick)
        row.addWidget(self._icon_default)
        layout.addLayout(row)
        start = QHBoxLayout()
        note = QLabel(
            "Orbis.cmd opens the window with no console that stays open. A desktop shortcut starts it the same way, "
            "with the icon above."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        self._shortcut = QPushButton("Create desktop shortcut")
        self._shortcut.setEnabled(shortcut.on_windows())
        self._shortcut.setToolTip("Windows only.")
        self._shortcut.clicked.connect(self.make_shortcut)
        start.addWidget(note, 1)
        start.addWidget(self._shortcut)
        layout.addLayout(start)
        return frame

    # --- widgets ------------------------------------------------------------------------------------------------

    def _grid(self, layout: QVBoxLayout) -> QGridLayout:
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(2, 1)
        layout.addLayout(grid)
        return grid

    def _row(self, grid: QGridLayout, row: int, label: str, editor: QWidget, hint: str) -> None:
        name = QLabel(label)
        name.setMinimumWidth(140)
        editor.setMinimumWidth(170)
        note = QLabel(hint)
        note.setObjectName("muted")
        note.setWordWrap(True)
        grid.addWidget(name, row, 0)
        grid.addWidget(editor, row, 1)
        grid.addWidget(note, row, 2)

    def _choice(self, grid: QGridLayout, row: int, key: str, label: str, hint: str) -> None:
        box = SteadyComboBox()
        labels = CHOICE_LABELS.get(key, {})
        for value in allowed_values(key) or []:
            box.addItem(labels.get(value, value), value)
        box.currentIndexChanged.connect(lambda _index, k=key, b=box: self._save(k, str(b.currentData())))
        self._editors[key] = box
        self._row(grid, row, label, box, hint)

    def _flag(self, grid: QGridLayout, row: int, key: str, label: str, hint: str) -> None:
        box = QCheckBox()
        box.toggled.connect(lambda on, k=key: self._save(k, "true" if on else "false"))
        self._editors[key] = box
        self._row(grid, row, label, box, hint)

    def _text(self, grid: QGridLayout, row: int, key: str, label: str, placeholder: str, hint: str) -> None:
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.editingFinished.connect(lambda k=key, e=edit: self._save(k, e.text().strip() or "auto"))
        self._editors[key] = edit
        self._row(grid, row, label, edit, hint)

    def _opener(self, target: Callable[[], Path]) -> Callable[[], None]:
        def open_it() -> None:
            folder = target()
            folder.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

        return open_it

    # --- settings -----------------------------------------------------------------------------------------------

    def shown(self) -> None:
        self._show_icon()
        self._fill()
        self._show_files()
        self._show_diagnostics()
        self._run_checks()

    def _fill(self) -> None:
        """Show the saved settings in the editors (without saving them again)."""
        settings, problem = prefs.load(self.context.paths)
        self._loading = True
        try:
            data = settings.model_dump(mode="json")
            for key, editor in self._editors.items():
                value = data[key]
                if isinstance(editor, QComboBox):
                    editor.setCurrentIndex(max(0, editor.findData(value)))
                elif isinstance(editor, QCheckBox):
                    editor.setChecked(bool(value))
                elif isinstance(editor, QSpinBox):
                    editor.setValue(int(value))
                elif isinstance(editor, QLineEdit):
                    editor.setText("" if value is None else str(value))
                editor.setEnabled(not problem and (key != "badge_overlay" or sys.platform == "win32"))
        finally:
            self._loading = False
        if problem:
            self._say(f"The settings file cannot be read, so nothing is changed: {problem}", "bad")
        else:
            self._say("", "")

    def _save(self, key: str, value: str) -> None:
        if self._loading:
            return
        before = prefs.load(self.context.paths)[0]
        problem = prefs.store(self.context.paths, key, value)
        if problem:
            self._fill()  # the editor goes back to what is saved
            self._say(f"Not saved: {problem}", "bad")
            return
        if prefs.load(self.context.paths)[0] == before:
            return  # the same as what was saved: nothing to say (a field that lost the focus untouched)
        self._say("Saved.", "ok")
        if key == "badge_size":
            self.context.watch.set_badge_size(value)

    def _say(self, text: str, kind: str) -> None:
        self._note.setText(text)
        self._note.setStyleSheet(status_style(kind) if kind else "background: transparent;")
        self._note.setVisible(bool(text))

    def settings(self) -> Settings:
        return prefs.load(self.context.paths)[0]

    # --- icon ---------------------------------------------------------------------------------------------------

    def _show_icon(self) -> None:
        paths = self.context.paths
        self._icon_preview.setPixmap(icon.app_icon(paths).pixmap(64, 64))
        self._icon_source.setText(f"Program icon: {icon.source(paths)}.")
        self._icon_default.setEnabled(icon.override_path(paths).is_file())

    def _pick_icon(self) -> None:
        name, _ = QFileDialog.getOpenFileName(
            self, "Choose the program's icon", "", "Pictures (*.png *.jpg *.jpeg *.webp *.bmp)"
        )
        if name:
            self.use_icon(Path(name))

    def use_icon(self, picture: Path) -> None:
        problem = icon.use_picture(self.context.paths, picture)
        self._say(
            f"The icon was not changed: {problem}" if problem else "The icon is changed.", "bad" if problem else "ok"
        )
        self._icon_changed()

    def make_shortcut(self) -> None:
        """A desktop shortcut that opens the window with no console, wearing the program's icon."""
        paths = self.context.paths
        ico = icon.write_ico(paths)
        if ico is None:
            self._say("The shortcut was not made: the icon file could not be written.", "bad")
            return
        try:
            link = shortcut.create_desktop_shortcut(paths.home, ico)
        except shortcut.ShortcutError as exc:
            self._say(f"The shortcut was not made: {exc}", "bad")
            return
        self._say(f"The shortcut is on your Desktop: {link}", "ok")

    def use_default_icon(self) -> None:
        problem = icon.use_default(self.context.paths)
        self._say(
            f"The icon was not changed: {problem}" if problem else "Back to Celine's portrait.",
            "bad" if problem else "ok",
        )
        self._icon_changed()

    def _icon_changed(self) -> None:
        self.context.refresh_icon()
        self._show_icon()

    # --- checks -------------------------------------------------------------------------------------------------

    def _run_checks(self) -> None:
        self._runner.run(self.context)

    def _show_checks(self, results: list[CheckResult]) -> None:
        for i, result in enumerate(results):
            set_row(
                self._checks, i, f"{result.status.value:<4} {result.name}", result.detail, _STATUS_KIND[result.status]
            )

    # --- files --------------------------------------------------------------------------------------------------

    def _kept_dir(self) -> Path:
        return self.context.paths.captures_dir.joinpath(*KEPT_PICTURES)

    def _kept_files(self) -> list[Path]:
        folder = self._kept_dir()
        return [p for p in folder.iterdir() if p.is_file()] if folder.is_dir() else []

    def _show_files(self) -> None:
        paths = self.context.paths
        self._paths.setText(f"Data: {paths.home}\nCaptures: {paths.captures_dir}\nLogs: {paths.logs_dir}")
        files = self._kept_files()
        size = sum(p.stat().st_size for p in files) / 1_000_000
        self._kept.setText(
            f"{len(files)} kept pictures ({size:.1f} MB) from the Arena page's Keep picture button."
            if files
            else "No kept pictures."
        )
        self._forget.setEnabled(bool(files))

    def _ask_forget(self) -> None:
        count = len(self._kept_files())
        answer = QMessageBox.question(self, "Delete kept pictures", f"Delete the {count} pictures kept on this PC?")
        if answer == QMessageBox.StandardButton.Yes:
            self.forget_pictures()

    def forget_pictures(self) -> int:
        """Delete the pictures "Keep picture" saved; how many."""
        deleted = 0
        for path in self._kept_files():
            try:
                path.unlink()
                deleted += 1
            except OSError as exc:
                self._say(f"{path.name} could not be deleted: {exc}", "bad")
        self._show_files()
        return deleted

    # --- diagnostics --------------------------------------------------------------------------------------------

    def _show_diagnostics(self) -> None:
        previous = self.context.previous
        if previous is None:
            self._last_run.setText("The last run ended normally.")
            self._last_run.setStyleSheet(status_style("ok"))
        else:
            self._last_run.setText(previous.describe())
            self._last_run.setStyleSheet(status_style("warn"))
        lines = crashlog.recent_lines(self.context.paths.logs_dir, PROBLEM_LINES)
        self._problems.setPlainText("\n".join(lines) if lines else "No problems recorded.")

    def report(self) -> str:
        """What to paste into a message when something went wrong."""
        settings = self.settings()
        previous = self.context.previous
        lines = [
            f"Orbis Codex {__version__}",
            f"Python {platform.python_version()} on {platform.platform()}",
            f"Settings: {settings.model_dump_json()}",
            f"Last run: {previous.describe() if previous else 'ended normally'}",
            "",
            *crashlog.recent_lines(self.context.paths.logs_dir, 60),
        ]
        dump = crashlog.latest_dump(self.context.paths.logs_dir)
        if dump is not None:
            lines += ["", f"--- {dump.name} ---", dump.read_text(encoding="utf-8", errors="replace")[:4000]]
        return "\n".join(lines)

    def _copy_report(self) -> None:
        QGuiApplication.clipboard().setText(self.report())
        self._say("The report is copied: paste it into a message.", "ok")
