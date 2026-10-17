"""The three opponents of the Opponents list side by side (SPEC D85): their heroes, the chance to win against each, and
the best one marked (the one to attack)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget

from e7ac.arena.compare import OpponentOdds, band
from e7ac.ui.context import AppContext
from e7ac.ui.theme import STATUS_COLOURS, TOKENS
from e7ac.ui.widgets import HeroRow

BAND_COLOUR: Final = {"good": STATUS_COLOURS["ok"], "even": STATUS_COLOURS["warn"], "bad": STATUS_COLOURS["bad"]}
PORTRAIT: Final = 44


class _Row(QWidget):
    def __init__(self, context: AppContext) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(10)
        self.number = QLabel()
        self.number.setFixedWidth(22)
        self.number.setStyleSheet("font-weight: 600; font-size: 14pt; background: transparent;")
        self.heroes = HeroRow(context, size=PORTRAIT, spacing=4)
        self.heroes.setFixedWidth(4 * (PORTRAIT + 4))
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(14)
        self.chance = QLabel()
        self.chance.setMinimumWidth(150)
        self.chance.setWordWrap(True)
        self.tag = QLabel()
        self.tag.setMinimumWidth(110)
        layout.addWidget(self.number)
        layout.addWidget(self.heroes)
        layout.addWidget(self.bar, 1)
        layout.addWidget(self.chance)
        layout.addWidget(self.tag)

    def show_odds(self, odds: OpponentOdds) -> None:
        self.number.setText(str(odds.number))
        self.heroes.set_codes(odds.codes)
        colour = BAND_COLOUR.get(band(odds.win), STATUS_COLOURS["idle"])
        if odds.win is None:
            self.bar.setValue(0)
            self.chance.setText(odds.note)
        else:
            self.bar.setValue(round(odds.win * 100))
            margin = f" (+/-{odds.margin:.0%})" if odds.margin is not None else ""
            self.chance.setText(f"{odds.win:.0%}{margin}")
        self.chance.setStyleSheet(f"color: {colour if odds.win is not None else TOKENS['muted']};")
        self.bar.setStyleSheet(
            f"QProgressBar {{ background: {TOKENS['panel_alt']}; border: 1px solid {TOKENS['border']}; "
            f"border-radius: 6px; }} QProgressBar::chunk {{ background: {colour}; border-radius: 5px; }}"
        )
        self.tag.setText(("★ best" + (" - close call" if odds.tie else "")) if odds.best else "")
        self.tag.setStyleSheet(f"color: {TOKENS['gold_bright']}; font-weight: 600; background: transparent;")


class OpponentsCard(QFrame):
    """Empty (and hidden) until the Opponents list has been read and compared."""

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.setObjectName("card")
        self._context = context
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)
        self._title = QLabel("Opponents on the list: the chance to win against each")
        self._title.setStyleSheet("font-weight: 600; background: transparent;")
        layout.addWidget(self._title)
        self._rows: list[_Row] = []
        self._column = QVBoxLayout()
        self._column.setSpacing(2)
        layout.addLayout(self._column)
        self._hint = QLabel()
        self._hint.setObjectName("muted")
        self._hint.setWordWrap(True)
        self._hint.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._hint)
        self.hide()

    def set_rows(self, rows: Sequence[OpponentOdds]) -> None:
        while len(self._rows) < len(rows):
            row = _Row(self._context)
            self._rows.append(row)
            self._column.addWidget(row)
        for index, row in enumerate(self._rows):
            row.setVisible(index < len(rows))
            if index < len(rows):
                row.show_odds(rows[index])
        best = next((r for r in rows if r.best), None)
        self._hint.setText(
            "Attack the one marked best: it has the highest chance to win. Faces not recognised are left without a "
            "number: name them below and the list is compared again."
            if best is not None
            else "No opponent could be compared yet."
        )
        self.setVisible(bool(rows))

    def clear(self) -> None:
        self.hide()
