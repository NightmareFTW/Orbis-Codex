"""Whether two readings of a gear piece are the same physical piece, across sources (SPEC D51, D52).

Hero Info shows a piece's stats, set, grade, item level, +N and score, but not its substat rolls, modified/reforged
flags or game id; a Fribbels save has rolls, flags and the game id, but no score, and for pieces it imported from the
game its "+N" is derived from the number of enhancements, a multiple of 3 that can be up to 2 below the real one
(DATA_SOURCES, Fribbels `scanner.js` convertEnhance). Two readings that agree on everything both know are one piece,
and `combine` keeps what each source knows.
"""

from __future__ import annotations

from typing import Final

from e7ac.domain.roster import Gear, Substat

ESTIMATE_BAND: Final = 2
"""How far below the real +N Fribbels' derived +N can be."""
GAME_ID_PREFIX: Final = "ingame:"
MAX_ENHANCE: Final = 15


def visible(gear: Gear) -> tuple[object, ...]:
    """What every source knows of a piece: slot, set, grade, item level, main stat and substats (not +N or score)."""
    subs = tuple((s.stat, round(s.value, 6)) for s in gear.substats)
    main = (gear.main.stat, round(gear.main.value, 6))
    return gear.slot, gear.set_code, gear.grade, gear.item_level, main, subs


def enhance_is_estimate(gear: Gear) -> bool:
    """A +N Fribbels derived from the enhancement count (a game-imported piece below +15, multiple of 3)."""
    external = gear.external_id or ""
    return external.startswith(GAME_ID_PREFIX) and gear.enhance < MAX_ENHANCE and gear.enhance % 3 == 0


def same_piece(old: Gear, new: Gear) -> bool:
    """True when both readings agree on everything they both know (different game ids are different pieces)."""
    if visible(old) != visible(new):
        return False
    if old.external_id and new.external_id and old.external_id != new.external_id:
        return False
    return _enhance_agrees(old, new) or _enhance_agrees(new, old)


def combine(old: Gear, new: Gear) -> Gear:
    """One piece from two readings of it (`same_piece`): the newer reading's values, with what only the older one
    knows (game id, rolls and flags, score), and the real +N rather than Fribbels' estimate."""
    subs = tuple(
        n if n.rolls is not None or o.rolls is None else _with_history(n, o)
        for o, n in zip(old.substats, new.substats, strict=True)
    )
    return new.model_copy(
        update={
            "enhance": max(old.enhance, new.enhance),
            "substats": subs,
            "score": new.score if new.score is not None else old.score,
            "external_id": new.external_id or old.external_id,
        }
    )


def _enhance_agrees(estimated: Gear, other: Gear) -> bool:
    if estimated.enhance == other.enhance:
        return True
    return enhance_is_estimate(estimated) and estimated.enhance < other.enhance <= estimated.enhance + ESTIMATE_BAND


def _with_history(new: Substat, old: Substat) -> Substat:
    """A substat read without rolls or flags (a screen) takes them from the reading that has them."""
    return new.model_copy(update={"rolls": old.rolls, "modified": old.modified, "reforged": old.reforged})
