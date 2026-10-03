"""e7calc (tyopoyt/epic7-damage-calc) — community cross-check source (docs/DATA_SOURCES.md §3).

`heroes.ts` is TypeScript, not data. We only extract *literal* values from the prettier-formatted source:
element, class, base ATK/HP/DEF and, per skill s1..s3, constant `rate`/`pow` (`() => 1.2`), the
`soulburn ? a : b` form, and `enhance: [...]`. Anything computed is ignored (never guessed); those skills simply
have no e7calc facts. Hero keys (`blood_blade_karin`) are mapped to hero codes via an exact normalised-name index
built from another source (Fribbels slugs/names); unmapped keys are reported, never fuzzy-matched.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Final

from pydantic import JsonValue

from e7ac.catalog.facts import EntityType, Fact, SourceRun, skill_id
from e7ac.catalog.names import NameIndex
from e7ac.domain.codes import DataStatus, Element, HeroClass, SourceId
from e7ac.sources.http import CachedHttp, FetchResult

# The default branch is `master` (checked 2026-10-03 with `git ls-remote --symref`).
HEROES_URL: Final = (
    "https://raw.githubusercontent.com/tyopoyt/epic7-damage-calc/master/damage-calc/src/assets/data/heroes.ts"
)
DEFAULT_MAX_AGE: Final = timedelta(days=1)

LEGACY_SUFFIX: Final = "_old"
# e7calc keys whose spelling differs from the official name. Checked by hand on 2026-10-03 and validated at
# runtime: a mapping is rejected when e7calc's element/class differ from the official ones.
KEY_ALIASES: Final[Mapping[str, str]] = {
    "archdemon_shadow": "Archdemon's Shadow",
    "baal_and_sezan": "Baal & Sezan",
    "sage_baal_and_sezan": "Sage Baal & Sezan",
    "summer_disciple_alexa": "Summer's Disciple Alexa",
    "kanna": "Bomb Model Kanna",
}
BASE_STAT_NOTE: Final = (
    "e7calc 'base' ATK/HP/DEF are tuned for its damage maths and can include permanent passive bonuses "
    "(e.g. Senya 1445 vs 1112, Gunther 1426 vs 951) - corroboration only"
)

_ELEMENTS: Final = {
    "fire": Element.FIRE,
    "ice": Element.ICE,
    "earth": Element.EARTH,
    "light": Element.LIGHT,
    "dark": Element.DARK,
}
_CLASSES: Final = {
    "warrior": HeroClass.WARRIOR,
    "knight": HeroClass.KNIGHT,
    "thief": HeroClass.THIEF,
    "ranger": HeroClass.RANGER,
    "mage": HeroClass.MAGE,
    "soul_weaver": HeroClass.SOUL_WEAVER,
}
_HERO_START: Final = re.compile(r"^\s*(?P<key>[a-z0-9_]+): new Hero\(\{", re.MULTILINE)
_SKILL_START: Final = re.compile(r"(?:^|[\s,{])(?P<key>s[123]): new Skill\(\{")
_NUM: Final = r"-?\d+(?:\.\d+)?"
_FIELD_NUM: Final = {
    "base_atk": re.compile(rf"^\s*baseAttack: (?P<v>{_NUM}),", re.MULTILINE),
    "base_hp": re.compile(rf"^\s*baseHP: (?P<v>{_NUM}),", re.MULTILINE),
    "base_def": re.compile(rf"^\s*baseDefense: (?P<v>{_NUM}),", re.MULTILINE),
}
_ELEMENT_RE: Final = re.compile(r"^\s*element: HeroElement\.(?P<v>\w+),", re.MULTILINE)
_CLASS_RE: Final = re.compile(r"^\s*class: HeroClass\.(?P<v>\w+),", re.MULTILINE)
_SKILLS_RE: Final = re.compile(r"^\s*skills: \{", re.MULTILINE)


def _const_or_soulburn(name: str) -> re.Pattern[str]:
    return re.compile(
        rf"^\s*{name}: \((?:_?soulburn(?:: boolean)?)?[^)]*\) => "
        rf"(?:(?P<const>{_NUM})|_?soulburn \? (?P<sb>{_NUM}) : (?P<base>{_NUM})),\s*$",
        re.MULTILINE,
    )


_RATE: Final = _const_or_soulburn("rate")
_POW: Final = _const_or_soulburn("pow")
_ENHANCE: Final = re.compile(rf"^\s*enhance: \[(?P<v>(?:\s*{_NUM}\s*,?)*)\],\s*$", re.MULTILINE)


def matching_brace(text: str, open_index: int) -> int:
    """Index of the `}` closing the `{` at `open_index`, skipping strings, template literals and comments."""
    if text[open_index] != "{":
        raise ValueError("open_index must point at '{'")
    depth = 0
    i = open_index
    length = len(text)
    while i < length:
        ch = text[i]
        if ch in "\"'`":
            i = _skip_string(text, i)
            continue
        if text.startswith("//", i):
            newline = text.find("\n", i)
            i = length if newline == -1 else newline
            continue
        if text.startswith("/*", i):
            close = text.find("*/", i + 2)
            i = length if close == -1 else close + 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("unbalanced braces")


def _skip_string(text: str, start: int) -> int:
    quote = text[start]
    i = start + 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == quote:
            return i + 1
        i += 1
    return len(text)


def _top_level(block: str) -> str:
    """The text of a `{...}` body with every nested `{...}` region removed (so nested objects cannot match)."""
    out: list[str] = []
    i = 0
    while i < len(block):
        if block[i] == "{":
            i = matching_brace(block, i) + 1
            out.append("{}")
            continue
        out.append(block[i])
        i += 1
    return "".join(out)


@dataclass(frozen=True, slots=True)
class E7calcSkill:
    slot: int
    rate: float | None = None
    rate_soulburn: float | None = None
    pow: float | None = None
    pow_soulburn: float | None = None
    enhance: tuple[float, ...] | None = None


@dataclass(frozen=True, slots=True)
class E7calcHero:
    key: str
    element: Element | None
    role: HeroClass | None
    base_atk: float | None
    base_hp: float | None
    base_def: float | None
    skills: tuple[E7calcSkill, ...] = ()


@dataclass(frozen=True, slots=True)
class E7calcData:
    heroes: list[E7calcHero]
    results: list[FetchResult]
    warnings: list[str] = field(default_factory=list)


def fetch(http: CachedHttp, *, refresh: bool = False, max_age: timedelta = DEFAULT_MAX_AGE) -> E7calcData:
    result = http.get_text("e7calc", HEROES_URL, max_age=max_age, refresh=refresh)
    heroes, warnings = parse_heroes_ts(result.text)
    return E7calcData(heroes=heroes, results=[result], warnings=warnings)


def parse_heroes_ts(text: str) -> tuple[list[E7calcHero], list[str]]:
    warnings: list[str] = []
    starts = list(_HERO_START.finditer(text))
    if not starts:
        raise ValueError("e7calc heroes.ts: no `key: new Hero({` entries found (format changed?)")
    heroes = []
    for match in starts:
        open_index = match.end() - 1
        try:
            close_index = matching_brace(text, open_index)
        except ValueError:
            warnings.append(f"e7calc {match.group('key')}: unbalanced braces, skipped")
            continue
        heroes.append(_parse_hero(match.group("key"), text[open_index + 1 : close_index], warnings))
    declared = text.count("new Hero(")
    if declared != len(heroes):
        warnings.append(f"e7calc: parsed {len(heroes)} heroes but the file declares {declared} `new Hero(`")
    return heroes, warnings


def _parse_hero(key: str, block: str, warnings: list[str]) -> E7calcHero:
    top = _top_level(block)

    def num(name: str) -> float | None:
        found = _FIELD_NUM[name].search(top)
        return float(found.group("v")) if found else None

    element_match = _ELEMENT_RE.search(top)
    class_match = _CLASS_RE.search(top)
    element = _ELEMENTS.get(element_match.group("v")) if element_match else None
    role = _CLASSES.get(class_match.group("v")) if class_match else None
    if element is None or role is None:
        warnings.append(f"e7calc {key}: element/class not recognised")
    skills = []
    skills_match = _SKILLS_RE.search(block)
    if skills_match is not None:
        skills_open = skills_match.end() - 1
        skills_body = block[skills_open + 1 : matching_brace(block, skills_open)]
        skills_top_level = _top_level(skills_body)
        for skill_match in _SKILL_START.finditer(skills_top_level):
            # locate the same skill in the full body to read its own (un-stripped) block
            full = re.search(rf"(?:^|[\s,{{]){skill_match.group('key')}: new Skill\(\{{", skills_body)
            if full is None:
                continue
            open_index = full.end() - 1
            body = skills_body[open_index + 1 : matching_brace(skills_body, open_index)]
            skills.append(_parse_skill(int(skill_match.group("key")[1]), _top_level(body)))
    return E7calcHero(
        key=key,
        element=element,
        role=role,
        base_atk=num("base_atk"),
        base_hp=num("base_hp"),
        base_def=num("base_def"),
        skills=tuple(skills),
    )


def _parse_skill(slot: int, body: str) -> E7calcSkill:
    def pair(pattern: re.Pattern[str]) -> tuple[float | None, float | None]:
        found = pattern.search(body)
        if not found:
            return None, None
        if found.group("const") is not None:
            return float(found.group("const")), None
        return float(found.group("base")), float(found.group("sb"))

    rate, rate_sb = pair(_RATE)
    pow_, pow_sb = pair(_POW)
    enhance_match = _ENHANCE.search(body)
    enhance = None
    if enhance_match:
        enhance = tuple(float(x) for x in re.findall(_NUM, enhance_match.group("v")))
    return E7calcSkill(slot=slot, rate=rate, rate_soulburn=rate_sb, pow=pow_, pow_soulburn=pow_sb, enhance=enhance)


def to_facts(
    data: E7calcData,
    names: NameIndex,
    known: Mapping[str, tuple[str, str]] | None = None,
) -> tuple[list[Fact], SourceRun]:
    """Map e7calc heroes to codes by exact normalised name (or an explicit alias) and emit facts.

    `known`: hero code -> (element, role) from another source. A mapping whose element/class disagree is rejected
    (protects against wrong aliases and name collisions)."""
    facts: list[Fact] = []
    warnings = list(data.warnings)
    unmapped: list[str] = []
    rejected: list[str] = []
    legacy = 0

    def add(
        entity_type: EntityType, entity_id: str, name: str, value: JsonValue, status: DataStatus, note: str = ""
    ) -> None:
        facts.append(
            Fact(
                entity_type=entity_type,
                entity_id=entity_id,
                field=name,
                value=value,
                source=SourceId.E7CALC,
                status=status,
                note=note,
            )
        )

    for hero in data.heroes:
        if hero.key.endswith(LEGACY_SUFFIX):
            legacy += 1
            continue
        lookup_name = KEY_ALIASES.get(hero.key, hero.key)
        if names.is_ambiguous(lookup_name):
            unmapped.append(f"{hero.key} (ambiguous name)")
            continue
        code = names.lookup(lookup_name)
        if code is None:
            unmapped.append(hero.key)
            continue
        expected = (known or {}).get(code)
        actual = (hero.element.value if hero.element else None, hero.role.value if hero.role else None)
        if expected is not None and actual != expected:
            rejected.append(f"{hero.key}->{code} (e7calc {actual}, catalog {expected})")
            continue
        if hero.element is not None:
            add(EntityType.HERO, code, "element", hero.element.value, DataStatus.COMMUNITY)
        if hero.role is not None:
            add(EntityType.HERO, code, "role", hero.role.value, DataStatus.COMMUNITY)
        for name, value in (("base.att", hero.base_atk), ("base.max_hp", hero.base_hp), ("base.def", hero.base_def)):
            if value is not None:
                add(EntityType.HERO, code, name, value, DataStatus.ASSUMED, note=BASE_STAT_NOTE)
        for skill in hero.skills:
            sid = skill_id(code, skill.slot)
            for name, value in (
                ("rate", skill.rate),
                ("rate_soulburn", skill.rate_soulburn),
                ("pow", skill.pow),
                ("pow_soulburn", skill.pow_soulburn),
            ):
                if value is not None:
                    add(EntityType.SKILL, sid, name, value, DataStatus.COMMUNITY)
            if skill.enhance is not None:
                enhance: list[JsonValue] = [float(x) for x in skill.enhance]
                add(EntityType.SKILL, sid, "enhance", enhance, DataStatus.COMMUNITY)
    if legacy:
        warnings.append(f"e7calc: {legacy} pre-rework '*{LEGACY_SUFFIX}' entries ignored (old versions of heroes)")
    if unmapped:
        warnings.append(f"e7calc: {len(unmapped)} hero key(s) without an exact name match: {', '.join(unmapped)}")
    if rejected:
        warnings.append(f"e7calc: {len(rejected)} mapping(s) rejected (element/class mismatch): {', '.join(rejected)}")
    digest = hashlib.sha256("".join(r.sha256 for r in data.results).encode()).hexdigest()[:16]
    run = SourceRun(
        source=SourceId.E7CALC,
        version=f"sha256:{digest}",
        retrieved_at=min(r.fetched_at for r in data.results),
        from_cache=all(r.from_cache for r in data.results),
        stale=any(r.stale for r in data.results),
        facts=len(facts),
        warnings=tuple(warnings),
    )
    return facts, run
