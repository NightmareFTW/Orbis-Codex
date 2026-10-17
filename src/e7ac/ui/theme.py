"""Look of the window: the game's own palette (SPEC D82) as tokens and one stylesheet. Change a token here and every
page follows; pages never hard-code a colour (the status colours are the badge's own, so the window and the badge on
the game agree).

The palette is read from the game's screens: near-black navy ground, ivory text, warm gold for headings, rules and
ornaments, bronze buttons with gold edges (Arena Shop), a blue button for the main action (Defense Team) and a green
one for go (Challenge), beige for the quiet numbers (the "pts" lines).
"""

from __future__ import annotations

from typing import Final

from e7ac.ui.watch_signal import AMBER, GREEN, LIGHT, RED

TOKENS: Final = {
    "ground_top": "#181c27",
    "ground_bottom": "#0d0f15",
    "side": "#0f121a",
    "panel": "#1a1e29",
    "panel_alt": "#232938",
    "border": "#3a3527",
    "gold": "#c9a96e",
    "gold_bright": "#ecd29a",
    "gold_dim": "#8a6d3b",
    "text": "#f2ecdc",
    "muted": "#a89a7e",
    "blue_top": "#2c62b8",
    "blue_bottom": "#173a78",
    "blue_edge": "#7fb0ff",
    "bronze_top": "#6e4d24",
    "bronze_bottom": "#3a2810",
    "green_top": "#34903f",
    "green_bottom": "#1e5a29",
    "green_edge": "#8ee09a",
    "red_top": "#b53a3a",
    "red_bottom": "#6e1d1d",
    "ok": GREEN,
    "warn": AMBER,
    "bad": RED,
    "idle": LIGHT,
}
STATUS_COLOURS: Final = {"ok": GREEN, "warn": AMBER, "bad": RED, "idle": LIGHT}
FONT_FAMILY: Final = "Segoe UI"
FONT_SIZE: Final = 10


def _gradient(top: str, bottom: str) -> str:
    return f"qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {top}, stop:1 {bottom})"


def stylesheet() -> str:
    t = TOKENS
    ground = _gradient(t["ground_top"], t["ground_bottom"])
    bronze = _gradient(t["bronze_top"], t["bronze_bottom"])
    blue = _gradient(t["blue_top"], t["blue_bottom"])
    green = _gradient(t["green_top"], t["green_bottom"])
    red = _gradient(t["red_top"], t["red_bottom"])
    return f"""
QWidget {{ background: transparent; color: {t["text"]}; font-family: "{FONT_FAMILY}"; font-size: {FONT_SIZE}pt; }}
QMainWindow, QStackedWidget#stack {{ background: {ground}; }}
QWidget#side {{ background: {t["side"]}; border-right: 1px solid {t["border"]}; }}
QLabel#brand {{ color: {t["gold_bright"]}; font-size: 15pt; font-weight: 600; letter-spacing: 3px; }}
QLabel#tagline {{ color: {t["muted"]}; font-size: 8pt; letter-spacing: 1px; }}
QTreeWidget#nav {{ background: transparent; border: none; outline: none; }}
QTreeWidget#nav::item {{ padding: 9px 14px; border-left: 3px solid transparent; color: {t["text"]}; }}
QTreeWidget#nav::item:hover {{ background: {t["panel"]}; }}
QTreeWidget#nav::item:selected {{
    background: {t["panel_alt"]}; border-left: 3px solid {t["gold"]}; color: {t["gold_bright"]};
}}
QTreeWidget#nav::branch {{ background: transparent; }}
QTreeWidget#nav::branch:hover {{ background: {t["panel"]}; }}
QTreeWidget#nav::branch:selected {{ background: {t["panel_alt"]}; border-left: 3px solid {t["gold"]}; }}
QLabel#title {{ font-size: 18pt; font-weight: 600; color: {t["text"]}; }}
QLabel#muted {{ color: {t["muted"]}; }}
QLabel#gold {{ color: {t["gold"]}; font-weight: 600; }}
QFrame#card {{ background: {t["panel"]}; border: 1px solid {t["border"]}; border-radius: 10px; }}
QFrame#card QLabel {{ background: transparent; }}
QPushButton {{
    background: {bronze}; border: 1px solid {t["gold_dim"]}; border-radius: 7px; padding: 8px 18px; color: {t["text"]};
}}
QPushButton:hover {{ border-color: {t["gold_bright"]}; }}
QPushButton:pressed {{ background: {t["bronze_bottom"]}; }}
QPushButton:disabled {{ background: {t["panel"]}; border-color: {t["border"]}; color: {t["muted"]}; }}
QPushButton#primary {{ background: {blue}; border: 1px solid {t["blue_edge"]}; font-weight: 600; }}
QPushButton#primary:hover {{ border-color: {t["text"]}; }}
QPushButton#go {{ background: {green}; border: 1px solid {t["green_edge"]}; font-weight: 600; }}
QPushButton#danger {{ background: {red}; border: 1px solid #e07070; font-weight: 600; }}
QPlainTextEdit, QLineEdit, QComboBox, QTableWidget {{
    background: {t["panel"]}; border: 1px solid {t["border"]}; border-radius: 6px; padding: 4px;
    selection-background-color: {t["panel_alt"]}; selection-color: {t["gold_bright"]};
}}
QComboBox QAbstractItemView {{ background: {t["panel"]}; selection-background-color: {t["panel_alt"]}; }}
QTableWidget {{ gridline-color: {t["border"]}; alternate-background-color: {t["panel_alt"]}; }}
QHeaderView::section {{
    background: {t["panel_alt"]}; color: {t["gold"]}; border: none; border-bottom: 1px solid {t["gold_dim"]};
    padding: 6px; font-weight: 600;
}}
QScrollBar:vertical {{ background: {t["side"]}; width: 12px; }}
QScrollBar::handle:vertical {{ background: {t["gold_dim"]}; border-radius: 5px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QStatusBar {{ background: {t["side"]}; color: {t["muted"]}; border-top: 1px solid {t["border"]}; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border: 1px solid {t["gold_dim"]}; border-radius: 4px; background: {t["panel"]};
}}
QCheckBox::indicator:checked {{ background: {t["gold"]}; border-color: {t["gold_bright"]}; }}
QCheckBox::indicator:disabled {{ border-color: {t["border"]}; }}
QToolTip {{ background: {t["panel_alt"]}; color: {t["text"]}; border: 1px solid {t["gold_dim"]}; }}
"""


def status_style(kind: str) -> str:
    """Stylesheet fragment colouring a label by status (ok / warn / bad / idle)."""
    return f"color: {STATUS_COLOURS.get(kind, STATUS_COLOURS['idle'])}; background: transparent;"
