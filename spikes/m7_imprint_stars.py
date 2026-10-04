"""Spike M7 / imprint_stars: the IMPRINT ICON (mode, lit squares, grade letters) and the STAR ROW (stars, awakened).

Throwaway experiment (CLAUDE.md: spikes are never imported by src/). Reads the user's own captures (git-ignored
fixtures + zoomed crops in the scratch dir) and writes OCR caches / contact sheets under the scratch dir only.
No pixel position of one resolution is used: every window is a multiple of an OCR line height, of the icon radius
(Rx, Ry) measured from the icon itself, or of the star pitch measured from the star row.

Imprint icon
1. Anchors (OCR): the hero level line ("Lv. Max/60", the TALLEST line starting with "Lv"; the artifact's "Lv.Max/6" is
   smaller), the imprint text block (lines right of the level line's left edge, below it, same left edge) and the CP
   line ("124,427"). Search window: x in [lv.x0 - 0.6h, text.x0], y in [lv.y1, cp.y0]   (h = level line height).
   Zoomed crops without a level line: x in [0, text.x0], y = text block centre +- 2.6 line heights.
2. Colour: bright saturated pixels (S >= 100, V >= 170) -> dominant hue (circular histogram). Family red / blue /
   other(hue). Too few bright saturated pixels + grey pixels -> the grey "Locked" icon.
3. Icon mask M = same hue (+-6) & S >= 0.75 * median S & V >= 0.24 * V_lit (keeps the DIM squares/ring, which are the
   same hue at ~30 % brightness). Components far from the largest one are dropped.
4. Geometry from the parts the grade letters never cover (they sit bottom-right): top = first row of M, left = first
   column; cx = mean x of the top rows, cy = mean y of the left columns (the icon is mirror-symmetric, so the topmost
   part is centred on cx and the leftmost on cy). Rx = cx - left, Ry = cy - top.
5. Mode, two independent features (both must agree, else 'unknown'):
   a. aspect Rx/Ry: team (four squares, wider than tall) ~1.22, self (crosshair) ~1.00 -> split at 1.11;
   b. centre disk (r = 0.1 Ry): bright icon pixels -> self (the cross centre); background -> team (gap between squares).
6. Team lit squares: one zone per square in (Rx, Ry) units around the square centres (0,-.55) (-.64,0) (.64,0) (0,.55),
   right square only its upper half, bottom square only its left part (the letters cover the rest).
   Zone level = median V of M pixels / V_lit: >= 0.7 lit, <= 0.5 dark, else unknown; coverage < 0.2 -> occluded.
   5b. Structure check (added after the negative controls): mask coverage of small zones in (Rx, Ry) units -
   team: square bodies present, the 3 free box corners and the centre empty; self: ring on the 3 free diagonals,
   centre filled, box corners mostly empty. Failure -> 'unknown' (never a guess).
   Locked: no saturated icon + a padlock blob (near-white, h/w ~1.2, ~1 text line high) + >= 1 grey square
   (desaturated, brighter than the background, with a hole) up-left of it. One sample only -> production must
   decide 'Locked' from the OCR text and use the icon as a consistency check.
7. Grade letters: band BELOW the icon footprint (y in [cy + 1.10 Ry, cy + 1.75 Ry]) holds only letters.
   a. column profile of bright letter pixels in the band: dark outlines between letters make valleys -> count n;
   b. the block width / Ry must be compatible with n ([0.42 n, 0.65 n + 0.35]); else the count is unresolved.
      (measured: B 0.70-0.80, SSS 1.48-1.73; 2 letters NEVER SEEN - any '2' is flagged unverified)
   Grade = (colour family, letter count) only for the pairs seen on the user's captures: (blue, 1) -> B,
   (red, 3) -> SSS. Anything else -> 'unknown' with the measurements (never an invented grade colour).
   c. (experiment) RapidOCR on the letter area, raw and binarised, to compare with a/b.
   d. (experiment) blob counts: components of the letter fill in the band (works) vs over the whole letter area
      (fails: letters fragment, the first letter merges with the bottom square).
   e. Letter colour measured on its own (band pixels, no icon-hue filter); it must agree with the icon colour.

Stars
1. Anchor: the hero name line = tallest mostly-alphabetic line above the level line (as hero_screen._read_name).
   Band: y in [name.y0, name.y1], x in [name.x0, name.x1 + 6h] (the OCR box sometimes includes the stars).
2. Yellow mask (H 10-35, S > 100, V > 150): every star (plain or awakened) has a yellow/orange top; the crimson
   background and the blue/grey art never fall in that hue range on the captures. Components grouped by equal top
   (heights may differ: the yellow part of an awakened star is only its upper ~60 %); the largest group = the row.
3. Tips: columns that have yellow in the top 30 % of the row -> one run per star tip (arms meet lower down).
   Pitch = median tip distance; the chain must be regular (each gap within 0.8-1.25 pitch).
   Narrow specks (< 0.4 x median run width) are dropped; the chain is split at gaps > 1.6 pitch and the longest
   chain kept (warning); any remaining gap outside 0.8-1.25 pitch -> count unresolved (None), never a guess.
4. Awakened per star: centre (tip x, top + 0.72 pitch). Centre darkness = median V of a 0.1-pitch disk / median V of
   the star's own yellow top. <= 0.70 + magenta/pink lower half -> awakened; >= 0.85 + yellow hue + no pink -> plain;
   else unknown (relative, so a brighter/darker rendering moves both).
5. Shape check (added after the negative controls: the orange 'Lv. Max/60' text and the gold set icons next to the
   CP line gave 1-4 'stars'): in pitch units, tip width <= 0.25, arm width >= 0.45, median mirror symmetry >= 0.45,
   pitch/h in 0.25-0.6, star top 0.15-0.45 h below the name line top. Failure -> count None.
6. Cross-check MECH-HERO-01 (community): level cap '/60' -> 6 stars; a mismatch is a warning, never a fix.

Negative controls: same-size windows over the imprint text / above / the hero art, and the star reader next to the
level and CP lines (--sweep: icon windows tiled over the whole capture + the star reader next to every OCR line).

Usage:
    uv run python spikes/m7_imprint_stars.py                    # all samples x scales 0.64 1.0 1.28 + contact sheets
    uv run python spikes/m7_imprint_stars.py --only heroinfo_charles --scales 1.0 -v
    uv run python spikes/m7_imprint_stars.py --letters-ocr      # also the OCR experiment on the grade letters
    uv run python spikes/m7_imprint_stars.py --perturb none jpeg50 blur noise8 dark bright
    uv run python spikes/m7_imprint_stars.py --sweep            # held-out negatives over the whole captures
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from e7ac.vision.image import load_image
from e7ac.vision.ocr import Box, RapidOcrReader, TextLine, Word

REPO = Path(__file__).resolve().parents[1]
SHOTS = REPO / "fixtures" / "screenshots"
SCRATCH = Path("/tmp/claude-0/-home-user-Orbis-Codex/d44236ee-bada-567e-a707-c7890345df7b")
M7 = SCRATCH / "scratchpad" / "m7"
OUT = M7 / "imprint_stars"
CROPS = SCRATCH / "images"
TRUTH = M7 / "truth.json"
SCALES = (0.64, 1.0, 1.28)

FULL = [
    "heroinfo_haru",
    "heroinfo_lots",
    "heroinfo_ainz",
    "heroinfo_straze",
    "heroinfo_politis",
    "heroinfo_charles",
    "equip_renoa",
    "equip_haru",
    "equip_straze",
]
CROP_NAMES = ["crop5", "crop6", "crop7", "crop8"]

# --- thresholds (all relative: hue units are OpenCV 0-179, V/S are 0-255 but always compared to measured levels) ---
BRIGHT_S, BRIGHT_V = 100, 170  # "clearly coloured" pixels used to find the icon hue
HUE_TOL = 6
MASK_S_FRAC = 0.85  # dim squares keep the icon's saturation; the red/magenta nebula behind red icons is less saturated
MASK_V_LADDER = (0.24, 0.16, 0.11)  # dim squares are ~0.30 V_lit; the nebula behind the icon stays below ~0.22 V_lit.
# Lower steps are tried only when the icon found is implausible (a darker rendering loses the dim squares).
# (a background-relative threshold (1.4 x 60th percentile) was tried: it let the red nebula into red icons on real
#  captures, so the fixed fraction stays; a much darker rendering then loses the dim squares -> sanity check abstains)
ASPECT_TEAM, ASPECT_SELF = (1.14, 1.42), (0.88, 1.12)  # plausible Rx/Ry per mode; outside both -> broken geometry
SIZE_RANGE = (0.7, 1.4)  # Ry / imprint text line height (measured 0.89-1.10 on all samples)
SYMMETRY_TOL = 0.15  # right extent of the upper half must mirror the left extent (letters never reach that high)
ASPECT_SPLIT, ASPECT_MARGIN = 1.11, 0.04
CENTRE_SPLIT = 0.5
LIT_MIN, DARK_MAX, ZONE_MIN_COVER = 0.70, 0.50, 0.20
SQUARE_CENTRES = {"top": (0.0, -0.55), "left": (-0.64, 0.0), "right": (0.64, 0.0), "bottom": (0.0, 0.55)}
SQUARE_HALF = (0.41, 0.39)  # half width (Rx units), half height (Ry units) of one square
STRUCT_ZONES = {  # name: (u, v, half size) in (Rx, Ry) units around the icon centre
    "sq_top": (0.0, -0.55, 0.15),
    "sq_left": (-0.64, 0.0, 0.15),
    "sq_right": (0.64, -0.1, 0.12),
    "c_tl": (-0.62, -0.62, 0.12),
    "c_tr": (0.62, -0.62, 0.12),
    "c_bl": (-0.62, 0.62, 0.12),
    "d_tl": (-0.5, -0.5, 0.1),
    "d_tr": (0.5, -0.5, 0.1),
    "d_bl": (-0.5, 0.5, 0.1),
    "ctr": (0.0, 0.0, 0.1),
}
TEAM_SQUARE_MIN = 0.2  # measured: squares 0.30-0.83 (dim ones 0.30-0.46)
TEAM_CORNER_MAX, TEAM_CENTRE_MAX = 0.15, 0.35  # measured 0.00 / 0.00 clean, centre up to 0.23 after JPEG q50 at 0.64
SELF_RING_MIN = 0.6  # measured: diagonals 0.88-1.00, centre 1.00
SELF_CORNER_MAX = 0.55  # measured: box corners 0.11-0.42 (a filled orange disk in the art: 0.69-0.86)
LETTER_BAND = (1.10, 1.75)  # rows below the icon footprint (which ends at cy + 1.0 Ry; + margin for blur), Ry units
LETTER_COLS = (-0.7, 2.4)  # Rx units from cx
LETTER_WIDTH_BINS = ((0.0, 1.0, 1), (1.0, 1.4, 2), (1.4, 2.2, 3))  # width / Ry -> letters ('2' never seen)
LETTER_W_PER = (0.42, 0.65, 0.35)  # n letters are compatible with a block width in [0.42 n, 0.65 n + 0.35] Ry
LOCK_HW, LOCK_H, LOCK_FILL = (1.05, 1.45), (0.7, 1.25), (0.45, 0.75)  # padlock: h/w 1.20-1.22, h/text 0.93-0.98
RING_WH, RING_H, RING_HOLE = (1.1, 1.6), (0.6, 1.2), (0.02, 0.2)  # grey squares: w/h 1.22-1.38, h/text 0.82-0.95
LOCK_RINGS_MIN = 1
KNOWN_GRADES = {("blue", 1): "B", ("red", 3): "SSS"}  # only what the user's captures show (truth.json)
YELLOW = ((10, 35), 100, 0.6)  # hue range, S min, V min as a fraction of the band's 99th-percentile yellow V
STAR_TIP_MAX, STAR_ARM_MIN, STAR_SYM_MIN = (
    0.25,
    0.45,
    0.45,
)  # clean stars: tip 0.05-0.18 (JPEG q50 @0.64: up to 0.32), arm 0.58-1.11 (dark: 0.53), sym med >= 0.57
STAR_PITCH_H = (0.25, 0.6)  # pitch / name line height, measured 0.33-0.48
STAR_TOP_H = (0.15, 0.45)  # (star top - name line top) / name line height, measured 0.24-0.33
STAR_DARK, STAR_BRIGHT = 0.70, 0.85  # centre V / the star's own yellow V: awakened <= 0.70, plain >= 0.85


# ---------------------------------------------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------------------------------------------
def source_path(name: str) -> Path:
    if name.startswith("crop"):
        return CROPS / f"{name[4:]}.png"
    return SHOTS / f"{name}.webp"


def scaled(img: np.ndarray, scale: float) -> np.ndarray:
    if scale == 1.0:
        return img
    return cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)


def _gamma(img: np.ndarray, gamma: float, gain: float) -> np.ndarray:
    f = (img.astype(np.float32) / 255.0) ** gamma * gain
    return np.clip(f * 255.0, 0, 255).astype(np.uint8)


def _jpeg(img: np.ndarray, quality: int) -> np.ndarray:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    assert ok
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def _noise(img: np.ndarray, sigma: float) -> np.ndarray:
    rng = np.random.default_rng(7)
    return np.clip(img.astype(np.float32) + rng.normal(0, sigma, img.shape), 0, 255).astype(np.uint8)


PERTURB = {
    "none": lambda im: im,
    "jpeg50": lambda im: _jpeg(im, 50),
    "blur": lambda im: cv2.GaussianBlur(im, (0, 0), 1.0),
    "noise8": lambda im: _noise(im, 8.0),
    "dark": lambda im: _gamma(im, 1.3, 0.75),
    "bright": lambda im: _gamma(im, 0.75, 1.1),
}
"""Pixel degradations applied AFTER scaling; the OCR anchors stay those of the clean image at that scale (this tests
the vision part only - OCR line boxes are not what is fragile here)."""

_READER: RapidOcrReader | None = None


def _reader() -> RapidOcrReader:
    global _READER
    if _READER is None:
        _READER = RapidOcrReader()
    return _READER


def ocr_lines(name: str, scale: float, img: np.ndarray) -> list[TextLine]:
    cache = OUT / "ocr" / f"{name}_{scale:.2f}.json"
    if not cache.exists():
        lines = _reader().read(img)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            json.dumps(
                [
                    {
                        "text": ln.text,
                        "score": ln.score,
                        "box": [ln.box.x0, ln.box.y0, ln.box.x1, ln.box.y1],
                        "words": [[w.text, w.score, w.box.x0, w.box.y0, w.box.x1, w.box.y1] for w in ln.words],
                    }
                    for ln in lines
                ]
            ),
            encoding="utf-8",
        )
    data = json.loads(cache.read_text(encoding="utf-8"))
    return [
        TextLine(d["text"], d["score"], Box(*d["box"]), tuple(Word(w[0], w[1], Box(*w[2:])) for w in d["words"]))
        for d in data
    ]


def hsv_int(img: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.int32)
    return hsv[..., 0], hsv[..., 1], hsv[..., 2]


def hue_diff(h: np.ndarray | int, ref: int) -> np.ndarray:
    d = np.abs(np.asarray(h) - ref)
    return np.minimum(d, 180 - d)


# ---------------------------------------------------------------------------------------------------------------
# OCR anchors
# ---------------------------------------------------------------------------------------------------------------
LEVEL_RE = re.compile(r"^\s*lv\s*\.?\s*(max|\d{1,2})\s*/\s*(\d{2})\s*$", re.I)
CP_RE = re.compile(r"^\d{1,3}(?:,\d{3})+$|^\d{4,7}$")


@dataclass
class Anchors:
    level: TextLine | None
    name: TextLine | None
    block: list[TextLine]
    cp: TextLine | None
    notes: list[str] = field(default_factory=list)


def find_anchors(lines: list[TextLine]) -> Anchors:
    levels = [ln for ln in lines if LEVEL_RE.match(ln.text)]
    level = max(levels, key=lambda ln: ln.box.height) if levels else None
    notes: list[str] = []
    if level is None:
        # zoomed crop: the imprint text is the leftmost text block
        block = _text_block(sorted(lines, key=lambda ln: (ln.box.y0, ln.box.x0)), None)
        return Anchors(None, None, block, None, ["no level line (crop?)"])
    h = level.box.height
    cands = [
        ln
        for ln in lines
        if ln.box.y0 >= level.box.y1 - 0.2 * h
        and ln.box.y1 <= level.box.y1 + 4.5 * h
        and level.box.x0 + 1.0 * h < ln.box.x0 < level.box.x0 + 5.0 * h
        and not CP_RE.match(ln.text.strip())
    ]
    block = _text_block(sorted(cands, key=lambda ln: ln.box.y0), h)
    below = block[-1].box.y1 if block else level.box.y1
    cps = [
        ln
        for ln in lines
        if CP_RE.match(ln.text.strip()) and ln.box.y0 >= below - 0.2 * h and abs(ln.box.x0 - level.box.x0) < 1.5 * h
    ]
    cp = min(cps, key=lambda ln: ln.box.y0) if cps else None
    if cp is None:
        notes.append("no CP line")
    names = [
        ln
        for ln in lines
        if ln.box.y1 <= level.box.y0 + 0.2 * h
        and abs(ln.box.x0 - level.box.x0) < 2.5 * h
        and sum(c.isalpha() for c in ln.text) >= 3
        and len(set(ln.text.strip())) > 2
    ]
    name = max(names, key=lambda ln: ln.box.height) if names else None
    return Anchors(level, name, block, cp, notes)


def _text_block(lines: list[TextLine], unit: float | None) -> list[TextLine]:
    """First text line + the lines wrapped under it (same left edge, directly below)."""
    if not lines:
        return []
    first = lines[0]
    block = [first]
    for ln in lines[1:]:
        last = block[-1]
        lh = unit or last.box.height
        if abs(ln.box.x0 - first.box.x0) < 0.6 * last.box.height and ln.box.y0 < last.box.y1 + 0.6 * lh:
            block.append(ln)
    return block


def imprint_window(img: np.ndarray, a: Anchors) -> tuple[int, int, int, int] | None:
    if not a.block:
        return None
    H, W = img.shape[:2]
    bx0 = min(ln.box.x0 for ln in a.block)
    by0 = min(ln.box.y0 for ln in a.block)
    by1 = max(ln.box.y1 for ln in a.block)
    if a.level is not None:
        h = a.level.box.height
        x0, x1 = a.level.box.x0 - 0.6 * h, bx0 - 0.05 * h
        y0 = a.level.box.y1
        y1 = a.cp.box.y0 if a.cp is not None else by1 + 1.5 * h
    else:
        th = float(np.median([ln.box.height for ln in a.block]))
        cy = (by0 + by1) / 2
        x0, x1, y0, y1 = 0, bx0 - 0.05 * th, cy - 2.6 * th, cy + 2.6 * th
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(W, int(x1)), min(H, int(y1))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    return x0, y0, x1, y1


# ---------------------------------------------------------------------------------------------------------------
# Imprint icon
# ---------------------------------------------------------------------------------------------------------------
@dataclass
class IconReading:
    mode: str = "unknown"  # self / team / locked / unknown
    mode_conf: float = 0.0
    family: str = "unknown"  # red / blue / grey / other(h=..)
    hue: int = -1
    lit: dict[str, str] = field(default_factory=dict)  # square -> lit / dark / unknown / occluded
    letters_width: int | None = None
    letters_valleys: int | None = None
    letters: int | None = None
    letters_conf: float = 0.0
    letters_blobs_band: int | None = None  # experiment
    letters_blobs_naive: int | None = None  # experiment
    letter_hue: int = -1
    letter_family: str = "unknown"
    grade: str = "unknown"
    grade_conf: float = 0.0
    feats: dict[str, float] = field(default_factory=dict)
    geom: tuple[float, float, float, float] | None = None  # cx, cy, Rx, Ry in window coords
    warnings: list[str] = field(default_factory=list)


def analyse_icon(win: np.ndarray, text_h: float | None = None) -> IconReading:
    """`text_h`: height of an imprint text line (size sanity check; the icon is drawn next to that text)."""
    r = IconReading()
    H, S, V = hsv_int(win)
    bright = (S >= BRIGHT_S) & (V >= BRIGHT_V)
    area = win.shape[0] * win.shape[1]
    if bright.sum() < 0.01 * area:
        return _grey_icon(r, H, S, V, text_h)
    hist = np.bincount(H[bright].ravel(), minlength=180)
    smooth = np.array([hist[[(i + k) % 180 for k in range(-4, 5)]].sum() for i in range(180)])
    hd = int(smooth.argmax())
    r.hue = hd
    r.family = _family(hd)
    core = bright & (hue_diff(H, hd) <= HUE_TOL)
    vlit = float(np.percentile(V[core], 95))
    smed = float(np.median(S[core]))
    hue_ok = hue_diff(H, hd) <= HUE_TOL
    lit_px = (V >= 0.6 * vlit) & (S >= 0.5 * smed)  # lit parts (highlights may be less saturated)
    for k, frac in enumerate(MASK_V_LADDER):
        dim_px = (V >= frac * vlit) & (S >= MASK_S_FRAC * smed)  # dim squares/ring: as saturated as the icon, darker
        m = _main_cluster((hue_ok & (lit_px | dim_px)).astype(np.uint8))
        ok = _geometry(r, m, text_h)
        if ok or k == len(MASK_V_LADDER) - 1:
            break
        r.warnings.append(f"dim threshold {frac} V_lit gave an implausible icon; retrying lower")
    if not ok:
        return r
    cx, cy, rx, ry = r.geom  # type: ignore[misc]
    aspect = rx / ry
    retried = k > 0
    if retried:
        r.feats["v_frac"] = frac
    # centre disk
    yy, xx = np.mgrid[0 : win.shape[0], 0 : win.shape[1]]
    disk = (xx - cx) ** 2 + (yy - cy) ** 2 <= (0.1 * ry) ** 2
    centre = float((m & (V >= 0.6 * vlit))[disk].mean()) if disk.any() else 0.0
    r.feats.update(centre=round(centre, 2), vlit=vlit)
    _decide_mode(r, aspect, centre)
    if retried:
        r.mode_conf = round(0.8 * r.mode_conf, 2)
    if r.mode in ("team", "self") and not _structure_ok(r, m, cx, cy, rx, ry):
        r.mode, r.mode_conf = "unknown", 0.0
        return r
    if r.mode == "team":
        r.lit = _lit_squares(m, V, vlit, cx, cy, rx, ry)
    _letters(r, m, H, S, V, vlit, cx, cy, rx, ry)
    if r.letter_family != r.family:
        r.warnings.append(f"letter colour {r.letter_family} (h={r.letter_hue}) differs from icon colour {r.family}")
    key = (r.family, r.letters)
    if r.letters is not None and key in KNOWN_GRADES and r.letter_family == r.family:
        r.grade = KNOWN_GRADES[key]
        r.grade_conf = r.letters_conf
    else:
        r.grade = f"unknown({r.family}/{r.letter_family},{r.letters} letters)"
    return r


def _family(h: int) -> str:
    """Colour family of a hue. Only red (SSS) and blue (B) are seen; any other hue is reported, never named."""
    return "red" if hue_diff(h, 0) <= 10 else "blue" if 95 <= h <= 115 else f"other(h={h})"


def _geometry(r: IconReading, m: np.ndarray, text_h: float | None) -> bool:
    """cx, cy, Rx, Ry from the unoccluded top/left extremes + plausibility checks. False = do not trust."""
    r.geom = None
    ys, xs = np.nonzero(m)
    if len(xs) < 30:
        r.warnings.append("icon mask too small")
        return False
    top, left = ys.min(), xs.min()
    ht, wd = ys.max() - top + 1, xs.max() - left + 1
    cx = float(xs[ys <= top + max(1.0, 0.06 * ht)].mean())
    cy = float(ys[xs <= left + max(1.0, 0.06 * wd)].mean())
    rx, ry = cx - left, cy - top
    if rx <= 2 or ry <= 2:
        r.warnings.append("degenerate icon geometry")
        return False
    aspect = rx / ry
    # the upper half (never covered by the letters) is mirror-symmetric about cx
    upper = m[max(0, int(cy - 0.3 * ry)) : int(cy + 0.2 * ry) + 1]
    ux = np.nonzero(upper.any(axis=0))[0]
    sym = float((ux.max() - cx) / rx) if ux.size else 0.0
    r.feats.update(aspect=round(aspect, 3), sym=round(sym, 2), rx=round(rx, 1), ry=round(ry, 1))
    plausible = ASPECT_TEAM[0] <= aspect <= ASPECT_TEAM[1] or ASPECT_SELF[0] <= aspect <= ASPECT_SELF[1]
    if text_h:
        size = ry / text_h
        r.feats["size"] = round(size, 2)
        plausible = plausible and SIZE_RANGE[0] <= size <= SIZE_RANGE[1]
    if abs(sym - 1.0) > SYMMETRY_TOL or not plausible:
        r.warnings.append(
            f"icon geometry not plausible (Rx/Ry {aspect:.2f}, right/left extent {sym:.2f}, "
            f"size {r.feats.get('size')}): part of the icon missing or background merged into it"
        )
        return False
    r.geom = (cx, cy, rx, ry)
    return True


def _cover(m: np.ndarray, cx: float, cy: float, rx: float, ry: float, u: float, v: float, half: float) -> float:
    z = _zone(cx, cy, rx, ry, u - half, u + half, v - half, v + half, m.shape)
    return float(m[z].mean()) if z is not None else -1.0


def _structure_ok(r: IconReading, m: np.ndarray, cx: float, cy: float, rx: float, ry: float) -> bool:
    """Shape verification in (Rx, Ry) units, only on the parts the grade letters never cover (top/left/upper right).
    team: the square bodies are there, the three free corners and the centre are empty;
    self: the crosshair ring crosses the three free diagonals, the centre is filled, the box corners mostly empty.
    Added after the negative-control test (a window over the imprint TEXT passed the geometry checks)."""
    f = {k: round(_cover(m, cx, cy, rx, ry, *z), 2) for k, z in STRUCT_ZONES.items()}
    r.feats.update({f"z_{k}": v for k, v in f.items()})
    if r.mode == "team":
        squares = min(f["sq_top"], f["sq_left"], f["sq_right"])
        corners = max(f["c_tl"], f["c_tr"], f["c_bl"])
        ok = squares >= TEAM_SQUARE_MIN and corners <= TEAM_CORNER_MAX and f["ctr"] <= TEAM_CENTRE_MAX
    else:
        ring = min(f["d_tl"], f["d_tr"], f["d_bl"])
        corners = max(f["c_tl"], f["c_tr"], f["c_bl"])  # a crosshair is round: its box corners stay mostly empty
        ok = ring >= SELF_RING_MIN and f["ctr"] >= SELF_RING_MIN and corners <= SELF_CORNER_MAX
    if not ok:
        r.warnings.append(f"{r.mode} icon structure not found (zone coverage {f}): not an imprint icon?")
    return ok


def _decide_mode(r: IconReading, aspect: float, centre: float) -> None:
    by_aspect = "team" if aspect >= ASPECT_SPLIT else "self"
    by_centre = "self" if centre >= CENTRE_SPLIT else "team"
    if by_aspect == by_centre:
        r.mode = by_aspect
        r.mode_conf = round(
            min(1.0, abs(aspect - ASPECT_SPLIT) / (2 * ASPECT_MARGIN)) * min(1.0, abs(centre - CENTRE_SPLIT) / 0.3), 2
        )
    else:
        r.warnings.append(f"mode features disagree: aspect->{by_aspect}, centre->{by_centre}")
    if abs(aspect - ASPECT_SPLIT) < ASPECT_MARGIN:
        r.warnings.append(f"aspect {aspect:.2f} inside the margin")


def _main_cluster(m: np.ndarray) -> np.ndarray:
    """The icon: the largest component plus the components close to it. Components touching the window's top
    border are background (the window starts at the level line; the icon never touches it - but the letters may
    reach the right border, which is the text's left edge)."""
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    if n <= 1:
        return m.astype(bool)
    hgt, wid = m.shape
    for i in range(1, n):
        x, y, w, h = st[i, :4]
        if y == 0:
            st[i, cv2.CC_STAT_AREA] = 0
    areas = st[1:, cv2.CC_STAT_AREA]
    if areas.max() == 0:
        return np.zeros_like(m, dtype=bool)
    big = int(np.argmax(areas)) + 1
    keep = {big}
    bx, by, bw, bh = st[big, :4]
    gx0, gy0, gx1, gy1 = bx, by, bx + bw, by + bh
    reach = 0.25 * max(bw, bh)
    changed = True
    while changed:
        changed = False
        for i in range(1, n):
            if i in keep or st[i, cv2.CC_STAT_AREA] < 0.01 * areas.max() or st[i, cv2.CC_STAT_AREA] == 0:
                continue
            x, y, w, h = st[i, :4]
            if x < gx1 + reach and x + w > gx0 - reach and y < gy1 + reach and y + h > gy0 - reach:
                keep.add(i)
                gx0, gy0, gx1, gy1 = min(gx0, x), min(gy0, y), max(gx1, x + w), max(gy1, y + h)
                changed = True
    return np.isin(lab, list(keep))


def _grey_icon(r: IconReading, H: np.ndarray, S: np.ndarray, V: np.ndarray, text_h: float | None) -> IconReading:
    """'Locked' icon (one sample: crop 5.png): translucent grey squares + a white padlock at the bottom right.
    Needs (a) a padlock blob: near-white, taller than wide, about one text line high, half filled (body + shackle);
    (b) >= 1 grey square: desaturated + brighter than the background, wider than tall, with a small hole, up-left of
    the padlock. Only the left/right squares are found on the sample (top/bottom are dimmer - see report), and at
    scale 0.64 the right one merges with the padlock outline, so only 1 is required.
    Production: decide 'Locked' from the OCR text; this is a consistency check only."""
    th = text_h or float(min(V.shape))
    bs, bv = float(np.median(S)), float(np.median(V))
    top_v = float(np.percentile(V, 99.5))
    white = (S <= 50) & (V >= 0.85 * top_v) & (top_v >= 2.5 * max(bv, 1.0))
    n, _, st, _ = cv2.connectedComponentsWithStats(white.astype(np.uint8), connectivity=8)
    locks = []
    for i in range(1, n):
        x, y, w, h, a = (int(v) for v in st[i])
        if (
            LOCK_HW[0] <= h / w <= LOCK_HW[1]
            and LOCK_H[0] <= h / th <= LOCK_H[1]
            and LOCK_FILL[0] <= a / (w * h) <= LOCK_FILL[1]
        ):
            locks.append((x + w / 2, y + h / 2, h))
    grey = (S <= 0.6 * bs) & (V >= 1.6 * bv) & ~white
    cnts, hier = cv2.findContours(grey.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    rings = []
    if hier is not None:
        for i, c in enumerate(cnts):
            child = hier[0][i][2]
            if hier[0][i][3] != -1 or child == -1:
                continue
            x, y, w, h = cv2.boundingRect(c)
            hole = cv2.contourArea(cnts[child]) / (w * h)
            if (
                RING_WH[0] <= w / h <= RING_WH[1]
                and RING_H[0] <= h / th <= RING_H[1]
                and RING_HOLE[0] <= hole <= RING_HOLE[1]
            ):
                rings.append((x + w / 2, y + h / 2))
    best = 0
    for lx, ly, _ in locks:
        best = max(best, sum(1 for gx, gy in rings if gx < lx and gy < ly))
    r.feats.update(locks=len(locks), rings=len(rings), rings_upleft=best)
    if best >= LOCK_RINGS_MIN:
        r.mode, r.family, r.mode_conf = "locked", "grey", 0.5
        r.grade = "none"
        r.warnings.append("padlock + grey squares: 'Locked' icon (1 sample only; confirm with the OCR text 'Locked')")
    else:
        r.warnings.append("no coloured icon and no Locked icon found")
    return r


def _zone(
    cx: float, cy: float, rx: float, ry: float, u0: float, u1: float, v0: float, v1: float, shape: tuple[int, int]
) -> tuple[slice, slice] | None:
    x0, x1 = int(round(cx + u0 * rx)), int(round(cx + u1 * rx))
    y0, y1 = int(round(cy + v0 * ry)), int(round(cy + v1 * ry))
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(shape[1], x1), min(shape[0], y1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return slice(y0, y1), slice(x0, x1)


def _lit_squares(
    m: np.ndarray, V: np.ndarray, vlit: float, cx: float, cy: float, rx: float, ry: float
) -> dict[str, str]:
    out: dict[str, str] = {}
    hw, hh = SQUARE_HALF
    for sq, (u, v) in SQUARE_CENTRES.items():
        u0, u1, v0, v1 = u - hw, u + hw, v - hh, v + hh
        if sq == "right":
            v1 = v  # upper half only (letters below)
        if sq == "bottom":
            u1 = u - 0.15  # left part only (letters right of it)
        z = _zone(cx, cy, rx, ry, u0, u1, v0, v1, m.shape)
        if z is None:
            out[sq] = "occluded"
            continue
        mz, vz = m[z], V[z]
        cover = float(mz.mean())
        if cover < ZONE_MIN_COVER:
            out[sq] = "occluded"
            continue
        level = float(np.median(vz[mz])) / vlit
        out[sq] = "lit" if level >= LIT_MIN else "dark" if level <= DARK_MAX else "unknown"
        out[sq + "_lvl"] = f"{level:.2f}/{cover:.2f}"
    return out


def _letters(
    r: IconReading,
    m: np.ndarray,
    H: np.ndarray,
    S: np.ndarray,
    V: np.ndarray,
    vlit: float,
    cx: float,
    cy: float,
    rx: float,
    ry: float,
) -> None:
    z = _zone(cx, cy, rx, ry, LETTER_COLS[0], LETTER_COLS[1], LETTER_BAND[0], LETTER_BAND[1], m.shape)
    if z is None:
        r.warnings.append("letter band outside the window")
        return
    vz, mz = V[z], m[z]
    if not mz.any():
        r.warnings.append("no grade letters below the icon")
        r.letters, r.letters_conf = 0, 0.3
        return
    # letter FILL: relative to the letters' own brightness, so the dark outlines between letters stay out of it
    fill_level = float(np.percentile(vz[mz], 90))
    fill = mz & (vz >= 0.75 * fill_level)
    rows = fill.any(axis=1)
    if rows.sum() < 2:
        r.warnings.append("no grade letters below the icon")
        r.letters, r.letters_conf = 0, 0.3
        return
    cols = fill.sum(axis=0).astype(float)
    xs = np.nonzero(cols > 0)[0]
    # ignore isolated specks: the block is the span holding 95 % of the letter pixels
    cum = np.cumsum(cols) / cols.sum()
    a, b = int(np.searchsorted(cum, 0.02)), int(np.searchsorted(cum, 0.98))
    width = (b - a + 1) / ry
    r.feats["letters_w"] = round(width, 2)
    by_width = next((n for lo, hi, n in LETTER_WIDTH_BINS if lo <= width < hi), None)
    # valleys: columns of the block where bright fill is rare compared with its neighbours
    prof = cv2.GaussianBlur(cols[None, :], (0, 0), sigmaX=max(0.6, 0.02 * ry)).ravel()[a : b + 1]
    peak = prof.max()
    segs, inside = 0, False
    for val in prof:
        if not inside and val >= 0.55 * peak:
            segs, inside = segs + 1, True
        elif inside and val < 0.35 * peak:
            inside = False
    r.letters_width, r.letters_valleys = by_width, segs
    r.feats["letters_px"] = float(xs.size)
    # --- experiment: blob counts (not used in the decision)
    # (a) in the same band: connected components of the letter fill (outlines split the letters' lower parts)
    floor = 0.02 * ry * ry
    nb, _, stb, _ = cv2.connectedComponentsWithStats(fill.astype(np.uint8), connectivity=8)
    r.letters_blobs_band = int(sum(1 for i in range(1, nb) if stb[i, cv2.CC_STAT_AREA] >= floor))
    # (b) naive: components of the same fill over the whole letter area (letters fragment, first letter merges with
    #     the icon's bottom square) - kept only to show why it is not usable
    za = _zone(cx, cy, rx, ry, LETTER_COLS[0], LETTER_COLS[1], 0.1, LETTER_BAND[1], m.shape)
    if za is not None:
        fa = m[za] & (V[za] >= 0.75 * fill_level)
        na, _, sta, _ = cv2.connectedComponentsWithStats(fa.astype(np.uint8), connectivity=8)
        r.letters_blobs_naive = int(sum(1 for i in range(1, na) if sta[i, cv2.CC_STAT_AREA] >= floor))
    # --- letter colour, measured WITHOUT the icon's hue: bright saturated pixels of the band
    Hz, Sz, Vb = H[z], S[z], V[z]
    vtop = float(np.percentile(Vb, 99)) if Vb.size else 0.0
    lp = (Sz >= BRIGHT_S) & (Vb >= 0.6 * vtop)
    if lp.sum() >= floor:
        hist = np.bincount(Hz[lp].ravel(), minlength=180)
        sm = np.array([hist[[(i + k) % 180 for k in range(-4, 5)]].sum() for i in range(180)])
        lh = int(sm.argmax())
        r.letter_hue = lh
        r.letter_family = _family(lh)
    # decision: the valley count, accepted only when the block width is compatible with it (never one over the other)
    lo, hi = LETTER_W_PER[0] * segs, LETTER_W_PER[1] * segs + LETTER_W_PER[2]
    if 1 <= segs <= 3 and lo <= width <= hi:
        r.letters, r.letters_conf = segs, 0.9 if by_width == segs else 0.7
    else:
        r.warnings.append(
            f"letter count unresolved: {segs} valley segment(s) but block width {width:.2f} Ry "
            f"(compatible range {lo:.2f}-{hi:.2f})"
        )
    if r.letters == 2:
        r.warnings.append("2 letters: never seen on a capture, bin is a guess")


def letters_ocr(win: np.ndarray, r: IconReading) -> dict[str, str]:
    """Experiment: RapidOCR on the grade-letter area, raw colour and binarised (bright fill on black)."""
    if r.geom is None:
        return {}
    cx, cy, rx, ry = r.geom
    z = _zone(cx, cy, rx, ry, -0.5, 2.3, 0.15, 1.6, win.shape[:2])
    if z is None:
        return {}
    crop = win[z]
    k = 64.0 / max(1.0, 1.15 * ry)  # letters ~1.1 Ry tall -> ~64 px
    big = cv2.resize(crop, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)
    big = cv2.copyMakeBorder(big, 24, 24, 24, 24, cv2.BORDER_CONSTANT, value=(0, 0, 0))
    _, S, V = hsv_int(big)
    binar = np.where((S >= 80) & (V >= 0.6 * r.feats.get("vlit", 255)), 0, 255).astype(np.uint8)
    out = {}
    for tag, im in (("raw", big), ("bin", cv2.cvtColor(binar, cv2.COLOR_GRAY2BGR))):
        lines = _reader().read(im)
        out[tag] = "|".join(f"{ln.text}:{ln.score:.2f}" for ln in lines) or "-"
    return out


# ---------------------------------------------------------------------------------------------------------------
# Stars
# ---------------------------------------------------------------------------------------------------------------
@dataclass
class StarReading:
    count: int | None = None
    awakened: int | None = None
    per_star: list[str] = field(default_factory=list)  # A / p / ?
    conf: float = 0.0
    tips: list[float] = field(default_factory=list)  # x in image coords
    top: float = 0.0
    pitch: float = 0.0
    feats: list[tuple[float, float, int]] = field(default_factory=list)  # dark centre, pink lower, centre hue
    shape: list[tuple[float, float, float]] = field(default_factory=list)  # tip width, arm width, symmetry
    warnings: list[str] = field(default_factory=list)


def read_stars(img: np.ndarray, name: TextLine | None) -> StarReading:
    s = StarReading()
    if name is None:
        s.warnings.append("no name line")
        return s
    h = name.box.height
    X0, Y0 = max(0, int(name.box.x0)), max(0, int(name.box.y0))
    X1, Y1 = min(img.shape[1], int(name.box.x1 + 6 * h)), min(img.shape[0], int(name.box.y1))
    band = img[Y0:Y1, X0:X1]
    H, S, V = hsv_int(band)
    (hl, hh), smin, vfrac = YELLOW
    yh = (H >= hl) & (H <= hh) & (S > smin)
    # V threshold relative to the brightest yellow of the band (a darker/brighter rendering moves both)
    vmin = max(60.0, vfrac * float(np.percentile(V[yh], 99))) if yh.sum() >= 0.02 * h * h else 255.0
    y = (yh & (V > vmin)).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(y, connectivity=8)
    comps = [i for i in range(1, n) if st[i, cv2.CC_STAT_HEIGHT] >= 0.2 * h and st[i, cv2.CC_STAT_AREA] >= 0.02 * h * h]
    if not comps:
        s.warnings.append("no yellow star parts next to the name")
        return s
    # group by equal top and similar height (one row of stars)
    groups: list[list[int]] = []
    for i in sorted(comps, key=lambda i: st[i, cv2.CC_STAT_LEFT]):
        for g in groups:
            j = g[0]
            # same top (all star tips are level); heights differ: an awakened star's yellow part is its upper ~60 %
            if abs(st[i, 1] - st[j, 1]) <= 0.08 * h and 0.4 <= st[i, 3] / st[j, 3] <= 2.5:
                g.append(i)
                break
        else:
            groups.append([i])
    g = max(groups, key=lambda g: sum(st[i, cv2.CC_STAT_AREA] for i in g))
    if len(groups) > 1:
        s.warnings.append(f"{len(groups) - 1} other yellow object(s) in the band ignored")
    row = np.isin(lab, g)
    ys, xs = np.nonzero(row)
    top, bot = ys.min(), ys.max()
    slice_rows = row[top : top + max(2, int(round(0.3 * (bot - top + 1))))]
    colhit = slice_rows.any(axis=0)
    runs, i = [], 0
    while i < colhit.size:
        if colhit[i]:
            j = i
            while j < colhit.size and colhit[j]:
                j += 1
            runs.append((i, j))
            i = j
        else:
            i += 1
    if runs:  # specks (noise, art) are much narrower than a star tip
        wmed = float(np.median([b - a for a, b in runs]))
        narrow = [r_ for r_ in runs if (r_[1] - r_[0]) < 0.4 * wmed]
        if narrow:
            s.warnings.append(f"{len(narrow)} narrow yellow speck(s) ignored")
        runs = [r_ for r_ in runs if (r_[1] - r_[0]) >= 0.4 * wmed]
    centres = [(a + b - 1) / 2 for a, b in runs]
    regular = True
    if len(centres) >= 2:
        pitch = float(np.median(np.diff(centres)))
        # chains split at big gaps (another object further right); keep the longest chain (ties: the leftmost)
        chains, cur = [], [centres[0]]
        for c0, c1 in zip(centres, centres[1:], strict=False):
            if c1 - c0 > 1.6 * pitch:
                chains.append(cur)
                cur = []
            cur.append(c1)
        chains.append(cur)
        best = max(chains, key=len)
        if len(chains) > 1:
            s.warnings.append(f"{len(chains) - 1} separate yellow group(s) beyond 1.6 pitch ignored")
        centres = best
        if len(centres) >= 2:
            pitch = float(np.median(np.diff(centres)))
        gaps = np.diff(centres)
        regular = all(0.8 * pitch <= d <= 1.25 * pitch for d in gaps)
        if not regular:
            s.warnings.append(f"irregular tip spacing {np.round(gaps, 1).tolist()} (pitch {pitch:.1f})")
    else:
        pitch = float(bot - top + 1) / 1.3 if centres else 0.0  # single star: pitch from its height (yellow part)
        s.warnings.append("single star: pitch estimated from its height")
    s.count, s.pitch, s.top = (len(centres) if regular else None), pitch, Y0 + top
    s.tips = [X0 + c for c in centres]
    if s.count is not None:  # also a single star (its pitch is then estimated from its height)
        _star_shape(s, y.astype(bool), top, centres, pitch, h)
    awake = []
    for c in centres:
        cxs, cys = c, top + 0.72 * pitch
        rr = max(1.0, 0.1 * pitch)
        yy, xx = np.mgrid[0 : band.shape[0], 0 : band.shape[1]]
        disk = (xx - cxs) ** 2 + (yy - cys) ** 2 <= rr * rr
        # dark = clearly darker than this star's own yellow top (relative: survives brightness changes)
        tipcol = row[:, max(0, int(c - 0.3 * pitch)) : int(c + 0.3 * pitch) + 1]
        vy = V[:, max(0, int(c - 0.3 * pitch)) : int(c + 0.3 * pitch) + 1][tipcol]
        ref = float(np.median(vy)) if vy.size else 255.0
        dark = float(np.median(V[disk])) / ref if disk.any() else 1.0  # centre brightness / own yellow brightness
        chue = int(np.median(H[disk])) if disk.any() else -1
        lower = (
            slice(int(top + 0.75 * pitch), int(min(band.shape[0], top + 1.2 * pitch))),
            slice(int(max(0, c - 0.35 * pitch)), int(min(band.shape[1], c + 0.35 * pitch))),
        )
        Hl, Sl, Vl = H[lower], S[lower], V[lower]
        pink = float((((Hl >= 140) & (Hl <= 178)) & (Sl > 80) & (Vl > 120)).mean()) if Hl.size else 0.0
        s.feats.append((round(dark, 2), round(pink, 2), chue))
        if dark <= STAR_DARK and pink >= 0.08:
            awake.append("A")
        elif dark >= STAR_BRIGHT and pink <= 0.04 and 10 <= chue <= 35:
            awake.append("p")
        else:
            awake.append("?")
    s.per_star = awake
    if s.count is None:
        pass  # irregular row: neither count nor awakening is reported
    elif "?" in awake:
        s.warnings.append("some stars neither clearly awakened nor plain")
    else:
        s.awakened = awake.count("A")
        first_plain = awake.index("p") if "p" in awake else len(awake)
        if "A" in awake[first_plain:]:
            s.warnings.append("awakened star after a plain one (assumed impossible: awakening fills from the left)")
    s.conf = 0.9 if not s.warnings else 0.5
    if s.count == 1:
        s.conf = min(s.conf, 0.4)  # pitch estimated, shape check weakest (sweep false positives were mostly singles)
    return s


def _star_shape(s: StarReading, y: np.ndarray, top: int, centres: list[float], p: float, h: float) -> None:
    """Shape verification of a candidate star row, in units of the star pitch p (added after the negative-control
    test: the orange 'Lv. Max/60' text and the gold set icons next to the CP line also give regular yellow 'tips').
    A star has a narrow tip (yellow width over the top 0.15 p), arms that fill most of the pitch lower down
    (max width over 0.3-0.8 p) and a mirror-symmetric upper part about its tip."""
    tips, arms, syms = [], [], []
    for c in centres:
        xa, xb = max(0, int(round(c - 0.5 * p))), int(round(c + 0.5 * p)) + 1
        sub = y[top : int(top + 0.9 * p) + 1, xa:xb]
        widths = sub.sum(axis=1) / p
        nt = max(1, int(round(0.15 * p)))
        tips.append(float(np.median(widths[:nt])))
        lo, hi = int(0.3 * p), int(0.8 * p) + 1
        arms.append(float(widths[lo:hi].max()) if widths.size > lo else 0.0)
        half, ci = int(round(0.45 * p)), int(round(c))
        L = y[top : int(top + 0.8 * p) + 1, max(0, ci - half) : ci]
        R = y[top : int(top + 0.8 * p) + 1, ci + 1 : ci + 1 + half][:, ::-1]
        k = min(L.shape[1], R.shape[1])
        L, R = L[:, L.shape[1] - k :], R[:, R.shape[1] - k :]
        syms.append(float((L & R).sum() / max(1, (L | R).sum())))
    s.shape = [(round(a, 2), round(b, 2), round(c, 2)) for a, b, c in zip(tips, arms, syms, strict=True)]
    ph = p / h
    toff = (top - 0) / h  # band starts at the line's y0
    ok = (
        STAR_TOP_H[0] <= toff <= STAR_TOP_H[1]
        and max(tips) <= STAR_TIP_MAX
        and min(arms) >= STAR_ARM_MIN
        and float(np.median(syms)) >= STAR_SYM_MIN
        and STAR_PITCH_H[0] <= ph <= STAR_PITCH_H[1]
    )
    if not ok:
        s.warnings.append(
            f"not a star row: tip/p max {max(tips):.2f} (<= {STAR_TIP_MAX}), arm/p min {min(arms):.2f} "
            f"(>= {STAR_ARM_MIN}), symmetry median {np.median(syms):.2f} (>= {STAR_SYM_MIN}), pitch/h {ph:.2f}, "
            f"top offset {toff:.2f} h"
        )
        s.count = None


# ---------------------------------------------------------------------------------------------------------------
# Truth + evaluation
# ---------------------------------------------------------------------------------------------------------------
ALL4 = {"top": "lit", "left": "lit", "right": "lit", "bottom": "lit"}


def truth() -> dict[str, dict[str, Any]]:
    t = json.loads(TRUTH.read_text(encoding="utf-8"))
    other = t["other_samples"]
    out: dict[str, dict[str, Any]] = {}
    for k in ("heroinfo_haru", "heroinfo_lots", "heroinfo_ainz", "heroinfo_straze", "heroinfo_politis"):
        out[k] = {"stars": t[k]["stars"], "awakening": t[k]["awakening"], **t[k]["imprint"]}
    for k in ("heroinfo_charles", "equip_renoa", "equip_haru", "equip_straze"):
        out[k] = {"stars": other[k]["stars"], "awakening": other[k]["awakening"], **other[k]["imprint"]}
    out["crop5"] = {"mode": "locked", "grade": "none"}
    out["crop6"] = {"mode": "self", "grade": "B"}
    out["crop7"] = {"mode": "team", "grade": "SSS", "lit": "all four"}
    # truth.json says only "partial lit"; by eye (this spike, crop 8.png): top and left squares dark, right lit,
    # bottom (mostly under the letters) lit where visible -> PROVISIONAL truth, to be confirmed by the user
    out["crop8"] = {"mode": "team", "grade": "SSS", "lit": ["right", "bottom"], "lit_provisional": True}
    for v in out.values():
        lit = v.get("lit")
        if lit is None or v.get("mode") != "team":
            v["lit_set"] = None
        elif isinstance(lit, str):  # "all four ..." texts
            v["lit_set"] = {"top", "left", "right", "bottom"}
        else:
            v["lit_set"] = set(lit)
    return out


def run(
    names: list[str], scales: list[float], verbose: bool, do_letters_ocr: bool, perturb: str = "none", grow: float = 0.0
) -> dict[str, Any]:
    T = truth()
    stats: dict[str, dict[str, list[int]]] = {}
    sheet_rows: dict[float, list[np.ndarray]] = {s: [] for s in scales}
    failures: list[str] = []
    records: list[dict[str, Any]] = []
    blob_log: list[tuple[str, str, int | None, int | None, int, int]] = []

    def score(field_: str, scale: float, ok: bool | None) -> None:
        d = stats.setdefault(field_, {})
        c = d.setdefault(f"{scale:.2f}", [0, 0, 0])  # correct, wrong, abstained
        c[0 if ok else 1 if ok is False else 2] += 1

    for name in names:
        base = load_image(source_path(name))
        t = T[name]
        for sc in scales:
            img = scaled(base, sc)
            lines = ocr_lines(name, sc, img)
            img = PERTURB[perturb](img)
            a = find_anchors(lines)
            win = imprint_window(img, a)
            if win is not None and grow:  # robustness: a looser window (more background around the icon)
                gx, gy = grow * (win[2] - win[0]), grow * (win[3] - win[1])
                win = (max(0, int(win[0] - gx)), max(0, int(win[1] - gy)), win[2], min(img.shape[0], int(win[3] + gy)))
            tag = f"{name}@{sc:.2f}"
            if win is None:
                failures.append(f"{tag}: no imprint window ({a.notes})")
                for f in ("mode", "lit", "letters", "grade"):
                    score(f, sc, None)
                continue
            x0, y0, x1, y1 = win
            crop = img[y0:y1, x0:x1]
            text_h = float(np.median([ln.box.height for ln in a.block]))
            r = analyse_icon(crop, text_h)
            # OCR 'Locked' text (production path): overrides/confirms the icon
            locked_text = any(ln.text.strip().casefold() == "locked" for ln in a.block)
            if locked_text and r.mode != "locked":
                r.warnings.append(f"OCR says Locked but icon says {r.mode}")
            # --- scoring
            mode_ok = None if r.mode == "unknown" else r.mode == t["mode"]
            score("mode", sc, mode_ok)
            if mode_ok is False or mode_ok is None:
                failures.append(f"{tag}: mode {r.mode} (truth {t['mode']}) feats={r.feats} warn={r.warnings}")
            if t["mode"] == "team":
                got = {k for k, v in r.lit.items() if v == "lit"}
                undecided = [k for k in ("top", "left", "right", "bottom") if r.lit.get(k) not in ("lit", "dark")]
                ok = None if undecided else got == t["lit_set"]
                score("lit", sc, ok)
                if ok is not True:
                    failures.append(
                        f"{tag}: lit {r.lit} truth {sorted(t['lit_set'])}"
                        f"{' (provisional truth)' if t.get('lit_provisional') else ''}"
                    )
            want_letters = {"B": 1, "SSS": 3}.get(t["grade"])
            if want_letters is not None:
                lok = None if r.letters is None else r.letters == want_letters
                score("letters", sc, lok)
                gok = None if r.grade.startswith("unknown") else r.grade == t["grade"]
                score("grade", sc, gok)
                if lok is not True or gok is not True:
                    failures.append(
                        f"{tag}: letters {r.letters} (w={r.letters_width} v={r.letters_valleys}) "
                        f"grade {r.grade} truth {t['grade']} feats={r.feats} warn={r.warnings}"
                    )
                # experiments: blob counts, letter colour measured on its own
                score(
                    "letters_blobs_band",
                    sc,
                    None if r.letters_blobs_band is None else r.letters_blobs_band == want_letters,
                )
                score(
                    "letters_blobs_naive",
                    sc,
                    None if r.letters_blobs_naive is None else r.letters_blobs_naive == want_letters,
                )
                want_fam = {"B": "blue", "SSS": "red"}[t["grade"]]
                score("letter_colour", sc, None if r.letter_family == "unknown" else r.letter_family == want_fam)
                blob_log.append((tag, t["grade"], r.letters_blobs_band, r.letters_blobs_naive, r.letter_hue, r.hue))
            ocr_res = letters_ocr(crop, r) if do_letters_ocr and r.mode != "locked" else {}
            if ocr_res:

                def exact(res: str, grade: str = t["grade"]) -> bool:  # first line, letters only, == grade
                    first = res.split("|")[0].rsplit(":", 1)[0]
                    return re.sub(r"[^A-Za-z]", "", first).upper() == grade

                ok_raw, ok_bin = exact(ocr_res.get("raw", "-")), exact(ocr_res.get("bin", "-"))
                score("letters_ocr_raw", sc, ok_raw)
                score("letters_ocr_bin", sc, ok_bin)
            # --- negative controls: windows of the same size with NO imprint icon must give 'unknown'
            ww, wh = x1 - x0, y1 - y0
            negs = {
                "text": (x1, y0, x1 + ww, y1),  # the imprint TEXT (same colour as the icon)
                "above": (x0, y0 - 2 * wh, x1, y0 - wh),  # portrait / name area above the level line
                "art": (img.shape[1] // 2, y0, img.shape[1] // 2 + ww, y1),  # hero art (full captures)
            }
            for nk, (nx0, ny0, nx1, ny1) in negs.items():
                if nx0 < 0 or ny0 < 0 or nx1 > img.shape[1] or ny1 > img.shape[0]:
                    continue
                if name.startswith("crop") and nk == "art":
                    continue
                rn = analyse_icon(img[ny0:ny1, nx0:nx1], text_h)
                score(f"neg_icon_{nk}", sc, rn.mode == "unknown")
                if rn.mode != "unknown":
                    failures.append(f"{tag}: NEGATIVE window '{nk}' read as {rn.mode} {rn.grade} feats={rn.feats}")
            # --- stars
            s = read_stars(img, a.name) if "stars" in t else StarReading()
            if "stars" in t:
                score("stars", sc, None if s.count is None else s.count == t["stars"])
                score("awakened", sc, None if s.awakened is None else s.awakened == t["awakening"])
                # MECH-HERO-01 (community): max level = stars x 10 -> independent cross-check from the level line
                mlv = LEVEL_RE.match(a.level.text) if a.level is not None else None
                cap = int(mlv.group(2)) if mlv else None
                cap_stars = cap // 10 if cap and cap % 10 == 0 else None
                score("stars_vs_levelcap", sc, None if cap_stars is None or s.count is None else cap_stars == s.count)
                if cap_stars is not None and s.count is not None and cap_stars != s.count:
                    s.warnings.append(f"star count {s.count} != level cap {cap}/10 (MECH-HERO-01, community)")
                for ln_ in (a.level, a.cp):  # negative: no star row next to the level / CP lines
                    if ln_ is not None:
                        sn = read_stars(img, ln_)
                        score("neg_stars", sc, sn.count is None)
                        if sn.count is not None:
                            failures.append(f"{tag}: NEGATIVE star row next to '{ln_.text}' read {sn.count}")
                if s.count != t["stars"] or s.awakened != t["awakening"]:
                    failures.append(
                        f"{tag}: stars {s.count}/{s.awakened} {''.join(s.per_star)} truth "
                        f"{t['stars']}/{t['awakening']} feats={s.feats} warn={s.warnings}"
                    )
            if verbose:
                print(
                    f"{tag:28s} win={win} mode={r.mode}({r.mode_conf}) fam={r.family} lit={r.lit} "
                    f"letters={r.letters}(w{r.letters_width},v{r.letters_valleys}) grade={r.grade} "
                    f"feats={r.feats} warn={r.warnings} ocr={ocr_res}"
                )
                if "stars" in t:
                    print(
                        f"{'':28s} stars={s.count} awakened={s.awakened} {''.join(s.per_star)} pitch={s.pitch:.1f} "
                        f"feats={s.feats} warn={s.warnings}"
                    )
            records.append(
                {
                    "tag": tag,
                    "mode_truth": t["mode"],
                    "grade_truth": t.get("grade"),
                    "mode": r.mode,
                    "feats": {k: float(v) for k, v in r.feats.items()},
                    "lit": {k: v for k, v in r.lit.items()},
                    "letters_w": r.letters_width,
                    "letters_v": r.letters_valleys,
                    "stars": s.count,
                    "awake": "".join(s.per_star),
                    "star_feats": s.feats,
                    "warnings": r.warnings + s.warnings,
                }
            )
            sheet_rows[sc].append(_tile(img, crop, r, s, tag, t))
    for sc, rows in sheet_rows.items():
        if rows:
            w = max(t.shape[1] for t in rows)
            sheet = np.vstack(
                [cv2.copyMakeBorder(t, 0, 4, 0, w - t.shape[1], cv2.BORDER_CONSTANT, value=(40, 40, 40)) for t in rows]
            )
            suffix = "" if perturb == "none" else f"_{perturb}"
            cv2.imwrite(str(OUT / f"sheet_{sc:.2f}{suffix}.png"), sheet)
    _margins(records)
    if blob_log:
        print("\n== letter experiments: (grade) band-blobs / naive-blobs / letter hue vs icon hue")
        for g in ("B", "SSS"):
            rows = [b for b in blob_log if b[1] == g]
            print(
                f"  {g}: band blobs {sorted({b[2] for b in rows if b[2] is not None})}  naive blobs "
                f"{sorted({b[3] for b in rows if b[3] is not None})}  letter hue {sorted({b[4] for b in rows})}  "
                f"icon hue {sorted({b[5] for b in rows})}"
            )
    return {"stats": stats, "failures": failures, "records": records}


def _margins(records: list[dict[str, Any]]) -> None:
    """Smallest distance of every decision feature to its threshold, per class (how close each call was)."""

    def rng(vals: list[float]) -> str:
        return f"{min(vals):.2f}..{max(vals):.2f} (n={len(vals)})" if vals else "-"

    print("\n== feature ranges per true class (all scales)")
    for mode in ("team", "self"):
        recs = [r for r in records if r["mode_truth"] == mode and "centre" in r["feats"]]
        print(
            f"  {mode}: aspect {rng([r['feats']['aspect'] for r in recs])} (split {ASPECT_SPLIT}), "
            f"centre {rng([r['feats']['centre'] for r in recs])} (split {CENTRE_SPLIT})"
        )
    lit_lv: dict[str, list[float]] = {"lit": [], "dark": []}
    for r in records:
        for k, v in r["lit"].items():
            if k.endswith("_lvl"):
                lit_lv.setdefault(r["lit"][k[:-4]], []).append(float(v.split("/")[0]))
    print(
        f"  square level: lit {rng(lit_lv.get('lit', []))} dark {rng(lit_lv.get('dark', []))} "
        f"(lit >= {LIT_MIN}, dark <= {DARK_MAX})"
    )
    for g in ("B", "SSS"):
        recs = [r for r in records if r["grade_truth"] == g and "letters_w" in r["feats"]]
        print(
            f"  {g}: letter width/Ry {rng([r['feats']['letters_w'] for r in recs])}, valleys "
            f"{sorted({r['letters_v'] for r in recs})}"
        )
    a = [f for r in records for f, m in zip(r["star_feats"], r["awake"], strict=False) if m == "A"]
    p = [f for r in records for f, m in zip(r["star_feats"], r["awake"], strict=False) if m == "p"]
    print(
        f"  awakened stars: centre/yellow V {rng([f[0] for f in a])} pink {rng([f[1] for f in a])} hue {sorted({f[2] for f in a})}"
    )
    print(
        f"  plain stars:    centre/yellow V {rng([f[0] for f in p])} pink {rng([f[1] for f in p])} hue {sorted({f[2] for f in p})}"
    )


def _tile(img: np.ndarray, crop: np.ndarray, r: IconReading, s: StarReading, tag: str, t: dict[str, Any]) -> np.ndarray:
    """Contact-sheet row: the icon (scaled to 150 px high, with geometry + zones) | the star row with tip markers."""
    vis = crop.copy()
    if r.geom is not None:
        cx, cy, rx, ry = r.geom
        cv2.drawMarker(vis, (int(cx), int(cy)), (0, 255, 0), cv2.MARKER_CROSS, max(4, int(0.3 * ry)), 1)
        cv2.rectangle(vis, (int(cx - rx), int(cy - ry)), (int(cx + rx), int(cy + ry)), (0, 255, 0), 1)
        cv2.rectangle(
            vis,
            (int(cx + LETTER_COLS[0] * rx), int(cy + LETTER_BAND[0] * ry)),
            (int(cx + LETTER_COLS[1] * rx), int(cy + LETTER_BAND[1] * ry)),
            (255, 255, 0),
            1,
        )
    k = 150 / vis.shape[0]
    icon = cv2.resize(vis, None, fx=k, fy=k, interpolation=cv2.INTER_NEAREST)
    stars = np.zeros((150, 10, 3), np.uint8)
    if s.tips:
        y0 = int(s.top - 0.4 * s.pitch)
        y1 = int(s.top + 1.6 * s.pitch)
        x0 = int(s.tips[0] - 1.2 * s.pitch)
        x1 = int(s.tips[-1] + 1.2 * s.pitch)
        sv = img[max(0, y0) : y1, max(0, x0) : x1].copy()
        for tx, mark in zip(s.tips, s.per_star, strict=True):
            col = (255, 0, 255) if mark == "A" else (0, 255, 255) if mark == "p" else (0, 0, 255)
            cv2.circle(
                sv, (int(tx - max(0, x0)), int(s.top + 0.72 * s.pitch - max(0, y0))), max(1, int(0.1 * s.pitch)), col, 1
            )
        ks = 150 / sv.shape[0]
        stars = cv2.resize(sv, None, fx=ks, fy=ks, interpolation=cv2.INTER_CUBIC)
    text = np.zeros((150, 560, 3), np.uint8)
    lit = ",".join(
        k[0].upper() if v == "lit" else k[0] if v == "dark" else "?" for k, v in r.lit.items() if not k.endswith("_lvl")
    )
    rows = [
        tag,
        f"mode {r.mode} ({r.mode_conf}) {r.family}  truth {t['mode']}",
        f"lit {lit or '-'}  truth {sorted(t['lit_set']) if t.get('lit_set') else '-'}",
        f"letters {r.letters} (w{r.letters_width} v{r.letters_valleys}) grade {r.grade}",
        f"stars {s.count}/{s.awakened} {''.join(s.per_star)}  truth {t.get('stars')}/{t.get('awakening')}",
    ]
    for i, line in enumerate(rows):
        cv2.putText(text, line, (6, 22 + 28 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (230, 230, 230), 1, cv2.LINE_AA)
    return np.hstack([icon, np.zeros((150, 6, 3), np.uint8), stars, np.zeros((150, 6, 3), np.uint8), text])


def sweep(names: list[str], scales: list[float]) -> None:
    """Held-out negatives (never used to set a threshold): (1) icon windows of the imprint window's size tiled over
    the WHOLE capture (stride = half a window; windows near the real icon skipped) -> any decided mode is a false
    positive; (2) the star reader run next to EVERY OCR line except the hero name -> any count is a false positive."""
    print("\n== sweep (held-out negatives)")
    for sc in scales:
        n_win = fp_win = n_ln = fp_ln = 0
        examples: list[str] = []
        for name in names:
            if name.startswith("crop"):
                continue
            img = scaled(load_image(source_path(name)), sc)
            a = find_anchors(ocr_lines(name, sc, img))
            win = imprint_window(img, a)
            if win is None:
                continue
            x0, y0, x1, y1 = win
            ww, wh = x1 - x0, y1 - y0
            text_h = float(np.median([ln.box.height for ln in a.block]))
            for ty in range(0, img.shape[0] - wh, wh // 2):
                for tx in range(0, img.shape[1] - ww, ww // 2):
                    if tx < x1 + ww and tx + ww > x0 - ww and ty < y1 + wh and ty + wh > y0 - wh:
                        continue  # near the real icon
                    n_win += 1
                    rn = analyse_icon(img[ty : ty + wh, tx : tx + ww], text_h)
                    if rn.mode != "unknown":
                        fp_win += 1
                        examples.append(f"{name}@{sc:.2f} window ({tx},{ty}) -> {rn.mode} {rn.grade} {rn.feats}")
            for ln in ocr_lines(name, sc, img):
                if a.name is None or ln.box == a.name.box:
                    continue
                nb = a.name.box
                if ln.box.x0 > nb.x0 and min(ln.box.y1, nb.y1) - max(ln.box.y0, nb.y0) > 0.5 * ln.box.height:
                    continue  # the star row itself, OCR'd as a separate 'text' line ('☆AAAA'): not a negative
                n_ln += 1
                sn = read_stars(img, ln)
                if sn.count is not None:
                    fp_ln += 1
                    examples.append(
                        f"{name}@{sc:.2f} line '{ln.text}' at x={ln.box.x0:.0f}/{img.shape[1]} -> {sn.count} stars "
                        f"shape={sn.shape}"
                    )
        print(
            f"  scale {sc:.2f}: icon windows {fp_win}/{n_win} false positives; star rows {fp_ln}/{n_ln} false positives"
        )
        for e in examples:
            print("    " + e)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--scales", nargs="*", type=float, default=list(SCALES))
    ap.add_argument("--letters-ocr", action="store_true")
    ap.add_argument("--perturb", nargs="*", default=["none"], choices=list(PERTURB))
    ap.add_argument("--grow", type=float, default=0.0, help="enlarge the icon window by this fraction (left/up/down)")
    ap.add_argument("--sweep", action="store_true", help="held-out negatives over the whole captures, then exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    names = args.only or (FULL + CROP_NAMES)
    OUT.mkdir(parents=True, exist_ok=True)
    if args.sweep:
        sweep(names, args.scales)
        return
    allres = {}
    for pert in args.perturb:
        print(f"\n######## perturbation: {pert}  grow: {args.grow}")
        res = run(names, args.scales, args.verbose, args.letters_ocr, pert, args.grow)
        print("\n== accuracy (correct / wrong / abstained) per scale")
        for f, d in res["stats"].items():
            print(f"  {f:16s} " + "  ".join(f"{k}: {v[0]}/{v[1]}/{v[2]}" for k, v in sorted(d.items())))
        print(f"\n== {len(res['failures'])} failure(s)")
        for f in res["failures"]:
            print("  " + f)
        allres[pert] = res
    (OUT / "results.json").write_text(json.dumps(allres, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
