"""Everything the app knows about one hero, in sections (SPEC D133): the page "Hero" shows it.

A `Sheet` is plain data: a header (name, element, class, stars) and sections of rows or paragraphs. It is gathered from
what is on this PC: the catalog, the roster (every copy of the hero), the skills read from the game, the hand-written
kit and what the Arena watch has seen. A section with nothing to say says so (never left out silently). Pure: no Qt."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from e7ac.appdata import open_engine
from e7ac.arena.references import ReferenceStore
from e7ac.arena.sightings import SightingStore, popularity, sightings_path
from e7ac.catalog.resolve import ResolvedEntity
from e7ac.domain.codes import Stat
from e7ac.domain.roster import HeroBuild
from e7ac.paths import AppPaths
from e7ac.predict.overrides import KitError, KitOverride, load_overrides
from e7ac.roster.store import build_from_row, list_owned
from e7ac.skills.store import SkillStore, StoredSkill, skills_dir
from e7ac.storage.db import session_scope

Row = tuple[str, str]


@dataclass(frozen=True, slots=True)
class Section:
    title: str
    rows: tuple[Row, ...] = ()
    """Label and value, one per line."""
    paragraphs: tuple[str, ...] = ()
    """Free text under the rows."""
    empty: str = ""
    """What to say when there is nothing in the section."""


@dataclass(frozen=True, slots=True)
class Sheet:
    code: str
    name: str
    tags: tuple[str, ...] = ()
    """Element, class, rarity."""
    sections: tuple[Section, ...] = ()
    builds: tuple[HeroBuild, ...] = ()
    """Every owned copy of the hero (the page draws them as stat tiles and a gear grid)."""
    skills: tuple[StoredSkill, ...] = ()
    """The skills as read from the game, in the hero's order."""


_STAT_NAMES = {
    Stat.ATK: "Attack",
    Stat.ATK_PERCENT: "Attack %",
    Stat.HP: "Health",
    Stat.HP_PERCENT: "Health %",
    Stat.DEF: "Defense",
    Stat.DEF_PERCENT: "Defense %",
    Stat.SPEED: "Speed",
    Stat.CRIT_CHANCE: "Crit Chance",
    Stat.CRIT_DAMAGE: "Crit Damage",
    Stat.EFFECTIVENESS: "Effectiveness",
    Stat.EFFECT_RESISTANCE: "Effect Resistance",
    Stat.DUAL_ATTACK: "Dual Attack",
}


def stat_text(stat: Stat, value: float) -> str:
    """ "Speed 45", "Crit Chance 62%": rates are fractions in the data, percentages on the screen."""
    shown = f"{value * 100:.1f}".rstrip("0").rstrip(".") + "%" if stat.is_rate else f"{value:g}"
    return f"{_STAT_NAMES[stat]} {shown}"


def build_sheet(paths: AppPaths, heroes: dict[str, ResolvedEntity], code: str) -> Sheet:
    """The sheet of a hero by its code."""
    hero = heroes.get(code)
    name = hero.name if hero is not None and hero.name else code
    tags = tuple(
        str(v).title() for key in ("element", "role") if hero is not None and (v := hero.value(key)) is not None
    ) + ((f"{hero.value('rarity')}-star base",) if hero is not None and hero.value("rarity") is not None else ())
    stored = SkillStore(skills_dir(paths.home)).load(code)
    sections = [
        _copies(paths, code),
        _skills(stored),
        _kit(code, stored),
        _arena(paths, heroes, code),
    ]
    builds = _builds(paths, code)
    ordered = tuple(sorted(stored, key=lambda s: (s.slot or 9, s.name)))
    return Sheet(code, name, tags, tuple(sections), builds, ordered)


def _builds(paths: AppPaths, code: str) -> tuple[HeroBuild, ...]:
    if not paths.database.is_file():
        return ()
    found: list[HeroBuild] = []
    with session_scope(open_engine(paths)) as session:
        for owned, row in list_owned(session):
            if owned.hero_code == code and row is not None:
                found.append(build_from_row(session, row))
    return tuple(found)


SET_NAMES = {
    "set_cri_dmg": "Destruction",
    "set_max_hp": "Health",
    "set_att": "Attack",
    "set_acc": "Hit",
    "set_res": "Resist",
    "set_def": "Defense",
    "set_cri": "Critical",
    "set_speed": "Speed",
    "set_rage": "Rage",
    "set_vampire": "Lifesteal",
    "set_revenge": "Revenge",
    "set_counter": "Counter",
    "set_scar": "Scar",
    "set_torrent": "Torrent",
}
"""Set code -> the name the game shows (only the ones that are certain; any other is shown as its code)."""


def set_name(code: str) -> str:
    return SET_NAMES.get(code, code.removeprefix("set_").replace("_", " ").title())


def _copies(paths: AppPaths, code: str) -> Section:
    """Every owned copy of the hero: stars, level, enhancements, imprint, gear, stats."""
    if not paths.database.is_file():
        return Section("Your hero", empty="No roster on this PC yet (scan your heroes or import Fribbels' file).")
    builds: list[HeroBuild] = []
    with session_scope(open_engine(paths)) as session:
        for owned, row in list_owned(session):
            if owned.hero_code == code and row is not None:
                builds.append(build_from_row(session, row))
    if not builds:
        return Section("Your hero", empty="Not in your roster (or no build of it was read yet).")
    rows: list[Row] = []
    for number, build in enumerate(builds, 1):
        mark = f" (copy {number})" if len(builds) > 1 else ""
        rows.append((f"Build{mark}", f"{build.stars} stars, awakening {build.awakening}, Lv. {build.level}"))
        s = build.skills
        rows.append(("Skill enhancements", f"S1 +{s.s1}  S2 +{s.s2}  S3 +{s.s3}"))
        if build.cp is not None:
            rows.append(("Combat power", f"{build.cp:,}"))
        if build.final_stats is not None:
            f = build.final_stats
            rows.append(("Stats", f"ATK {f.atk}  DEF {f.defense}  HP {f.hp}  SPD {f.speed}"))
            rows.append(
                (
                    "",
                    f"Crit {f.crit_chance * 100:.0f}% / {f.crit_damage * 100:.0f}%  Eff {f.effectiveness * 100:.0f}%  "
                    f"Res {f.effect_resistance * 100:.0f}%  Dual {f.dual_attack * 100:.0f}%",
                )
            )
        if build.imprint is not None:
            grade = build.imprint.grade.value if build.imprint.grade is not None else "?"
            rows.append(("Imprint", f"{grade}: {stat_text(build.imprint.stat, build.imprint.value)}"))
        if build.exclusive_equipment is not None:
            ee = build.exclusive_equipment
            text = ee.option_text or (stat_text(ee.stat, ee.value) if ee.stat and ee.value is not None else "?")
            rows.append(("Exclusive equipment", text))
        if build.artifact is not None:
            rows.append(("Artifact", f"{build.artifact.code}, level {build.artifact.level}"))
        for slot, gear in sorted(build.gear.items(), key=lambda kv: kv[0].value):
            subs = ", ".join(stat_text(x.stat, x.value) for x in gear.substats)
            rows.append(
                (
                    slot.value.title(),
                    f"{gear.set_code} +{gear.enhance}: {stat_text(gear.main.stat, gear.main.value)}"
                    + (f" | {subs}" if subs else ""),
                )
            )
        rows.append(("Read", f"{build.captured_at:%Y-%m-%d} from {build.source.value}"))
    return Section("Your hero", tuple(rows))


def _skills(stored: list[StoredSkill]) -> Section:
    """The three skills as they were read from the game, in order, with their effects."""
    if not stored:
        return Section("Skills as read from the game", empty="Not read yet: use the Skill reader (Dev tools).")
    rows: list[Row] = []
    paragraphs: list[str] = []
    for skill in sorted(stored, key=lambda s: (s.slot or 9, s.name)):
        slot = f"S{skill.slot}" if skill.slot else "S?"
        kind = "passive" if skill.passive else f"cooldown {skill.cooldown}" if skill.cooldown else "no cooldown"
        level = f" +{skill.level}" if skill.level is not None else ""
        rows.append((slot, f"{skill.name}{level} ({kind})"))
        paragraphs.append(f"{slot} {skill.name}: {skill.text}")
        if skill.soulburn:
            paragraphs.append(f"    Soulburn ({skill.soulburn_cost or '?'} Soul): {skill.soulburn}")
        if skill.enhance:
            paragraphs.append("    Enhancements: " + "; ".join(f"+{n} {t}" for n, t in skill.enhance))
        for effect in skill.effects:
            paragraphs.append(f"    {effect.title}: {effect.text}")
    return Section("Skills as read from the game", tuple(rows), tuple(paragraphs))


def _kit(code: str, stored: list[StoredSkill]) -> Section:
    """How the simulation plays the hero: whether a hand-written kit exists, where its numbers come from, what it leaves
    out."""
    try:
        hand: KitOverride | None = load_overrides().get(code)
    except KitError:
        hand = None
    read = len({s.slot for s in stored if s.slot})
    rows: list[Row] = [("Skills read", f"{read} of 3")]
    if hand is None:
        rows.append(("Kit in the simulation", "automatic (epic7db's icons and text), not read by hand"))
        return Section("In the simulation", tuple(rows), empty="")
    rows.append(("Kit in the simulation", f"written by hand ({hand.status})"))
    rows.append(("Numbers from", hand.source))
    return Section(
        "In the simulation",
        tuple(rows),
        tuple(f"Not simulated: {item}" for item in hand.unmodelled),
    )


def _arena(paths: AppPaths, heroes: dict[str, ResolvedEntity], code: str) -> Section:
    """How often the Arena watch has seen the hero, and whether it is in your last team."""
    sightings, _ = SightingStore(sightings_path(paths.home)).read()
    pop = popularity(sightings)
    seen = next((h for h in pop.heroes if h.code == code), None)
    mine = code in ReferenceStore(Path(paths.home) / "references" / "arena").last_team()
    rows: list[Row] = [("In your last Arena team", "yes" if mine else "no")]
    if pop.teams:
        count = seen.teams if seen is not None else 0
        share = f" ({seen.share * 100:.0f}% of the teams)" if seen is not None else ""
        rows.append(("Seen in opponent teams", f"{count} of {pop.teams}{share}"))
        together: dict[str, int] = defaultdict(int)
        for (a, b), n in pop.pairs:
            if code in (a, b):
                together[b if a == code else a] += n
        if together:
            top = sorted(together.items(), key=lambda kv: -kv[1])[:5]
            rows.append(("Often with", ", ".join(f"{_name(heroes, c)} ({n}x)" for c, n in top)))
    return Section("In the Arena", tuple(rows), empty="The Arena watch has not seen anything yet.")


def _name(heroes: dict[str, ResolvedEntity], code: str) -> str:
    hero = heroes.get(code)
    return hero.name if hero is not None and hero.name else code
