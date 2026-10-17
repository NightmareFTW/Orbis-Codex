"""The skill audit (SPEC D90): every hero of the catalog, what the program knows of its skills, and what it still needs.

For each hero the audit takes epic7db's page (skill text, cooldown, soulburn, enhancement steps, highlighted keywords,
effect icons), runs the kit readers on it (icons and text, SPEC D63 / D88, plus the hand file when there is one) and
records, per skill: what was read (effect kinds), whether the skill is still "not read", what the readers left out, the
cooldown without and with the enhancements, and the keywords of the text no reader knows (the named mechanics:
"offering", "venom", ...). Over all heroes it counts those keywords (a mechanic seen on many heroes is worth modelling
first) and builds the list of what only the game can tell: the description of each unknown named effect (shown by
holding any skill that mentions it) and the numbers of the skills of the heroes met most. UI-free: the CLI and the
window print it.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Final

from e7ac.predict.effects import Scaling, kit_from
from e7ac.predict.overrides import KitOverride, apply_override
from e7ac.sources.epic7db import HeroLibrary, SkillInfo

_HANDLED: Final = re.compile(
    r"\b(?:fear|stun|sleep|dispel\w*|cleans\w*|combat readiness|extra turn|immunity|immortal\w*|invincib\w*|heal\w*|"
    r"recover\w*|barrier|burn\w*|bleed\w*|poison\w*|provok\w*|silenc\w*|counter\w*|defense|attack|speed|"
    r"revive\w*|resurrect\w*|reviv\w*|decreas\w*|increas\w*)\b",
    re.IGNORECASE,
)
_GENERIC: Final = re.compile(
    r"^(?:target'?s? max health|caster'?s? max health|max health|defense|attack|speed|damage|all enemies|all allies|"
    r"penetrates|penetrate|health|critical hit|crit\w*|dual attack|extra attack|soulburn|effect resistance|"
    r"effectiveness|hit chance|evasion|the caster|target)$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class SkillAudit:
    slot: int
    name: str
    cooldown: int | None
    """Without enhancements (what epic7db lists)."""
    max_cooldown: int | None
    """With every step that shortens it."""
    has_text: bool
    icons: tuple[str, ...]
    """Effect icons epic7db lists for the skill (none for newer heroes)."""
    read: tuple[str, ...]
    """Effect kinds the simulation gets from the skill (icons, text and the hand file)."""
    unread: bool
    """The text describes effects and none was read: the skill counts as a plain attack."""
    unmodelled: tuple[str, ...]
    keywords: tuple[str, ...]
    """The terms the page highlights in the text."""
    unknown: tuple[str, ...]
    """Highlighted terms no reader knows: the named mechanics of the skill."""
    steps: tuple[str, ...]
    """The kinds of enhancement step (effect chance, cooldown, Combat Readiness, ...)."""
    soulburn: bool


@dataclass(frozen=True, slots=True)
class HeroAudit:
    code: str
    name: str
    page: bool
    """epic7db has a usable page for the hero."""
    hand: bool
    """A hand-written kit exists."""
    skills: tuple[SkillAudit, ...]
    library: bool
    """The page carries Fribbels hero-library data (average stats, set use, top builds)."""

    @property
    def done(self) -> bool:
        """Every skill read (by icons, text or hand) and nothing flagged."""
        return self.page and bool(self.skills) and not any(s.unread for s in self.skills)

    @property
    def unknown(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(u for s in self.skills for u in s.unknown))


def audit_hero(
    code: str,
    name: str,
    skills: Mapping[int, SkillInfo],
    amounts: Mapping[int, Mapping[str, Scaling]] | None = None,
    hand: KitOverride | None = None,
    library: HeroLibrary | None = None,
) -> HeroAudit:
    kit = kit_from(dict(skills), amounts)
    cooldowns = {s: k.cooldown for s, k in skills.items() if k.cooldown is not None}
    if hand is not None:
        kit, cooldowns = apply_override(kit, cooldowns, hand)
    rows = []
    for slot in (1, 2, 3):
        info = skills.get(slot)
        if info is None:
            continue
        read = [e.kind for e in kit.on_use.get(slot, [])]
        if slot == 2:
            read += [e.kind for e in (*kit.at_start, *kit.on_turn)]
            read += ["revive"] if kit.revive is not None else []
            read += ["counter"] if kit.counter else []
        rows.append(
            SkillAudit(
                slot=slot,
                name=info.name,
                cooldown=info.cooldown,
                max_cooldown=info.max_cooldown,
                has_text=bool(info.text),
                icons=tuple(info.effects),
                read=tuple(read),
                unread=slot in kit.unread,
                unmodelled=tuple(dict.fromkeys(n for n in kit.unmodelled if n)),
                keywords=info.keywords,
                unknown=tuple(k for k in info.keywords if is_named_mechanic(k)),
                steps=tuple(dict.fromkeys(step_kind(desc) for _, desc in info.enhance)),
                soulburn=bool(info.soulburn),
            )
        )
    return HeroAudit(code, name, bool(skills), hand is not None, tuple(rows), library is not None)


def is_named_mechanic(keyword: str) -> bool:
    """A highlighted term that is neither a generic effect the readers know nor a plain stat / target phrase: the name
    of a mechanic ("offering", "venom", "focus", "erosion", ...)."""
    word = keyword.strip().lower()
    return bool(word) and not _GENERIC.match(word) and not _HANDLED.search(word)


def step_kind(description: str) -> str:
    text = description.lower()
    for key, kind in (
        ("cooldown", "cooldown"),
        ("effect chance", "effect chance"),
        ("combat readiness", "combat readiness"),
        ("heal", "healing"),
        ("barrier", "barrier"),
        ("damage", "damage"),
    ):
        if key in text:
            return kind
    return "other"


@dataclass(frozen=True, slots=True)
class Vocabulary:
    keyword: str
    heroes: int
    """How many heroes mention it."""
    weight: int
    """The same, counting how often each hero is met in Arena (the sightings; 0 when unknown)."""
    examples: tuple[str, ...]
    """The heroes (names) that mention it, the most met first."""


def vocabulary(audits: Sequence[HeroAudit], seen: Mapping[str, int] | None = None) -> list[Vocabulary]:
    """The unknown named mechanics over all heroes, the most widespread (and most met) first."""
    seen = seen or {}
    mentions: dict[str, list[HeroAudit]] = defaultdict(list)
    for hero in audits:
        for word in hero.unknown:
            mentions[word].append(hero)
    rows = [
        Vocabulary(
            word,
            len(heroes),
            sum(seen.get(h.code, 0) for h in heroes),
            tuple(h.name for h in sorted(heroes, key=lambda h: -seen.get(h.code, 0)))[:5],
        )
        for word, heroes in mentions.items()
    ]
    return sorted(rows, key=lambda r: (-r.weight, -r.heroes, r.keyword))


@dataclass(frozen=True, slots=True)
class ScreenNeed:
    """One skill whose in-game popup would teach the program something no other source has."""

    code: str
    name: str
    slot: int
    why: tuple[str, ...]
    """The unknown mechanics it would describe, or what is not settled for the skill itself."""


def screens_needed(
    audits: Sequence[HeroAudit], known: set[str] | None = None, seen: Mapping[str, int] | None = None
) -> list[ScreenNeed]:
    """The fewest skill popups that show a description of every unknown named mechanic (a popup shows the description of
    each effect the skill names), chosen greedily by how many still-unseen mechanics each one adds, the most met heroes
    first among equals. `known`: mechanics already described (captured or read before)."""
    seen = seen or {}
    remaining = {w for h in audits for w in h.unknown} - (known or set())
    options = [(h, s, set(s.unknown) & remaining) for h in audits for s in h.skills if set(s.unknown) & remaining]
    chosen: list[ScreenNeed] = []
    while remaining and options:
        h, s, words = max(options, key=lambda o: (len(o[2] & remaining), seen.get(o[0].code, 0)))
        gained = words & remaining
        if not gained:
            break
        chosen.append(ScreenNeed(h.code, h.name, s.slot, tuple(sorted(gained))))
        remaining -= gained
    return chosen


@dataclass(slots=True)
class Summary:
    heroes: int = 0
    no_page: list[str] = field(default_factory=list)
    done: int = 0
    hand: int = 0
    unread: int = 0
    """Heroes with at least one skill flagged "not read"."""
    skills: int = 0
    skills_unread: int = 0
    library: int = 0
    cooldown_cuts: int = 0
    """Skills whose cooldown is shorter with the enhancements."""
    steps: Counter[str] = field(default_factory=Counter)


def summarize(audits: Sequence[HeroAudit]) -> Summary:
    total = Summary(heroes=len(audits))
    for hero in audits:
        if not hero.page:
            total.no_page.append(hero.name)
            continue
        total.done += hero.done
        total.hand += hero.hand
        total.unread += any(s.unread for s in hero.skills)
        total.library += hero.library
        for skill in hero.skills:
            total.skills += 1
            total.skills_unread += skill.unread
            total.cooldown_cuts += skill.max_cooldown != skill.cooldown
            total.steps.update(skill.steps)
    return total


type Progress = Callable[[int, int, str], None]


def to_json(audits: Sequence[HeroAudit]) -> list[dict[str, object]]:
    return [asdict(hero) for hero in audits]
