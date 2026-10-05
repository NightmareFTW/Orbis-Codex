"""Read what a Hero Info capture shows beyond the stats panel (M7): gear, sets, artifact, EE, imprint icon and stars.

This module only combines the image readers, each measured on the user's captures:
- `gear_panel`: the six pieces (values, item level, "+N", score, frame colour), the artifact and the EE;
- `stat_icons`: the stat type of every gear value from its icon (MECH-GEAR-11; templates from the same capture);
- `sets`: the set badge of every piece and the active-set icons of the CP row (MECH-GEAR-15);
- `imprint_icon` and `star_row`: imprint mode/grade (MECH-IMP-02) and awakening (MECH-HERO-03).

A stat is taken only when its icon and its value agree: "%" on ATK/DEF/HP gives the percent stat; a rate without "%",
Speed with "%" or the Dual Attack Chance icon (never a gear stat) is sent to review. Nothing unreadable is guessed:
the field is None and the caller keeps the current build's value or stores nothing, with a note.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np
import numpy.typing as npt

from e7ac.domain.codes import Stat
from e7ac.domain.roster import GearSlot
from e7ac.settings import GameLanguage
from e7ac.vision.gear_panel import GearPanel, Read, ValueRow, parse_gear_panel
from e7ac.vision.hero_screen import HeroScreenReading
from e7ac.vision.imprint_icon import ImprintIconReading, read_imprint_icon
from e7ac.vision.ocr import TextLine, TextReader
from e7ac.vision.sets import SetIconMatcher, SetMatch
from e7ac.vision.star_row import StarRowReading, read_star_row
from e7ac.vision.stat_icons import IconError, IconMatch, IconWindow, build_templates, match_icons

type BgrImage = npt.NDArray[np.uint8]

_PERCENT_OF: Final[Mapping[Stat, Stat]] = {
    Stat.ATK: Stat.ATK_PERCENT,
    Stat.DEF: Stat.DEF_PERCENT,
    Stat.HP: Stat.HP_PERCENT,
}
_RATES: Final = frozenset({Stat.CRIT_CHANCE, Stat.CRIT_DAMAGE, Stat.EFFECTIVENESS, Stat.EFFECT_RESISTANCE})
ICON_CONFIDENCE: Final = (0.6, 0.4)
"""Confidence of an accepted icon = 0.6 + 0.4 x min(1, margin / ICON_FULL_MARGIN): a ranking aid, not a probability
(correct icons on the captures had margins 0.16-0.6)."""
ICON_FULL_MARGIN: Final = 0.4


@dataclass(frozen=True, slots=True)
class StatRead:
    """One gear (or EE) value with its stat type; `stat` or `value` None = review."""

    stat: Stat | None
    value: float | None
    """Flat stats in game units, rates as fractions (the `Stat` value convention)."""
    raw: str
    confidence: float
    note: str = ""


@dataclass(frozen=True, slots=True)
class PieceRead:
    slot: GearSlot
    main: StatRead
    subs: tuple[StatRead, ...]
    item_level: Read[int]
    enhance: Read[int]
    score: Read[int]
    frame: Read[str]
    """'red' or 'purple' (MECH-GEAR-12; the grade mapping is the caller's, `assumed`)."""
    set_match: SetMatch | None
    """None when no set icons are available (run `e7 catalog sync`)."""
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HeroImageReading:
    panel: GearPanel
    pieces: dict[GearSlot, PieceRead]
    exclusive: StatRead | None
    active_sets: tuple[SetMatch, ...]
    imprint_icon: ImprintIconReading
    stars: StarRowReading
    warnings: tuple[str, ...]


def read_hero_images(
    image: BgrImage,
    lines: Sequence[TextLine],
    screen: HeroScreenReading,
    reader: TextReader,
    *,
    artifact_names: Mapping[str, str],
    set_matcher: SetIconMatcher | None,
    language: GameLanguage = GameLanguage.EN,
) -> HeroImageReading:
    """`lines` = the OCR of the whole capture, already parsed into `screen`; `reader` does the second OCR passes;
    `artifact_names` = catalog artifact display name -> code (ambiguous names removed); `set_matcher` None = sets
    are not read (no cached Stove icons)."""
    warnings: list[str] = []
    panel = parse_gear_panel(image, lines, reader, artifact_names, language)
    warnings.extend(f"gear panel: {w}" for w in panel.warnings)
    rows: list[ValueRow] = [row for piece in panel.pieces.values() for row in (piece.main, *piece.subs)]
    if panel.exclusive is not None:
        rows.append(panel.exclusive.value)
    stats = _read_stats(image, screen, rows, warnings)
    pieces: dict[GearSlot, PieceRead] = {}
    for slot, piece in panel.pieces.items():
        set_match = set_matcher.piece_set(image, piece.icon_box) if set_matcher is not None else None
        pieces[slot] = PieceRead(
            slot=slot,
            main=stats[id(piece.main)],
            subs=tuple(stats[id(row)] for row in piece.subs),
            item_level=piece.item_level,
            enhance=piece.enhance,
            score=piece.score,
            frame=piece.frame,
            set_match=set_match,
            warnings=piece.warnings,
        )
    exclusive = stats[id(panel.exclusive.value)] if panel.exclusive is not None else None
    active: tuple[SetMatch, ...] = ()
    if set_matcher is not None and screen.anchors.cp is not None and panel.present:
        active = tuple(set_matcher.active_sets(image, screen.anchors.cp))
    if set_matcher is None and panel.pieces:
        warnings.append("set icons are not cached, so the gear sets cannot be read: run e7 catalog sync")
    icon = read_imprint_icon(image, screen.anchors)
    stars = read_star_row(image, screen.anchors, screen.level_cap)
    warnings.extend(f"imprint icon: {w}" for w in icon.warnings)
    warnings.extend(f"stars: {w}" for w in stars.warnings)
    return HeroImageReading(panel, pieces, exclusive, active, icon, stars, tuple(warnings))


def stat_of(family: Stat, percent: bool) -> tuple[Stat | None, str]:
    """The stat a value means, from its icon family and its "%": (None, why) when they disagree (MECH-GEAR-11)."""
    if family is Stat.DUAL_ATTACK:
        return None, "Dual Attack Chance icon: never a gear stat"
    if family in _PERCENT_OF:
        return (_PERCENT_OF[family] if percent else family), ""
    if family is Stat.SPEED:
        return (None, "Speed icon with a '%' value") if percent else (Stat.SPEED, "")
    if family in _RATES:
        return (family, "") if percent else (None, f"{family.value} icon without '%'")
    return None, f"unexpected icon family {family.value}"


def _read_stats(
    image: BgrImage, screen: HeroScreenReading, rows: Sequence[ValueRow], warnings: list[str]
) -> dict[int, StatRead]:
    if not rows:
        return {}
    try:
        templates = build_templates(image, screen.anchors.labels)
    except IconError as exc:
        warnings.append(f"stat icons not readable ({exc}): every gear stat type needs review")
        return {id(row): StatRead(None, row.value, row.text, 0.0, "stat icons not readable") for row in rows}
    matches = match_icons(image, templates, [IconWindow(row.icon_window, row.group) for row in rows])
    return {id(row): _stat_read(row, match) for row, match in zip(rows, matches, strict=True)}


def _stat_read(row: ValueRow, match: IconMatch) -> StatRead:
    if match.family is None:
        note = f"icon not recognised (best {match.best.value} {match.score:.2f}, margin {match.margin:.2f})"
        return StatRead(None, row.value, row.text, 0.0, note)
    stat, why = stat_of(match.family, row.percent)
    if stat is None:
        return StatRead(None, row.value, row.text, 0.0, why)
    icon_confidence = ICON_CONFIDENCE[0] + ICON_CONFIDENCE[1] * min(1.0, match.margin / ICON_FULL_MARGIN)
    confidence = round(min(row.confidence, icon_confidence), 3) if row.value is not None else 0.0
    note = "" if row.value is not None else "value not read"
    return StatRead(stat, row.value, row.text, confidence, note)
