"""Choosing among the three opponents of the Opponents list (SPEC D85): one row per opponent with its heroes and the
chance to win against it, and the best one marked. UI-free: the badge, the window and the log all show these rows."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Final

TEAM_SIZE: Final = 4

SIMULATIONS: Final = 3000
"""Battles simulated per opponent when comparing the three (about 1.5 s each; the 95% margin is about 2 points; a single
chosen match is predicted with the full 10,000)."""
CLOSE: Final = 0.02
"""Two chances closer than this are a tie in practice (the simulations' own noise)."""
GOOD: Final = 0.6
BAD: Final = 0.4
"""A chance from `GOOD` up is shown as good, up to `BAD` as bad, in between as an even fight."""


class Unavailable(Exception):  # noqa: N818 (not an error of the program: a prediction that cannot be made)
    """A prediction that cannot be made, and why."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


Predictor = Callable[[tuple[str, ...], tuple[str, ...]], tuple[float, float]]
"""(your team, their team) -> (chance to win, its 95% half-width); raises `Unavailable`."""
Known = dict[tuple[tuple[str, ...], tuple[str | None, ...]], tuple[float, float]]
"""Predictions already made: the same two teams are never simulated twice (the numbers would change between looks)."""


@dataclass(frozen=True, slots=True)
class OpponentOdds:
    number: int
    """Position on the list, from 1, top to bottom."""
    codes: tuple[str | None, ...]
    """The four heroes (None: not recognised)."""
    win: float | None
    """Chance to win (0-1); None when it cannot be told (a hero not recognised, no stats, your team not known)."""
    margin: float | None
    """95% half-width of `win`."""
    note: str
    """Why `win` is None, or "" ."""
    best: bool = False
    tie: bool = False
    """Another opponent is within `CLOSE` of the best one."""


def mark_best(rows: list[OpponentOdds]) -> list[OpponentOdds]:
    """The rows with the best one marked (the highest chance; the first on a tie) and whether it is a close call."""
    known = [r for r in rows if r.win is not None]
    if not known:
        return list(rows)
    best = max(known, key=lambda r: (r.win or 0.0, -r.number))
    close = any(r is not best and abs((r.win or 0.0) - (best.win or 0.0)) < CLOSE for r in known)
    return [replace(r, best=r is best, tie=r is best and close) for r in rows]


def verdict(rows: list[OpponentOdds]) -> tuple[str, str]:
    """The two lines of the badge: "Best: opponent 2 (72%)" and every chance ("1: 55%  2: 72%  3: ?")."""
    chances = "  ".join(f"{r.number}: {'?' if r.win is None else f'{r.win:.0%}'}" for r in rows)
    best = next((r for r in rows if r.best), None)
    if best is None or best.win is None:
        return "Opponents not compared", chances
    return f"Best: opponent {best.number} ({best.win:.0%})" + (" - close" if best.tie else ""), chances


def band(win: float | None) -> str:
    """ "good", "bad", "even" or "unknown", for the colour of a chance."""
    if win is None:
        return "unknown"
    return "good" if win >= GOOD else "bad" if win <= BAD else "even"


def compare(
    team: Sequence[str], defences: Sequence[Sequence[str | None]], predict: Predictor, known: Known
) -> list[OpponentOdds]:
    """The chance to win against each defence of the list, best one marked. A defence with a hero nobody could name is
    left without a number (never guessed), and so is every defence while your team is not known."""
    rows = []
    for number, defence in enumerate(defences, 1):
        codes = tuple(defence)
        if len(team) != TEAM_SIZE:
            rows.append(OpponentOdds(number, codes, None, None, "your team is not known: open a VS screen once"))
        elif len(codes) != TEAM_SIZE or None in codes:
            rows.append(OpponentOdds(number, codes, None, None, "a hero is not recognised"))
        else:
            key = (tuple(team), codes)
            if key not in known:
                try:
                    known[key] = predict(tuple(team), tuple(c for c in codes if c))
                except Unavailable as exc:
                    rows.append(OpponentOdds(number, codes, None, None, exc.reason))
                    continue
            win, margin = known[key]
            rows.append(OpponentOdds(number, codes, win, margin, ""))
    return mark_best(rows)
