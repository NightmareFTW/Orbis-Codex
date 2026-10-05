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
FRIBBELS_ID_PREFIX: Final = "fribbels:"
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
    """True when both readings agree on everything they both know. Two different ids of one kind (two game ids, or two
    Fribbels ids) are two pieces; a Fribbels id and a game id may be one piece (Fribbels adds the game id later)."""
    if visible(old) != visible(new):
        return False
    if old.external_id and new.external_id and _kind(old) == _kind(new) and old.external_id != new.external_id:
        return False
    return _enhance_agrees(old, new) or _enhance_agrees(new, old)


def is_screen_or_manual(gear: Gear) -> bool:
    """A piece the roster knows from Hero Info (only it shows a score) or from the user's own entry (no source id)."""
    return gear.score is not None or not (gear.external_id or "").startswith((GAME_ID_PREFIX, FRIBBELS_ID_PREFIX))


def combine(old: Gear, new: Gear) -> Gear:
    """One piece from two readings of it (`same_piece`): the newer reading's values, with what only the older one
    knows (game id, rolls, modified/reforged flags, score), and the real +N rather than Fribbels' estimate. A screen
    reading (no source id) never knows rolls or flags; a Fribbels reading knows the modified flag."""
    screen = new.external_id is None
    subs = tuple(_substat(o, n, screen) for o, n in zip(old.substats, new.substats, strict=True))
    ids = [i for i in (new.external_id, old.external_id) if i]
    game_ids = [i for i in ids if i.startswith(GAME_ID_PREFIX)]
    return new.model_copy(
        update={
            "enhance": max(old.enhance, new.enhance),
            "substats": subs,
            "score": new.score if new.score is not None else old.score,
            "external_id": next(iter(game_ids or ids), None),
        }
    )


def _kind(gear: Gear) -> str:
    return (gear.external_id or "").partition(":")[0]


def _enhance_agrees(estimated: Gear, other: Gear) -> bool:
    if estimated.enhance == other.enhance:
        return True
    return enhance_is_estimate(estimated) and estimated.enhance < other.enhance <= estimated.enhance + ESTIMATE_BAND


def _substat(old: Substat, new: Substat, screen: bool) -> Substat:
    return new.model_copy(
        update={
            "rolls": new.rolls if new.rolls is not None else old.rolls,
            "modified": old.modified if screen else new.modified,
            "reforged": new.reforged or old.reforged,
        }
    )
