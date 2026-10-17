"""Look of the window: colour tokens and one stylesheet. Change a token here and every page follows; pages never
hard-code a colour (the status colours are the badge's own, so the window and the badge on the game agree)."""

from __future__ import annotations

from typing import Final

from e7ac.ui.watch_signal import AMBER, GREEN, LIGHT, RED

TOKENS: Final = {
    "bg": "#15171c",
    "panel": "#1d2027",
    "panel_alt": "#242833",
    "border": "#2f3442",
    "text": "#e6e8ee",
    "muted": "#9aa1b2",
    "accent": "#5b8def",
    "accent_text": "#ffffff",
    "ok": GREEN,
    "warn": AMBER,
    "bad": RED,
    "idle": LIGHT,
}
STATUS_COLOURS: Final = {"ok": GREEN, "warn": AMBER, "bad": RED, "idle": LIGHT}
FONT_FAMILY: Final = "Segoe UI"
FONT_SIZE: Final = 10


def stylesheet() -> str:
    t = TOKENS
    return f"""
QWidget {{ background: {t["bg"]}; color: {t["text"]}; font-family: "{FONT_FAMILY}"; font-size: {FONT_SIZE}pt; }}
QListWidget#nav {{ background: {t["panel"]}; border: none; outline: none; padding: 8px 0; }}
QListWidget#nav::item {{ padding: 10px 18px; border-left: 3px solid transparent; }}
QListWidget#nav::item:selected {{ background: {t["panel_alt"]}; border-left: 3px solid {t["accent"]}; }}
QListWidget#nav::item:disabled {{ color: {t["muted"]}; font-size: 8pt; padding-top: 14px; }}
QLabel#title {{ font-size: 16pt; font-weight: 600; }}
QLabel#muted {{ color: {t["muted"]}; }}
QFrame#card {{ background: {t["panel"]}; border: 1px solid {t["border"]}; border-radius: 8px; }}
QFrame#card QLabel {{ background: transparent; }}
QPushButton {{ background: {t["panel_alt"]}; border: 1px solid {t["border"]}; border-radius: 6px; padding: 7px 16px; }}
QPushButton:hover {{ border-color: {t["accent"]}; }}
QPushButton:disabled {{ color: {t["muted"]}; }}
QPushButton#primary {{ background: {t["accent"]}; color: {t["accent_text"]}; border: none; font-weight: 600; }}
QPushButton#danger {{ background: {t["bad"]}; color: {t["accent_text"]}; border: none; font-weight: 600; }}
QPlainTextEdit, QLineEdit, QComboBox, QTableWidget {{
    background: {t["panel"]}; border: 1px solid {t["border"]}; border-radius: 6px; padding: 4px;
}}
QTableWidget {{ gridline-color: {t["border"]}; alternate-background-color: {t["panel_alt"]}; }}
QHeaderView::section {{ background: {t["panel_alt"]}; border: none; padding: 6px; font-weight: 600; }}
QStatusBar {{ background: {t["panel"]}; color: {t["muted"]}; }}
QCheckBox {{ background: transparent; spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border: 1px solid {t["muted"]}; border-radius: 4px; background: {t["panel"]};
}}
QCheckBox::indicator:checked {{ background: {t["accent"]}; border-color: {t["accent"]}; }}
QCheckBox::indicator:disabled {{ border-color: {t["border"]}; }}
"""


def status_style(kind: str) -> str:
    """Stylesheet fragment colouring a label by status (ok / warn / bad / idle)."""
    return f"color: {STATUS_COLOURS.get(kind, STATUS_COLOURS['idle'])}; background: transparent;"
