"""epic7db.com hero pages: skill cooldowns (docs/DATA_SOURCES.md §4; robots allow all, no licence: personal use,
cached, polite). HTML only: each skill shows its icon `<slug>_skill_<n>.webp`, then a `cooldown` block reading
"None", "Passive" or "<n> Turns". A page is used only when it names the hero (a slug pointing at another hero is
never used). SPEC D90: also each skill's name, soulburn text, soul gain, enhancement steps and highlighted keywords,
the maximum-enhancement cooldown, and the Fribbels hero-library block every hero page embeds (average stats, set use
rates, top builds).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta
from html import unescape as html_unescape
from typing import Final

from e7ac.domain.codes import Stat
from e7ac.sources.http import CachedHttp, FetchError

BASE: Final = "https://epic7db.com/heroes"
MAX_AGE: Final = timedelta(days=30)
_SKILL: Final = re.compile(
    r'_skill_(?P<slot>[1-3])\.webp".*?class="cooldown">\s*(?P<text>[^<]*?)\s*</div>', re.DOTALL | re.IGNORECASE
)
_TURNS: Final = re.compile(r"^(\d+)\s+turns?$", re.IGNORECASE)
_COOLDOWN: Final = re.compile(r'class="cooldown">\s*([^<]*?)\s*</div>', re.IGNORECASE)
_EFFECT: Final = re.compile(r'status_effects/[^"]*"\s+alt="([^"]+)"\s+title="([^"]*)"', re.IGNORECASE)
_TEXT: Final = re.compile(r'class="bottom">(.*?)</div>', re.DOTALL | re.IGNORECASE)
_NAME: Final = re.compile(r'^\d\.webp"\s+alt="([^"]*)"', re.IGNORECASE)
_SOULBURN: Final = re.compile(r'class="soulburn">\s*<p>(.*?)</p>', re.DOTALL | re.IGNORECASE)
_SOULS: Final = re.compile(r'class="soul-gain">\s*\+\s*(\d+)\s*souls?', re.IGNORECASE)
_UPGRADE: Final = re.compile(
    r'<div class="level">\s*(\d+)\s*</div>\s*<div class="description">\s*([^<]*?)\s*</div>', re.IGNORECASE
)
_STRONG: Final = re.compile(r"<strong>(.*?)</strong>", re.DOTALL | re.IGNORECASE)
_COOLDOWN_STEP: Final = re.compile(r"-\s*(\d+)\s*turns?\s*cooldown", re.IGNORECASE)


def slug(name: str) -> str:
    """'Blood Blade Karin' -> 'blood-blade-karin' (apostrophes dropped, other symbols as hyphens)."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower().replace("'", "")).strip("-")


@dataclass(frozen=True, slots=True)
class SkillInfo:
    """What epic7db shows of one skill: its cooldown, the status effects of its icons (name -> the value in the icon's
    description, e.g. "Increases target's Attack by 50%" -> 0.5, or None) and its description text."""

    slot: int
    cooldown: int | None
    """Turns; 0 = none; None = passive or not shown. The cooldown of the skill without enhancements."""
    effects: dict[str, float | None]
    text: str
    name: str = ""
    soulburn: str = ""
    """The soulburn variant's text, "(-10 souls)" included, when the skill has one."""
    souls: int | None = None
    """Souls the skill gains."""
    enhance: tuple[tuple[int, str], ...] = ()
    """Skill enhancement steps: (level, description), e.g. (3, "-1 turn cooldown")."""
    keywords: tuple[str, ...] = ()
    """The terms the page highlights in the text (bold): the named effects and mechanics the skill mentions."""
    effect_titles: dict[str, str] = field(default_factory=dict)
    """Icon name -> the full description shown for it (when epic7db has the icon)."""
    captured: bool = False
    """Read from the game's own skill popup (`skills.merge`), not from epic7db: the numbers are the game's, at the
    enhancement level of the hero captured (`level`)."""
    level: int | None = None
    cooldown_max: int | None = None
    """The cooldown fully enhanced, when it is known directly (a capture at some level, the steps still to come taken
    off); `max_cooldown` is then this."""

    @property
    def max_cooldown(self) -> int | None:
        """The cooldown with every enhancement step that shortens it ("-1 turn cooldown") applied: what a fully enhanced
        skill has, e.g. the 5 turns shown for Lady of the Scales S3 are 4 in the game at +5 (NV-41)."""
        if self.cooldown_max is not None:
            return self.cooldown_max
        if self.cooldown is None:
            return None
        cut = sum(int(n) for _, desc in self.enhance for n in _COOLDOWN_STEP.findall(desc))
        return max(0, self.cooldown - cut) if self.cooldown else 0


def fetch_kit(http: CachedHttp, hero_name: str) -> dict[int, SkillInfo]:
    """Skill slot -> SkillInfo. Empty when the page is missing, unusable or about another hero."""
    try:
        page = http.get_text("epic7db/heroes", f"{BASE}/{slug(hero_name)}", max_age=MAX_AGE, validate=_validate)
    except FetchError:  # an unknown kit is reported by the caller, never fatal
        return {}
    if hero_name.lower() not in page.text.lower():
        return {}
    return parse_kit(page.text)


def fetch_cooldowns(http: CachedHttp, hero_name: str) -> dict[int, int]:
    """Skill slot -> cooldown in turns (0: none; passives left out)."""
    return {s: k.cooldown for s, k in fetch_kit(http, hero_name).items() if k.cooldown is not None}


def parse_kit(html: str) -> dict[int, SkillInfo]:
    kit: dict[int, SkillInfo] = {}
    blocks = re.split(r"_skill_(?=[1-3]\.webp)", html)[1:]
    for block in blocks:
        slot = int(block[0])
        if slot in kit:
            continue
        body = block.split("_skill_", 1)[0]
        cooldown_text = _COOLDOWN.search(body)
        turns = _TURNS.match(cooldown_text.group(1).strip()) if cooldown_text else None
        cooldown = (
            int(turns.group(1))
            if turns
            else (0 if cooldown_text and cooldown_text.group(1).strip().lower() == "none" else None)
        )
        titles = {html_unescape(name): html_unescape(title) for name, title in _EFFECT.findall(body)}
        effects = {name: _value(title) for name, title in titles.items()}
        text_match = _TEXT.search(body)
        text = _plain(text_match.group(1)) if text_match else ""
        name_match = _NAME.match(block)
        soulburn = _SOULBURN.search(body)
        souls = _SOULS.search(body)
        kit[slot] = SkillInfo(
            slot,
            cooldown,
            effects,
            text,
            name=html_unescape(name_match.group(1)) if name_match else "",
            soulburn=_plain(soulburn.group(1)) if soulburn else "",
            souls=int(souls.group(1)) if souls else None,
            enhance=tuple((int(level), _plain(desc)) for level, desc in _UPGRADE.findall(body)),
            keywords=tuple(dict.fromkeys(_plain(k).lower() for k in _STRONG.findall(text_match.group(1))))
            if text_match
            else (),
            effect_titles=titles,
        )
    return kit


def _plain(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html_unescape(html))).strip()


def _value(title: str) -> float | None:
    found = re.search(r"by (\d+(?:\.\d+)?)%", title)
    return round(float(found.group(1)) / 100, 6) if found else None


def parse_cooldowns(html: str) -> dict[int, int]:
    """Kept for callers that only need cooldowns."""
    return {s: k.cooldown for s, k in parse_kit(html).items() if k.cooldown is not None}


def _validate(text: str) -> None:
    if 'class="cooldown"' not in text:
        raise ValueError("no skill cooldowns in the page")


# --- the Fribbels hero library block of a hero page (SPEC D90) -------------------------------------------------------

_LIBRARY_STATS: Final = {
    "attack": (Stat.ATK, 1.0),
    "defense": (Stat.DEF, 1.0),
    "health": (Stat.HP, 1.0),
    "speed": (Stat.SPEED, 1.0),
    "crit_chance": (Stat.CRIT_CHANCE, 0.01),
    "crit_damage": (Stat.CRIT_DAMAGE, 0.01),
    "effectiveness": (Stat.EFFECTIVENESS, 0.01),
    "effect_resistance": (Stat.EFFECT_RESISTANCE, 0.01),
}
_LIBRARY_STAT: Final = re.compile(
    r'<li><img[^>]*alt="([a-z_]+)"[^>]*>[^<]*?:\s*(\d+(?:\.\d+)?)%?\s*</li>', re.IGNORECASE
)
_LIBRARY_SET_BLOCK: Final = re.compile(
    r"<h4>Set \d+ \((\d+(?:\.\d+)?)% Use Rate\)</h4>(.*?)</ul>", re.DOTALL | re.IGNORECASE
)
_LIBRARY_SET: Final = re.compile(r'alt="([^"]+?) gear set"', re.IGNORECASE)
_LIBRARY_BUILD: Final = re.compile(
    r"<h3>Build \d+</h3>(.*?)(?=<h3>Build \d+</h3>|View All Fribbels Builds|</section>|$)", re.DOTALL | re.IGNORECASE
)
_LIBRARY_ARTIFACT: Final = re.compile(r"Artifact\s*(?:</h4>)?.*?<img[^>]*alt=\"([^\"]+)\"", re.DOTALL | re.IGNORECASE)
_LIBRARY_UPDATED: Final = re.compile(r"Updated on ([^<]+?)\.\s*<", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class LibraryBuild:
    """One of the top builds the Fribbels hero library lists for a hero."""

    stats: dict[Stat, float]
    sets: tuple[str, ...]
    artifact: str


@dataclass(frozen=True, slots=True)
class HeroLibrary:
    """What the Fribbels hero library (builds submitted by users of the optimizer, shown on each epic7db hero page)
    says of a hero: the average of the final stats, how often each set combination is worn, the top builds. Percent
    stats are fractions (19% = 0.19), as in the rest of the program."""

    updated: str
    average: dict[Stat, float]
    set_combos: tuple[tuple[float, tuple[str, ...]], ...]
    """(share of the builds, set names), most used first."""
    builds: tuple[LibraryBuild, ...]


def parse_library(html: str) -> HeroLibrary | None:
    """The Fribbels block of a hero page, or None when the page has none (a hero nobody optimized)."""
    start = html.find('id="fribbels"')
    if start < 0:
        return None
    end = html.find("</section>", start)
    block = html[start : end if end > 0 else len(html)]
    split = block.find("Top Fribbels Builds")
    head, tail = (block[:split], block[split:]) if split > 0 else (block, "")
    average = _library_stats(head.split("Set 1 (")[0])
    if not average:
        return None
    combos = tuple(
        (float(rate) / 100, tuple(html_unescape(s) for s in _LIBRARY_SET.findall(sets)))
        for rate, sets in _LIBRARY_SET_BLOCK.findall(head)
    )
    builds = []
    for body in _LIBRARY_BUILD.findall(tail):
        artifact = _LIBRARY_ARTIFACT.search(body)
        builds.append(
            LibraryBuild(
                _library_stats(body),
                tuple(html_unescape(s) for s in _LIBRARY_SET.findall(body)),
                html_unescape(artifact.group(1)) if artifact else "",
            )
        )
    updated = _LIBRARY_UPDATED.search(block)
    return HeroLibrary(updated.group(1).strip() if updated else "", average, combos, tuple(builds))


def _library_stats(text: str) -> dict[Stat, float]:
    found: dict[Stat, float] = {}
    for name, number in _LIBRARY_STAT.findall(text):
        if name.lower() in _LIBRARY_STATS:
            stat, scale = _LIBRARY_STATS[name.lower()]
            found.setdefault(stat, round(float(number) * scale, 6))
    return found


def fetch_library(http: CachedHttp, hero_name: str) -> HeroLibrary | None:
    """The hero library block of the hero's page (the page is shared with `fetch_kit`: one request, cached)."""
    try:
        page = http.get_text("epic7db/heroes", f"{BASE}/{slug(hero_name)}", max_age=MAX_AGE, validate=_validate)
    except FetchError:
        return None
    if hero_name.lower() not in page.text.lower():
        return None
    return parse_library(page.text)


# --- the RTA tier list: a proxy for which heroes matter in PvP (SPEC D91) ---------------------------------------------

RTA_URL: Final = "https://epic7db.com/tierlists/epic-seven-rta-tier-list"
_TIER_HERO: Final = re.compile(r'href="https://epic7db\.com/heroes/([a-z0-9-]+)"', re.IGNORECASE)
_ALL_HEROES: Final = "All Epic Seven Heroes"


def parse_tier_order(html: str) -> list[str]:
    """Hero slugs in the order the tier list gives them, best tier first (the alphabetical index of every hero at the
    end of the page is left out)."""
    cut = html.find(_ALL_HEROES)
    found = _TIER_HERO.findall(html[:cut] if cut > 0 else html)
    return list(dict.fromkeys(found))


def fetch_tier_order(http: CachedHttp) -> list[str]:
    """The RTA tier list's hero slugs, best first; empty when the page cannot be read (the order is only a hint)."""
    try:
        page = http.get_text("epic7db/tierlists", RTA_URL, max_age=timedelta(days=7), validate=_validate_tiers)
    except FetchError:
        return []
    return parse_tier_order(page.text)


def _validate_tiers(text: str) -> None:
    if "/heroes/" not in text:
        raise ValueError("no heroes in the tier list page")
