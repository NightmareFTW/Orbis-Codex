"""Spike M7 / sets: read the gear SET of each piece and the active-set icons from the user's captures.

Throwaway experiment (CLAUDE.md: spikes are never imported by src/). Reads the user's own Hero Info / Equipment
captures (git-ignored) and the 24 official Stove set icons (downloaded once, politely, to the scratchpad).

Method (nothing is positioned in pixels of one resolution; every size derives from an OCR line height):
1. OCR (RapidOcrReader, cached per capture+scale) gives the anchors:
   - Hero Info gear grid: the "Average Equipment Score" line -> region below/right of it;
   - CP row: the "124,427"-like number in the left part -> a strip to its right, bounded by the stat value column;
   - Equipment screen set list: lines ending in "Set" -> a box left of the text (the text is the truth!).
2. Localisation by the badge FRAME: every set icon has the same gold shield rim, so a rim-only template
   (mean of the 24 icons, ring mask) is searched with masked ZNCC on grey levels over a range of heights that is a
   multiple of the anchor line height. Peaks -> NMS -> best common scale.
3. Classification: each badge crop is compared with each of the 24 Stove icons (full alpha mask, grey ZNCC, small
   scale/shift search). Grey levels make the comparison independent of the red/blue fill, and the fill colour
   (red vs blue, measured inside the rim) is checked separately against the Stove icon's fill as a consistency vote.
4. Decision: accept the best set only if score >= SCORE_MIN and margin (best - second) >= MARGIN_MIN, else REVIEW
   (golden rule: never silently pick a near-miss).
5. Consistency: per-set piece counts (catalog `pieces`, MECH-GEAR-05 `community`) -> completed sets; they must equal
   the active-set icons next to CP (Hero Info) or the OCR'd set names (Equipment screen).

Usage:
    E7AC_HOME=.../m7/home uv run python spikes/m7_sets.py            # all captures, scales 0.64 1.0 1.28
    uv run python spikes/m7_sets.py --scales 1.0 --only heroinfo_haru
    uv run python spikes/m7_sets.py --frames            # frame-detection margins (badge vs best non-badge peak)
    uv run python spikes/m7_sets.py --stress 0.5 1.0    # all 24 Stove icons painted over the real badges
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from e7ac.vision.image import load_image
from e7ac.vision.ocr import RapidOcrReader, TextLine

REPO = Path(__file__).resolve().parents[1]
SHOTS = REPO / "fixtures" / "screenshots"
M7 = Path("/tmp/claude-0/-home-user-Orbis-Codex/d44236ee-bada-567e-a707-c7890345df7b/scratchpad/m7")
ICONS = M7 / "stove_sets"
OUT = M7 / "sets"
CATALOG = M7 / "home" / "e7ac.sqlite3"

HEROINFO = ["heroinfo_haru", "heroinfo_lots", "heroinfo_ainz", "heroinfo_straze", "heroinfo_politis", "heroinfo_charles"]
EQUIP = ["equip_renoa", "equip_haru", "equip_straze"]
SLOTS_LEFT = ["weapon", "helmet", "armor"]
SLOTS_RIGHT = ["necklace", "ring", "boots"]

# Spike-tuned thresholds (see report); production must re-derive them on more captures.
FRAME_MIN = 0.62  # rim-template ZNCC for a badge candidate (real >= 0.69, best non-badge <= 0.54 on 18 runs)
SCORE_MIN = 0.70  # best full-icon ZNCC to accept a set
MARGIN_MIN = 0.12  # best - second best to accept a set

# Badge height searched as a multiple of the anchor OCR line height (broad on purpose: the exact ratio is found).
RATIO_RANGE = (0.55, 1.75)
RATIO_STEPS = 31


# --------------------------------------------------------------------------------------------- reference icons
@dataclass
class Icon:
    code: str
    name: str
    pieces: int
    bgra: Any  # cropped to the alpha bbox
    fill: str  # "red" | "blue" measured on the Stove icon


def _fill_colour(bgr: Any, mask: Any) -> tuple[str, float]:
    """Dominant fill inside the rim: red vs blue by hue votes over saturated, not-bright pixels."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    sel = (mask > 0) & (s > 90) & (v > 35) & (v < 200)
    red = int((sel & ((h < 8) | (h > 165))).sum())
    blue = int((sel & (h > 100) & (h < 135)).sum())
    tot = red + blue
    if tot == 0:
        return "unknown", 0.0
    return ("red", red / tot) if red >= blue else ("blue", blue / tot)


def _inner_mask(alpha: Any, frac: float) -> Any:
    m = (alpha > 128).astype(np.uint8)
    k = max(1, round(frac * m.shape[1]))
    return cv2.erode(m, np.ones((k, k), np.uint8))


def load_icons() -> dict[str, Icon]:
    con = sqlite3.connect(CATALOG)
    rows = con.execute("select entity_id, name, data_json from catalog_entity where entity_type='set'").fetchall()
    icons: dict[str, Icon] = {}
    for code, name, data in rows:
        pieces = json.loads(data)["fields"]["pieces"]["value"]
        im = cv2.imread(str(ICONS / f"{code}.png"), cv2.IMREAD_UNCHANGED)
        if im is None:
            raise SystemExit(f"missing Stove icon {code}.png in {ICONS} (download once, politely)")
        ys, xs = np.nonzero(im[..., 3] > 8)
        im = im[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
        fill, _ = _fill_colour(im[..., :3], _inner_mask(im[..., 3], 0.18))
        icons[code] = Icon(code, name, int(pieces), im, fill)
    return icons


def rim_template(icons: dict[str, Icon]) -> tuple[Any, Any]:
    """Grey mean of all icons (same size) + a ring mask (outer rim + a band of fill just inside it)."""
    ref = next(iter(icons.values())).bgra
    hh, ww = ref.shape[:2]
    acc = np.zeros((hh, ww), np.float32)
    for ic in icons.values():
        im = cv2.resize(ic.bgra, (ww, hh), interpolation=cv2.INTER_AREA)
        acc += cv2.cvtColor(im[..., :3], cv2.COLOR_BGR2GRAY).astype(np.float32)
    acc /= len(icons)
    alpha = ref[..., 3]
    outer = (alpha > 128).astype(np.uint8)
    inner = _inner_mask(alpha, 0.30)
    ring = (outer - inner).astype(np.uint8)
    return acc, ring


# --------------------------------------------------------------------------------------------- matching helpers
def _resize_tpl(gray: Any, mask: Any, height: float) -> tuple[Any, Any]:
    s = height / gray.shape[0]
    g = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    m = cv2.resize(mask.astype(np.float32), (g.shape[1], g.shape[0]), interpolation=cv2.INTER_AREA)
    return g.astype(np.float32), (m > 0.5).astype(np.float32)


def _match(img: Any, tpl: Any, mask: Any) -> Any:
    if img.shape[0] < tpl.shape[0] or img.shape[1] < tpl.shape[1]:
        return np.full((1, 1), -1.0, np.float32)
    r = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED, mask=mask)
    r[~np.isfinite(r)] = -1.0  # masked ZNCC is undefined on flat patches (zero variance)
    return np.clip(r, -1.0, 1.0)


@dataclass
class Peak:
    score: float
    x: int  # top-left in full image
    y: int
    w: int
    h: int

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


def find_frames(gray: Any, roi: tuple[int, int, int, int], h_ref: float, rim: tuple[Any, Any],
                max_peaks: int, ratio_range: tuple[float, float] = RATIO_RANGE) -> tuple[list[Peak], float]:
    """Badge frames inside roi=(x0,y0,x1,y1): best common height (the one whose top peaks score highest) + peaks."""
    x0, y0, x1, y1 = roi
    sub = gray[y0:y1, x0:x1]
    best: tuple[float, list[Peak], float] = (-9.0, [], 0.0)
    for ratio in np.linspace(*ratio_range, RATIO_STEPS):
        height = ratio * h_ref
        if height < 12:
            continue
        tpl, mask = _resize_tpl(rim[0], rim[1], height)
        r = _match(sub, tpl, mask)
        peaks = _nms(r, tpl.shape[1], tpl.shape[0], max_peaks, FRAME_MIN)
        peaks = [Peak(s, px + x0, py + y0, tpl.shape[1], tpl.shape[0]) for s, px, py in peaks]
        # Score of a scale = mean of the top-k peaks (k = expected count, missing peaks count as FRAME_MIN-0.2).
        top = sorted((p.score for p in peaks), reverse=True)[:max_peaks]
        quality = float(np.mean(top + [FRAME_MIN - 0.2] * (max_peaks - len(top)))) if max_peaks else 0.0
        if quality > best[0]:
            best = (quality, peaks, height)
    return best[1], best[2]


def _nms(r: Any, w: int, h: int, k: int, thr: float) -> list[tuple[float, int, int]]:
    r = r.copy()
    out = []
    for _ in range(k * 3):
        y, x = np.unravel_index(int(r.argmax()), r.shape)
        s = float(r[y, x])
        if s < thr:
            break
        out.append((s, int(x), int(y)))
        cv2.rectangle(r, (int(x - 0.8 * w), int(y - 0.8 * h)), (int(x + 0.8 * w), int(y + 0.8 * h)), -9.0, -1)
        if len(out) >= k:
            break
    return out


@dataclass
class SetMatch:
    code: str | None  # None = REVIEW (abstained)
    best: str
    score: float
    second: str
    margin: float
    fill: str
    fill_ok: bool
    ranking: list[tuple[str, float]] = field(default_factory=list)
    peak: Peak | None = None


def classify(img: Any, gray: Any, peak: Peak, icons: dict[str, Icon]) -> SetMatch:
    """Full-icon grey ZNCC vs every Stove icon, +-8% height and +-12% shift around the detected frame."""
    pad_x, pad_y = round(0.12 * peak.w) + 2, round(0.12 * peak.h) + 2
    ya, yb = max(0, peak.y - pad_y), min(gray.shape[0], peak.y + peak.h + pad_y)
    xa, xb = max(0, peak.x - pad_x), min(gray.shape[1], peak.x + peak.w + pad_x)
    crop = gray[ya:yb, xa:xb]
    scores: dict[str, float] = {}
    for code, ic in icons.items():
        g = cv2.cvtColor(ic.bgra[..., :3], cv2.COLOR_BGR2GRAY).astype(np.float32)
        m = (ic.bgra[..., 3] > 128).astype(np.float32)
        best = -1.0
        for f in (0.92, 0.96, 1.0, 1.04, 1.08):
            tpl, mask = _resize_tpl(g, m, peak.h * f)
            best = max(best, float(_match(crop, tpl, mask).max()))
        scores[code] = best
    ranking = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    (c1, s1), (c2, s2) = ranking[0], ranking[1]
    badge = img[peak.y : peak.y + peak.h, peak.x : peak.x + peak.w]
    ring_inner = cv2.resize(_inner_mask(icons[c1].bgra[..., 3], 0.18), (badge.shape[1], badge.shape[0]),
                            interpolation=cv2.INTER_NEAREST)
    fill, _ = _fill_colour(badge, ring_inner)
    fill_ok = fill == icons[c1].fill
    accepted = s1 >= SCORE_MIN and (s1 - s2) >= MARGIN_MIN and fill_ok
    return SetMatch(c1 if accepted else None, c1, s1, c2, s1 - s2, fill, fill_ok, ranking[:5], peak)


# --------------------------------------------------------------------------------------------- OCR + anchors
_reader = RapidOcrReader()


def ocr(name: str, scale: float, img: Any) -> list[TextLine]:
    cache = OUT / "ocr_cache" / f"{name}_{scale:.2f}.json"
    from e7ac.vision.ocr import Box, Word

    if cache.exists():
        data = json.loads(cache.read_text())
        return [TextLine(d["text"], d["score"], Box(*d["box"]),
                         tuple(Word(w[0], w[1], Box(*w[2])) for w in d["words"])) for d in data]
    lines = _reader.read(img)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps([
        {"text": l.text, "score": l.score, "box": [l.box.x0, l.box.y0, l.box.x1, l.box.y1],
         "words": [[w.text, w.score, [w.box.x0, w.box.y0, w.box.x1, w.box.y1]] for w in l.words]} for l in lines
    ]))
    return lines


CP_RE = re.compile(r"^\d{1,3}(?:,\d{3})+$")


def cp_line(lines: list[TextLine], width: int) -> TextLine | None:
    cands = [l for l in lines if CP_RE.match(l.text.replace(" ", "")) and l.box.x1 < 0.35 * width]
    return max(cands, key=lambda l: l.box.height, default=None)


def value_column_right(lines: list[TextLine], width: int) -> float | None:
    """Right edge of the stat value column (numbers right of the stat labels in the left panel)."""
    labels = [l for l in lines if l.text.strip() in ("Attack", "Defense", "Health", "Speed") and l.box.x1 < 0.4 * width]
    xs = []
    for lab in labels:
        row = [l for l in lines if abs(l.box.cy - lab.box.cy) < 0.5 * lab.box.height and l.box.x0 > lab.box.x1
               and re.match(r"^[\d.,%]+$", l.text) and l.box.x1 < 0.45 * width]
        if row:
            xs.append(min(row, key=lambda l: l.box.x0).box.x1)
    return float(np.median(xs)) if xs else None


# --------------------------------------------------------------------------------------------- per capture
def _slot_order(slot: str) -> int:
    order = SLOTS_LEFT + SLOTS_RIGHT
    return order.index(slot) if slot in order else 10 + int(slot[1:])


@dataclass
class Result:
    name: str
    scale: float
    pieces: dict[str, SetMatch] = field(default_factory=dict)
    active: list[SetMatch] = field(default_factory=list)
    text_sets: list[str] = field(default_factory=list)  # equipment screen: OCR'd set names -> codes
    notes: list[str] = field(default_factory=list)
    badge_h: float = 0.0
    active_h: float = 0.0


def assign_slots(peaks: list[Peak], notes: list[str]) -> dict[str, Peak]:
    """2 columns x 3 rows. Columns split at the largest x gap (> 2 badge widths); rows = y clusters (tolerance half a
    badge). Rows are only numbered when 3 row clusters exist; otherwise the slot is ambiguous and reported (in
    production the gear-card parser gives the slot, this is only the spike's stand-in)."""
    if not peaks:
        return {}
    w, h = peaks[0].w, peaks[0].h
    xs = sorted(p.cx for p in peaks)
    gaps = [(xs[i + 1] - xs[i], i) for i in range(len(xs) - 1)]
    if gaps and max(gaps)[0] > 2 * w:
        split = (xs[max(gaps)[1]] + xs[max(gaps)[1] + 1]) / 2
        cols = ([p for p in peaks if p.cx < split], [p for p in peaks if p.cx >= split])
    else:
        notes.append("only one badge column found -> column unknown, slots not assigned")
        return {f"?{i}": p for i, p in enumerate(sorted(peaks, key=lambda p: p.cy))}
    rows: list[list[Peak]] = []
    for p in sorted(peaks, key=lambda p: p.cy):
        if rows and abs(np.mean([q.cy for q in rows[-1]]) - p.cy) < 0.5 * h:
            rows[-1].append(p)
        else:
            rows.append([p])
    out: dict[str, Peak] = {}
    if len(rows) != 3:
        notes.append(f"{len(rows)} badge rows (expected 3) -> rows not numbered")
        return {f"?{i}": p for i, p in enumerate(sorted(peaks, key=lambda p: (p.cy, p.cx)))}
    for r, row in enumerate(rows):
        for p in row:
            slot = (SLOTS_LEFT if p in cols[0] else SLOTS_RIGHT)[r]
            if slot in out:
                notes.append(f"two badges for slot {slot}")
                continue
            out[slot] = p
    return out


def run_heroinfo(name: str, scale: float, icons: dict[str, Icon], rim: tuple[Any, Any]) -> Result:
    img = load_image(SHOTS / f"{name}.webp")
    if scale != 1.0:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    H, W = gray.shape
    lines = ocr(name, scale, img)
    res = Result(name, scale)

    anchor = next((l for l in lines if "equipment score" in l.text.lower()), None)
    if anchor is None:
        res.notes.append("no 'Average Equipment Score' anchor -> no gear section (no gear equipped?)")
    else:
        h = anchor.box.height
        roi = (max(0, int(anchor.box.x0 - 2 * h)), int(anchor.box.y1), W, H)
        peaks, bh = find_frames(gray, roi, h, rim, max_peaks=6)
        res.badge_h = bh
        slots = assign_slots(peaks, res.notes)
        for slot, peak in sorted(slots.items(), key=lambda kv: _slot_order(kv[0])):
            res.pieces[slot] = classify(img, gray, peak, icons)

    cp = cp_line(lines, W)
    vr = value_column_right(lines, W)
    if cp is None or vr is None:
        res.notes.append(f"CP anchor missing (cp={cp is not None}, value column={vr is not None})")
    else:
        h = cp.box.height
        roi = (int(cp.box.x1), max(0, int(cp.box.y0 - 0.5 * h)), int(vr + 0.5 * h), int(cp.box.y1 + 0.5 * h))
        peaks, ah = find_frames(gray, roi, h, rim, max_peaks=3, ratio_range=(0.4, 1.3))
        res.active_h = ah
        for p in sorted(peaks, key=lambda p: p.cx):
            res.active.append(classify(img, gray, p, icons))
    return res


SET_LINE_RE = re.compile(r"^(.*\S)\s*Set$")


def run_equip(name: str, scale: float, icons: dict[str, Icon], rim: tuple[Any, Any]) -> Result:
    img = load_image(SHOTS / f"{name}.webp")
    if scale != 1.0:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    H, W = gray.shape
    lines = ocr(name, scale, img)
    res = Result(name, scale)
    by_name = {ic.name.lower(): code for code, ic in icons.items()}
    set_lines = [l for l in lines if SET_LINE_RE.match(l.text.strip()) and l.box.x1 < 0.4 * W]
    for l in sorted(set_lines, key=lambda l: l.box.y0):
        code = by_name.get(l.text.strip().lower())
        res.text_sets.append(code or f"?{l.text}")
        h = l.box.height
        roi = (max(0, int(l.box.x0 - 2.5 * h)), max(0, int(l.box.y0 - 0.6 * h)), int(l.box.x0), int(l.box.y1 + 0.6 * h))
        peaks, ah = find_frames(gray, roi, h, rim, max_peaks=1, ratio_range=(0.5, 1.6))
        res.active_h = ah
        if peaks:
            res.active.append(classify(img, gray, peaks[0], icons))
        else:
            res.notes.append(f"no icon left of {l.text!r}")
    # Gear badges: equipment cards sit between the stat value column and the hero list; search the band between the
    # CP line and the 'Manage Equipment' button for 6 frames (heights relative to the '+15'-free CP line height).
    cp = cp_line(lines, W)
    manage = next((l for l in lines if l.text.strip().lower().startswith("manage")), None)
    vr = value_column_right(lines, W)
    if cp and manage and vr:
        lv = next((l for l in lines if l.text.replace(" ", "").lower().startswith("lv.") and l.box.x1 < 0.4 * W), None)
        top = int(lv.box.y1) if lv else int(cp.box.y0 - 6 * cp.box.height)
        # right bound: the hero list = alphabetic lines in the right part of the gear band (the selected hero's name
        # is the left-most one); 0.75*W only as a last resort (documented fragility).
        # First choice: the list entry whose text equals the screen title (the selected hero, e.g. "Renoa").
        title = min((l for l in lines if l.box.y1 < 0.12 * H and l.box.x1 < 0.25 * W
                     and re.search(r"[A-Za-z]{3,}", l.text)), key=lambda l: l.box.x0, default=None)
        names = [l for l in lines if l.box.x0 > 0.6 * W and top < l.box.cy < manage.box.y0
                 and re.search(r"[A-Za-z]{3,}", l.text)]
        sel = [l for l in names if title and l.text.strip() == title.text.strip()]
        if sel:
            right = int(sel[0].box.x0 - 0.5 * cp.box.height)
        else:
            res.notes.append("selected hero not found in the list; right bound from the left-most list name")
            right = int(min(l.box.x0 for l in names) - 0.5 * cp.box.height) if names else int(0.75 * W)
        roi = (int(vr + 0.5 * cp.box.height), top, right, int(manage.box.y0))
        peaks, bh = find_frames(gray, roi, cp.box.height, rim, max_peaks=6, ratio_range=(0.45, 1.3))
        res.badge_h = bh
        slots = assign_slots(peaks, res.notes)
        for slot, peak in sorted(slots.items(), key=lambda kv: _slot_order(kv[0])):
            res.pieces[slot] = classify(img, gray, peak, icons)
    else:
        res.notes.append("equip gear anchors missing")
    return res


# --------------------------------------------------------------------------------------------- consistency
def completed_sets(pieces: dict[str, SetMatch], icons: dict[str, Icon]) -> tuple[Counter[str], list[str]]:
    counts = Counter(m.code for m in pieces.values() if m.code)
    done: Counter[str] = Counter()
    broken = []
    for code, n in counts.items():
        k = n // icons[code].pieces
        if k:
            done[code] = k
        if n % icons[code].pieces:
            broken.append(f"{code} x{n % icons[code].pieces} unfinished")
    return done, broken


def check(res: Result, icons: dict[str, Icon]) -> list[str]:
    msgs = []
    done, broken = completed_sets(res.pieces, icons)
    review = [s for s, m in res.pieces.items() if m.code is None]
    if review:
        msgs.append(f"REVIEW pieces: {review}")
    if broken:
        msgs.append("unfinished sets: " + ", ".join(broken))
    active = Counter(m.code for m in res.active if m.code)
    if res.name.startswith("equip"):
        txt = Counter(c for c in res.text_sets if not c.startswith("?"))
        msgs.append(f"text sets {dict(txt)} vs icons {dict(active)}: {'OK' if txt == active else 'MISMATCH'}")
        if res.pieces:
            msgs.append(f"pieces->completed {dict(done)} vs text {dict(txt)}: {'OK' if done == txt else 'MISMATCH'}")
    elif res.pieces or res.active:
        msgs.append(f"pieces->completed {dict(done)} vs CP icons {dict(active)}: {'OK' if done == active else 'MISMATCH'}")
    return msgs


# --------------------------------------------------------------------------------------------- contact sheets
def _icon_bgr(ic: Icon, size: int) -> Any:
    im = cv2.resize(ic.bgra, (size, size), interpolation=cv2.INTER_AREA)
    a = im[..., 3:4].astype(np.float32) / 255
    return (im[..., :3] * a + 40 * (1 - a)).astype(np.uint8)


def sheet(results: list[Result], img_cache: dict[tuple[str, float], Any], icons: dict[str, Icon], path: Path) -> None:
    S = 72
    rows = []
    for res in results:
        img = img_cache[(res.name, res.scale)]
        items = [(slot, m) for slot, m in res.pieces.items()] + [(f"act{i}", m) for i, m in enumerate(res.active)]
        for label, m in items:
            p = m.peak
            pad = round(0.15 * p.w)
            crop = img[max(0, p.y - pad) : p.y + p.h + pad, max(0, p.x - pad) : p.x + p.w + pad]
            crop = cv2.resize(crop, (S, S), interpolation=cv2.INTER_CUBIC)
            tile = np.full((S, S * 3 + 330, 3), 25, np.uint8)
            tile[:, :S] = crop
            tile[:, S + 4 : 2 * S + 4] = _icon_bgr(icons[m.best], S)
            tile[:, 2 * S + 8 : 3 * S + 8] = _icon_bgr(icons[m.second], S)
            colour = (80, 220, 80) if m.code else (60, 60, 255)
            txt1 = f"{res.name[9:] if res.name.startswith('heroinfo') else res.name} x{res.scale} {label}"
            txt2 = f"{m.best[4:]} {m.score:.2f} m{m.margin:.2f} {m.fill}{'' if m.fill_ok else '!'}"
            txt3 = f"2nd {m.second[4:]}  {'OK' if m.code else 'REVIEW'}"
            for i, t in enumerate((txt1, txt2, txt3)):
                cv2.putText(tile, t, (3 * S + 14, 20 + 22 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 1, cv2.LINE_AA)
            rows.append(tile)
    if not rows:
        return
    cols = 2
    while len(rows) % cols:
        rows.append(np.zeros_like(rows[0]))
    grid = np.vstack([np.hstack(rows[i : i + cols]) for i in range(0, len(rows), cols)])
    cv2.imwrite(str(path), grid)



# --------------------------------------------------------------------------------------------- stress tests
def _degrade(patch: Any, rng: Any, strength: float) -> Any:
    """Emulate the game's own rendering + capture: blur, gamma, hue jitter, noise, lossy re-encode."""
    out = cv2.GaussianBlur(patch, (0, 0), 0.35 + 0.5 * strength)
    gamma = 1.0 + rng.uniform(-0.15, 0.15) * strength
    out = np.clip(255.0 * (out / 255.0) ** gamma, 0, 255).astype(np.uint8)
    hsv = cv2.cvtColor(out, cv2.COLOR_BGR2HSV).astype(np.int16)
    hsv[..., 0] = (hsv[..., 0] + int(rng.integers(-3, 4) * strength)) % 180
    out = cv2.cvtColor(hsv.clip(0, 255).astype(np.uint8), cv2.COLOR_HSV2BGR)
    out = np.clip(out + rng.normal(0, 4 * strength, out.shape), 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".webp", out, [cv2.IMWRITE_WEBP_QUALITY, int(85 - 25 * strength)])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def stress_insert(icons: dict[str, Icon], rim: tuple[Any, Any], scales: list[float], strength: float) -> None:
    """Paint every Stove icon (alpha-blended, degraded) over each real badge, re-detect the frame locally, classify.
    Measures the 15 sets that never appear in the captures. Optimistic: the template IS the source art."""
    rng = np.random.default_rng(7)
    stats: dict[str, list[tuple[bool, float, float, str, bool]]] = {c: [] for c in icons}
    for name in HEROINFO[:5]:
        for scale in scales:
            img0 = load_image(SHOTS / f"{name}.webp")
            if scale != 1.0:
                img0 = cv2.resize(img0, None, fx=scale, fy=scale,
                                  interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
            res = run_heroinfo(name, scale, icons, rim)
            for slot, m in res.pieces.items():
                p = m.peak
                for code, ic in icons.items():
                    img = img0.copy()
                    t = cv2.resize(ic.bgra, (p.w, p.h), interpolation=cv2.INTER_AREA)
                    a = t[..., 3:4].astype(np.float32) / 255
                    reg = img[p.y : p.y + p.h, p.x : p.x + p.w].astype(np.float32)
                    img[p.y : p.y + p.h, p.x : p.x + p.w] = (t[..., :3] * a + reg * (1 - a)).astype(np.uint8)
                    pad = p.h
                    ya, xa = max(0, p.y - pad), max(0, p.x - pad)
                    yb, xb = min(img.shape[0], p.y + p.h + pad), min(img.shape[1], p.x + p.w + pad)
                    img[ya:yb, xa:xb] = _degrade(img[ya:yb, xa:xb], rng, strength)
                    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
                    # local re-detection: ROI = 3 badge sizes around, same anchor-relative ratio search
                    peaks, _ = find_frames(gray, (xa, ya, xb, yb), res.badge_h, rim, max_peaks=1,
                                           ratio_range=(0.85, 1.15))
                    if not peaks:
                        stats[code].append((False, 0.0, 0.0, "no-frame", False))
                        continue
                    cm = classify(img, gray, peaks[0], icons)
                    stats[code].append((cm.code == code, cm.score, cm.margin, cm.second if cm.best == code else
                                        f"WRONG:{cm.best}", cm.code is None))
    print(f"\n== synthetic insertion, degradation strength {strength}")
    tot = ok = wrong = abst = 0
    for code, rows in stats.items():
        n = len(rows)
        c = sum(r[0] for r in rows)
        ab = sum(r[4] for r in rows)
        w = n - c - ab
        tot += n
        ok += c
        abst += ab
        wrong += w
        runner = Counter(r[3] for r in rows).most_common(2)
        print(f"   {code:14s} fill={icons[code].fill:4s} n={n:3d} ok={c:3d} review={ab:3d} wrong={w:3d} "
              f"score[min/med]={min(r[1] for r in rows):.2f}/{np.median([r[1] for r in rows]):.2f} "
              f"margin[min/med]={min(r[2] for r in rows):.2f}/{np.median([r[2] for r in rows]):.2f} runner-up={runner}")
    print(f"   TOTAL n={tot} correct={ok} review={abst} wrong={wrong}")


def frame_margins(icons: dict[str, Icon], rim: tuple[Any, Any], scales: list[float]) -> None:
    """How far the best NON-badge frame peak is from the weakest real badge (gear grid and CP row)."""
    print("\n== frame detection margins (weakest real badge vs best extra peak)")
    for name in HEROINFO:
        for scale in scales:
            img = load_image(SHOTS / f"{name}.webp")
            if scale != 1.0:
                img = cv2.resize(img, None, fx=scale, fy=scale,
                                 interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
            H, W = gray.shape
            lines = ocr(name, scale, img)
            out = []
            anchor = next((l for l in lines if "equipment score" in l.text.lower()), None)
            if anchor:
                h = anchor.box.height
                roi = (max(0, int(anchor.box.x0 - 2 * h)), int(anchor.box.y1), W, H)
                _, bh = find_frames(gray, roi, h, rim, max_peaks=6)
                tpl, mask = _resize_tpl(rim[0], rim[1], bh)
                r = _match(gray[roi[1]:roi[3], roi[0]:roi[2]], tpl, mask)
                allp = _nms(r, tpl.shape[1], tpl.shape[0], 9, -1.0)
                pk = [s for s, _, _ in allp]
                s7, x7, y7 = allp[6]
                junk = classify(img, gray, Peak(s7, x7 + roi[0], y7 + roi[1], tpl.shape[1], tpl.shape[0]), icons)
                out.append(f"gear: 6th={pk[5]:.2f} 7th={pk[6]:.2f} (7th classifies as {junk.best} {junk.score:.2f} "
                           f"m{junk.margin:.2f} fill_ok={junk.fill_ok} -> {junk.code or 'REVIEW'})")
            cp, vr = cp_line(lines, W), value_column_right(lines, W)
            if cp and vr:
                h = cp.box.height
                roi = (int(cp.box.x1), max(0, int(cp.box.y0 - 0.5 * h)), int(vr + 0.5 * h), int(cp.box.y1 + 0.5 * h))
                peaks, ah = find_frames(gray, roi, h, rim, max_peaks=3, ratio_range=(0.4, 1.3))
                tpl, mask = _resize_tpl(rim[0], rim[1], ah if ah else 0.67 * h)
                r = _match(gray[roi[1]:roi[3], roi[0]:roi[2]], tpl, mask)
                allp = _nms(r, tpl.shape[1], tpl.shape[0], 4, -1.0)
                pk = [s for s, _, _ in allp]
                s3, x3, y3 = allp[len(peaks)] if len(allp) > len(peaks) else allp[-1]
                junk = classify(img, gray, Peak(s3, x3 + roi[0], y3 + roi[1], tpl.shape[1], tpl.shape[0]), icons)
                out.append(f"cp: peaks={[round(x, 2) for x in pk]} accepted={len(peaks)} (first rejected classifies "
                           f"as {junk.best} {junk.score:.2f} m{junk.margin:.2f} -> {junk.code or 'REVIEW'})")
            print(f"   {name} x{scale}: " + "; ".join(out))

# --------------------------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scales", nargs="+", type=float, default=[0.64, 1.0, 1.28])
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--stress", type=float, nargs="*", help="synthetic insertion at these degradation strengths")
    ap.add_argument("--frames", action="store_true", help="frame-detection margins")
    args = ap.parse_args()
    icons = load_icons()
    rim = rim_template(icons)
    if args.frames:
        frame_margins(icons, rim, args.scales)
        return 0
    if args.stress:
        for st in args.stress:
            stress_insert(icons, rim, args.scales, st)
        return 0
    names = [n for n in HEROINFO + EQUIP if not args.only or n in args.only]
    summary: dict[str, Any] = {}
    for name in names:
        results, cache = [], {}
        for scale in args.scales:
            t0 = time.time()
            res = (run_equip if name.startswith("equip") else run_heroinfo)(name, scale, icons, rim)
            img = load_image(SHOTS / f"{name}.webp")
            if scale != 1.0:
                img = cv2.resize(img, None, fx=scale, fy=scale,
                                 interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
            cache[(name, scale)] = img
            results.append(res)
            print(f"== {name} x{scale}  badge_h={res.badge_h:.1f} active_h={res.active_h:.1f}  ({time.time() - t0:.1f}s)")
            for slot, m in res.pieces.items():
                print(f"   {slot:8s} {m.code or 'REVIEW':14s} best={m.best:14s} {m.score:.3f} 2nd={m.second:14s} "
                      f"margin={m.margin:.3f} fill={m.fill}{'' if m.fill_ok else '(!)'} frame={m.peak.score:.2f}")
            for i, m in enumerate(res.active):
                print(f"   active{i}  {m.code or 'REVIEW':14s} best={m.best:14s} {m.score:.3f} 2nd={m.second:14s} "
                      f"margin={m.margin:.3f} fill={m.fill}{'' if m.fill_ok else '(!)'} frame={m.peak.score:.2f}")
            if res.text_sets:
                print(f"   text sets: {res.text_sets}")
            for n in res.notes + check(res, icons):
                print(f"   - {n}")
            summary[f"{name}@{scale}"] = {
                "pieces": {s: {"set": m.code, "best": m.best, "score": round(m.score, 3), "second": m.second,
                               "margin": round(m.margin, 3), "fill": m.fill, "fill_ok": m.fill_ok}
                           for s, m in res.pieces.items()},
                "active": [{"set": m.code, "best": m.best, "score": round(m.score, 3), "margin": round(m.margin, 3)}
                           for m in res.active],
                "text_sets": res.text_sets,
                "checks": check(res, icons),
                "notes": res.notes,
            }
        sheet(results, cache, icons, OUT / f"sheet_{name}.png")
    (OUT / "results.json").write_text(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
