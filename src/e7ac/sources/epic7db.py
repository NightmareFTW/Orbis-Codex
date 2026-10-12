"""epic7db.com hero pages: skill cooldowns (docs/DATA_SOURCES.md §4; robots allow all, no licence: personal use,
cached, polite). HTML only: each skill shows its icon `<slug>_skill_<n>.webp`, then a `cooldown` block reading
"None", "Passive" or "<n> Turns". A page is used only when it names the hero (a slug pointing at another hero is
never used).
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Final

from e7ac.sources.http import CachedHttp, FetchError

BASE: Final = "https://epic7db.com/heroes"
MAX_AGE: Final = timedelta(days=30)
_SKILL: Final = re.compile(
    r'_skill_(?P<slot>[1-3])\.webp".*?class="cooldown">\s*(?P<text>[^<]*?)\s*</div>', re.DOTALL | re.IGNORECASE
)
_TURNS: Final = re.compile(r"^(\d+)\s+turns?$", re.IGNORECASE)


def slug(name: str) -> str:
    """'Blood Blade Karin' -> 'blood-blade-karin' (apostrophes dropped, other symbols as hyphens)."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower().replace("'", "")).strip("-")


def fetch_cooldowns(http: CachedHttp, hero_name: str) -> dict[int, int]:
    """Skill slot -> cooldown in turns (0: none; passives left out). Empty when the page is missing or unusable."""
    try:
        page = http.get_text("epic7db/heroes", f"{BASE}/{slug(hero_name)}", max_age=MAX_AGE, validate=_validate)
    except FetchError:  # an unknown cooldown is reported by the caller, never fatal
        return {}
    if hero_name.lower() not in page.text.lower():
        return {}
    return parse_cooldowns(page.text)


def parse_cooldowns(html: str) -> dict[int, int]:
    found: dict[int, int] = {}
    for match in _SKILL.finditer(html):
        slot, text = int(match["slot"]), match["text"].strip()
        turns = _TURNS.match(text)
        if turns:
            found.setdefault(slot, int(turns.group(1)))
        elif text.lower() == "none":
            found.setdefault(slot, 0)
    return found


def _validate(text: str) -> None:
    if 'class="cooldown"' not in text:
        raise ValueError("no skill cooldowns in the page")
