"""Spike M7 / gear_layout: the STRUCTURE of the Hero Info gear panel, the artifact box and the EE box from OCR lines.

Throwaway experiment (CLAUDE.md: spikes are never imported by src/). Reads the user's own Hero Info captures
(git-ignored) and a local catalog snapshot (artifact names -> codes). Nothing is positioned in pixels of one resolution:
the unit `h` is the height of the OCR line "Average Equipment Score: N"; every window is a multiple of `h` around an
OCR line or a structure found in the image (value columns, piece rows, icon centre).

Method
1. Anchor: the line "Average Equipment Score: N" (N = avg score). Absent -> no gear panel (e.g. no gear equipped).
2. Value columns: value-shaped lines (525, 2,765, 9%) below the anchor are clustered by RIGHT edge (right-aligned
   text). A cluster is a value column only if it has two rows closer than 1.2h (a stat list); the item level/score
   columns have one line per piece (4.6h apart) and never qualify. Two columns -> left/right by x; one column -> by its
   offset from the anchor (fallback ratio, flagged).
3. Pieces: lines of a column, top to bottom; a piece = first line + every line within 3.4h below it
   (main->4th sub = 3.0h, sub1->next main = 3.8h, so one missed row never splits or merges pieces).
   Main row = first row when it lines up with the piece grid (same row across columns, the icon's top); else flagged.
4. Slot: row index k = round((main.cy - (anchor.cy + 1.53h)) / P), P = measured piece pitch (fallback 4.58h);
   left column k -> weapon/helmet/armor, right column -> necklace/ring/boots. |fraction| > 0.3 -> review.
5. Icon block of each piece (x in [col.x1 - 7h, col.x1 - 3h], y in [ref - 1h, ref + 3.6h]):
   '+N' badge (top), item level (top-left number), score (bottom number). Icon centre x = score centre (or the
   column median). Missing/odd fields -> second OCR pass on an upscaled crop around the expected spot.
   No badge after both passes -> +0, confirmed by the absence of the bright red badge pill (colour test).
6. Grade: hue votes of the saturated, not-bright pixels in the icon interior: red vs purple clusters.
   Cross-check (community knowledge, unverified): at +0 epic has 4 subs, heroic 3.
7. Artifact: "Lv.X/Y" line above the anchor and right of it; name = the line just below; '+N' = above-left of the Lv
   line (second pass if missing; absent -> +0). Name -> catalog code: exact normalised, else fuzzy with margin.
8. EE: a value line ("12%") with a text line below it, above the anchor and LEFT of the artifact box.

Usage:
    E7AC_HOME=.../m7/home uv run python spikes/m7_gear_layout.py               # 6 captures x scales 0.64 1.0 1.28
    uv run python spikes/m7_gear_layout.py --only heroinfo_politis --scales 1.0 -v
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sqlite3
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from e7ac.vision.image import load_image
from e7ac.vision.ocr import Box, RapidOcrReader, TextLine, Word

REPO = Path(__file__).resolve().parents[1]
SHOTS = REPO / "fixtures" / "screenshots"
M7 = Path("/tmp/claude-0/-home-user-Orbis-Codex/d44236ee-bada-567e-a707-c7890345df7b/scratchpad/m7")
OUT = M7 / "gear_layout"
CATALOG = M7 / "home" / "e7ac.sqlite3"
TRUTH = M7 / "truth.json"
OTHER_CACHE = M7 / "sets" / "ocr_cache"  # same capture+scale+resize method -> reused read-only when present

CAPTURES = ["heroinfo_haru", "heroinfo_lots", "heroinfo_ainz", "heroinfo_straze", "heroinfo_politis",
            "heroinfo_charles"]
LEFT_SLOTS = ["weapon", "helmet", "armor"]
RIGHT_SLOTS = ["necklace", "ring", "boots"]

# Grade colour truth, transcribed by eye for THIS spike (truth.json has no grade): R = red frame, P = purple frame.
GRADE_EYE = {
    "heroinfo_haru": "RRRRRR", "heroinfo_lots": "RRRRRR", "heroinfo_ainz": "PRRRRP",
    "heroinfo_straze": "RPRRRP", "heroinfo_politis": "PXRRRR",
}  # order weapon helmet armor necklace ring boots. X = Politis helmet: purple+red swirl background, gold epic-style
# inner frame + corner ornament, lilac card outline; matches neither look -> grade unknown, the right answer is REVIEW.
# truth.json says "Lv.Max/8" for Lady of the Scales; a 6x zoom shows "Lv.Max/6" (the 6 sits on the artwork and the
# first OCR pass read 8). Corrected here, reported.
LV_TEXT_FIX = {"heroinfo_lots": "Lv.Max/6"}

# ---- layout ratios (multiples of h = anchor line height), measured on the 2000x1167 captures, see report ----------
ROW_PITCH = 0.72  # sub row -> next sub row, in h (31 px at h = 43); the unit h is re-derived from it (see parse)
COL_TOL = 0.35  # right-edge clustering tolerance
ROW_MAX = 1.2  # two rows of one stat list are closer than this
PIECE_SPAN = 3.4  # main -> last sub is 3.0h; sub1 -> next main is 3.8h
FIRST_ROW = 1.53  # anchor.cy -> first main-stat row cy
PITCH_FALLBACK = 4.58  # main -> next main
ICON_X = (7.0, 3.0)  # icon block between col.x1 - 7h and col.x1 - 3h
ICON_CX_FALLBACK = 4.85  # col.x1 - icon centre (only when no score was read in the column)
RIGHT_COL_MIN = 9.4  # single column: x1 - anchor.x0 > 9.4h -> right column

VALUE_RE = re.compile(r"^\d{1,3}(?:,\d{3})*%?$")
VALUE_TAIL_RE = re.compile(r"(\d{1,3}(?:,\d{3})*%?)$")
BADGE_RE = re.compile(r"^\+\s?(\d{1,2})$")
INT_RE = re.compile(r"^\d{1,3}$")
ANCHOR_RE = re.compile(r"Average\s*Equipment\s*Score\s*[:;.]?\s*(\d{1,3})?", re.I)
LV_RE = re.compile(r"^Lv\s*\.?\s*(Max|\d{1,2})\s*/\s*(\d{1,2})?$", re.I)  # the max may hide on the art
EE_VALUE_RE = re.compile(r"^\+?\d{1,3}(?:\.\d)?%?$")


# --------------------------------------------------------------------------------------------- data
@dataclass
class Fld:
    """A parsed field: value + confidence (0..1) + how it was obtained (never silently guessed)."""

    value: Any
    conf: float
    how: str
    note: str = ""

    def __str__(self) -> str:
        return f"{self.value}({self.how},{self.conf:.2f}{',' + self.note if self.note else ''})"


@dataclass
class Piece:
    col: str  # "L" | "R"
    rows: list[TextLine]
    slot: Fld | None = None
    ref_y: float = 0.0  # main-row centre y
    main_ok: bool = True
    icon_cx: float = 0.0
    level: Fld | None = None
    enhance: Fld | None = None
    score: Fld | None = None
    grade: Fld | None = None
    hue: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    pill: float = 0.0
    level_check: Any = None
    probe: list[tuple[int, float]] = field(default_factory=list)

    @property
    def main(self) -> str | None:
        """None when the first row is not on the grid (the main stat was not read): never promote a sub."""
        return clean_value(self.rows[0].text)[0] if self.rows and self.main_ok else None

    @property
    def subs(self) -> list[str]:
        return [clean_value(r.text)[0] for r in (self.rows[1:] if self.main_ok else self.rows)]


@dataclass
class Panel:
    name: str
    scale: float
    h: float = 0.0
    h0: float = 0.0
    row_pitch: float = 0.0
    d1: float = 0.0  # main row -> first sub row (centres)
    anchor: TextLine | None = None
    avg: Fld | None = None
    cols: dict[str, float] = field(default_factory=dict)  # "L"/"R" -> right edge x
    pieces: list[Piece] = field(default_factory=list)
    pitch: float = 0.0
    artifact: dict[str, Any] = field(default_factory=dict)
    ee: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    second_pass: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------------------------- OCR
_reader = RapidOcrReader()


def _lines_from_json(data: list[dict[str, Any]]) -> list[TextLine]:
    return [TextLine(d["text"], d["score"], Box(*d["box"]),
                     tuple(Word(w[0], w[1], Box(*w[2])) for w in d["words"])) for d in data]


def ocr(name: str, scale: float, img: Any) -> list[TextLine]:
    mine = OUT / "ocr_cache" / f"{name}_{scale:.2f}.json"
    for cache in (mine, OTHER_CACHE / f"{name}_{scale:.2f}.json"):
        if cache.exists():
            return _lines_from_json(json.loads(cache.read_text()))
    lines = _reader.read(img)
    mine.parent.mkdir(parents=True, exist_ok=True)
    mine.write_text(json.dumps([
        {"text": l.text, "score": l.score, "box": [l.box.x0, l.box.y0, l.box.x1, l.box.y1],
         "words": [[w.text, w.score, [w.box.x0, w.box.y0, w.box.x1, w.box.y1]] for w in l.words]} for l in lines]))
    return lines


def clean_value(text: str) -> tuple[str, bool]:
    """Value text and whether characters were stripped (an icon glyph glued to the number)."""
    t = text.replace(" ", "")
    if VALUE_RE.match(t):
        return t, False
    m = VALUE_TAIL_RE.search(t)
    if m and len(t) - len(m.group(1)) <= 2 and not t[: len(t) - len(m.group(1))].isdigit():
        return m.group(1), True
    return t, False


def is_value(line: TextLine) -> bool:
    t, _ = clean_value(line.text)
    return bool(VALUE_RE.match(t))


# --------------------------------------------------------------------------------------------- gear panel
def find_anchor(lines: list[TextLine]) -> tuple[TextLine | None, Fld | None]:
    for l in lines:
        m = ANCHOR_RE.search(l.text)
        if not m:
            continue
        if m.group(1):
            return l, Fld(int(m.group(1)), min(1.0, l.score), "ocr")
        # number split into its own line on the same row
        right = [o for o in lines if INT_RE.match(o.text) and abs(o.box.cy - l.box.cy) < 0.4 * l.box.height
                 and 0 <= o.box.x0 - l.box.x1 < 2 * l.box.height]
        if right:
            return l, Fld(int(right[0].text), 0.8, "ocr-split")
        return l, None
    return None, None


def value_columns(lines: list[TextLine], a: TextLine, h: float, warn: list[str]) -> dict[str, float]:
    cands = sorted((l for l in lines if l.box.cy > a.box.y1 and l.box.x0 > a.box.x0 - 1.5 * h and is_value(l)),
                   key=lambda l: l.box.x1)
    clusters: list[list[TextLine]] = []
    for l in cands:
        if clusters and l.box.x1 - clusters[-1][-1].box.x1 <= COL_TOL * h:
            clusters[-1].append(l)
        else:
            clusters.append([l])
    cols = []
    for c in clusters:
        ys = sorted(l.box.cy for l in c)
        if len(c) >= 2 and any(b - a_ < ROW_MAX * h for a_, b in zip(ys, ys[1:], strict=False)):
            cols.append((len(c), statistics.median(l.box.x1 for l in c)))
    if len(cols) > 2:
        warn.append(f"{len(cols)} value-column candidates, kept the 2 largest")
        cols = sorted(cols, reverse=True)[:2]
    xs = sorted(x for _, x in cols)
    if len(xs) == 2:
        return {"L": xs[0], "R": xs[1]}
    if len(xs) == 1:
        side = "R" if xs[0] - a.box.x0 > RIGHT_COL_MIN * h else "L"
        warn.append(f"one value column only -> {side} by anchor offset ratio (fallback)")
        return {side: xs[0]}
    return {}


def row_pitch(lines: list[TextLine], cols: dict[str, float], a: TextLine, h0: float) -> float:
    diffs = []
    for x1 in cols.values():
        ys = sorted(l.box.cy for l in lines if l.box.cy > a.box.y1 and abs(l.box.x1 - x1) <= COL_TOL * h0
                    and is_value(l))
        diffs += [b - a_ for a_, b in zip(ys, ys[1:], strict=False) if 0.4 * h0 < b - a_ < 1.0 * h0]
    return statistics.median(diffs) if len(diffs) >= 3 else 0.0


def split_pieces(lines: list[TextLine], x1: float, a: TextLine, h: float, col: str) -> list[Piece]:
    rows = sorted((l for l in lines if l.box.cy > a.box.y1 and abs(l.box.x1 - x1) <= COL_TOL * h and is_value(l)),
                  key=lambda l: l.box.cy)
    pieces: list[Piece] = []
    for l in rows:
        if pieces and l.box.cy - pieces[-1].rows[0].box.cy < PIECE_SPAN * h:
            pieces[-1].rows.append(l)
        else:
            pieces.append(Piece(col, [l]))
    return pieces


def assign_slots(panel: Panel, h: float) -> None:
    a = panel.anchor
    assert a is not None
    starts = sorted(p.rows[0].box.cy for p in panel.pieces)
    # piece pitch from same-column consecutive pieces (diff / round(diff / fallback))
    diffs = []
    for col in ("L", "R"):
        ys = sorted(p.rows[0].box.cy for p in panel.pieces if p.col == col)
        for y0, y1 in zip(ys, ys[1:], strict=False):
            n = round((y1 - y0) / (PITCH_FALLBACK * h))
            if n >= 1:
                diffs.append((y1 - y0) / n)
    pitch = statistics.median(diffs) if diffs else PITCH_FALLBACK * h
    panel.pitch = pitch
    y_first = a.box.cy + FIRST_ROW * h
    # grid: row k centre = median of starts that round to k (shared by both columns)
    grid: dict[int, list[float]] = {}
    for y in starts:
        grid.setdefault(round((y - y_first) / pitch), []).append(y)
    for p in panel.pieces:
        y = p.rows[0].box.cy
        f = (y - y_first) / pitch
        k = round(f)
        names = LEFT_SLOTS if p.col == "L" else RIGHT_SLOTS
        ok = 0 <= k < 3 and abs(f - k) <= 0.3
        p.slot = Fld(names[k] if 0 <= k < 3 else None, 1.0 - abs(f - k) if ok else 0.2,
                     "grid", "" if ok else f"row fraction {f:.2f} -> REVIEW")
        if not ok:
            p.warnings.append(f"slot row fraction {f:.2f}")
        others = [o for o in grid.get(k, []) if o != y]
        row_y = statistics.median(others) if others else y_first + k * pitch
        p.main_ok = abs(y - row_y) < 0.35 * h
        if not p.main_ok:
            p.warnings.append("first row not on the piece grid: main stat probably missed")
        if len(p.rows) > 5:
            p.warnings.append(f"{len(p.rows)} rows > 5")
        r = panel.row_pitch or ROW_PITCH * h
        if any(b.box.cy - a_.box.cy > 1.6 * r for a_, b in zip(p.rows, p.rows[1:], strict=False)):
            p.warnings.append("gap inside the piece: a row was probably missed -> REVIEW")
        p.ref_y = y if p.main_ok else row_y
    d1s = [p.rows[1].box.cy - p.rows[0].box.cy for p in panel.pieces if p.main_ok and len(p.rows) > 1]
    d1s = [d for d in d1s if d < 1.6 * (panel.row_pitch or ROW_PITCH * h)]
    panel.d1 = statistics.median(d1s) if d1s else 0.0


def icon_fields(panel: Panel, img: Any, lines: list[TextLine], h: float, second: bool, verify: bool) -> None:
    for col, x1 in panel.cols.items():
        col_pieces = [p for p in panel.pieces if p.col == col]
        xa, xb = x1 - ICON_X[0] * h, x1 - ICON_X[1] * h
        for p in col_pieces:
            band = [l for l in lines if xa <= l.box.x0 and l.box.x1 <= xb
                    and p.ref_y - 1.0 * h <= l.box.cy <= p.ref_y + 3.6 * h]
            top = [l for l in band if l.box.cy < p.ref_y + 1.2 * h]
            bottom = [l for l in band if l.box.cy > p.ref_y + 1.6 * h]
            scores = [l for l in bottom if INT_RE.match(l.text.strip())]
            badges = [l for l in top if BADGE_RE.match(l.text.strip())]
            levels = [l for l in top if re.match(r"^\d{2,4}$", l.text.strip())]
            if scores:
                s = max(scores, key=lambda l: l.score)
                p.score = Fld(int(s.text.strip()), s.score, "ocr")
                p.icon_cx = (s.box.x0 + s.box.x1) / 2
            if badges:
                b = badges[0]
                p.enhance = Fld(int(BADGE_RE.match(b.text.strip()).group(1)), b.score, "ocr")  # type: ignore[union-attr]
            if levels:
                lv = min(levels, key=lambda l: l.box.x0)
                txt = lv.text.strip()
                p.level = Fld(int(txt), lv.score if len(txt) == 2 else 0.3, "ocr",
                              "" if len(txt) == 2 else "not 2 digits")
        cxs = [p.icon_cx for p in col_pieces if p.icon_cx]
        cx_col = statistics.median(cxs) if cxs else x1 - ICON_CX_FALLBACK * h
        for p in col_pieces:
            if not p.icon_cx:
                p.icon_cx = cx_col
                p.warnings.append("icon centre from column median" if cxs else "icon centre from ratio fallback")
            if second:
                second_pass(panel, p, img, h, verify)
                probe_rows(panel, p, img, h)
            p.pill = badge_pill(img, p, h)
            if p.enhance is None:
                # no '+N' read: +0 only when the red pill is visibly absent; a pill we cannot read -> REVIEW
                if p.pill < PILL_MAX_ABSENT:
                    p.enhance = Fld(0, 0.9, "absent", f"pill={p.pill:.3f}")
                else:
                    p.enhance = Fld(None, 0.0, "absent?", f"pill={p.pill:.3f} RED PILL PRESENT -> REVIEW")
            p.grade, p.hue = grade_colour(img, p, h)


# Second pass on the item level: the number sits on a gold corner ornament (read as 'F', '¥', '新' before the digits)
# and on the item art (read as a trailing '1'). A tight crop drops both; several renderings vote.
LEVEL_TIGHT = (-1.08, -0.35, -0.33, 0.65)  # (x0, y0, x1, y1) around (icon cx, main-row cy), in h
LEVEL_MID = (-1.15, -0.45, -0.25, 0.75)


def _render(crop: Any, h: float, mode: str) -> Any:
    f = max(1.5, 100.0 / h)
    up = cv2.resize(crop, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    border = (0, 0, 0)
    if mode == "gray":  # HSV value channel: digits are the brightest thing, hue of the art no longer matters
        up = cv2.cvtColor(cv2.cvtColor(up, cv2.COLOR_BGR2HSV)[..., 2], cv2.COLOR_GRAY2BGR)
    elif mode == "bin":  # bright and not too saturated (cream digits) -> black on white
        hsv = cv2.cvtColor(up, cv2.COLOR_BGR2HSV)
        m = ((hsv[..., 2] > 190) & (hsv[..., 1] < 150)).astype(np.uint8) * 255
        up = cv2.cvtColor(255 - m, cv2.COLOR_GRAY2BGR)
        border = (255, 255, 255)
    return cv2.copyMakeBorder(up, 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=border)


def _read_variants(img: Any, cx: float, ry: float, h: float,
                   variants: list[tuple[tuple[float, float, float, float], str]]) -> list[list[str]]:
    out = []
    for (a, b, c, d), mode in variants:
        crop = _region(img, cx + a * h, ry + b * h, cx + c * h, ry + d * h)
        out.append([l.text.strip() for l in _reader.read(_render(crop, h, mode))] if crop.size else [])
    return out


def vote(reads: list[list[str]], pattern: str) -> tuple[Any, int, dict[str, int]]:
    """Majority over renderings: a value needs >= 2 votes and more votes than any other candidate."""
    tally: dict[str, int] = {}
    for texts in reads:
        for t in {t for t in texts if re.fullmatch(pattern, t)}:
            tally[t] = tally.get(t, 0) + 1
    if not tally:
        return None, 0, tally
    ranked = sorted(tally.items(), key=lambda kv: -kv[1])
    best, n = ranked[0]
    if n >= 2 and (len(ranked) == 1 or ranked[1][1] < n):
        return best, n, tally
    return None, n, tally


ROW_INK_MIN = 0.2  # ink at an expected row / weakest read row of the piece; real rows >= 0.31, empty <= 0.17


def row_ink(img: Any, x1: float, cy: float, h: float) -> float:
    """Fraction of light, unsaturated pixels clearly brighter than the cell background (value cell ~1.5h wide)."""
    reg = _region(img, x1 - 1.5 * h, cy - 0.3 * h, x1 + 0.05 * h, cy + 0.3 * h)
    if reg.size == 0:
        return 0.0
    hsv = cv2.cvtColor(reg, cv2.COLOR_BGR2HSV)
    v = hsv[..., 2].astype(int)
    # sub values are semi-transparent grey (V 150-200, S 40-55) over a varying background: contrast, not level
    return float(((v > np.median(v) + 45) & (hsv[..., 1] < 90)).mean())


def read_row(img: Any, x1: float, cy: float, h: float, r: float, x0_h: float) -> tuple[Any, int, dict[str, int]]:
    """OCR a value cell (right edge x1, centre cy) in two renderings; keep only lines whose centre maps back within
    0.35 row pitches of cy and whose right edge is within 0.5h of x1 (a neighbour row is never taken), then vote."""
    xa, ya = max(0, int(x1 - x0_h * h)), max(0, int(cy - 0.6 * h))
    crop = _region(img, xa, ya, x1 + 0.2 * h, cy + 0.6 * h)
    f = max(1.5, 100.0 / h)
    reads = []
    for mode in ("col", "gray"):
        got = []
        for l in (_reader.read(_render(crop, h, mode)) if crop.size else []):
            lcy = ya + ((l.box.y0 + l.box.y1) / 2 - 40) / f
            lx1 = xa + (l.box.x1 - 40) / f
            if abs(lcy - cy) < 0.35 * r and abs(lx1 - x1) < 0.5 * h:
                got.append(clean_value(l.text)[0])
        reads.append(got)
    return vote(reads, VALUE_RE.pattern.strip("^$")) + (reads,)  # type: ignore[return-value]


def probe_rows(panel: Panel, p: Piece, img: Any, h: float) -> None:
    """A piece with < 4 subs may be legit (heroic +0 starts with 3) or an OCR miss: look for text ink at the
    expected sub positions (main + d1 + k*r) and re-read any that has ink. Never invents a row without a read."""
    if not panel.d1:
        return
    x1, r = panel.cols[p.col], panel.row_pitch
    slot = p.slot.value if p.slot else "?"
    if not p.main_ok:  # first row off the grid: read the main stat where the grid says it is
        v, n, tally, reads = read_row(img, x1, p.ref_y, h, r, 3.0)
        panel.second_pass.append(f"{slot} probe main: {reads} -> {v}")
        if v is None:
            p.warnings.append(f"main stat unreadable {reads} -> REVIEW")
            return
        p.rows.insert(0, TextLine(v, 0.5 + 0.1 * n, Box(x1 - 2.5 * h, p.ref_y - 0.45 * h, x1, p.ref_y + 0.45 * h), ()))
        p.main_ok = True
        p.warnings.append(f"main recovered by probe: {v} (votes {tally})")
    main_cy = p.rows[0].box.cy
    known = [row_ink(img, x1, row.box.cy, h) for row in p.rows[1:]] or [0.6 * row_ink(img, x1, main_cy, h)]
    ref = min(known)
    p.probe = []
    for k in range(4):
        cy = main_cy + panel.d1 + k * r
        if any(abs(row.box.cy - cy) < 0.35 * r for row in p.rows[1:]):
            continue
        ink = row_ink(img, x1, cy, h)
        p.probe.append((k + 1, round(ink / ref, 2) if ref else 0.0))
        if not ref or ink < ROW_INK_MIN * ref:
            continue
        v, n, tally, reads = read_row(img, x1, cy, h, r, 2.0)
        if v is not None:
            p.rows.append(TextLine(v, 0.5 + 0.1 * n, Box(x1 - 1.5 * h, cy - 0.35 * h, x1, cy + 0.35 * h), ()))
            p.rows[1:] = sorted(p.rows[1:], key=lambda l: l.box.cy)
            p.warnings.append(f"sub {k + 1} recovered by probe: {v} (votes {tally})")
        else:
            p.warnings.append(f"sub {k + 1}: ink {ink / ref:.2f} of a read row but unreadable {reads} -> REVIEW")
        panel.second_pass.append(f"{slot} probe sub {k + 1}: ink {ink / ref:.2f} -> {reads} -> {v}")


def second_pass(panel: Panel, p: Piece, img: Any, h: float, verify: bool = False) -> None:
    cx, ry = p.icon_cx, p.ref_y
    slot = p.slot.value if p.slot else "?"
    need_level = p.level is None or p.level.conf < 0.5
    if need_level or verify:
        reads = _read_variants(img, cx, ry, h, [(LEVEL_TIGHT, "col"), (LEVEL_TIGHT, "gray"), (LEVEL_TIGHT, "bin"),
                                                (LEVEL_MID, "gray")])
        v, n, tally = vote(reads, r"\d{2}")
        before = str(p.level) if p.level else "missing"
        if need_level:
            if v is not None:
                p.level = Fld(int(v), 0.5 + 0.1 * n, "ocr2", f"votes {tally}")
            elif p.level is not None:
                p.level = Fld(None, 0.0, "review", f"1st {p.level.value}, 2nd {tally}")
        else:
            p.level_check = v  # verification of a good first read
            if v is not None and p.level is not None and int(v) != p.level.value:
                p.warnings.append(f"level 1st {p.level.value} != 2nd {v} -> REVIEW")
                p.level = Fld(None, 0.0, "review", f"1st/2nd disagree {tally}")
        panel.second_pass.append(f"{slot} level: {before} -> {reads} -> {p.level.value if p.level else None}")
    if p.enhance is None:
        reads = _read_variants(img, cx, ry, h, [((0.0, -0.9, 1.8, 0.5), "col"), ((0.25, -0.65, 1.65, 0.35), "col"),
                                                ((0.25, -0.65, 1.65, 0.35), "gray")])
        v, n, tally = vote(reads, r"\+\s?\d{1,2}")
        if v is not None:
            p.enhance = Fld(int(v.lstrip("+").strip()), 0.5 + 0.1 * n, "ocr2", f"votes {tally}")
        panel.second_pass.append(f"{slot} badge: missing -> {reads} -> {p.enhance.value if p.enhance else 'none'}")
    if p.score is None:
        reads = _read_variants(img, cx, ry, h, [((-1.3, 2.1, 1.3, 3.5), "col"), ((-1.3, 2.1, 1.3, 3.5), "gray")])
        v, n, tally = vote(reads, r"\d{1,3}")
        if v is not None:
            p.score = Fld(int(v), 0.5 + 0.1 * n, "ocr2", f"votes {tally}")
        panel.second_pass.append(f"{slot} score: missing -> {reads} -> {p.score.value if p.score else None}")


def _region(img: Any, x0: float, y0: float, x1: float, y1: float) -> Any:
    H, W = img.shape[:2]
    return img[max(0, int(y0)):min(H, int(y1)), max(0, int(x0)):min(W, int(x1))]


PILL_MAX_ABSENT = 0.04  # bright-red fraction below which the '+N' pill is taken as absent (see report)
PURPLE_MIN, RED_MAX = 0.8, 0.2  # purple fraction of the red+purple votes in the background strips


def _bright_red(reg: Any, hue_max: int) -> float:
    if reg.size == 0:
        return 0.0
    hsv = cv2.cvtColor(reg, cv2.COLOR_BGR2HSV)
    hh, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    return float((((hh <= hue_max) | (hh >= 172)) & (s > 150) & (v > 170)).mean())


def badge_pill(img: Any, p: Piece, h: float) -> float:
    """Fraction of bright saturated red pixels where the '+N' pill sits (top-right corner of the icon).

    Pill box measured from the OCR '+15' boxes: x cx+0.37..1.44h, y ref-0.45..+0.23h."""
    return _bright_red(_region(img, p.icon_cx + 0.4 * h, p.ref_y - 0.4 * h, p.icon_cx + 1.4 * h, p.ref_y + 0.2 * h), 8)


def grade_colour(img: Any, p: Piece, h: float) -> tuple[Fld, dict[str, float]]:
    """Red vs purple background, voted on edge strips of the icon interior where the item art rarely reaches.

    Interior ~ [cx +- 1.22h] x [ref - 0.12h, ref + 2.25h] (measured). Strips (b = 0.3h): left edge below the level
    number, top edge between level and badge, bottom-left edge. The badge pill (red) and the set icon are outside.
    """
    reg = _region(img, p.icon_cx - 1.22 * h, p.ref_y - 0.12 * h, p.icon_cx + 1.22 * h, p.ref_y + 2.25 * h)
    H, W = reg.shape[:2]
    b = max(2, int(0.3 * h))
    m = np.zeros((H, W), bool)
    m[int(0.75 * h):, :b] = True
    m[:b, int(1.15 * h):int(1.5 * h)] = True
    m[-b:, :int(1.2 * h)] = True
    hsv = cv2.cvtColor(reg, cv2.COLOR_BGR2HSV)
    hh, s, v = hsv[..., 0].astype(int), hsv[..., 1], hsv[..., 2]
    sel = (s > 90) & (v > 40) & (v < 210) & m
    red = int((sel & ((hh <= 10) | (hh >= 172))).sum())
    purple = int((sel & (hh >= 135) & (hh < 172)).sum())
    tot = red + purple
    cov = tot / max(1, int(m.sum()))
    pf = purple / tot if tot else 0.0
    stats = {"pfrac": pf, "cov": cov}
    if cov < 0.2:
        return Fld(None, 0.0, "colour", f"coverage {cov:.2f}"), stats
    if pf >= PURPLE_MIN:
        return Fld("purple", min(1.0, pf), "colour"), stats
    if pf <= RED_MAX:
        return Fld("red", min(1.0, 1 - pf), "colour"), stats
    return Fld(None, 0.0, "colour", f"REVIEW mixed purple fraction {pf:.2f}"), stats


# --------------------------------------------------------------------------------------------- artifact / EE
def load_artifacts() -> dict[str, list[str]]:
    con = sqlite3.connect(CATALOG)
    out: dict[str, list[str]] = {}
    for code, name in con.execute("select entity_id, name from catalog_entity where entity_type='artifact'"):
        out.setdefault(norm(name), []).append(code)
    return out


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.casefold())


def match_artifact(text: str, arts: dict[str, list[str]]) -> Fld:
    n = norm(text)
    if n in arts:
        codes = arts[n]
        return Fld(codes[0] if len(codes) == 1 else None, 1.0 if len(codes) == 1 else 0.0, "exact",
                   "" if len(codes) == 1 else f"duplicate name {codes}")
    ranked = sorted(((difflib.SequenceMatcher(None, n, k).ratio(), k) for k in arts), reverse=True)
    (s1, k1), (s2, _) = ranked[0], ranked[1]
    if s1 >= 0.85 and s1 - s2 >= 0.08 and len(arts[k1]) == 1:
        return Fld(arts[k1][0], s1, "fuzzy", f"margin {s1 - s2:.2f}")
    return Fld(None, 0.0, "fuzzy", f"REVIEW best {k1} {s1:.2f} margin {s1 - s2:.2f}")


ART_PILL_MAX_ABSENT = 0.15  # bright red/orange fraction in the artifact pill box below which there is no '+N'


def parse_artifact(panel: Panel, img: Any, lines: list[TextLine], arts: dict[str, list[str]], second: bool) -> None:
    a, h = panel.anchor, panel.h
    assert a is not None
    lvs = [l for l in lines if LV_RE.match(l.text.replace(" ", "")) and l.box.x0 > a.box.x0
           and l.box.y1 < a.box.y0 and l.box.y0 > a.box.y0 - 6 * h]
    if not lvs:
        panel.artifact = {"present": Fld(None, 0.0, "REVIEW: no Lv line (no artifact, or OCR missed it)")}
        return
    lv = lvs[0]
    m = LV_RE.match(lv.text.replace(" ", ""))
    assert m
    cur, mx = m.group(1), (int(m.group(2)) if m.group(2) else None)
    art: dict[str, Any] = {
        "present": Fld(True, lv.score, "ocr"),
        "lv_text": Fld(lv.text.replace(" ", ""), lv.score if mx else 0.3, "ocr", "" if mx else "max part unread"),
        "lv": (cur, mx), "box": lv.box}
    names = [l for l in lines if abs(l.box.x0 - lv.box.x0) < 0.6 * h and 0.3 * h < l.box.cy - lv.box.cy < 1.6 * h
             and re.search(r"[A-Za-z]{3}", l.text)]
    if names:
        art["name"] = Fld(names[0].text, names[0].score, "ocr")
        art["code"] = match_artifact(names[0].text, arts)
    else:
        art["name"] = Fld(None, 0.0, "missing")
        art["code"] = Fld(None, 0.0, "missing")
    # '+N' pill: above-left of the Lv line (measured: x lv.x0-0.78h..lv.x0, y lv.cy-0.94h..-0.41h)
    pill = _bright_red(_region(img, lv.box.x0 - 0.75 * h, lv.box.cy - 0.9 * h, lv.box.x0 - 0.05 * h,
                               lv.box.cy - 0.45 * h), 22)
    art["pill"] = pill
    badges = [l for l in lines if BADGE_RE.match(l.text.strip()) and lv.box.x0 - 2.5 * h < l.box.x1 < lv.box.x0
              + 0.3 * h and lv.box.cy - 1.6 * h < l.box.cy < lv.box.cy + 0.2 * h]
    if badges:
        art["enhance"] = Fld(int(BADGE_RE.match(badges[0].text.strip()).group(1)), badges[0].score, "ocr")  # type: ignore[union-attr]
    else:
        v = None
        if second:
            reads = []
            for (x0, y0, x1, y1), mode in [((-1.5, -1.4, 0.3, -0.1), "col"), ((-1.0, -1.1, 0.1, -0.3), "col"),
                                           ((-1.0, -1.1, 0.1, -0.3), "gray")]:
                crop = _region(img, lv.box.x0 + x0 * h, lv.box.cy + y0 * h, lv.box.x0 + x1 * h, lv.box.cy + y1 * h)
                reads.append([l.text.strip() for l in _reader.read(_render(crop, h, mode))] if crop.size else [])
            v, n, tally = vote(reads, r"\+\s?\d{1,2}")
            panel.second_pass.append(f"artifact badge: missing -> {reads} -> {v} (pill {pill:.3f})")
        if v is not None:
            art["enhance"] = Fld(int(v.lstrip("+").strip()), 0.5 + 0.1 * n, "ocr2", f"votes {tally}")
        elif pill < ART_PILL_MAX_ABSENT:
            art["enhance"] = Fld(0, 0.8, "absent", f"pill={pill:.3f}")
        else:
            art["enhance"] = Fld(None, 0.0, "absent?", f"pill={pill:.3f} PILL PRESENT, unread -> REVIEW")
    # consistency (community, unverified: skill level +1 every 3 enhancement levels; 'Max' when X == Y)
    e = art["enhance"].value
    x = int(cur) if cur.isdigit() else mx
    art["lv_check"] = None if e is None or x is None else (1 + e // 3 == x)
    if art["lv_check"] is False:  # one of the two reads is wrong; we cannot tell which -> both to REVIEW
        art["lv_text"].conf = min(art["lv_text"].conf, 0.2)
        art["lv_text"].note = "inconsistent with +N -> REVIEW"
        art["enhance"].conf = min(art["enhance"].conf, 0.4)
        art["enhance"].note = (art["enhance"].note + " inconsistent with Lv -> REVIEW").strip()
        panel.warnings.append(f"artifact Lv {art['lv']} vs +{e}: inconsistent (community rule, unverified)")
    panel.artifact = art


LABELS = {"Attack": "atk", "Defense": "def", "Health": "hp", "Speed": "spd", "Critical Hit Chance": "cc",
          "Critical Hit Damage": "cd", "Effectiveness": "eff", "Effect Resistance": "er"}


def _icon_mask(crop: Any) -> Any:
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    m = ((hsv[..., 1] < 70) & (hsv[..., 2] > 120)).astype(np.uint8)
    ys, xs = np.nonzero(m)
    if len(xs) < 5:
        return np.zeros((32, 32), np.float32)
    m = m[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    hh, ww = m.shape
    side = max(hh, ww)
    pad = np.zeros((side, side), np.uint8)
    pad[(side - hh) // 2:(side - hh) // 2 + hh, (side - ww) // 2:(side - ww) // 2 + ww] = m
    return cv2.GaussianBlur(cv2.resize(pad.astype(np.float32), (32, 32), interpolation=cv2.INTER_AREA), (3, 3), 0)


def classify_icon(crop: Any, lines: list[TextLine], img: Any) -> Fld:
    """EE stat icon vs the stat-label icons of the left panel of the same capture (templates found by their OCR
    label, icon left of the label at -1.45..-0.3 line heights). Cosine similarity, best vs second (margin rule)."""
    temps = {}
    for l in lines:
        key = LABELS.get(l.text.strip())
        if key and key not in temps:
            lh = l.box.height
            t = _region(img, l.box.x0 - 1.45 * lh, l.box.y0, l.box.x0 - 0.3 * lh, l.box.y1)
            if t.size:
                temps[key] = _icon_mask(t)
    if len(temps) < 2 or crop.size == 0:
        return Fld(None, 0.0, "icon", "no templates")
    q = _icon_mask(crop)
    sims = sorted(((float((q * t).sum() / (np.sqrt((q * q).sum() * (t * t).sum()) + 1e-6)), k)
                   for k, t in temps.items()), reverse=True)
    (s1, k1), (s2, k2) = sims[0], sims[1]
    ok = s1 >= 0.7 and s1 - s2 >= 0.08
    return Fld(k1 if ok else None, s1, "icon", f"2nd {k2} {s2:.2f} margin {s1 - s2:.2f}" + ("" if ok else " REVIEW"))


def parse_ee(panel: Panel, img: Any, lines: list[TextLine]) -> None:
    a, h = panel.anchor, panel.h
    assert a is not None
    art_x0 = panel.artifact["box"].x0 if panel.artifact.get("present") and panel.artifact["present"].value else None
    right_limit = (art_x0 - 3.0 * h) if art_x0 else a.box.x1
    vals = [l for l in lines if EE_VALUE_RE.match(l.text.replace(" ", "")) and l.box.y1 < a.box.y0
            and l.box.y0 > a.box.y0 - 6 * h and a.box.x0 - 1.5 * h < l.box.x0 and l.box.x1 < right_limit]
    for v in vals:
        names = [l for l in lines if abs(l.box.x0 - v.box.x0) < 0.6 * h and 0.3 * h < l.box.cy - v.box.cy < 1.4 * h
                 and re.search(r"[A-Za-z]{3}", l.text)]
        if names:
            # the stat icon is INSIDE the OCR line box (the detector boxes icon + text, the recogniser drops the icon)
            icon = _region(img, v.box.x0 - 0.1 * h, v.box.y0, v.box.x0 + 0.65 * h, v.box.y1)
            panel.ee = {"value": Fld(v.text.replace(" ", ""), v.score, "ocr"),
                        "name": Fld(names[0].text, names[0].score, "ocr"),
                        "dx_from_artifact_h": (v.box.x0 - art_x0) / h if art_x0 else None,
                        "dy_from_artifact_h": (v.box.cy - panel.artifact["box"].cy) / h if art_x0 else None,
                        "icon_crop": icon, "icon_stat": classify_icon(icon, lines, img)}
            return
    panel.ee = None


# --------------------------------------------------------------------------------------------- driver
def parse(name: str, scale: float, img: Any, lines: list[TextLine], arts: dict[str, list[str]],
          second: bool = True, verify: bool = False) -> Panel:
    panel = Panel(name, scale)
    a, avg = find_anchor(lines)
    if a is None:
        panel.warnings.append("no 'Average Equipment Score' line: no gear panel (no gear equipped?)")
        return panel
    # Unit: the anchor line height is only a first guess: at small scales the OCR detector pads boxes (0.64x:
    # +12..23 % height). The row pitch of the value columns (distance between centres) is padding-free -> h.
    h0 = a.box.height
    panel.anchor, panel.avg = a, avg
    panel.cols = value_columns(lines, a, h0, panel.warnings)
    panel.h0 = h0
    panel.row_pitch = row_pitch(lines, panel.cols, a, h0)
    h = panel.h = panel.row_pitch / ROW_PITCH if panel.row_pitch else h0
    if not panel.row_pitch:
        panel.warnings.append("no row pitch: unit from the anchor height (padding-sensitive)")
    for col, x1 in panel.cols.items():
        panel.pieces += split_pieces(lines, x1, a, h, col)
    if panel.pieces:
        assign_slots(panel, h)
        icon_fields(panel, img, lines, h, second, verify)
    # consistency (observed 5/5, status assumed): avg = floor(sum of 6 scores / 6)
    sc = [p.score.value for p in panel.pieces if p.score and p.score.value is not None]
    if avg and len(sc) == 6 and sum(sc) // 6 != avg.value:
        panel.warnings.append(f"avg check failed: floor({sum(sc)}/6)={sum(sc) // 6} != {avg.value}")
    parse_artifact(panel, img, lines, arts, second)
    parse_ee(panel, img, lines)
    return panel


def scaled(name: str, scale: float) -> Any:
    img = load_image(SHOTS / f"{name}.webp")
    if scale != 1.0:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
    return img


# --------------------------------------------------------------------------------------------- evaluation
FIELDS = ["slot", "main", "subs", "level", "enhance", "score", "grade"]
PILLS: dict[str, list[float]] = {}


def evaluate(panel: Panel, truth: dict[str, Any], tally: dict[str, list[int]], fails: list[str],
             first_pass: Panel | None) -> None:
    tag = f"{panel.name} x{panel.scale}"

    def count(key: str, ok: bool | None, msg: str = "") -> None:
        t = tally.setdefault(key, [0, 0, 0])  # correct, wrong, abstained
        t[0 if ok else (2 if ok is None else 1)] += 1
        if not ok:
            fails.append(f"{tag} {key}: {'ABSTAIN' if ok is None else 'WRONG'} {msg}")

    if truth.get("pieces") is None:  # charles: no gear
        count("panel_absent", panel.anchor is None and not panel.pieces, f"anchor={panel.anchor}")
        return
    count("anchor+avg", bool(panel.avg and panel.avg.value == truth["avg_score"]),
          f"{panel.avg} vs {truth['avg_score']}")
    by_slot = {p.slot.value: p for p in panel.pieces if p.slot and p.slot.value}
    fp_slot = {p.slot.value: p for p in first_pass.pieces if p.slot and p.slot.value} if first_pass else {}
    grades = GRADE_EYE[panel.name]
    for i, tp in enumerate(truth["pieces"]):
        s = tp["slot"]
        p = by_slot.get(s)
        count("slot", p is not None, f"{s} not found")
        if p is None:
            for k in FIELDS[1:]:
                count(k, None, f"{s} piece missing")
            continue
        count("main", p.main == tp["main"][0], f"{s} {p.main!r} vs {tp['main'][0]!r}")
        want = [v for v, _ in tp["subs"]]
        count("subs", p.subs == want, f"{s} {p.subs} vs {want}")
        for k in ("level", "enhance", "score"):
            want_v = tp["item_level" if k == "level" else k]
            f: Fld | None = getattr(p, k)
            got = f.value if f else None
            ok = None if got is None else (got == want_v)
            if f is not None and f.conf < 0.5 and ok is not None:
                ok = None if not ok else ok  # low-confidence wrong values count as abstained (flagged)
            count(k, ok, f"{s} {f} vs {want_v}")
            if first_pass:
                fpp = fp_slot.get(s)
                ff = getattr(fpp, k) if fpp else None
                count(k + "_1st", None if ff is None or ff.how == "absent" and k != "enhance" else ff.value == want_v,
                      f"{s} {ff} vs {want_v}")
        g = p.grade.value if p.grade else None
        if grades[i] == "X":
            count("grade_odd_review", g is None, f"{s} {p.grade} (odd frame, REVIEW expected) hue={p.hue}")
        else:
            want_g = {"R": "red", "P": "purple"}[grades[i]]
            count("grade", None if g is None else g == want_g, f"{s} {p.grade} vs {want_g} hue={p.hue}")
        PILLS.setdefault("enh>0" if tp["enhance"] else "enh=0", []).append(round(p.pill, 3))
    art = truth["artifact"]
    pa = panel.artifact
    count("art_code", pa.get("code") is not None and pa["code"].value == art["code"],
          f"{pa.get('name')} -> {pa.get('code')} vs {art['code']}")
    ae = pa.get("enhance")
    ok_ae = None if ae is None or ae.value is None else ae.value == art["enhance"]
    count("art_enhance", None if ok_ae is False and ae.conf < 0.5 else ok_ae, f"{ae} vs {art['enhance']}")
    if "pill" in pa:
        PILLS.setdefault("art enh>0" if art["enhance"] else "art enh=0", []).append(round(pa["pill"], 3))
    want_lv = LV_TEXT_FIX.get(panel.name, art["lv_text"])
    lt = pa.get("lv_text")
    ok_lt = lt is not None and lt.value.replace(" ", "") == want_lv
    count("art_lv_text", ok_lt if ok_lt or lt is None or lt.conf >= 0.5 else None, f"{lt} vs {want_lv}")
    count("art_lv_check", None if pa.get("lv_check") is None else pa["lv_check"],
          f"lv={pa.get('lv')} enh={pa.get('enhance')}")
    ee = truth["ee"]
    if ee is None:
        count("ee_absent", panel.ee is None, f"found {panel.ee and panel.ee['value']}")
    else:
        count("ee_value", bool(panel.ee and panel.ee["value"].value == ee["value"]),
              f"{panel.ee and panel.ee['value']}")
        count("ee_name", bool(panel.ee and panel.ee["name"].value == ee["name_text"]),
              f"{panel.ee and panel.ee['name']}")


# --------------------------------------------------------------------------------------------- contact sheet
def sheet(panel: Panel, img: Any, path: Path) -> None:
    vis = img.copy()
    h = panel.h
    if panel.anchor is None:
        cv2.imwrite(str(path), cv2.resize(vis, None, fx=0.5, fy=0.5))
        return
    a = panel.anchor.box
    th = max(1, round(h / 20))
    cv2.rectangle(vis, (int(a.x0), int(a.y0)), (int(a.x1), int(a.y1)), (0, 255, 255), th)
    for x1 in panel.cols.values():
        cv2.line(vis, (int(x1), int(a.y1)), (int(x1), vis.shape[0] - 1), (255, 255, 0), th)
        xa, xb = x1 - ICON_X[0] * h, x1 - ICON_X[1] * h
        cv2.line(vis, (int(xa), int(a.y1)), (int(xa), vis.shape[0] - 1), (128, 128, 128), th)
        cv2.line(vis, (int(xb), int(a.y1)), (int(xb), vis.shape[0] - 1), (128, 128, 128), th)
    fs = h / 60
    for p in panel.pieces:
        y0 = p.rows[0].box.y0
        y1 = p.rows[-1].box.y1
        x1 = panel.cols[p.col]
        colour = (0, 255, 0) if p.main_ok and not p.warnings else (0, 0, 255)
        cv2.rectangle(vis, (int(x1 - 2.8 * h), int(y0)), (int(x1 + 0.1 * h), int(y1)), colour, th)
        cx, ry = p.icon_cx, p.ref_y
        cv2.rectangle(vis, (int(cx - 1.2 * h), int(ry - 0.1 * h)), (int(cx + 1.2 * h), int(ry + 2.2 * h)),
                      (255, 0, 255) if p.grade and p.grade.value == "purple" else (0, 128, 255), th)
        label = (f"{p.slot.value if p.slot else '?'} L{p.level.value if p.level else '?'} "
                 f"+{p.enhance.value if p.enhance else '?'} S{p.score.value if p.score else '?'} "
                 f"{(p.grade.value or '?')[:3] if p.grade else '?'} {len(p.subs)}s")
        org = (int(cx - 1.4 * h), int(ry + 4.1 * h))
        cv2.putText(vis, label, org, cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 0, 0), th * 4)
        cv2.putText(vis, label, org, cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 255, 255), th)
    if panel.artifact.get("box") is not None:
        b = panel.artifact["box"]
        txt = f"{panel.artifact['code'].value} +{panel.artifact['enhance'].value}"
        cv2.rectangle(vis, (int(b.x0), int(b.y0)), (int(b.x1), int(b.y1)), (0, 255, 0), th)
        cv2.putText(vis, txt, (int(b.x0), int(b.y0 - 1.4 * h)), cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 255, 255), th)
    if panel.ee:
        cv2.putText(vis, f"EE {panel.ee['value'].value} {panel.ee['name'].value}", (int(a.x0), int(a.y0 - 0.2 * h)),
                    cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 255, 255), th)
    x0 = int(max(0, a.x0 - 2.5 * h))
    crop = vis[:, x0:]
    f = 900 / crop.shape[0]
    cv2.imwrite(str(path), cv2.resize(crop, None, fx=f, fy=f, interpolation=cv2.INTER_AREA))


# --------------------------------------------------------------------------------------------- stress (line deletion)
def _piece_lines(panel: Panel, p: Piece, lines: list[TextLine]) -> set[int]:
    h = panel.h
    ids = {id(r) for r in p.rows}
    for l in lines:
        if (p.icon_cx - 1.6 * h <= (l.box.x0 + l.box.x1) / 2 <= p.icon_cx + 1.8 * h) and p.ref_y - 1.0 * h <= l.box.cy <= p.ref_y + 3.6 * h:
            ids.add(id(l))
    return ids


def stress(arts: dict[str, list[str]], scales: list[float], truth: dict[str, Any]) -> None:
    """Simulated OCR misses / empty slots by deleting OCR lines (the image is unchanged, so second=False)."""
    scenarios = {
        "no weapon": (["weapon"], None),
        "no weapon+necklace (row 0 empty)": (["weapon", "necklace"], None),
        "left column empty": (["weapon", "helmet", "armor"], None),
        "right column empty": (["necklace", "ring", "boots"], None),
        "only boots": (["weapon", "helmet", "armor", "necklace", "ring"], None),
        "helmet main missed": ([], ("helmet", 0)),
        "armor 2nd sub missed": ([], ("armor", 2)),
        "ring last sub missed": ([], ("ring", 4)),
    }
    tally: dict[str, list[int]] = {}
    for name in CAPTURES[:5]:
        tp = {t["slot"]: t for t in truth[name]["pieces"]}
        for scale in scales:
            img = scaled(name, scale)
            lines = ocr(name, scale, img)
            base = parse(name, scale, img, lines, arts, second=False)
            by_slot = {p.slot.value: p for p in base.pieces if p.slot}
            for sc, (drop_slots, drop_row) in scenarios.items():
                drop: set[int] = set()
                for sl in drop_slots:
                    drop |= _piece_lines(base, by_slot[sl], lines)
                if drop_row:
                    drop.add(id(by_slot[drop_row[0]].rows[drop_row[1]]))
                kept = [l for l in lines if id(l) not in drop]
                got = parse(name, scale, img, kept, arts, second=bool(drop_row))
                res = {p.slot.value: p for p in got.pieces if p.slot and p.slot.value}
                want_slots = [s for s in tp if s not in drop_slots]
                ok = sorted(res) == sorted(want_slots)
                msg = ""
                if ok and drop_row:
                    sl, i = drop_row
                    pc = res[sl]
                    flagged = any("REVIEW" in w or "missed" in w or "recovered" in w for w in pc.warnings)
                    want_main = tp[sl]["main"][0] if pc.main is not None else None  # recovered or REVIEW
                    full = [v for v, _ in tp[sl]["subs"]]
                    want_subs = [v for j, v in enumerate(full) if j != i - 1]
                    # a missed sub is either recovered (full list, flagged) or flagged; never silently dropped
                    ok = flagged and pc.main == want_main and pc.subs in (want_subs, full)
                    msg += " RECOVERED" if pc.subs == full and i else ""
                    msg = f"main={pc.main} subs={pc.subs} warn={pc.warnings}"
                    print(f"  {name} x{scale} {sc}: {msg}")
                elif ok:
                    ok = all(res[s].main == tp[s]["main"][0] for s in want_slots)
                t = tally.setdefault(sc, [0, 0])
                t[0 if ok else 1] += 1
                if not ok:
                    print(f"  FAIL {name} x{scale} {sc}: slots={sorted(res)} {msg} warn={got.warnings}")
    print("==== stress (ok / fail) over 5 captures x scales")
    for sc, (a_, b) in tally.items():
        print(f"  {sc:34s} {a_:2d}/{b:2d}")


# --------------------------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scales", nargs="+", type=float, default=[0.64, 1.0, 1.28])
    ap.add_argument("--only", nargs="*")
    ap.add_argument("-v", action="store_true")
    ap.add_argument("--verify", action="store_true", help="also run the level second pass on good first reads")
    ap.add_argument("--stress", action="store_true", help="simulated misses / empty slots by deleting OCR lines")
    args = ap.parse_args()
    truth_all = json.loads(TRUTH.read_text())
    truth = {k: v for k, v in truth_all.items() if k.startswith("heroinfo_") and "pieces" in v}
    truth["heroinfo_charles"] = {"pieces": None}
    arts = load_artifacts()
    if args.stress:
        stress(arts, args.scales, truth)
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    tally_by_scale: dict[float, dict[str, list[int]]] = {}
    fails: list[str] = []
    for name in CAPTURES:
        if args.only and name not in args.only:
            continue
        for scale in args.scales:
            t0 = time.time()
            img = scaled(name, scale)
            lines = ocr(name, scale, img)
            first = parse(name, scale, img, lines, arts, second=False)
            panel = parse(name, scale, img, lines, arts, second=True, verify=args.verify)
            tally = tally_by_scale.setdefault(scale, {})
            evaluate(panel, truth[name], tally, fails, first)
            sheet(panel, img, OUT / f"sheet_{name}_{scale:.2f}.png")
            print(f"== {name} x{scale} h={panel.h:.1f} (h/s={panel.h / scale:.1f}, anchor h0/s={panel.h0 / scale:.1f}) pitch={panel.pitch / panel.h if panel.h else 0:.2f}h "
                  f"cols={ {k: round(v) for k, v in panel.cols.items()} } avg={panel.avg} ({time.time() - t0:.1f}s)")
            for w in panel.warnings:
                print("   WARN", w)
            for p in panel.pieces:
                print(f"   {p.col} {str(p.slot):28s} main={str(p.main):6s} subs={p.subs} lvl={p.level} "
                      f"enh={p.enhance} score={p.score} grade={p.grade} pf={p.hue.get('pfrac', 0):.2f} "
                      f"pill={p.pill:.2f} probe={p.probe} {'; '.join(p.warnings)}")
            if panel.artifact:
                pa = panel.artifact
                print("   ART", {k: str(v) for k, v in pa.items() if k != "box"})
            if panel.ee:
                print("   EE ", {k: (str(v) if k != "icon_crop" else v.shape) for k, v in panel.ee.items()})
                cv2.imwrite(str(OUT / f"ee_icon_{name}_{scale:.2f}.png"),
                            cv2.resize(panel.ee["icon_crop"], None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC))
            if args.v or panel.second_pass:
                for s in panel.second_pass:
                    print("   2ND", s)
    print("\n==== accuracy (correct / wrong / abstained) per scale")
    keys = sorted({k for t in tally_by_scale.values() for k in t})
    for k in keys:
        row = "  ".join(f"x{s}: {t.get(k, [0, 0, 0])[0]:2d}/{t.get(k, [0, 0, 0])[1]:2d}/{t.get(k, [0, 0, 0])[2]:2d}"
                        for s, t in tally_by_scale.items())
        print(f"  {k:14s} {row}")
    print("\n==== pill fractions by truth")
    for k, v in sorted(PILLS.items()):
        print(f"  {k:10s} n={len(v):2d} min={min(v):.3f} max={max(v):.3f}")
    print("\n==== failures")
    for f in fails:
        print("  ", f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
