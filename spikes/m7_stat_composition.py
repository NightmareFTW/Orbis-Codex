"""Spike M7 / stat_composition: do the transcribed gear + catalog data reproduce the FINAL stats shown on Hero Info?

Throwaway experiment (CLAUDE.md: spikes are never imported by src/). Reads the user's own Hero Info / Equipment
captures (git-ignored), the eye-transcribed ground truth (scratchpad `truth.json`) and a catalog snapshot (SQLite).

Question: can "displayed final stats == composition of (base + gear + sets + artifact + self imprint + EE)" serve as an
independent cross-check of the M7 gear OCR, like MECH-STAT-06 (final - ▲ = base) does for the stats panel?

Model (no number below is invented; each one comes from the catalog or docs/MECHANICS.md, with its status):
- MECH-STAT-02 (`community`): ATK/DEF/HP = base + base * sum(% from gear, self imprint, static sets)
  + sum(flat from gear, artifact); SPD = base + flat + base * sum(speed-set %); CC/CD/EFF/ER/DAC additive.
- MECH-STAT-03 (`community`): base = Lv60 6-star awakened, catalog `base.*` (Fribbels; MECH-STAT-06 `verified` for
  Haru and Straze through the Equipment tab).
- MECH-GEAR-06 (`verified` magnitudes) + MECH-GEAR-05 (`community` piece counts): catalog `set.static_bonus` /
  `set.pieces`. The sets of each hero are NOT in the truth: every set hypothesis that fits <= 6 pieces is enumerated
  and the ones consistent with the residuals are reported (a hypothesis, never a fact).
- MECH-ART-01 (`verified`) +30 = 13 x +0; MECH-ART-02 (`community`) linear in the enhance level, one decimal:
  value(L) = v0 + (v30 - v0) * L / 30 with catalog `*_min` (= +0) and `*_max` (= +30).
- MECH-IMP-02/03: a self imprint adds its stat to the hero; a team imprint does not show on the hero.
- EE: stat type from catalog `ee.stat` (Fribbels, `community`); magnitude only from the screen (truth "12%"),
  catalog `ee.value` is `assumed` (NV-08) and is NOT used.
- MECH-STAT-05 (`verified`, 1 sample): Crit Chance display capped at 100%.
- Display rounding of flat stats: `unknown` in the docs -> measured here: floor of the exact total fits 16/16 flat
  stats (6 of them discriminate: round-half-up fails there); `--rounding either` keeps the lenient floor-or-round rule.

Findings on the 4 heroes (details in the agent report): with the sets read from the icons (spikes/m7_sets.py; text on
the Equipment tab for Haru/Straze) and Straze's EE, 36/36 displayed stats are reproduced at every scale; every
single-field gear OCR error is caught except Crit Chance values while CC is capped (Straze); combat-only sets are
invisible to the stats by construction.

Everything that scales: the displayed stats come from `e7ac.vision.hero_screen.parse_hero_screen` (anchored on the
stat labels) run on RapidOCR at image scales 0.64 / 1.0 / 1.28; the contact sheet crop is derived from the OCR'd
label column (row height units), never from pixel positions of one resolution.

Usage:
    E7AC_HOME=.../m7/home uv run python spikes/m7_stat_composition.py              # full report (floor rule)
    uv run python spikes/m7_stat_composition.py --rounding either --verbose        # lenient rule, list every miss
    uv run python spikes/m7_stat_composition.py --scales 1.0 --no-mutations --only heroinfo_haru
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from e7ac.domain.codes import Stat
from e7ac.vision.hero_screen import HeroScreenReading, ScreenError, parse_hero_screen
from e7ac.vision.image import load_image
from e7ac.vision.ocr import Box, RapidOcrReader, TextLine, Word

REPO = Path(__file__).resolve().parents[1]
SHOTS = REPO / "fixtures" / "screenshots"
M7 = Path("/tmp/claude-0/-home-user-Orbis-Codex/d44236ee-bada-567e-a707-c7890345df7b/scratchpad/m7")
TRUTH = M7 / "truth.json"
CATALOG = M7 / "home" / "e7ac.sqlite3"
SETS_SPIKE = M7 / "sets" / "results.json"  # output of spikes/m7_sets.py (icons; independent evidence, not truth)
OUT = M7 / "stat_composition"

# Heroes of the task (Politis skipped: Lv5 5-star, no Lv60 6-star base applies). Codes are matched exactly.
HEROES: dict[str, str] = {
    "heroinfo_haru": "c1192",
    "heroinfo_lots": "c6005",  # Lady of the Scales (name in truth; code resolved by exact catalog name, see main)
    "heroinfo_ainz": "c1155",
    "heroinfo_straze": "c1034",
}
EQUIP_TWINS = {"equip_haru": "heroinfo_haru", "equip_straze": "heroinfo_straze"}

ORDER = [
    Stat.ATK,
    Stat.DEF,
    Stat.HP,
    Stat.SPEED,
    Stat.CRIT_CHANCE,
    Stat.CRIT_DAMAGE,
    Stat.EFFECTIVENESS,
    Stat.EFFECT_RESISTANCE,
    Stat.DUAL_ATTACK,
]
FLAT = {Stat.ATK, Stat.DEF, Stat.HP, Stat.SPEED}
PCT_OF = {Stat.ATK_PERCENT: Stat.ATK, Stat.DEF_PERCENT: Stat.DEF, Stat.HP_PERCENT: Stat.HP}
SHORT = {
    Stat.ATK: "ATK",
    Stat.DEF: "DEF",
    Stat.HP: "HP",
    Stat.SPEED: "SPD",
    Stat.CRIT_CHANCE: "CC",
    Stat.CRIT_DAMAGE: "CD",
    Stat.EFFECTIVENESS: "EFF",
    Stat.EFFECT_RESISTANCE: "ER",
    Stat.DUAL_ATTACK: "DAC",
}
CRIT_CHANCE_CAP = 1.0  # MECH-STAT-05 (verified, 1 sample)

# Truth icon families -> stat codes (truth.json "_doc": % on atk/def/hp = the percent stat)
FAMILY_FLAT = {"atk": Stat.ATK, "def": Stat.DEF, "hp": Stat.HP, "spd": Stat.SPEED}
FAMILY_PCT = {
    "atk": Stat.ATK_PERCENT,
    "def": Stat.DEF_PERCENT,
    "hp": Stat.HP_PERCENT,
    "cc": Stat.CRIT_CHANCE,
    "cd": Stat.CRIT_DAMAGE,
    "eff": Stat.EFFECTIVENESS,
    "er": Stat.EFFECT_RESISTANCE,
}
FAMILIES = ["atk", "def", "hp", "spd", "cc", "cd", "eff", "er"]

# ---- tolerances (see `tolerance_note`) ----
RATE_TOL = 0.0005  # half of the display step 0.1% (rates as fractions)
ROUNDING = {"mode": "floor"}
"""Display rule for flat stats, `unknown` in the docs: "floor" = displayed == floor(exact); "either" = floor or
round-half-up accepted (residual window (-1, +0.5]). Set by --rounding."""


# ============================================================================ catalog


@dataclass(frozen=True)
class Fact:
    value: Any
    status: str
    sources: tuple[str, ...]


@dataclass
class Catalog:
    heroes: dict[str, dict[str, Fact]]
    hero_names: dict[str, list[str]]
    artifacts: dict[str, dict[str, Fact]]
    sets: dict[str, dict[str, Fact]]


def load_catalog(path: Path = CATALOG) -> Catalog:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    (snap,) = con.execute("select id from catalog_snapshot where is_current = 1").fetchone()
    out: dict[str, dict[str, dict[str, Fact]]] = defaultdict(dict)
    names: dict[str, list[str]] = defaultdict(list)
    for etype, eid, name, data in con.execute(
        "select entity_type, entity_id, name, data_json from catalog_entity where snapshot_id = ?", (snap,)
    ):
        fields = json.loads(data)["fields"]
        out[etype][eid] = {k: Fact(v["value"], v["status"], tuple(v["sources"])) for k, v in fields.items()}
        if etype == "hero":
            names[name].append(eid)
    con.close()
    return Catalog(out["hero"], names, out["artifact"], out["set"])


# ============================================================================ components


@dataclass(frozen=True)
class Item:
    """One additive contribution: stat code (Stat enum, % stats as *_rate), value (fraction for rates)."""

    stat: Stat
    value: float
    source: str  # e.g. "boots.sub3", "artifact", "imprint(self)", "set_speed", "ee"
    status: str  # provenance of the number (verified/community/assumed/unknown/truth/ocr)


def parse_gear_value(text: str, family: str) -> tuple[Stat, float]:
    """'2,765' + hp -> (HP, 2765); '13%' + hp -> (HP%, 0.13). Raises on impossible combos (no silent fix)."""
    t = text.replace(",", "").strip()
    pct = t.endswith("%")
    number = float(t.rstrip("%"))
    if pct:
        if family not in FAMILY_PCT:
            raise ValueError(f"{text!r}: a % value on a {family} icon")
        return FAMILY_PCT[family], round(number / 100, 6)
    if family not in FAMILY_FLAT:
        raise ValueError(f"{text!r}: a flat value on a {family} icon (rates always show %)")
    return FAMILY_FLAT[family], number


def gear_items(pieces: Sequence[dict[str, Any]]) -> list[Item]:
    items: list[Item] = []
    for piece in pieces:
        stat, value = parse_gear_value(*piece["main"])
        items.append(Item(stat, value, f"{piece['slot']}.main", "truth"))
        for i, (text, fam) in enumerate(piece["subs"], 1):
            stat, value = parse_gear_value(text, fam)
            items.append(Item(stat, value, f"{piece['slot']}.sub{i}", "truth"))
    return items


def artifact_items(cat: Catalog, code: str, enhance: int) -> tuple[list[Item], list[str]]:
    """MECH-ART-01/02: v(L) = v0 + (v30 - v0) * L / 30, one decimal; v30 must be 13 * v0 (else warning)."""
    art = cat.artifacts[code]
    items: list[Item] = []
    notes: list[str] = []
    for key, stat in (("atk", Stat.ATK), ("def", Stat.DEF), ("hp", Stat.HP)):
        lo, hi = art.get(f"{key}_min"), art.get(f"{key}_max")
        if lo is None or not lo.value:
            continue
        v0 = float(lo.value)
        if hi is None:
            notes.append(f"{code} {key}: no +30 value; using the 13x rule (MECH-ART-01) on +0")
            v30, st = 13 * v0, "community"
        else:
            v30 = float(hi.value)
            if abs(v30 - 13 * v0) > 1e-9:
                notes.append(f"{code} {key}: +30 {v30} != 13 x +0 {v0} (MECH-ART-01 broken)")
            st = "community" if "community" in (lo.status, hi.status) else lo.status
        value = round(v0 + (v30 - v0) * enhance / 30, 1)  # MECH-ART-02 linear, one decimal (community)
        items.append(Item(stat, value, f"artifact {code}+{enhance}", f"{st} (scaling MECH-ART-02 community)"))
    return items, notes


IMPRINT_RE = re.compile(r"^(?P<label>[A-Za-z ]+?)\s*\+\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<pct>%?)$")
IMPRINT_LABELS = {
    "Attack": Stat.ATK,
    "Defense": Stat.DEF,
    "Health": Stat.HP,
    "Speed": Stat.SPEED,
    "Critical Hit Chance": Stat.CRIT_CHANCE,
    "Critical Hit Damage": Stat.CRIT_DAMAGE,
    "Effectiveness": Stat.EFFECTIVENESS,
    "Effect Resistance": Stat.EFFECT_RESISTANCE,
}


def imprint_item(text: str) -> Item:
    m = IMPRINT_RE.match(text.strip())
    if m is None or m.group("label") not in IMPRINT_LABELS:
        raise ValueError(f"imprint text not understood: {text!r}")
    stat = IMPRINT_LABELS[m.group("label")]
    value = float(m.group("value"))
    if m.group("pct"):
        stat = {Stat.ATK: Stat.ATK_PERCENT, Stat.DEF: Stat.DEF_PERCENT, Stat.HP: Stat.HP_PERCENT}.get(stat, stat)
        value = round(value / 100, 6)
    return Item(stat, value, "imprint", "truth")


# ============================================================================ sets


@dataclass(frozen=True)
class SetHypothesis:
    """A multiset of sets (by code) that fits in 6 pieces; only its static stat vector matters for the stats."""

    codes: tuple[str, ...]

    def label(self) -> str:
        return "+".join(c.removeprefix("set_") for c in self.codes) or "(none)"


def set_items(cat: Catalog, codes: Iterable[str]) -> list[Item]:
    items: list[Item] = []
    for code in codes:
        fact = cat.sets[code].get("static_bonus")
        for bonus in fact.value if fact else []:
            stat = Stat(bonus["stat"])
            if bonus["of_base"] and stat in (Stat.ATK, Stat.DEF, Stat.HP):
                stat = {Stat.ATK: Stat.ATK_PERCENT, Stat.DEF: Stat.DEF_PERCENT, Stat.HP: Stat.HP_PERCENT}[stat]
            elif bonus["of_base"] and stat is Stat.SPEED:
                stat = Stat.SPEED  # handled as % of base speed below (tagged by source prefix "set%")
            items.append(Item(stat, float(bonus["value"]), f"set%{code}" if bonus["of_base"] else code, fact.status))
    return items


def static_vector(cat: Catalog, codes: Iterable[str]) -> tuple[tuple[str, float], ...]:
    acc: dict[str, float] = defaultdict(float)
    for it in set_items(cat, codes):
        key = "speed%" if it.source.startswith("set%") and it.stat is Stat.SPEED else it.stat.value
        acc[key] += it.value
    return tuple(sorted((k, round(v, 6)) for k, v in acc.items() if v))


def all_set_hypotheses(cat: Catalog) -> dict[tuple[tuple[str, float], ...], list[SetHypothesis]]:
    """Every multiset of sets with total pieces <= 6 (piece counts: catalog, MECH-GEAR-05 community),
    grouped by its static stat vector (combat-only sets are invisible to the stats)."""
    pieces = {code: int(f["pieces"].value) for code, f in cat.sets.items()}
    codes = sorted(pieces)
    groups: dict[tuple[tuple[str, float], ...], list[SetHypothesis]] = defaultdict(list)
    for n in range(0, 4):
        for combo in itertools.combinations_with_replacement(codes, n):
            if sum(pieces[c] for c in combo) > 6:
                continue
            groups[static_vector(cat, combo)].append(SetHypothesis(combo))
    return groups


# ============================================================================ prediction


def base_stats(cat: Catalog, code: str) -> dict[Stat, Fact]:
    h = cat.heroes[code]
    return {s: h[f"base.{s.value}"] for s in ORDER}


def predict(base: dict[Stat, float], items: Sequence[Item]) -> tuple[dict[Stat, float], dict[Stat, list[str]]]:
    """Exact (unrounded) MECH-STAT-02 composition + a human-readable breakdown per stat."""
    pct: dict[Stat, float] = defaultdict(float)
    flat: dict[Stat, float] = defaultdict(float)
    speed_pct = 0.0
    why: dict[Stat, list[str]] = defaultdict(list)
    for it in items:
        if it.stat in PCT_OF:
            pct[PCT_OF[it.stat]] += it.value
            why[PCT_OF[it.stat]].append(f"{it.source} {it.value * 100:g}%")
        elif it.stat is Stat.SPEED and it.source.startswith("set%"):
            speed_pct += it.value
            why[Stat.SPEED].append(f"{it.source[4:]} {it.value * 100:g}% of base")
        else:
            flat[it.stat] += it.value
            why[it.stat].append(f"{it.source} {it.value * 100:g}%" if it.stat.is_rate else f"{it.source} {it.value:g}")
    out: dict[Stat, float] = {}
    for s in ORDER:
        if s in (Stat.ATK, Stat.DEF, Stat.HP):
            out[s] = base[s] * (1 + pct[s]) + flat[s]
        elif s is Stat.SPEED:
            out[s] = base[s] * (1 + speed_pct) + flat[s]
        else:
            out[s] = base[s] + flat[s]
    return out, why


@dataclass
class Residual:
    stat: Stat
    predicted: float  # exact, before display rounding / cap
    displayed: float | None
    residual: float | None  # displayed - predicted (fraction for rates)
    verdict: str  # exact | rounding(floor) | rounding(round) | capped | OUT
    fits_floor: bool = False
    fits_round: bool = False


def compare(stat: Stat, predicted: float, displayed: float | None, tol_scale: float = 1.0) -> Residual:
    if displayed is None:
        return Residual(stat, predicted, None, None, "no reading")
    r = displayed - predicted
    if stat in FLAT:
        exact = round(predicted, 6)  # float noise (137.97 -> 137.96999...) must not flip a floor
        fits_floor = math.floor(exact) == displayed
        fits_round = math.floor(exact + 0.5) == displayed
        accepted = fits_floor if ROUNDING["mode"] == "floor" else (fits_floor or fits_round)
        if abs(r) < 1e-9:
            verdict = "exact"
        elif accepted:
            verdict = "rounding(" + "/".join(n for n, ok in (("floor", fits_floor), ("round", fits_round)) if ok) + ")"
        else:
            verdict = "OUT"
        return Residual(stat, predicted, displayed, r, verdict, fits_floor, fits_round)
    if stat is Stat.CRIT_CHANCE and predicted > CRIT_CHANCE_CAP - RATE_TOL:
        ok = abs(displayed - CRIT_CHANCE_CAP) <= RATE_TOL
        return Residual(stat, predicted, displayed, r, "capped" if ok else "OUT", ok, ok)
    ok = abs(r) <= RATE_TOL * tol_scale
    return Residual(stat, predicted, displayed, r, "exact" if ok else "OUT", ok, ok)


def extra_window(stat: Stat, predicted: float, displayed: float) -> tuple[float, float]:
    """Interval [lo, hi) of an additive extra x on the exact sum that keeps the display consistent
    (current display rule; CC above the cap -> open upwards)."""
    if stat in FLAT:
        lo = displayed - predicted - (0.0 if ROUNDING["mode"] == "floor" else 0.5)
        return lo, displayed + 1 - predicted
    if stat is Stat.CRIT_CHANCE and abs(displayed - CRIT_CHANCE_CAP) <= RATE_TOL:
        return CRIT_CHANCE_CAP - predicted - RATE_TOL, math.inf
    r = displayed - predicted
    return r - RATE_TOL, r + RATE_TOL


def fmt_value(stat: Stat, v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v * 100:.2f}%" if stat.is_rate else f"{v:.2f}"


def fmt_res(stat: Stat, r: float | None) -> str:
    if r is None:
        return "-"
    return f"{r * 100:+.2f}pp" if stat.is_rate else f"{r:+.2f}"


# ============================================================================ OCR (displayed values)


_reader = RapidOcrReader()


def lines_from_json(data: list[dict[str, Any]]) -> list[TextLine]:
    out: list[TextLine] = []
    for d in data:
        words = []
        for w in d.get("words", []):
            if len(w) == 5:  # dump format: [text, x0, y0, x1, y1]
                words.append(Word(w[0], d["score"], Box(*w[1:])))
            else:  # cache format: [text, score, [x0, y0, x1, y1]]
                words.append(Word(w[0], w[1], Box(*w[2])))
        out.append(TextLine(d["text"], d["score"], Box(*d["box"]), tuple(words)))
    return out


def scaled(img: Any, scale: float) -> Any:
    if scale == 1.0:
        return img
    return cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)


def ocr_lines(name: str, img: Any, scale: float) -> list[TextLine]:
    cache = OUT / "ocr_cache" / f"{name}_{scale:.2f}.json"
    if cache.exists():
        return lines_from_json(json.loads(cache.read_text()))
    lines = _reader.read(img)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        json.dumps(
            [
                {
                    "text": ln.text,
                    "score": ln.score,
                    "box": [ln.box.x0, ln.box.y0, ln.box.x1, ln.box.y1],
                    "words": [[w.text, w.score, [w.box.x0, w.box.y0, w.box.x1, w.box.y1]] for w in ln.words],
                }
                for ln in lines
            ]
        )
    )
    return lines


@dataclass
class Displayed:
    scale: float
    reading: HeroScreenReading | None
    values: dict[Stat, float | None] = field(default_factory=dict)
    confidence: dict[Stat, float] = field(default_factory=dict)
    lines: list[TextLine] = field(default_factory=list)
    error: str = ""


def read_displayed(name: str, scale: float) -> Displayed:
    img = scaled(load_image(SHOTS / f"{name}.webp"), scale)
    lines = ocr_lines(name, img, scale)
    try:
        reading = parse_hero_screen(lines)
    except ScreenError as exc:
        return Displayed(scale, None, lines=lines, error=str(exc))
    d = Displayed(scale, reading, lines=lines)
    for s in ORDER:
        r = reading.stats.get(s)
        d.values[s] = r.final if r else None
        d.confidence[s] = r.confidence if r else 0.0
    return d


# ============================================================================ analysis per hero


@dataclass
class HeroCase:
    name: str
    code: str
    truth: dict[str, Any]
    base: dict[Stat, Fact]
    fixed_items: list[Item]  # gear + artifact + self imprint (no sets, no EE)
    ee_item: Item | None
    notes: list[str]


def build_case(cat: Catalog, name: str, code: str, truth: dict[str, Any]) -> HeroCase:
    notes: list[str] = []
    items = gear_items(truth["pieces"])
    art = truth["artifact"]
    a_items, a_notes = artifact_items(cat, art["code"], int(art["enhance"]))
    items += a_items
    notes += a_notes
    imp = truth["imprint"]
    if imp["mode"] == "self":
        it = imprint_item(imp["text"])
        hero = cat.heroes[code]
        own = hero.get("imprint.stat")
        vals = hero.get("imprint.values")
        if own is not None and vals is not None:
            expect = vals.value.get(imp["grade"])
            ok = own.value == it.stat.value and expect is not None and abs(expect - it.value) < 1e-9
            notes.append(
                f"self imprint {imp['text']!r} ({imp['grade']}) vs catalog {own.value} {expect} "
                f"({own.status}): {'OK' if ok else 'MISMATCH'}"
            )
        items.append(Item(it.stat, it.value, "imprint(self)", "screen text (truth)"))
    else:
        notes.append(f"team imprint {imp['text']!r} not added (MECH-IMP-03)")
    ee_item = None
    if truth.get("ee"):
        ee_stat = cat.heroes[code].get("ee.stat")
        text = truth["ee"]["value"]
        if ee_stat is None:
            notes.append("EE present but the catalog has no ee.stat: unknown item")
        else:
            stat = Stat(ee_stat.value)
            value = float(text.rstrip("%")) / 100 if text.endswith("%") else float(text)
            ee_item = Item(stat, value, "ee", f"stat {ee_stat.status} (Fribbels), value from screen")
            cat_val = cat.heroes[code].get("ee.value")
            if cat_val is not None:
                notes.append(
                    f"EE screen {text} {stat.value}; catalog ee.value {cat_val.value} is {cat_val.status} (NV-08) "
                    "- not used"
                )
    return HeroCase(name, code, truth, base_stats(cat, code), items, ee_item, notes)


@dataclass
class Fit:
    hyp_vector: tuple[tuple[str, float], ...]
    hyps: list[SetHypothesis]
    residuals: dict[Stat, Residual]
    n_out: int
    cost: float


def evaluate(
    case: HeroCase,
    displayed: dict[Stat, float | None],
    cat: Catalog,
    groups: dict[tuple[tuple[str, float], ...], list[SetHypothesis]],
    *,
    with_ee: bool,
    items_override: list[Item] | None = None,
) -> list[Fit]:
    base = {s: float(f.value) for s, f in case.base.items()}
    fixed = list(items_override if items_override is not None else case.fixed_items)
    if with_ee and case.ee_item is not None:
        fixed.append(case.ee_item)
    fits: list[Fit] = []
    for vec, hyps in groups.items():
        items = fixed + set_items(cat, hyps[0].codes)
        pred, _ = predict(base, items)
        res = {s: compare(s, pred[s], displayed.get(s)) for s in ORDER}
        n_out = sum(1 for r in res.values() if r.verdict == "OUT")
        cost = sum(abs(r.residual or 0) / (1.0 if s in FLAT else 0.01) for s, r in res.items() if r.verdict == "OUT")
        fits.append(Fit(vec, hyps, res, n_out, cost))
    fits.sort(key=lambda f: (f.n_out, f.cost, len(f.hyp_vector)))
    return fits


def explain_vector(vec: tuple[tuple[str, float], ...]) -> str:
    return ", ".join(f"{k} +{v * 100:g}%" for k, v in vec) or "no static bonus"


# ============================================================================ mutations (detection power)


@dataclass
class Mutation:
    kind: str
    field: str
    before: str
    after: str
    pieces: list[dict[str, Any]]


def _digit_variants(text: str) -> Iterable[tuple[str, str]]:
    digits = [i for i, c in enumerate(text) if c.isdigit()]
    for i in digits:
        for d in "0123456789":
            if d != text[i]:
                yield "digit_sub", text[:i] + d + text[i + 1 :]
    if len(digits) > 1:
        for i in digits:
            yield "digit_drop", text[:i] + text[i + 1 :]
    for i in [*digits, (digits[-1] + 1) if digits else 0]:
        for d in "0123456789":
            yield "digit_insert", text[:i] + d + text[i:]


def mutations(pieces: list[dict[str, Any]]) -> Iterable[Mutation]:
    """Single-field OCR-style errors on every gear value: every digit substitution / deletion / insertion,
    % lost or added (atk/def/hp flat <-> %), and every icon-family swap the value format allows."""
    for p_i, piece in enumerate(pieces):
        fields = [("main", piece["main"])] + [(f"sub{i}", s) for i, s in enumerate(piece["subs"], 1)]
        for f_name, (text, fam) in fields:

            def variant(new_text: str, new_fam: str, p_i: int = p_i, f_name: str = f_name) -> list[dict[str, Any]]:
                out = json.loads(json.dumps(pieces))
                if f_name == "main":
                    out[p_i]["main"] = [new_text, new_fam]
                else:
                    out[p_i]["subs"][int(f_name[3:]) - 1] = [new_text, new_fam]
                return out

            label = f"{piece['slot']}.{f_name}"
            for kind, new in _digit_variants(text):
                try:
                    parsed = parse_gear_value(new, fam)
                except ValueError:
                    continue
                if parsed == parse_gear_value(text, fam):  # "525" -> "0525": same value, not an error
                    continue
                yield Mutation(kind, label, f"{text} {fam}", f"{new} {fam}", variant(new, fam))
            if fam in ("atk", "def", "hp"):
                new = text[:-1] if text.endswith("%") else text + "%"
                yield Mutation("pct_toggle", label, f"{text} {fam}", f"{new} {fam}", variant(new, fam))
            for other in FAMILIES:
                if other == fam:
                    continue
                try:
                    parse_gear_value(text, other)
                except ValueError:
                    continue
                yield Mutation("icon_swap", label, f"{text} {fam}", f"{text} {other}", variant(text, other))


def detection_run(
    case: HeroCase,
    displayed: dict[Stat, float | None],
    cat: Catalog,
    groups: dict[tuple[tuple[str, float], ...], list[SetHypothesis]],
    known_vector: tuple[tuple[str, float], ...],
) -> dict[str, Any]:
    """Mode A: sets known (the accepted hypothesis); mode B: sets free (any hypothesis that fits -> undetected)."""
    art = case.truth["artifact"]
    a_items, _ = artifact_items(cat, art["code"], int(art["enhance"]))
    extra = [it for it in case.fixed_items if it.source.startswith(("imprint", "artifact"))]
    assert len(extra) >= len(a_items)
    stats: dict[str, Counter[str]] = {
        "A_total": Counter(),
        "A_caught": Counter(),
        "A_localised": Counter(),
        "B_caught": Counter(),
    }
    missed_a: list[str] = []
    missed_b: list[str] = []
    known_groups = {known_vector: groups[known_vector]}
    for m in mutations(case.truth["pieces"]):
        items = gear_items(m.pieces) + extra
        fa = evaluate(case, displayed, cat, known_groups, with_ee=True, items_override=items)[0]
        caught_a = fa.n_out > 0
        stats["A_total"][m.kind] += 1
        if caught_a:
            stats["A_caught"][m.kind] += 1
            # localisation: are the stats out exactly the stats the wrong field feeds?
            fed = set()
            for side in (m.before, m.after):
                text, fam = side.rsplit(" ", 1)
                st = parse_gear_value(text, fam)[0]
                fed.add(PCT_OF.get(st, st))
            out = {s for s, r in fa.residuals.items() if r.verdict == "OUT"}
            stats["A_localised"][m.kind] += int(out <= fed)
        else:
            missed_a.append(f"{m.kind} {m.field}: {m.before} -> {m.after}")
        fb = evaluate(case, displayed, cat, groups, with_ee=True, items_override=items)[0]
        if fb.n_out > 0:
            stats["B_caught"][m.kind] += 1
        else:
            missed_b.append(f"{m.kind} {m.field}: {m.before} -> {m.after} (fits sets {fb.hyps[0].label()})")
    return {"stats": stats, "missed_a": missed_a, "missed_b": missed_b}


def structure_mutation_run(
    case: HeroCase,
    displayed: dict[Stat, float | None],
    cat: Catalog,
    known_sets: tuple[str, ...],
) -> tuple[int, int, list[str]]:
    """Errors of the other M7 readers: a set icon misread as another set with the same piece count, the artifact
    enhance level misread (every other level 0..30), the imprint mode flipped (self <-> team), the EE dropped."""
    gear = gear_items(case.truth["pieces"])
    art = case.truth["artifact"]
    a_items, _ = artifact_items(cat, art["code"], int(art["enhance"]))
    imp_items = [it for it in case.fixed_items if it.source.startswith("imprint")]
    ee = [case.ee_item] if case.ee_item else []
    pieces = {code: int(f["pieces"].value) for code, f in cat.sets.items()}
    trials: list[tuple[str, list[Item]]] = []
    for i, code in enumerate(known_sets):
        for other in sorted(pieces):
            if other == code or pieces[other] != pieces[code]:
                continue
            swapped = (*known_sets[:i], other, *known_sets[i + 1 :])
            trials.append((f"set {code}->{other}", gear + a_items + imp_items + ee + set_items(cat, swapped)))
    for level in range(31):
        if level == int(art["enhance"]):
            continue
        alt, _ = artifact_items(cat, art["code"], level)
        trials.append(
            (f"artifact +{art['enhance']}->+{level}", gear + alt + imp_items + ee + set_items(cat, known_sets))
        )
    if imp_items:
        trials.append(("imprint self->team (dropped)", gear + a_items + ee + set_items(cat, known_sets)))
    else:
        it = imprint_item(case.truth["imprint"]["text"])
        trials.append(("imprint team->self (added)", gear + a_items + [it] + ee + set_items(cat, known_sets)))
    if ee:
        trials.append(("EE dropped", gear + a_items + imp_items + set_items(cat, known_sets)))
    base = {s: float(f.value) for s, f in case.base.items()}
    caught = 0
    missed: list[str] = []
    for label, items in trials:
        pred, _ = predict(base, items)
        if any(compare(s, pred[s], displayed.get(s)).verdict == "OUT" for s in ORDER):
            caught += 1
        else:
            missed.append(label)
    return len(trials), caught, missed


def base_windows(
    case: HeroCase, displayed: dict[Stat, float | None], cat: Catalog, known_sets: tuple[str, ...]
) -> dict[str, str]:
    """Interval of base values consistent with the display, everything else fixed (corroborates MECH-STAT-03 for
    heroes without an Equipment-tab capture). Width < 1 for a flat stat = the integer base is pinned."""
    items = list(case.fixed_items) + ([case.ee_item] if case.ee_item else []) + set_items(cat, known_sets)
    base = {s: float(f.value) for s, f in case.base.items()}
    out: dict[str, str] = {}
    for s in ORDER:
        shown = displayed.get(s)
        if shown is None:
            continue
        zero = dict(base)
        zero[s] = 0.0
        pred0, _ = predict(zero, items)
        one = dict(base)
        one[s] = 1.0
        pred1, _ = predict(one, items)
        slope = pred1[s] - pred0[s]  # final per unit of base (1 + sum of % of base)
        lo, hi = extra_window(s, pred0[s], shown)
        lo, hi = lo / slope, hi / slope
        if s in FLAT:
            ints = [v for v in range(math.ceil(lo), math.ceil(hi)) if lo <= v < hi]
            out[SHORT[s]] = (
                f"[{lo:.2f}, {hi:.2f}) integers {ints} catalog {base[s]:g} "
                f"{'OK' if base[s] in ints else 'NOT IN WINDOW'}"
            )
        else:
            txt_hi = "inf" if hi == math.inf else f"{hi * 100:.2f}%"
            ok = lo <= base[s] <= hi
            out[SHORT[s]] = f"[{lo * 100:.2f}%, {txt_hi}] catalog {base[s] * 100:g}% {'OK' if ok else 'NOT IN WINDOW'}"
    return out


def display_models(
    case: HeroCase, displayed: dict[Stat, float | None], cat: Catalog, known_sets: tuple[str, ...]
) -> dict[str, list[bool]]:
    """Which display/rounding rule of the flat stats reproduces the screen (the rule is `unknown` in the docs):
    floor/round of the exact total, or floor applied per term (all % of base summed, or each % item alone)."""
    items = list(case.fixed_items) + ([case.ee_item] if case.ee_item else []) + set_items(cat, known_sets)
    base = {s: float(f.value) for s, f in case.base.items()}
    models: dict[str, list[bool]] = defaultdict(list)
    for s in (Stat.ATK, Stat.DEF, Stat.HP, Stat.SPEED):
        pcts = [
            it.value
            for it in items
            if PCT_OF.get(it.stat) is s or (s is Stat.SPEED and it.stat is s and it.source.startswith("set%"))
        ]
        flats = [it.value for it in items if it.stat is s and not it.source.startswith(("set%", "artifact"))]
        arts = [it.value for it in items if it.stat is s and it.source.startswith("artifact")]
        exact = round(base[s] * (1 + sum(pcts)) + sum(flats) + sum(arts), 6)
        shown = displayed[s]
        models["floor(total)"].append(math.floor(exact) == shown)
        models["round(total)"].append(math.floor(exact + 0.5) == shown)
        per_sum = base[s] + math.floor(round(base[s] * sum(pcts), 6)) + sum(flats) + sum(math.floor(a) for a in arts)
        models["floor per term (sum of %)"].append(per_sum == shown)
        per_each = base[s] + sum(math.floor(round(base[s] * p, 6)) for p in pcts) + sum(flats)
        per_each += sum(math.floor(a) for a in arts)
        models["floor per % item"].append(per_each == shown)
        per_round = base[s] + sum(math.floor(round(base[s] * p, 6) + 0.5) for p in pcts) + sum(flats)
        per_round += sum(math.floor(a + 0.5) for a in arts)
        models["round per % item"].append(per_round == shown)
    return models


def value_colours(img: Any, disp: Displayed) -> dict[str, float]:
    """Red-ness of each displayed stat value (mean R - max(G, B) over the bright text pixels), located from the OCR
    lines on each label's row (row-height units). Observation: a capped Crit Chance is drawn in red."""
    out: dict[str, float] = {}
    labels = {
        "Attack": Stat.ATK,
        "Defense": Stat.DEF,
        "Health": Stat.HP,
        "Speed": Stat.SPEED,
        "Critical Hit Chance": Stat.CRIT_CHANCE,
        "Critical Hit Damage": Stat.CRIT_DAMAGE,
        "Effectiveness": Stat.EFFECTIVENESS,
        "Effect Resistance": Stat.EFFECT_RESISTANCE,
        "Dual Attack Chance": Stat.DUAL_ATTACK,
    }
    for ln in disp.lines:
        stat = labels.get(ln.text.strip())
        if stat is None or SHORT[stat] in out:
            continue
        h = ln.box.height
        cands = [
            v
            for v in disp.lines
            if v is not ln
            and abs(v.box.cy - ln.box.cy) <= 0.4 * h
            and ln.box.x1 < v.box.x0 < ln.box.x0 + 12 * h
            and re.match(r"^\d[\d,.]*%?", v.text.strip())
        ]
        if not cands:
            continue
        v = min(cands, key=lambda c: c.box.x0)
        crop = img[int(v.box.y0) : int(v.box.y1), int(v.box.x0) : int(v.box.x1)].astype(np.float32)
        bright = crop.max(axis=2) > 150
        if bright.sum() < 5:
            continue
        b, g, r = crop[..., 0][bright], crop[..., 1][bright], crop[..., 2][bright]
        out[SHORT[stat]] = round(float(np.mean(r - np.maximum(g, b))), 1)
    return out


def displayed_mutation_run(
    case: HeroCase,
    displayed: dict[Stat, float | None],
    cat: Catalog,
    groups: dict[tuple[tuple[str, float], ...], list[SetHypothesis]],
    known_vector: tuple[tuple[str, float], ...],
) -> tuple[int, int, list[str]]:
    """Single-digit substitutions in the displayed final values (stats-panel OCR errors), sets known."""
    total = caught = 0
    missed: list[str] = []
    for s in ORDER:
        v = displayed.get(s)
        if v is None:
            continue
        text = f"{v:.0f}" if s in FLAT else f"{v * 100:.1f}"
        for i, c in enumerate(text):
            if not c.isdigit():
                continue
            for d in "0123456789":
                if d == c:
                    continue
                new = text[:i] + d + text[i + 1 :]
                nv = float(new) if s in FLAT else float(new) / 100
                mut = dict(displayed)
                mut[s] = nv
                fit = evaluate(case, mut, cat, {known_vector: groups[known_vector]}, with_ee=True)[0]
                total += 1
                if fit.n_out:
                    caught += 1
                else:
                    missed.append(f"{SHORT[s]} {text} -> {new}")
    return total, caught, missed


# ============================================================================ contact sheet


def sheet_tile(name: str, img: Any, disp: Displayed, rows: dict[Stat, Residual], title: str) -> Any:
    lines = disp.lines
    labels = [ln for ln in lines if ln.text.strip() in ("Attack", "Dual Attack Chance")]
    tops = [ln for ln in lines if ln.text.strip() == "Attack"]
    bottoms = [ln for ln in lines if ln.text.strip() == "Dual Attack Chance"]
    if not tops or not bottoms or not labels:
        return np.zeros((100, 400, 3), np.uint8)
    top, bot = tops[0].box, bottoms[0].box
    h = top.height
    x0 = int(max(0, top.x0 - 1.5 * h))
    x1 = int(min(img.shape[1], top.x0 + 12.5 * h))
    y0 = int(max(0, top.y0 - 0.4 * h))
    y1 = int(min(img.shape[0], bot.y1 + 0.4 * h))
    crop = img[y0:y1, x0:x1]
    target_h = 360
    f = target_h / crop.shape[0]
    crop = cv2.resize(crop, None, fx=f, fy=f, interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_CUBIC)
    side = np.full((crop.shape[0], 470, 3), 30, np.uint8)
    row_h = crop.shape[0] / 9.6
    for i, s in enumerate(ORDER):
        r = rows[s]
        y = int((0.55 + i) * row_h + 0.3 * row_h)
        ok = r.verdict != "OUT"
        color = (90, 220, 90) if ok else (60, 60, 255)
        txt = f"{SHORT[s]:>3} ocr {fmt_value(s, r.displayed):>9} pred {fmt_value(s, r.predicted):>9} {r.verdict}"
        cv2.putText(side, txt, (6, y), cv2.FONT_HERSHEY_SIMPLEX, 0.43, color, 1, cv2.LINE_AA)
    tile = np.hstack([crop, side])
    head = np.full((26, tile.shape[1], 3), 0, np.uint8)
    cv2.putText(head, title, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return np.vstack([head, tile])


# ============================================================================ main


def tolerance_note() -> str:
    rule = (
        "displayed must equal floor(exact)"
        if ROUNDING["mode"] == "floor"
        else "displayed must equal floor(exact) or round(exact) (window (-1, +0.5] around exact)"
    )
    return (
        f"flat stats: {rule}; "
        f"rates: |displayed - exact| <= {RATE_TOL * 100:.2f} pp (half the 0.1% display step); "
        "CC: predicted >= 100% must display 100.0% (MECH-STAT-05)"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scales", type=float, nargs="*", default=[0.64, 1.0, 1.28])
    ap.add_argument("--no-mutations", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--rounding", choices=["either", "floor"], default="floor")
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args()
    ROUNDING["mode"] = args.rounding

    OUT.mkdir(parents=True, exist_ok=True)
    cat = load_catalog()
    truth = json.loads(TRUTH.read_text())
    groups = all_set_hypotheses(cat)
    print(
        f"catalog: {len(cat.heroes)} heroes, {len(cat.artifacts)} artifacts, {len(cat.sets)} sets; "
        f"{sum(len(v) for v in groups.values())} set multisets (<=6 pieces) -> {len(groups)} distinct static vectors"
    )
    print("tolerance:", tolerance_note())
    # exact name resolution for the truth's hero names (golden rule: never by fuzzy name)
    for name, code in HEROES.items():
        tname = truth[name]["hero"]
        cname = tname.split(" ", 1)[1] if re.match(r"^c\d{4} ", tname) else tname
        hits = cat.hero_names.get(cname, [])
        assert hits == [code], f"{name}: catalog name {cname!r} -> {hits}, expected [{code}]"

    icon_sets = json.loads(SETS_SPIKE.read_text()) if SETS_SPIKE.exists() else {}
    report: dict[str, Any] = {"tolerance": tolerance_note(), "heroes": {}}
    tiles: list[Any] = []
    summary_rows: list[tuple[str, str, str, str, str, str]] = []

    for name, code in HEROES.items():
        if args.only and name not in args.only:
            continue
        case = build_case(cat, name, code, truth[name])
        print(f"\n=========== {name} ({code}) ===========")
        print("base (catalog):", {SHORT[s]: (f.value, f.status) for s, f in case.base.items()})
        for n in case.notes:
            print("  note:", n)
        for it in case.fixed_items:
            if it.source.split(".")[0] not in ("weapon", "helmet", "armor", "necklace", "ring", "boots"):
                print(f"  item: {it.source:28s} {it.stat.value:12s} {it.value:g}  [{it.status}]")
        per_scale: dict[float, dict[Stat, float | None]] = {}
        disp_by_scale: dict[float, Displayed] = {}
        for sc in args.scales:
            d = read_displayed(name, sc)
            disp_by_scale[sc] = d
            if d.error:
                print(f"  OCR x{sc}: {d.error}")
                continue
            per_scale[sc] = d.values
            print(
                f"  OCR x{sc}: "
                + " ".join(f"{SHORT[s]}={fmt_value(s, v)}" for s, v in d.values.items())
                + f"  conf_min={min(d.confidence.values()):.3f}"
            )
        # also the stored dump (word boxes estimated) for comparison
        dump = M7 / "ocr" / f"{name.removeprefix('heroinfo_')}.json"
        if dump.exists():
            r = parse_hero_screen(lines_from_json(json.loads(dump.read_text())))
            dv = {s: (r.stats[s].final if s in r.stats else None) for s in ORDER}
            per_scale[-1.0] = dv
        agree = all(v == next(iter(per_scale.values())) for v in per_scale.values())
        print(f"  displayed values identical across scales (+ stored dump): {agree}")
        displayed = per_scale.get(1.0) or next(iter(per_scale.values()))

        # 1) no sets, no EE: raw residuals
        base_f = {s: float(f.value) for s, f in case.base.items()}
        pred0, _ = predict(base_f, case.fixed_items)
        print("  -- residuals with gear + artifact + self imprint only (no sets, no EE):")
        raw = {}
        for s in ORDER:
            r = compare(s, pred0[s], displayed[s])
            raw[s] = r
            print(
                f"     {SHORT[s]:>3} pred {fmt_value(s, r.predicted):>10} shown {fmt_value(s, r.displayed):>10} "
                f"res {fmt_res(s, r.residual):>10}  {r.verdict}"
            )
        # 2) set inference (with and without EE)
        hero_rep: dict[str, Any] = {"notes": case.notes, "displayed": {SHORT[s]: v for s, v in displayed.items()}}
        for with_ee in [False, True] if case.ee_item else [False]:
            fits = evaluate(case, displayed, cat, groups, with_ee=with_ee)
            ok = [f for f in fits if f.n_out == 0]
            tag = "with EE" if with_ee else "without EE"
            print(f"  -- set hypotheses consistent with ALL displayed stats ({tag}): {len(ok)} static vectors")
            for f in ok[:8]:
                labels = sorted({h.label() for h in f.hyps}, key=len)
                print(
                    f"     {explain_vector(f.hyp_vector):45s} e.g. {', '.join(labels[:4])}"
                    f"{f' (+{len(labels) - 4} more)' if len(labels) > 4 else ''}"
                )
            if not ok:
                f = fits[0]
                print(f"     none; closest: {explain_vector(f.hyp_vector)} with {f.n_out} stat(s) out")
            hero_rep[f"fits_{tag.replace(' ', '_')}"] = [
                {"vector": explain_vector(f.hyp_vector), "examples": sorted({h.label() for h in f.hyps}, key=len)[:6]}
                for f in ok
            ]
        # icon evidence (other spike) for comparison
        icons = icon_sets.get(f"{name}@1.0", {}).get("active", [])
        icon_codes = tuple(sorted(a["set"] for a in icons if a.get("set")))
        icon_vec = static_vector(cat, icon_codes)
        print(f"  -- sets spike (icons, not truth): {icon_codes} -> static {explain_vector(icon_vec)}")
        final_fits = evaluate(
            case, displayed, cat, {icon_vec: groups.get(icon_vec, [SetHypothesis(icon_codes)])}, with_ee=True
        )
        best = final_fits[0]
        print(f"  -- full model with the icon sets{' + EE' if case.ee_item else ''}: {best.n_out} stat(s) out")
        rows = {}
        for s in ORDER:
            r = best.residuals[s]
            rows[s] = r
            print(
                f"     {SHORT[s]:>3} pred {fmt_value(s, r.predicted):>10} shown {fmt_value(s, r.displayed):>10} "
                f"res {fmt_res(s, r.residual):>10}  {r.verdict}"
            )
            summary_rows.append(
                (
                    name.removeprefix("heroinfo_"),
                    SHORT[s],
                    fmt_value(s, r.predicted),
                    fmt_value(s, r.displayed),
                    fmt_res(s, r.residual),
                    r.verdict,
                )
            )
        # the full model must also hold at every scale's reading
        for sc, vals in per_scale.items():
            f = evaluate(case, vals, cat, {icon_vec: groups.get(icon_vec, [SetHypothesis(icon_codes)])}, with_ee=True)[
                0
            ]
            print(f"     scale {sc if sc > 0 else 'dump'}: {f.n_out} out")
        hero_rep["icon_sets"] = list(icon_codes)
        hero_rep["final"] = {
            SHORT[s]: {"pred": r.predicted, "shown": r.displayed, "res": r.residual, "verdict": r.verdict}
            for s, r in rows.items()
        }
        hero_rep["raw_no_sets"] = {
            SHORT[s]: {"pred": r.predicted, "shown": r.displayed, "res": r.residual} for s, r in raw.items()
        }
        # how tightly the display pins the artifact values (MECH-ART-02) and the EE (NV-08): remove the item,
        # recompute, and print the interval of values that keeps the display consistent
        icon_set_items = set_items(cat, icon_codes)
        windows: dict[str, str] = {}
        probes = [it for it in case.fixed_items if it.source.startswith(("artifact", "imprint"))]
        probes += [case.ee_item] if case.ee_item else []
        for probe in probes:
            rest = [it for it in case.fixed_items if it is not probe]
            if case.ee_item is not None and probe is not case.ee_item:
                rest.append(case.ee_item)
            pred_rest, _ = predict(base_f, rest + icon_set_items)
            target = PCT_OF.get(probe.stat, probe.stat)
            lo, hi = extra_window(target, pred_rest[target], float(displayed[target] or 0))
            if probe.stat in PCT_OF:  # % of base -> window on the percentage
                lo, hi = lo / base_f[target], hi / base_f[target]
            inside = lo <= probe.value < hi
            unit = (lambda v: f"{v * 100:.2f}%") if probe.stat.is_rate else (lambda v: f"{v:.2f}")
            text = (
                f"{probe.source} {probe.stat.value}: model {unit(probe.value)}; display allows "
                f"[{unit(lo)}, {unit(hi) if hi != math.inf else 'inf'}) -> {'inside' if inside else 'OUTSIDE'}"
            )
            windows[f"{probe.source} {probe.stat.value}"] = text
            print("  -- window:", text)
        hero_rep["windows"] = windows
        bw = base_windows(case, displayed, cat, icon_codes)
        for k, v in bw.items():
            status = case.base[next(s for s in ORDER if SHORT[s] == k)].status
            print(f"  -- base window {k:>3}: {v}  [catalog status {status}]")
        hero_rep["base_windows"] = bw
        n_s, c_s, m_s = structure_mutation_run(case, displayed, cat, icon_codes)
        print(
            f"  -- structure errors (set swap / artifact level / imprint mode / EE): {n_s}; caught {c_s}; missed: {m_s}"
        )
        hero_rep["structure_mutations"] = [n_s, c_s, m_s]
        dm = display_models(case, displayed, cat, icon_codes)
        print("  -- display rule fits (ATK, DEF, HP, SPD):", {k: v for k, v in dm.items()})
        hero_rep["display_models"] = dm
        # team imprint counterfactual (MECH-IMP-03 check)
        imp = case.truth["imprint"]
        if imp["mode"] == "team":
            it = imprint_item(imp["text"])
            alt = evaluate(
                case,
                displayed,
                cat,
                {icon_vec: groups.get(icon_vec, [SetHypothesis(icon_codes)])},
                with_ee=True,
                items_override=[*case.fixed_items, it],
            )[0]
            outs = [f"{SHORT[s]} {fmt_res(s, r.residual)}" for s, r in alt.residuals.items() if r.verdict == "OUT"]
            print(f"  -- counterfactual: team imprint {imp['text']!r} added to the hero -> out: {outs or 'none'}")
            hero_rep["team_imprint_counterfactual_out"] = outs
        # 3) mutation test
        if not args.no_mutations:
            det = detection_run(case, displayed, cat, groups, icon_vec)
            st = det["stats"]
            tot = sum(st["A_total"].values())
            ca = sum(st["A_caught"].values())
            cb = sum(st["B_caught"].values())
            print(
                f"  -- gear OCR-error mutations: {tot}; caught with sets known {ca} ({ca / tot:.1%}), "
                f"sets free {cb} ({cb / tot:.1%})"
            )
            for k in sorted(st["A_total"]):
                print(
                    f"     {k:12s} {st['A_total'][k]:4d}  A {st['A_caught'][k] / st['A_total'][k]:6.1%}  "
                    f"B {st['B_caught'][k] / st['A_total'][k]:6.1%}  "
                    f"localised {st['A_localised'][k]}/{st['A_caught'][k]}"
                )
            print(f"     missed with sets known ({len(det['missed_a'])}):")
            for m in det["missed_a"][: (None if args.verbose else 40)]:
                print("       ", m)
            if args.verbose:
                print(f"     missed with sets free ({len(det['missed_b'])}):")
                for m in det["missed_b"]:
                    print("       ", m)
            tot_d, caught_d, missed_d = displayed_mutation_run(case, displayed, cat, groups, icon_vec)
            print(
                f"  -- displayed-value digit substitutions: {tot_d}; caught {caught_d} ({caught_d / tot_d:.1%});"
                f" missed: {missed_d}"
            )
            hero_rep["mutations"] = {
                "total": tot,
                "caught_sets_known": ca,
                "caught_sets_free": cb,
                "by_kind": {
                    k: [st["A_total"][k], st["A_caught"][k], st["B_caught"][k], st["A_localised"][k]]
                    for k in st["A_total"]
                },
                "missed_sets_known": det["missed_a"],
                "missed_sets_free": det["missed_b"],
                "displayed_digit_subs": [tot_d, caught_d, missed_d],
            }
        report["heroes"][name] = hero_rep
        # contact sheet tile (scale 1.0 reading)
        d1 = disp_by_scale.get(1.0)
        if d1 is not None and d1.reading is not None:
            img = load_image(SHOTS / f"{name}.webp")
            tiles.append(sheet_tile(name, img, d1, rows, f"{name} ({code}) sets={'+'.join(icon_codes)}"))

    # Equipment-tab twins: same hero, same build? final values must match Hero Info (and final - ▲ = base)
    print("\n=========== Equipment tab twins ===========")
    for eq, hi in EQUIP_TWINS.items():
        for sc in args.scales:
            d = read_displayed(eq, sc)
            if d.reading is None:
                print(f"  {eq} x{sc}: {d.error}")
                continue
            h = read_displayed(hi, sc)
            base = base_stats(cat, HEROES[hi])
            diffs = {SHORT[s]: (d.values[s], h.values[s]) for s in ORDER if d.values[s] != h.values[s]}
            bonus_ok = sum(
                1
                for s in ORDER
                if d.reading.stats.get(s)
                and d.values[s] is not None
                and abs((d.values[s] or 0) - (d.reading.stats[s].bonus or 0) - float(base[s].value))
                <= (0.5 if s in FLAT else RATE_TOL)
            )
            print(
                f"  {eq} x{sc}: finals differing from {hi}: {diffs or 'none'}; final-▲=base on {bonus_ok}/9; "
                f"set names {d.reading.set_names}"
            )

    # colour of the displayed values: is a capped Crit Chance drawn differently (red)?
    print("\n=========== value colour (R - max(G,B) of bright text pixels; >40 = red) ===========")
    for name in [*HEROES, "heroinfo_politis", "heroinfo_charles", *EQUIP_TWINS, "equip_renoa"]:
        for sc in args.scales:
            d = read_displayed(name, sc)
            if d.reading is None:
                continue
            cols = value_colours(scaled(load_image(SHOTS / f"{name}.webp"), sc), d)
            red = [k for k, v in cols.items() if v > 40]
            cc = d.values.get(Stat.CRIT_CHANCE)
            print(f"  {name:18s} x{sc}: CC={fmt_value(Stat.CRIT_CHANCE, cc)} red values {red}  {cols}")

    if tiles:
        w = max(t.shape[1] for t in tiles)
        tiles = [np.pad(t, ((0, 6), (0, w - t.shape[1]), (0, 0))) for t in tiles]
        cv2.imwrite(str(OUT / "sheet_final_stats.png"), np.vstack(tiles))
    (OUT / "report.json").write_text(json.dumps(report, indent=1, default=str))
    print("\nsummary (icon sets + EE):")
    for row in summary_rows:
        print("  " + " | ".join(f"{c:>10}" for c in row))
    print(f"\nwrote {OUT / 'report.json'} and {OUT / 'sheet_final_stats.png'}")


if __name__ == "__main__":
    main()
