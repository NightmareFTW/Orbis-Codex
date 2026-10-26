"""The skill popup of the game's Hero Info screen (SPEC D91): hold a skill icon and the game shows its description, the
soulburn variant, the enhancement steps and, to the right, one panel for each effect the text names (Fear, Offering,
Venom, ...). This reads all of it from a capture (screen capture and OCR only, the golden rule), so the program learns
a hero's skills, their numbers at the hero's enhancement level and the descriptions of its unique effects without
anyone typing them or sending screenshots.

The layout (checked on the user's captures, cropped and full window):

    [+5] Skill title            |  [icon] Effect title (Unique Effect)
         Acquire 1 Soul   4 turns  |  its description lines ...
    description lines ...       |  [icon] Another effect ...
    Soulburn Effect  Consume 10 Soul |  Penetrate          (no icon: a plain mechanic, no indent)
    soulburn text ...           |  its description
    Skill Enhance
    +1  +5% damage dealt ...

The title is the line right of a small "+N" badge (the enhancement level); the main column is aligned on the "Skill
Enhance" / "Soulburn Effect" titles; the effect panels are the lines to the right of the main column, split where
the vertical gap grows. Everything is geometry over the OCR lines (`TextReader`), so the parser is tested with synthetic
lines and the golden tests run only where the local captures exist. Text keeps what the game says, with the OCR's
diacritic noise removed; numbers are never corrected here (they are checked against epic7db's text by the caller).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median
from typing import Any, Final

from e7ac.vision.labels import match_label
from e7ac.vision.ocr import TextLine, TextReader

BADGE: Final = re.compile(r"^\+\s*(\d{1,2})$")
ACQUIRE: Final = re.compile(r"acquire\s+(\d+)\s+([a-z]+)", re.IGNORECASE)
TURNS: Final = re.compile(r"(\d+)\s*turns?\b", re.IGNORECASE)
CONSUME: Final = re.compile(r"consume\s+(\d+)\s+soul", re.IGNORECASE)
STEP: Final = re.compile(r"^\+\s*(\d{1,2})(?:\s+([+-]\s*\d.*))?$")
"""An enhancement row: "+3", its text on the same row, or the whole row in one OCR line ("+3 +10% effect chance")."""
UI_LABELS: Final = frozenset(
    {
        "skill",
        "enhance",
        "skill enhance",
        "promotion",
        "memory",
        "imprint",
        "memory imprint",
        "awaken",
        "cancel",
        "hero transmission",
    }
)
"""Text of the Hero Info screen around the popup (its tabs and buttons), never part of it."""
TEXT_STEP: Final = re.compile(r"^[+-]\s*\d+(?:\.\d+)?\s*(?:%|turns?)", re.IGNORECASE)
CONTINUES: Final = frozenset({"prevent", "prevents", "of", "the", "to", "which", "and", "for", "with", "by", "in"})
"""A panel title ending in one of these goes on in the next line."""
FLAGS: Final = re.compile(r"\(([^)]*)\)\s*$")
MIN_LINES: Final = 6
"""Fewer lines than this is not a skill popup."""


@dataclass(frozen=True, slots=True)
class EffectPanel:
    """One effect panel to the right of the popup: its title, the marks in the title's brackets and its description."""

    title: str
    marks: tuple[str, ...]
    """"Unique Effect", "Undispellable", ... as the game writes them."""
    text: str


@dataclass(frozen=True, slots=True)
class SkillPopup:
    hero: str | None
    """The hero's name at the top left of the screen (None on a cropped capture)."""
    name: str
    level: int | None
    """The skill's enhancement level (the "+N" badge); None when the badge is not shown."""
    gains: tuple[tuple[str, int], ...]
    """What the skill gains: (("Soul", 1),) or (("Focus", 2), ("Soul", 3))."""
    cooldown: int | None
    cooldown_note: str
    """The cost row text next to the turns ("La Vie en Rose 3 Turns" for an internal cooldown)."""
    text: str
    soulburn: str
    soulburn_cost: int | None
    enhance: tuple[tuple[int, str], ...]
    effects: tuple[EffectPanel, ...]
    confidence: float
    """The lowest OCR score of the lines used."""


_QUOTES: Final = {0x2019: "'", 0x2018: "'", 0x201C: '"', 0x201D: '"'}


def clean(text: str) -> str:
    """The OCR's text without diacritic noise ("Immortality" read as "Immortality" with an accent), curly quotes
    straightened."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", plain.translate(_QUOTES)).strip()


def read_skill_popup(image: Any, reader: TextReader) -> SkillPopup | None:
    """The popup in a capture, or None when it has none."""
    return parse_skill_popup(reader.read(image))


def parse_skill_popup(lines: Sequence[TextLine], titles: Sequence[str] = ()) -> SkillPopup | None:
    """The popup on a screen. `titles`: the names of the skills of the hero the popup is known to belong to. A popup
    with none of the marks the title is found by (a passive that cannot be enhanced has no badge, no cost row and no
    "Skill Enhance") is still read when one of its lines is one of these names."""
    rows = [(line, clean(line.text)) for line in lines if clean(line.text)]
    if len(rows) < MIN_LINES:
        return None
    title = _title(rows) or _title_without_badge(rows) or _title_above_description(rows) or _title_named(rows, titles)
    if title is None:
        return None
    badge, title_line = title
    below = [r for r in rows if r[0].box.y0 >= title_line.box.y0 - 4 and r[0] is not title_line and r[0] is not badge]
    corner = [r for r in rows if r[0].box.y0 >= title_line.box.y0 - 40 and r[1].lower() not in UI_LABELS]
    enhance_row = _anchor(below, "skill enhance", title_line)
    burn_row = _anchor(below, "soulburn effect", title_line)
    left = _main_left(title_line, below, enhance_row, burn_row)
    if left is None:
        return None
    tolerance = 0.25 * median(r[0].box.height for r in below) if below else 12.0
    aligned = [r for r in below if abs(r[0].box.x0 - left) <= max(12.0, tolerance)]
    right_edge = max((r[0].box.x1 for r in aligned), default=left)
    main = [r for r in below if r[0].box.x0 >= left - 12 and r[0].box.x0 < right_edge - 10]
    if enhance_row is None and burn_row is None:  # nothing marks where the text ends: the paragraph does
        main = _paragraph(main)
    side = [r for r in corner if r[0].box.x0 >= right_edge - 10 and len(re.sub(r"\W", "", r[1])) > 2]
    first_desc = next((r for r in main if abs(r[0].box.x0 - left) <= max(12.0, tolerance) and not _cost(r[1])), None)
    cost_rows = [r for r in main if first_desc is None or r[0].box.y0 < first_desc[0].box.y0]
    gains = tuple((kind.title(), int(n)) for r in cost_rows for n, kind in ACQUIRE.findall(r[1]))
    turns = next((TURNS.search(r[1]) for r in cost_rows if TURNS.search(r[1])), None)
    note = next((r[1] for r in cost_rows if TURNS.search(r[1]) and not ACQUIRE.search(r[1]) and len(r[1]) > 12), "")
    enhance_y = enhance_row[0].box.y0 if enhance_row else float("inf")
    burn_y = burn_row[0].box.y0 if burn_row else float("inf")
    description = _join(
        r
        for r in main
        if first_desc
        and r[0].box.y0 >= first_desc[0].box.y0
        and r[0].box.y0 < min(burn_y, enhance_y)
        and not CONSUME.search(r[1])
    )
    burn_text = _join(
        r
        for r in main
        if burn_row and r[0].box.y0 > burn_row[0].box.y1 - 6 and r[0].box.y0 < enhance_y and not CONSUME.search(r[1])
    )
    consume = next((CONSUME.search(r[1]) for r in main if burn_row and CONSUME.search(r[1])), None)
    steps = _steps([r for r in main if enhance_row and r[0].box.y0 > enhance_row[0].box.y1 - 6])
    used = [title_line, *(r[0] for r in cost_rows), *(r[0] for r in main if r[0].box.y0 >= title_line.box.y1)]
    panels = _panels(side, title_line)
    scores = [t.score for t in used if t.score] or [0.0]
    level = BADGE.match(clean(badge.text)) if badge else None
    return SkillPopup(
        hero=_hero(rows, title_line, left),
        name=clean(title_line.text),
        level=int(level.group(1)) if level else None,
        gains=gains,
        cooldown=int(turns.group(1)) if turns and not note else None,  # "Sacred Blessing 3 turns": not a skill cooldown
        cooldown_note=note,
        text=description,
        soulburn=burn_text,
        soulburn_cost=int(consume.group(1)) if consume else None,
        enhance=steps,
        effects=panels,
        confidence=min(scores),
    )


def looks_like_popup(lines: Sequence[TextLine]) -> bool:
    """Whether a screen shows a skill popup's own labels ("Skill Enhance", "Soulburn Effect", an "Acquire N Soul" row)
    whether or not `parse_skill_popup` could read it: a popup the reader does not understand yet (a layout never seen)
    is kept as a picture instead of being passed over in silence."""
    texts = [clean(line.text).lower() for line in lines]
    return any(text == "skill enhance" or text.startswith("soulburn effect") or ACQUIRE.search(text) for text in texts)


def read_hero_name(lines: Sequence[TextLine]) -> str | None:
    """The hero's name on the hero's own screen (no popup open): the tallest text of the screen's top left, where the
    name is written large over the art, before a held skill's popup covers it (SPEC D97). Sizes are relative to the
    text found, so a small window reads like a large one. None when nothing there looks like a name; whether it is a
    hero is for the catalog to say (an exact name or the margin rule, never a guess)."""
    rows = [(line, clean(line.text)) for line in lines if clean(line.text)]
    if len(rows) < 3:
        return None
    wide = max(r[0].box.x1 for r in rows)
    high = max(r[0].box.y1 for r in rows)
    corner = [
        r
        for r in rows
        if r[0].box.x0 <= 0.12 * wide
        and r[0].box.y0 <= 0.25 * high
        and not r[1].lower().startswith("lv")
        and sum(c.isalpha() for c in r[1]) >= 3
    ]
    name = max(corner, key=lambda r: r[0].box.height, default=None)
    if name is None or name[0].box.height < 0.04 * high:  # the name is the screen's largest text
        return None
    return re.sub(r"[^A-Za-z0-9' .&:-]+", "", name[1]).strip() or None


def _title_without_badge(rows: Sequence[tuple[TextLine, str]]) -> tuple[None, TextLine] | None:
    """(None, title line) for a skill at +0, which shows no badge: the line right above its "Acquire N Soul" row."""
    for acquire, text in rows:
        if not ACQUIRE.search(text):
            continue
        above = [
            line
            for line, other in rows
            if line is not acquire
            and abs(line.box.x0 - acquire.box.x0) <= 14
            and 0 <= acquire.box.y0 - line.box.y1 <= 40
            and not ACQUIRE.search(other)
            and len(other) >= 3
        ]
        if above:
            return None, min(above, key=lambda line: acquire.box.y0 - line.box.y1)
    return None


def _title_above_description(rows: Sequence[tuple[TextLine, str]]) -> tuple[None, TextLine] | None:
    """(None, title line) for a skill with neither a "+N" badge nor an "Acquire N Soul" row, a passive at +0 (Spirit Eye
    Celine's Sixth Sense, the user's capture 2026-10-26): the popup's first line, right of the icon and above the first
    line of the description. The description's left edge is the one of the "Skill Enhance" label, which a popup always
    has; a line right of the popup (the effect panels, the screen's own buttons) is never the title."""
    enhance = next((r for r in rows if r[1].lower() == "skill enhance"), None)
    if enhance is None:
        return None
    left = enhance[0].box.x0
    body = [
        r
        for r in rows
        if r is not enhance
        and abs(r[0].box.x0 - left) <= 14
        and r[0].box.y0 < enhance[0].box.y0 - 4
        and len(r[1]) >= 8
        and not BADGE.match(r[1])
    ]
    if not body:
        return None
    first = min(body, key=lambda r: r[0].box.y0)
    right = max(r[0].box.x1 for r in body)
    reach = 4 * first[0].box.height + 120  # the title is within a few lines above the description
    above = [
        r
        for r in rows
        if left + 40 <= r[0].box.x0 < right
        and r[0].box.y1 <= first[0].box.y0 + 4
        and first[0].box.y0 - r[0].box.y0 <= reach
        and r[1].lower() not in UI_LABELS
        and sum(c.isalpha() for c in r[1]) >= 3
    ]
    return (None, min(above, key=lambda r: r[0].box.y0)[0]) if above else None


def _title_named(rows: Sequence[tuple[TextLine, str]], titles: Sequence[str]) -> tuple[None, TextLine] | None:
    """(None, title line) when a line is one of the hero's skill names (exactly, or the margin rule) and a paragraph
    starts below it, one icon to its left (The Law of Skuggiheim, Harsetti's passive, the user's capture 2026-10-26: a
    title, then the text, nothing else)."""
    if not titles:
        return None
    names = {name: name for name in titles if name}
    for line, text in rows:
        if line.box.height < 20 or match_label(text, names) is None:
            continue
        under = [
            r
            for r in rows
            if line.box.x0 - 260 <= r[0].box.x0 <= line.box.x0 - 60 and 0 <= r[0].box.y0 - line.box.y1 <= 170
        ]
        if len(under) >= 2:
            return None, line
    return None


def _paragraph(rows: Sequence[tuple[TextLine, str]]) -> list[tuple[TextLine, str]]:
    """The rows from the first one down to the first vertical gap wider than most of a line: a paragraph of text and not
    what the screen has further down."""
    ordered = sorted(rows, key=lambda r: r[0].box.y0)
    if not ordered:
        return []
    unit = median(r[0].box.height for r in ordered)
    kept = [ordered[0]]
    for row in ordered[1:]:
        if row[0].box.y0 - kept[-1][0].box.y1 > 0.8 * unit:
            break
        kept.append(row)
    return kept


def _title(rows: Sequence[tuple[TextLine, str]]) -> tuple[TextLine, TextLine] | None:
    """(badge, title line): the "+N" line with a text line right next to it on the same row (not an enhancement step,
    whose "+N" is followed by its effect: "+2% Combat Readiness")."""
    best: tuple[TextLine, TextLine] | None = None
    for badge, text in rows:
        if not BADGE.match(text) or badge.box.height > 60:
            continue
        for line, other in rows:
            gap = line.box.x0 - badge.box.x1
            if (
                line is not badge
                and -6 <= gap <= 70
                and abs(line.box.cy - badge.box.cy) <= 0.8 * max(line.box.height, badge.box.height)
                and not BADGE.match(other)
                and not TEXT_STEP.match(other)
                and len(other) >= 3
                and (best is None or line.box.y0 < best[1].box.y0)
            ):
                best = (badge, line)
    return best


def _anchor(rows: Sequence[tuple[TextLine, str]], text: str, title: TextLine) -> tuple[TextLine, str] | None:
    """The topmost line below the title that starts with `text` (the popup's own, not a tab label further down)."""
    found = [r for r in rows if r[1].lower().startswith(text) and r[0].box.y0 > title.box.y1 - 4]
    return min(found, key=lambda r: r[0].box.y0, default=None)


def _main_left(
    title: TextLine,
    rows: Sequence[tuple[TextLine, str]],
    enhance: tuple[TextLine, str] | None,
    burn: tuple[TextLine, str] | None,
) -> float | None:
    if enhance:
        return enhance[0].box.x0
    if burn:
        return burn[0].box.x0
    near = [  # no enhancement steps and no soulburn: the description lines left of the title, one icon away
        r[0].box.x0
        for r in rows
        if title.box.x0 - 260 <= r[0].box.x0 <= title.box.x0 - 60 and r[0].box.y0 > title.box.y1 - 4
    ]
    return median(near) if near else None


def _cost(text: str) -> bool:
    return bool(ACQUIRE.search(text) or (TURNS.search(text) and len(text) < 40))


def _join(rows: Any) -> str:
    return clean(" ".join(r[1] for r in rows))


def _steps(rows: Sequence[tuple[TextLine, str]]) -> tuple[tuple[int, str], ...]:
    """The enhancement steps: a "+1" badge paired with the text on its row, a whole row read as one line ("+1 -1 turn
    cooldown"), or a row whose badge the OCR missed (numbered after the step before it)."""
    ordered = sorted(rows, key=lambda r: r[0].box.y0)
    used: set[int] = set()
    found: list[tuple[int, int, str]] = []  # (row, level or 0, text)
    for i, (line, text) in enumerate(ordered):
        match = STEP.match(text)
        if not match:
            continue
        if match.group(2):
            found.append((i, int(match.group(1)), match.group(2).strip()))
            used.add(i)
            continue
        partners = [
            (other.box.x0, j)
            for j, (other, other_text) in enumerate(ordered)
            if j != i
            and other.box.x0 > line.box.x1 - 4
            and abs(other.box.cy - line.box.cy) <= 0.6 * max(other.box.height, line.box.height)
            and not STEP.match(other_text)
        ]
        if partners:
            j = min(partners)[1]
            found.append((i, int(match.group(1)), ordered[j][1]))
            used.update((i, j))
    found += [(i, 0, text) for i, (_, text) in enumerate(ordered) if i not in used and TEXT_STEP.match(text)]
    steps: list[tuple[int, str]] = []
    last = 0
    for _, level, text in sorted(found):
        last = level or last + 1
        steps.append((last, text))
    return tuple(steps)


def _panels(side: Sequence[tuple[TextLine, str]], title: TextLine) -> tuple[EffectPanel, ...]:
    """The effect panels: the lines right of the main column from the title's row down, split where the vertical gap
    between two lines grows (a panel's padding), the first line of each being its title."""
    rows = sorted((r for r in side if r[0].box.y0 >= title.box.y0 - 30), key=lambda r: r[0].box.y0)
    if not rows:
        return ()
    height = median(r[0].box.height for r in rows)
    lefts = sorted(r[0].box.x0 for r in rows)
    anchor = max(lefts, key=lambda x: sum(abs(y - x) <= 15 for y in lefts))  # the left edge most lines share
    kept = [r for r in rows if anchor - 15 <= r[0].box.x0 <= anchor + 3.2 * height]
    groups: list[list[tuple[TextLine, str]]] = []
    for row in kept:
        if groups and row[0].box.y0 - groups[-1][-1][0].box.y1 <= 0.8 * height:
            groups[-1].append(row)
        else:
            groups.append([row])
    return tuple(_panel(g) for g in groups if len(g) >= 2)


def _panel(lines: Sequence[tuple[TextLine, str]]) -> EffectPanel:
    title, rest = lines[0][1], list(lines[1:])
    if len(rest) > 1 and re.match(r"^\([^)]*\)$", rest[0][1]):  # "(Undispellable)" wrapped onto its own line
        title, rest = f"{title} {rest[0][1]}", rest[1:]
    if len(rest) > 1 and title.split()[-1].lower() in CONTINUES:  # "Debuffs Which Prevent" / "Movement"
        title, rest = f"{title} {rest[0][1]}", rest[1:]
    flags = FLAGS.search(title)
    marks = tuple(m.strip() for m in flags.group(1).split(",")) if flags else ()
    return EffectPanel(FLAGS.sub("", title).strip() if flags else title, marks, _join(rest))


def _hero(rows: Sequence[tuple[TextLine, str]], title: TextLine, left: float) -> str | None:
    """The hero's name: the topmost tall line at the screen's left edge (the "Lv. Max/60" line below it is tall too)."""
    wide = max(r[0].box.x1 for r in rows)  # the capture's width, as far as the text goes
    tall = [
        r for r in rows if r[0].box.height >= 60 and r[0].box.x0 <= 0.12 * wide and not r[1].lower().startswith("lv")
    ]
    top = min(tall, key=lambda r: r[0].box.y0, default=None)
    return re.sub(r"[^A-Za-z0-9' .&:-]+", "", top[1]).strip() if top else None
