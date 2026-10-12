"""epic7db.com hero pages: skill cooldowns (docs/DATA_SOURCES.md §4; robots allow all, no licence: personal use,
cached, polite). HTML only: each skill shows its icon `<slug>_skill_<n>.webp`, then a `cooldown` block reading
"None", "Passive" or "<n> Turns". A page is used only when it names the hero (a slug pointing at another hero is
never used).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from html import unescape as html_unescape
from typing import Final

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


def slug(name: str) -> str:
    """'Blood Blade Karin' -> 'blood-blade-karin' (apostrophes dropped, other symbols as hyphens)."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower().replace("'", "")).strip("-")


@dataclass(frozen=True, slots=True)
class SkillInfo:
    """What epic7db shows of one skill: its cooldown, the status effects of its icons (name -> the value in the icon's
    description, e.g. "Increases target's Attack by 50%" -> 0.5, or None) and its description text."""

    slot: int
    cooldown: int | None
    """Turns; 0 = none; None = passive or not shown."""
    effects: dict[str, float | None]
    text: str


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
        effects = {html_unescape(name): _value(html_unescape(title)) for name, title in _EFFECT.findall(body)}
        text_match = _TEXT.search(body)
        text = (
            re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html_unescape(text_match.group(1)))).strip()
            if text_match
            else ""
        )
        kit[slot] = SkillInfo(slot, cooldown, effects, text)
    return kit


def _value(title: str) -> float | None:
    found = re.search(r"by (\d+(?:\.\d+)?)%", title)
    return round(float(found.group(1)) / 100, 6) if found else None


def parse_cooldowns(html: str) -> dict[int, int]:
    """Kept for callers that only need cooldowns."""
    return {s: k.cooldown for s, k in parse_kit(html).items() if k.cooldown is not None}


def _validate(text: str) -> None:
    if 'class="cooldown"' not in text:
        raise ValueError("no skill cooldowns in the page")
