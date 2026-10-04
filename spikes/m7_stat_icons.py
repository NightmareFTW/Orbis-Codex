"""Spike M7 (stat_icons): classify the stat icon left of every gear value on the Hero Info screen.

Throwaway experiment, never imported by src/. Reads the user's own captures (git-ignored fixtures) and writes
contact sheets / OCR caches under the scratch dir only.

    uv run python spikes/m7_stat_icons.py                       # all methods, scales 0.64 / 1.0 / 1.28
    uv run python spikes/m7_stat_icons.py --methods grad2 --perturb jpeg60 noise8 dark bright blur --sheets

Pipeline (no pixel position of any resolution is hard-coded; every length is a multiple of a measured pitch):
1. OCR (RapidOCR, `e7ac.vision.ocr`) of the whole capture.
2. Templates at runtime: the nine stat labels of the left panel (margin-safe `match_label`) fix the label column;
   left of each label, Otsu on min(B,G,R) isolates the grey icon -> 9 templates incl. Dual Attack Chance.
3. Gear rows: number-only lines in the gear panel cluster by right edge into two right-aligned columns; main-stat
   rows are piece starts confirmed across columns; substat index = rows below the main stat (by measured pitch).
4. Icon column: per gear column, median x of the best template match in a broad band left of the values.
5. Classification at (column x, row y): NCC of every template over small shift (+-0.25 pitch) and scale
   (0.70-1.30) ranges, on one of several feature maps (methods below); margin rule best vs second-best.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from e7ac.settings import GameLanguage
from e7ac.vision.image import load_image
from e7ac.vision.labels import STAT_LABELS, match_label
from e7ac.vision.ocr import Box, RapidOcrReader, TextLine, Word

REPO = Path(__file__).resolve().parents[1]
SHOTS = REPO / "fixtures" / "screenshots"
SCRATCH = Path(
    os.environ.get(
        "M7_SCRATCH", "/tmp/claude-0/-home-user-Orbis-Codex/d44236ee-bada-567e-a707-c7890345df7b/scratchpad/m7"
    )
)
OUT = SCRATCH / "stat_icons"
CAPTURES = ("haru", "lots", "ainz", "straze", "politis")
TEST_SCALES = (0.64, 1.0, 1.28)


# ---------------------------------------------------------------------------------------------------------------
# Input: scaled (and optionally degraded) captures, cached OCR
# ---------------------------------------------------------------------------------------------------------------
def scaled_image(name: str, scale: float) -> np.ndarray:
    img = load_image(SHOTS / f"heroinfo_{name}.webp")
    if scale == 1.0:
        return img
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    return cv2.resize(img, None, fx=scale, fy=scale, interpolation=interp)


def _jpeg(img: np.ndarray, quality: int) -> np.ndarray:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    assert ok
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def _noise(img: np.ndarray, sigma: float) -> np.ndarray:
    rng = np.random.default_rng(7)
    return np.clip(img.astype(np.float32) + rng.normal(0, sigma, img.shape), 0, 255).astype(np.uint8)


def _gamma(img: np.ndarray, gamma: float, gain: float) -> np.ndarray:
    f = (img.astype(np.float32) / 255.0) ** gamma * gain
    return np.clip(f * 255.0, 0, 255).astype(np.uint8)


PERTURBATIONS: dict[str, Callable[[np.ndarray], np.ndarray]] = {
    "none": lambda im: im,
    "jpeg60": lambda im: _jpeg(im, 60),
    "noise8": lambda im: _noise(im, 8.0),
    "dark": lambda im: _gamma(im, 1.3, 0.8),  # darker, lower contrast
    "bright": lambda im: _gamma(im, 0.75, 1.1),  # washed out
    "blur": lambda im: cv2.GaussianBlur(im, (0, 0), 0.8),
}

_READER: RapidOcrReader | None = None


def ocr_lines(name: str, scale: float, img: np.ndarray, tag: str = "none") -> list[TextLine]:
    """OCR of the (scaled, perturbed) image, cached as JSON under the scratch dir (OCR is the slow part)."""
    global _READER
    suffix = "" if tag == "none" else f"_{tag}"
    cache = OUT / "ocr" / f"{name}_{scale:.2f}{suffix}.json"
    if not cache.exists():
        if _READER is None:
            _READER = RapidOcrReader()
        lines = _READER.read(img)
        cache.parent.mkdir(parents=True, exist_ok=True)
        data = [
            {
                "text": ln.text,
                "score": ln.score,
                "box": [ln.box.x0, ln.box.y0, ln.box.x1, ln.box.y1],
                "words": [[w.text, w.score, w.box.x0, w.box.y0, w.box.x1, w.box.y1] for w in ln.words],
            }
            for ln in lines
        ]
        cache.write_text(json.dumps(data), encoding="utf-8")
    data = json.loads(cache.read_text(encoding="utf-8"))
    return [
        TextLine(d["text"], d["score"], Box(*d["box"]), tuple(Word(w[0], w[1], Box(*w[2:])) for w in d["words"]))
        for d in data
    ]


# ---------------------------------------------------------------------------------------------------------------
# Structure: stat-label templates (left panel) and gear value rows (right panel), all anchored on OCR text
# ---------------------------------------------------------------------------------------------------------------
NAMES = ("atk", "def", "hp", "spd", "cc", "cd", "eff", "er", "dac")
LABEL_TO_NAME = dict(zip(STAT_LABELS[GameLanguage.EN], NAMES, strict=True))
_VALUE = re.compile(r"^\d{1,3}(?:,\d{3})*%?$")


@dataclass
class Template:
    name: str
    crop: np.ndarray  # BGR: icon bbox plus a margin of TEMPLATE_PAD * size
    mask: np.ndarray  # uint8 0/1, same size as crop


@dataclass
class LabelColumn:
    x0: float
    pitch: float
    rows: dict[str, TextLine]


def label_column(lines: list[TextLine]) -> LabelColumn:
    """The stat labels of the left panel (exact or margin-safe fuzzy match), their common left edge and row pitch.
    The imprint text ("Effectiveness + 27%") can match a label too: only lines on the common left edge count."""
    found: list[tuple[str, TextLine]] = []
    for ln in lines:
        key = match_label(ln.text, LABEL_TO_NAME)
        if key is not None:
            found.append((LABEL_TO_NAME[key], ln))
    if len({k for k, _ in found}) < 5:
        raise RuntimeError(f"stat labels not found ({sorted(k for k, _ in found)})")
    x0 = float(np.median([ln.box.x0 for _, ln in found]))
    rows: dict[str, TextLine] = {}
    for k, ln in found:
        if abs(ln.box.x0 - x0) < 0.5 * ln.box.height and k not in rows:
            rows[k] = ln
    cys = sorted(ln.box.cy for ln in rows.values())
    return LabelColumn(x0, float(np.median(np.diff(cys))), rows)


TEMPLATE_PAD = 0.15


def extract_templates(img: np.ndarray, col: LabelColumn) -> dict[str, Template]:
    """Crop the icon left of each label: Otsu on min(B,G,R) (grey icon on a dark panel), components whose centre
    is near the row centre, bounding box + pad. Box: x in [x0 - 1.6p, x0 - 0.1p], y = row centre +- 0.6p."""
    p = col.pitch
    out: dict[str, Template] = {}
    for name, ln in col.rows.items():
        cy = ln.box.cy
        x_a, x_b = max(0, int(round(col.x0 - 1.6 * p))), int(round(col.x0 - 0.1 * p))
        y_a, y_b = max(0, int(round(cy - 0.6 * p))), int(round(cy + 0.6 * p))
        region = img[y_a:y_b, x_a:x_b]
        white = cv2.normalize(region.min(axis=2), None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        _, m = cv2.threshold(white, 0, 1, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        n, lab, stats, cents = cv2.connectedComponentsWithStats(m, connectivity=8)
        keep = np.zeros_like(m)
        for i in range(1, n):
            if stats[i, cv2.CC_STAT_AREA] >= 0.004 * p * p and abs(cents[i][1] - region.shape[0] / 2) <= 0.38 * p:
                keep[lab == i] = 1
        ys, xs = np.nonzero(keep)
        if len(xs) == 0:
            continue
        bx0, by0, bx1, by1 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
        pad = int(round(TEMPLATE_PAD * max(bx1 - bx0, by1 - by0)))
        gx0, gy0 = x_a + bx0 - pad, y_a + by0 - pad
        crop = img[gy0 : y_a + by1 + pad, gx0 : x_a + bx1 + pad].copy()
        mask = np.zeros(crop.shape[:2], np.uint8)
        mask[pad : pad + by1 - by0, pad : pad + bx1 - bx0] = keep[by0:by1, bx0:bx1]
        out[name] = Template(name, crop, mask)
    return out


@dataclass
class GearRow:
    column: int  # 0 = left (weapon/helmet/armor), 1 = right (necklace/ring/boots)
    piece: int  # 0..2 top to bottom
    index: int  # 0 = main stat, 1..4 = substats
    text: str
    box: Box
    is_main: bool


@dataclass
class GearLayout:
    rows: list[GearRow]
    right_edges: tuple[float, ...]
    pitch: float  # substat row pitch
    main_rows: list[float]
    gap1: float  # main stat -> first substat
    warnings: list[str] = field(default_factory=list)


def _panel_origin(lines: list[TextLine], width: float) -> tuple[float, float]:
    """Top-left of the gear panel: the "Average Equipment Score" line if read (English only), else the image's
    right half."""
    for ln in lines:
        if "equipment score" in ln.text.casefold():
            return ln.box.x0, ln.box.y1
    return width / 2, 0.0


def gear_layout(lines: list[TextLine], labels: LabelColumn, width: float) -> GearLayout:
    """Number-only lines of the gear panel, right-aligned in two columns (the two most populous right-edge
    clusters). A piece starts after a gap of more than one row; it is a main stat when the other column starts a
    piece on the same row, or when the line is clearly taller. Line heights alone are NOT reliable (a substat line
    can be as tall as a main-stat line at some scales)."""
    tol = 0.35 * labels.pitch
    px, py = _panel_origin(lines, width)
    cands = [
        ln
        for ln in lines
        if _VALUE.match(ln.text.replace(" ", "")) and (ln.box.x0 + ln.box.x1) / 2 > px and ln.box.cy > py
    ]
    clusters: list[list[TextLine]] = []
    for ln in sorted(cands, key=lambda c: c.box.x1):
        if clusters and abs(ln.box.x1 - np.median([c.box.x1 for c in clusters[-1]])) <= tol:
            clusters[-1].append(ln)
        else:
            clusters.append([ln])
    clusters = sorted(sorted(clusters, key=len, reverse=True)[:2], key=lambda c: np.median([x.box.x1 for x in c]))
    warnings: list[str] = []
    if not clusters:
        return GearLayout([], (), labels.pitch, [], labels.pitch, ["no gear values found (no gear equipped?)"])
    if len(clusters) != 2 or min(len(c) for c in clusters) < 5:
        warnings.append(f"gear value columns unclear: sizes {[len(c) for c in clusters]}")
    heights = sorted(ln.box.height for c in clusters for ln in c)
    sub_h = float(np.median(heights[: max(1, len(heights) * 2 // 3)]))
    diffs = [d for c in clusters for d in np.diff(sorted(ln.box.cy for ln in c)) if d < 1.5 * sub_h]
    pitch = float(np.median(diffs)) if diffs else sub_h
    cols = [sorted(c, key=lambda ln: ln.box.cy) for c in clusters]
    starts = [[ln for i, ln in enumerate(c) if i == 0 or ln.box.cy - c[i - 1].box.cy > 1.7 * pitch] for c in cols]
    mains: list[TextLine] = []
    for ci in range(len(cols)):
        other = starts[1 - ci] if len(starts) == 2 else []
        for ln in starts[ci]:
            if any(abs(o.box.cy - ln.box.cy) < 0.4 * pitch for o in other) or ln.box.height > 1.15 * sub_h:
                mains.append(ln)
    main_rows: list[float] = []
    for y in sorted(m.box.cy for m in mains):
        if not main_rows or y - main_rows[-1] > 2 * pitch:
            main_rows.append(y)
    if len(main_rows) != 3:
        warnings.append(f"{len(main_rows)} main-stat rows found (3 expected)")
    gaps = [
        c[i + 1].box.cy - ln.box.cy
        for c in cols
        for i, ln in enumerate(c[:-1])
        if ln in mains and c[i + 1].box.cy - ln.box.cy < 1.6 * pitch
    ]
    gap1 = float(np.median(gaps)) if gaps else pitch
    rows: list[GearRow] = []
    for ci, c in enumerate(cols):
        for ln in c:
            above = [y for y in main_rows if y <= ln.box.cy + 0.4 * pitch]
            if not above:
                warnings.append(f"column {ci}: line {ln.text!r} above every main-stat row")
                continue
            pi, y_main = len(above) - 1, above[-1]
            if ln in mains:
                rows.append(GearRow(ci, pi, 0, ln.text, ln.box, True))
                continue
            idx = 1 + int(round((ln.box.cy - y_main - gap1) / pitch))
            if not 1 <= idx <= 4:
                warnings.append(f"column {ci}: line {ln.text!r} at substat index {idx}")
            rows.append(GearRow(ci, pi, idx, ln.text, ln.box, False))
    edges = tuple(float(np.median([x.box.x1 for x in c])) for c in clusters)
    return GearLayout(rows, edges, pitch, main_rows, gap1, warnings)


# ---------------------------------------------------------------------------------------------------------------
# Ground truth (transcribed by eye; truth.json in the scratch dir)
# ---------------------------------------------------------------------------------------------------------------
SLOTS = (("weapon", "helmet", "armor"), ("necklace", "ring", "boots"))


def truth_for(name: str) -> dict[tuple[int, int, int], tuple[str, str]]:
    data = json.loads((SCRATCH / "truth.json").read_text(encoding="utf-8"))[f"heroinfo_{name}"]
    by_slot = {p["slot"]: p for p in data["pieces"] if p}
    out: dict[tuple[int, int, int], tuple[str, str]] = {}
    for ci, slots in enumerate(SLOTS):
        for pi, slot in enumerate(slots):
            piece = by_slot.get(slot)
            if piece is None:
                continue
            out[(ci, pi, 0)] = (piece["main"][0], piece["main"][1])
            for si, (text, fam) in enumerate(piece["subs"]):
                out[(ci, pi, si + 1)] = (text, fam)
    return out


# ---------------------------------------------------------------------------------------------------------------
# Feature maps
# ---------------------------------------------------------------------------------------------------------------
def grad_field(bgr: np.ndarray, sigma: float, signed: bool = False) -> np.ndarray:
    """Colour gradient (per pixel the channel with the largest magnitude) as a 2-channel field.
    Unsigned = doubled angle (gx^2 - gy^2, 2 gx gy) / |g|: same magnitude, independent of contrast polarity, so a
    grey icon brighter than a dark background and a grey icon darker than a white nebula give the same field."""
    f = cv2.GaussianBlur(bgr.astype(np.float32), (0, 0), sigma)
    gx = cv2.Sobel(f, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(f, cv2.CV_32F, 0, 1, ksize=3)
    best = (gx * gx + gy * gy).argmax(axis=2)[..., None]
    gx = np.take_along_axis(gx, best, axis=2)[..., 0]
    gy = np.take_along_axis(gy, best, axis=2)[..., 0]
    if signed:
        return np.dstack([gx, gy]).astype(np.float32)
    m = np.sqrt(gx * gx + gy * gy) + 1e-6
    return np.dstack([(gx * gx - gy * gy) / m, 2 * gx * gy / m]).astype(np.float32)


def _border_plane(f: np.ndarray, ring: float = 0.15) -> np.ndarray:
    """Least-squares plane through the window's border ring: a smooth background estimate (nebula gradients)."""
    h, w = f.shape
    k = max(1, int(round(ring * min(h, w))))
    sel = np.zeros((h, w), bool)
    sel[:k, :] = sel[-k:, :] = sel[:, :k] = sel[:, -k:] = True
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    a = np.stack([xx[sel], yy[sel], np.ones(int(sel.sum()), np.float32)], axis=1)
    coef, *_ = np.linalg.lstsq(a, f[sel], rcond=None)
    return (xx * coef[0] + yy * coef[1] + coef[2]).astype(np.float32)


def adaptive_mask(bgr: np.ndarray, sigma: float) -> np.ndarray:
    """Icon mask without fixed colour thresholds: three achromatic cues (whiteness = min channel, low chroma,
    luminance), each minus a background plane fitted to the window border; Otsu on each, keep the cue whose split
    is most separable (between-class / total variance). Polarity: the icon is the class the border is not.
    Returned blurred (float) so matching tolerates one-pixel edge differences."""
    f = bgr.astype(np.float32)
    mx, mn = f.max(axis=2), f.min(axis=2)
    cues = [mn, -(mx - mn), cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)]
    best, best_eta = None, -1.0
    h, w = mn.shape
    k = max(1, int(round(0.15 * min(h, w))))
    for c in cues:
        r8 = cv2.normalize(c - _border_plane(c), None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        _, m = cv2.threshold(r8, 0, 1, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        p1 = float(m.mean())
        if p1 in (0.0, 1.0):
            continue
        v = r8.astype(np.float32)
        eta = p1 * (1 - p1) * (v[m == 1].mean() - v[m == 0].mean()) ** 2 / (v.var() + 1e-6)
        border = np.concatenate([m[:k].ravel(), m[-k:].ravel(), m[:, :k].ravel(), m[:, -k:].ravel()])
        if border.mean() > 0.5:
            m = 1 - m
        if eta > best_eta:
            best, best_eta = m, eta
    if best is None:
        best = np.zeros((h, w), np.uint8)
    return cv2.GaussianBlur(best.astype(np.float32), (0, 0), sigma)


HOG_SIZE, HOG_CELL, HOG_BINS = 32, 8, 9


def hog_dense(bgr: np.ndarray) -> np.ndarray:
    """HOG of every 32x32 patch (stride 1): colour gradient, unsigned orientation, 9 bins (linear interpolation),
    8x8 cells, 2x2-cell blocks with L2-Hys -> (H-31, W-31, 324), L2-normalised.
    Own numpy version: the opencv-python-headless wheel here has no HOGDescriptor."""
    f = bgr.astype(np.float32)
    gx = cv2.Sobel(f, cv2.CV_32F, 1, 0, ksize=1)
    gy = cv2.Sobel(f, cv2.CV_32F, 0, 1, ksize=1)
    best = (gx * gx + gy * gy).argmax(axis=2)[..., None]
    gx = np.take_along_axis(gx, best, axis=2)[..., 0]
    gy = np.take_along_axis(gy, best, axis=2)[..., 0]
    m = np.sqrt(gx * gx + gy * gy)
    ang = (np.degrees(np.arctan2(gy, gx)) % 180.0) / (180.0 / HOG_BINS)
    b0 = np.floor(ang).astype(int) % HOG_BINS
    b1 = (b0 + 1) % HOG_BINS
    w1 = ang - np.floor(ang)
    chans = np.zeros(m.shape + (HOG_BINS,), np.float32)
    for b in range(HOG_BINS):
        chans[..., b] = m * ((b0 == b) * (1 - w1) + (b1 == b) * w1)
    ii = np.pad(chans, ((1, 0), (1, 0), (0, 0))).cumsum(0).cumsum(1)
    c = HOG_CELL
    cells = ii[c:, c:] - ii[:-c, c:] - ii[c:, :-c] + ii[:-c, :-c]
    n = HOG_SIZE // c
    hh, ww = bgr.shape[0] - HOG_SIZE + 1, bgr.shape[1] - HOG_SIZE + 1
    if hh <= 0 or ww <= 0:
        return np.zeros((0, 0, 0), np.float32)
    grid = np.stack(
        [np.stack([cells[j * c : j * c + hh, i * c : i * c + ww] for i in range(n)], axis=2) for j in range(n)],
        axis=2,
    )
    blocks = []
    for j in range(n - 1):
        for i in range(n - 1):
            v = grid[:, :, j : j + 2, i : i + 2].reshape(hh, ww, -1)
            v = np.minimum(v / (np.linalg.norm(v, axis=2, keepdims=True) + 1e-6), 0.2)
            blocks.append(v / (np.linalg.norm(v, axis=2, keepdims=True) + 1e-6))
    d = np.concatenate(blocks, axis=2)
    return d / (np.linalg.norm(d, axis=2, keepdims=True) + 1e-9)


# ---------------------------------------------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------------------------------------------
def resize_to(img: np.ndarray, scale: float) -> np.ndarray:
    h, w = img.shape[:2]
    size = (max(3, int(round(w * scale))), max(3, int(round(h * scale))))
    return cv2.resize(img, size, interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)


def crop_window(img: np.ndarray, x0: float, y0: float, x1: float, y1: float) -> tuple[np.ndarray, tuple[int, int]]:
    h, w = img.shape[:2]
    a, b = max(0, int(round(x0))), max(0, int(round(y0)))
    c, d = min(w, int(round(x1))), min(h, int(round(y1)))
    return img[b:d, a:c], (a, b)


SCALES = tuple(float(s) for s in np.round(np.arange(0.70, 1.31, 0.05), 3))
"""Gear icon size / left-panel icon size searched. Measured: substats 0.85-0.95, main stats 0.95-1.0."""


@dataclass
class Match:
    name: str
    score: float
    x: float  # centre of the matched template, image coordinates
    y: float
    scale: float


class Matcher:
    """Scores every template over a window (all shifts inside it, all scales). One instance per capture.
    Methods: grad2 (unsigned gradient NCC), gradS (signed gradient NCC), mask (adaptive mask NCC), hog (HOG+cosine);
    "a+b" = mean of the parts' scores."""

    def __init__(self, method: str, templates: dict[str, Template], pitch: float, scales=SCALES) -> None:
        self.method, self.templates, self.scales = method, templates, scales
        self.sigma = max(0.5, 0.025 * pitch)  # gradient pre-blur, proportional to the UI scale
        self.size = float(np.median([max(t.crop.shape[:2]) for t in templates.values()]))
        feats: dict[str, Callable[[np.ndarray], np.ndarray]] = {
            "grad2": lambda im: grad_field(im, self.sigma),
            "gradS": lambda im: grad_field(im, self.sigma, signed=True),
            "mask": lambda im: adaptive_mask(im, self.sigma),
        }
        self.parts = method.split("+")
        self.feature = {k: feats[k] for k in self.parts if k in feats}
        self.tfeat = {
            k: {n: [(sc, f(resize_to(t.crop, sc))) for sc in scales] for n, t in templates.items()}
            for k, f in self.feature.items()
        }
        self.thog = {
            n: hog_dense(cv2.resize(t.crop, (HOG_SIZE, HOG_SIZE), interpolation=cv2.INTER_AREA))[0, 0]
            for n, t in templates.items()
        }

    def _ncc(self, kind: str, win: np.ndarray, org: tuple[int, int]) -> dict[str, Match]:
        fw = self.feature[kind](win)
        out: dict[str, Match] = {}
        for name, per_scale in self.tfeat[kind].items():
            best = Match(name, -1.0, 0.0, 0.0, 0.0)
            for sc, tf in per_scale:
                th, tw = tf.shape[:2]
                if th > fw.shape[0] or tw > fw.shape[1]:
                    continue
                r = cv2.matchTemplate(fw, tf, cv2.TM_CCORR_NORMED)
                _, mx, _, loc = cv2.minMaxLoc(r)
                if mx > best.score:
                    best = Match(name, float(mx), org[0] + loc[0] + tw / 2, org[1] + loc[1] + th / 2, sc)
            out[name] = best
        return out

    def _hog(self, win: np.ndarray, org: tuple[int, int]) -> dict[str, Match]:
        out = {n: Match(n, -1.0, 0.0, 0.0, 0.0) for n in self.templates}
        for sc in self.scales:
            f = HOG_SIZE / (sc * self.size)
            d = hog_dense(resize_to(win, f))
            if d.size == 0:
                continue
            for name, td in self.thog.items():
                sims = d @ td
                y, x = np.unravel_index(int(sims.argmax()), sims.shape)
                if sims[y, x] > out[name].score:
                    cx, cy = org[0] + (x + HOG_SIZE / 2) / f, org[1] + (y + HOG_SIZE / 2) / f
                    out[name] = Match(name, float(sims[y, x]), cx, cy, sc)
        return out

    def scores(self, win: np.ndarray, org: tuple[int, int]) -> dict[str, Match]:
        per = [self._hog(win, org) if k == "hog" else self._ncc(k, win, org) for k in self.parts]
        if len(per) == 1:
            return per[0]
        return {  # fusion: mean score; location/scale of the first part
            n: Match(n, float(np.mean([p[n].score for p in per])), per[0][n].x, per[0][n].y, per[0][n].scale)
            for n in self.templates
        }


# ---------------------------------------------------------------------------------------------------------------
# Locate and classify
# ---------------------------------------------------------------------------------------------------------------
@dataclass
class IconResult:
    row: GearRow
    ranked: list[Match]  # best first

    @property
    def best(self) -> Match:
        return self.ranked[0]

    @property
    def margin(self) -> float:
        return self.ranked[0].score - self.ranked[1].score


def icon_column_centres(img: np.ndarray, layout: GearLayout, matcher: Matcher) -> list[float]:
    """Per gear column, the icons' x centre: median over the column's rows of the best match in a broad band
    [right edge - 4.6p, min(value left edge, right edge - 1.5p)] (the band stops before the digits). Measured
    centre: right edge - 3.1p at all three scales, so the band has >1p of slack on each side."""
    p = layout.pitch
    centres = []
    for ci, x_r in enumerate(layout.right_edges):
        xs = []
        for r in (r for r in layout.rows if r.column == ci):
            right = min(r.box.x0, x_r - 1.5 * p)
            win, org = crop_window(img, x_r - 4.6 * p, r.box.cy - 0.75 * p, right, r.box.cy + 0.75 * p)
            if win.shape[1] < 1.2 * matcher.size:
                continue
            m = matcher.scores(win, org)
            xs.append(max(m.values(), key=lambda mm: mm.score).x)
        centres.append(float(np.median(xs)) if xs else float("nan"))
    return centres


SHIFT = 0.15  # search half-range around (column centre, row centre), in pitches; measured offsets <= 0.09p
NARROW = True  # second pass: scale range narrowed to the per-capture median scale of main / substat rows +-0.05


def classify_at(img: np.ndarray, matcher: Matcher, xc: float, cy: float, pitch: float) -> list[Match]:
    half = 0.5 * matcher.size * max(matcher.scales) + SHIFT * pitch
    win, org = crop_window(img, xc - half, cy - half, xc + half, cy + half)
    return sorted(matcher.scores(win, org).values(), key=lambda m: m.score, reverse=True)


def decide(ranked: list[Match], min_score: float, min_margin: float) -> tuple[str, str]:
    """Margin rule: accept only a clear winner; a DAC winner is reported as such (never remapped)."""
    if ranked[0].score < min_score:
        return "abstain", f"best {ranked[0].name} {ranked[0].score:.3f} < {min_score}"
    if ranked[0].score - ranked[1].score < min_margin:
        gap = ranked[0].score - ranked[1].score
        return "abstain", f"margin {ranked[0].name}-{ranked[1].name} {gap:.3f} < {min_margin}"
    if ranked[0].name == "dac":
        return "dac", "Dual Attack Chance icon matched: not a gear stat, needs review"
    return "ok", ""


@dataclass
class CaptureRun:
    img: np.ndarray
    layout: GearLayout
    templates: dict[str, Template]
    matcher: Matcher  # the sub-stat matcher (also used for blank windows)
    centres: list[float]
    results: list[IconResult]
    scale_main: float = 1.0
    scale_sub: float = 1.0


def run_capture(name: str, scale: float, method: str, perturb: str = "none", narrow: bool | None = None) -> CaptureRun:
    narrow = NARROW if narrow is None else narrow
    img = PERTURBATIONS[perturb](scaled_image(name, scale))
    lines = ocr_lines(name, scale, img, perturb)
    col = label_column(lines)
    templates = extract_templates(img, col)
    layout = gear_layout(lines, col, img.shape[1])
    matcher = Matcher(method, templates, layout.pitch)
    centres = icon_column_centres(img, layout, matcher)
    results = [IconResult(r, classify_at(img, matcher, centres[r.column], r.box.cy, layout.pitch)) for r in layout.rows]
    s_main = float(np.median([r.best.scale for r in results if r.row.is_main] or [1.0]))
    s_sub = float(np.median([r.best.scale for r in results if not r.row.is_main] or [1.0]))
    if not narrow:
        return CaptureRun(img, layout, templates, matcher, centres, results, s_main, s_sub)
    # main-stat icons are drawn bigger than substat icons; each kind has one size per screen: search only near it
    by_kind = {
        kind: Matcher(method, templates, layout.pitch, scales=(s - 0.05, s, s + 0.05))
        for kind, s in ((True, s_main), (False, s_sub))
    }
    results = [
        IconResult(r.row, classify_at(img, by_kind[r.row.is_main], centres[r.row.column], r.row.box.cy, layout.pitch))
        for r in results
    ]
    return CaptureRun(img, layout, templates, by_kind[False], centres, results, s_main, s_sub)


# ---------------------------------------------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------------------------------------------
@dataclass
class Record:
    capture: str
    scale: float
    perturb: str
    key: tuple[int, int, int]
    text: str
    truth: str
    best: str
    score: float
    second: str
    second_score: float
    impostor: str  # best template other than the true one = what an icon missing from the templates would get
    impostor_score: float
    impostor_margin: float
    is_main: bool
    crop: np.ndarray
    dac_score: float = 0.0

    @property
    def margin(self) -> float:
        return self.score - self.second_score


@dataclass
class Blank:
    """A window with no icon (empty 4th substat slot, or the gap between two pieces)."""

    capture: str
    scale: float
    where: str
    score: float
    margin: float
    best: str


def evaluate(method: str, scales=TEST_SCALES, captures=CAPTURES, perturb: str = "none"):
    recs: list[Record] = []
    blanks: list[Blank] = []
    timings: list[float] = []
    warnings: list[str] = []
    for sc in scales:
        for n in captures:
            t0 = time.perf_counter()
            run = run_capture(n, sc, method, perturb)
            timings.append(time.perf_counter() - t0)
            warnings += [f"{n}@{sc}: {w}" for w in run.layout.warnings]
            truth = truth_for(n)
            half = 0.7 * run.matcher.size
            for res in run.results:
                key = (res.row.column, res.row.piece, res.row.index)
                if key not in truth:
                    warnings.append(f"{n}@{sc}: row {key} {res.row.text!r} not in truth")
                    continue
                t_text, t_fam = truth[key]
                if t_text != res.row.text:
                    warnings.append(f"{n}@{sc}: row {key} OCR {res.row.text!r} vs truth {t_text!r}")
                imp = [m for m in res.ranked if m.name != t_fam]
                b = res.best
                crop, _ = crop_window(run.img, b.x - half, b.y - half, b.x + half, b.y + half)
                recs.append(
                    Record(n, sc, perturb, key, res.row.text, t_fam, b.name, b.score, res.ranked[1].name,
                           res.ranked[1].score, imp[0].name, imp[0].score, imp[0].score - imp[1].score,
                           res.row.is_main, crop.copy(), next(m.score for m in res.ranked if m.name == "dac"))
                )  # fmt: skip
            found = {(r.row.column, r.row.piece, r.row.index) for r in run.results}
            warnings += [
                f"{n}@{sc}: truth row {k} {truth[k]} not found by OCR/layout" for k in sorted(set(truth) - found)
            ]
            # negatives: empty 4th substat slots and the gaps between two pieces (no icon there)
            p, lay = run.layout.pitch, run.layout
            for ci in range(len(lay.right_edges)):
                for pi, y_main in enumerate(lay.main_rows):
                    if (ci, pi, 0) in truth and (ci, pi, 4) not in truth:
                        rk = classify_at(run.img, run.matcher, run.centres[ci], y_main + lay.gap1 + 3 * p, p)
                        blanks.append(Blank(n, sc, f"empty sub4 c{ci}p{pi}", rk[0].score,
                                            rk[0].score - rk[1].score, rk[0].name))  # fmt: skip
                    if pi + 1 < len(lay.main_rows):
                        y = (y_main + lay.gap1 + 3 * p + lay.main_rows[pi + 1]) / 2
                        rk = classify_at(run.img, run.matcher, run.centres[ci], y, p)
                        blanks.append(Blank(n, sc, f"gap c{ci}p{pi}", rk[0].score, rk[0].score - rk[1].score,
                                            rk[0].name))  # fmt: skip
    return recs, blanks, timings, warnings


def contact_sheet(recs: list[Record], path: Path, per_row: int = 15) -> None:
    tiles = []
    for r in recs:
        tile = (
            cv2.resize(r.crop, (96, 96), interpolation=cv2.INTER_CUBIC)
            if r.crop.size
            else np.zeros((96, 96, 3), np.uint8)
        )
        tile = cv2.copyMakeBorder(tile, 30, 0, 0, 0, cv2.BORDER_CONSTANT, value=(0, 0, 0))
        ok = r.best == r.truth
        cv2.putText(tile, f"{r.text} [{r.truth}]", (2, 12), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 255, 0), 1)
        cv2.putText(tile, f"{r.best} {r.score:.2f}/{r.margin:.2f}", (2, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.38,
                    (0, 255, 255) if ok else (0, 0, 255), 1)  # fmt: skip
        tiles.append(tile)
    blank = np.zeros_like(tiles[0])
    rows = [np.hstack((tiles[i : i + per_row] + [blank] * per_row)[:per_row]) for i in range(0, len(tiles), per_row)]
    cv2.imwrite(str(path), np.vstack(rows))


def template_sheet(path: Path) -> None:
    rows = []
    for sc in TEST_SCALES:
        for n in CAPTURES:
            img = scaled_image(n, sc)
            tpl = extract_templates(img, label_column(ocr_lines(n, sc, img)))
            tiles = []
            for k in NAMES:
                t = tpl[k]
                c = cv2.resize(t.crop, (64, 64), interpolation=cv2.INTER_NEAREST)
                m = cv2.resize(t.mask * 255, (64, 64), interpolation=cv2.INTER_NEAREST)
                tiles.append(np.hstack([c, cv2.cvtColor(m, cv2.COLOR_GRAY2BGR), np.zeros((64, 4, 3), np.uint8)]))
            rows.append(np.hstack(tiles))
    cv2.imwrite(str(path), np.vstack(rows))


def pct(values, qs=(0, 5, 25, 50)) -> str:
    v = np.asarray(values, dtype=float)
    if v.size == 0:
        return "-"
    return " ".join(f"p{q}={np.percentile(v, q):.3f}" for q in qs)


def tally(recs: list[Record], min_score: float, min_margin: float) -> tuple[int, int, int, int]:
    """(correct, wrong, abstained, total) under the margin rule. A DAC winner counts as wrong unless abstained."""
    good = wrong = abst = 0
    for r in recs:
        if r.score < min_score or r.margin < min_margin:
            abst += 1
        elif r.best == r.truth:
            good += 1
        else:
            wrong += 1
    return good, wrong, abst, len(recs)


THRESHOLDS = {
    "grad2": (0.45, 0.15),
    "gradS": (0.55, 0.10),
    "hog": (0.60, 0.05),
    "mask": (0.60, 0.03),
    "grad2+hog": (0.50, 0.10),
}
"""(min score, min margin) per method - provisional, set from the distributions printed by `report`."""


def report(method: str, recs, blanks, timings, warnings) -> None:
    min_score, min_margin = THRESHOLDS.get(method, (0.0, 0.0))
    print(f"\n######## {method}  (margin rule: score >= {min_score}, margin >= {min_margin})")
    for w in sorted(set(warnings)):
        print("  warning:", w)
    for sc in sorted({r.scale for r in recs}):
        rs = [r for r in recs if r.scale == sc]
        ok = [r for r in rs if r.best == r.truth]
        g, w, a, t = tally(rs, min_score, min_margin)
        unknown = sum(1 for r in rs if r.impostor_score >= min_score and r.impostor_margin >= min_margin)
        bl = [b for b in blanks if b.scale == sc]
        blank_acc = sum(1 for b in bl if b.score >= min_score and b.margin >= min_margin)
        print(f"== scale {sc}: top-1 {len(ok)}/{len(rs)} | margin rule: correct {g}, wrong {w}, abstain {a} / {t}"
              f" | unknown-icon accepted {unknown}/{len(rs)} | blank accepted {blank_acc}/{len(bl)}")  # fmt: skip
        for r in rs:
            if r.best != r.truth:
                rule = "ABSTAINED by rule" if r.score < min_score or r.margin < min_margin else "ACCEPTED"
                print(f"   TOP1-WRONG ({rule}) {r.capture} {r.key} {r.text!r} truth={r.truth} got={r.best} "
                      f"{r.score:.3f} 2nd={r.second} {r.second_score:.3f}")  # fmt: skip
            elif r.score < min_score or r.margin < min_margin:
                print(f"   ABSTAIN {r.capture} {r.key} {r.text!r} truth={r.truth} {r.score:.3f} "
                      f"2nd={r.second} {r.second_score:.3f}")  # fmt: skip
        print("   genuine score   ", pct([r.score for r in ok]),
              "| main", pct([r.score for r in ok if r.is_main], (0,)),
              "sub", pct([r.score for r in ok if not r.is_main], (0,)))  # fmt: skip
        print("   genuine margin  ", pct([r.margin for r in ok]))
        print("   impostor score  ", pct([r.impostor_score for r in rs], (50, 95, 100)), "(true template removed)")
        print("   impostor margin ", pct([r.impostor_margin for r in rs], (50, 95, 100)))
        print(
            "   blank score     ",
            pct([b.score for b in bl], (50, 100)),
            "margin",
            pct([b.margin for b in bl], (50, 100)),
        )
        print("   DAC template: rank-1 on", sum(r.best == "dac" for r in rs), "rows; best DAC score",
              f"{max(r.dac_score for r in rs):.3f}")  # fmt: skip
    print(f"   time per capture (OCR cached): median {np.median(timings):.2f}s max {max(timings):.2f}s")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--methods", nargs="+", default=["grad2", "gradS", "hog", "mask", "grad2+hog"])
    ap.add_argument("--perturb", nargs="+", default=["none"], choices=sorted(PERTURBATIONS))
    ap.add_argument("--scales", nargs="+", type=float, default=list(TEST_SCALES))
    ap.add_argument("--sheets", action="store_true", help="write contact sheets to the scratch dir")
    ap.add_argument("--single-pass", action="store_true", help="no per-kind scale narrowing, shift 0.25 pitch")
    args = ap.parse_args()
    if args.single_pass:
        global NARROW, SHIFT
        NARROW, SHIFT = False, 0.25
    OUT.mkdir(parents=True, exist_ok=True)
    if args.sheets:
        template_sheet(OUT / "templates.png")
    for pert in args.perturb:
        for method in args.methods:
            recs, blanks, timings, warnings = evaluate(method, tuple(args.scales), perturb=pert)
            print(f"\n[perturbation: {pert}]", end="")
            report(method, recs, blanks, timings, warnings)
            if args.sheets:
                for sc in args.scales:
                    contact_sheet([r for r in recs if r.scale == sc], OUT / f"sheet_{method}_{pert}_{sc:.2f}.png")


if __name__ == "__main__":
    sys.exit(main())
