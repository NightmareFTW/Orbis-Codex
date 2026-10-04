"""UI text of the game per client language, and safe fuzzy matching (margin rule, never a silent near-miss).

English strings are copied from the user's own captures (2026-10-04, Stove PC client). The Portuguese table is empty
on purpose until we see a Portuguese capture: game text is never invented (SPEC D39).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from difflib import SequenceMatcher
from typing import Final

from e7ac.domain.codes import Stat
from e7ac.settings import GameLanguage

MIN_SIMILARITY: Final = 0.85
MIN_MARGIN: Final = 0.10

STAT_LABELS: Final[Mapping[GameLanguage, Mapping[str, Stat]]] = {
    GameLanguage.EN: {
        "Attack": Stat.ATK,
        "Defense": Stat.DEF,
        "Health": Stat.HP,
        "Speed": Stat.SPEED,
        "Critical Hit Chance": Stat.CRIT_CHANCE,
        "Critical Hit Damage": Stat.CRIT_DAMAGE,
        "Effectiveness": Stat.EFFECTIVENESS,
        "Effect Resistance": Stat.EFFECT_RESISTANCE,
        "Dual Attack Chance": Stat.DUAL_ATTACK,
    },
    GameLanguage.PT: {},
}
NO_SET_EFFECT: Final[Mapping[GameLanguage, str]] = {GameLanguage.EN: "No set effect"}
LEVEL_PREFIX: Final[Mapping[GameLanguage, str]] = {GameLanguage.EN: "Lv."}
IMPRINT_LOCKED: Final[Mapping[GameLanguage, str]] = {GameLanguage.EN: "Locked"}
"""Shown instead of the imprint when the hero has none (with a padlock icon; user capture 2026-10-04)."""


def normalise(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"\s+", " ", folded).strip()


def match_label(text: str, choices: Mapping[str, object]) -> str | None:
    """The choice `text` refers to: exact (normalised) first; otherwise the best fuzzy match only when it is both
    similar enough and clearly better than the runner-up. None = not recognised (never a near-miss guess)."""
    wanted = normalise(text)
    if not wanted:
        return None
    by_norm = {normalise(c): c for c in choices}
    if wanted in by_norm:
        return by_norm[wanted]
    scored = sorted(((SequenceMatcher(None, wanted, n).ratio(), c) for n, c in by_norm.items()), reverse=True)
    if not scored or scored[0][0] < MIN_SIMILARITY:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < MIN_MARGIN:
        return None
    return scored[0][1]
