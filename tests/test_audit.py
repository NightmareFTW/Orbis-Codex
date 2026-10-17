"""The skill audit (SPEC D90): epic7db's skill details (name, soulburn, enhancement steps, keywords, maximum cooldown),
the Fribbels library block of a hero page, and the audit over many heroes (synthetic pages)."""

from __future__ import annotations

from typing import cast

from e7ac.domain.codes import Stat
from e7ac.predict.audit import (
    audit_hero,
    is_named_mechanic,
    screens_needed,
    step_kind,
    summarize,
    to_json,
    vocabulary,
)
from e7ac.sources.epic7db import parse_kit, parse_library
from e7ac.sources.http import CachedHttp


def skill(
    slot: int,
    name: str,
    cooldown: str,
    text: str,
    steps: list[tuple[int, str]],
    icons: str = "",
    soulburn: str = "",
    souls: int | None = None,
) -> str:
    upgrades = "".join(
        f'<div class="upgrade"><div class="level"> {level} </div><div class="description"> {desc} </div>'
        f'<div class="cost"><div class="resource">Gold (4000)</div></div></div>'
        for level, desc in steps
    )
    gain = f'<div class="soul-gain"> + {souls} souls </div>' if souls else ""
    burn = f'<div class="soulburn"><p>{soulburn}</p></div>' if soulburn else ""
    return (
        f'<div class="icon"><img src="/images/skills/x_skill_{slot}.webp" alt="{name}"></div>'
        f'<div class="details"><div class="main"><div class="top"><div class="cooldown"> {cooldown} </div>{gain}'
        f'<div class="status-effects">{icons}</div></div><div class="bottom"><p><p>{text}</p></p></div></div>{burn}'
        f'</div><div class="skill-upgrades"><div class="content">{upgrades}</div></div>'
    )


def stat_item(name: str, label: str, value: str) -> str:
    return f'<li><img src="/a.png" alt="{name}">{label}: {value}</li>'


LIBRARY = (
    '<section id="fribbels" class="section-accordion"><p>Updated on October 16th, 2026 at 03:40:28.</p>'
    "<ul><li><h4>Average Stats:</h4></li>"
    + stat_item("attack", "Attack", "1383")
    + stat_item("crit_chance", "Crit Chance", "19%")
    + stat_item("crit_damage", "Crit Damage", "153%")
    + stat_item("defense", "Defense", "1439")
    + stat_item("effectiveness", "Effectiveness", "51%")
    + stat_item("effect_resistance", "Effect Resistance", "182%")
    + stat_item("health", "Health", "16673")
    + stat_item("speed", "Speed", "262")
    + '</ul><ul class="gearsets"><li><h4>Set 1 (38.26% Use Rate)</h4></li>'
    '<li class="gearset"><img alt="Resist gear set"> Resist</li>'
    '<li class="gearset"><img alt="Speed gear set"> Speed</li></ul>'
    '<ul class="gearsets"><li><h4>Set 2 (23.24% Use Rate)</h4></li>'
    '<li class="gearset"><img alt="Immunity gear set"> Immunity</li></ul>'
    "<h2>Top Fribbels Builds</h2><h3>Build 1</h3><ul>"
    + stat_item("attack", "Attack", "1403")
    + stat_item("crit_chance", "Crit Chance", "103%")
    + stat_item("speed", "Speed", "312")
    + '</ul><h4>Gear Sets</h4><li><img alt="Speed gear set"> Speed</li>'
    '<h4>Artifact</h4><img alt="Bird\'s-Eye View">'
    "<h3>Build 2</h3><ul>"
    + stat_item("attack", "Attack", "1346")
    + '</ul><li><img alt="Defense gear set"> Defense</li>'
    '<h4>Artifact</h4><img alt="Cure-All Magic Wand"> View All Fribbels Builds</section>'
)

PAGE = (
    skill(
        1,
        "Divine Punishment",
        "None",
        "Attacks the enemy and has a 75% chance to inflict <strong>fear</strong> for 1 turn, before recovering "
        "Health of all allies proportional to the <strong>target's max Health</strong>.",
        [(1, "+5% damage dealt"), (2, "+10% effect chance")],
        soulburn="Increases effect chance to 100%, and attacks all enemies. (-10 souls)",
        souls=1,
    )
    + skill(
        2,
        "Scales of Equity",
        "Passive",
        "At the start of battle, grants <strong>offering</strong> to the foremost ally.",
        [(1, "+2% Combat Readiness increase")],
    )
    + skill(
        3,
        "The Caress of Mercy",
        "5 Turns",
        "With the Goddess's authority, <strong>dispels all debuffs</strong> from all allies before "
        "<strong>increasing Combat Readiness</strong> by 25%. Grants <strong>abundance</strong> for 2 turns.",
        [(1, "+2% Combat Readiness"), (3, "-1 turn cooldown"), (5, "-1 turn cooldown")],
        souls=3,
    )
    + LIBRARY
)


def test_skill_details_are_read_from_a_page() -> None:
    kit = parse_kit(PAGE)
    s1, s2, s3 = kit[1], kit[2], kit[3]
    assert (s1.name, s1.souls, s1.keywords) == ("Divine Punishment", 1, ("fear", "target's max health"))
    assert s1.soulburn.startswith("Increases effect chance to 100%") and s1.soulburn.endswith("(-10 souls)")
    assert s1.enhance == ((1, "+5% damage dealt"), (2, "+10% effect chance"))
    assert s2.cooldown is None and s2.max_cooldown is None and s2.keywords == ("offering",)
    assert (s3.cooldown, s3.max_cooldown) == (5, 3)  # both "-1 turn cooldown" steps count
    assert kit[1].max_cooldown == 0  # no cooldown stays none


def test_the_fribbels_library_block_of_a_page_is_read() -> None:
    library = parse_library(PAGE)
    assert library is not None and library.updated.startswith("October 16th, 2026")
    assert library.average[Stat.ATK] == 1383 and library.average[Stat.CRIT_CHANCE] == 0.19
    assert library.average[Stat.EFFECT_RESISTANCE] == 1.82 and library.average[Stat.SPEED] == 262
    assert [(round(rate, 4), sets) for rate, sets in library.set_combos] == [
        (0.3826, ("Resist", "Speed")),
        (0.2324, ("Immunity",)),
    ]
    first, second = library.builds
    assert first.stats[Stat.SPEED] == 312 and first.stats[Stat.CRIT_CHANCE] == 1.03
    assert first.sets == ("Speed",) and first.artifact == "Bird's-Eye View"
    assert second.sets == ("Defense",) and second.artifact == "Cure-All Magic Wand"
    assert parse_library("<html>no block</html>") is None


def test_named_mechanics_are_the_terms_no_reader_knows() -> None:
    for known in ("fear", "target's max health", "dispels all debuffs", "increasing combat readiness", "healing"):
        assert not is_named_mechanic(known)
    for named in ("offering", "abundance", "venom", "focus", "erosion", "fighting spirit"):
        assert is_named_mechanic(named)
    assert step_kind("-1 turn cooldown") == "cooldown" and step_kind("+10% effect chance") == "effect chance"
    assert step_kind("+5% healing") == "healing" and step_kind("+15% damage dealt") == "damage"


def test_the_audit_of_a_hero_records_what_was_read_and_what_is_unknown() -> None:
    hero = audit_hero("c6005", "Lady", parse_kit(PAGE), library=parse_library(PAGE))
    assert hero.page and hero.library and not hero.hand
    s1, s2, s3 = hero.skills
    assert s1.max_cooldown == 0 and s3.cooldown == 5 and s3.max_cooldown == 3
    assert s1.steps == ("damage", "effect chance") and s1.soulburn and not s3.soulburn
    assert s1.unknown == () and s2.unknown == ("offering",) and s3.unknown == ("abundance",)
    assert hero.unknown == ("offering", "abundance")
    assert not hero.done  # her S2 and S3 mention effects no reader reads


def test_an_unknown_page_is_reported_and_the_json_keeps_everything() -> None:
    blank = audit_hero("c9999", "Nobody", {})
    assert not blank.page and not blank.done and blank.skills == ()
    total = summarize([blank, audit_hero("c6005", "Lady", parse_kit(PAGE), library=parse_library(PAGE))])
    assert total.heroes == 2 and total.no_page == ["Nobody"] and total.library == 1 and total.cooldown_cuts == 1
    rows = to_json([blank])
    assert rows[0]["code"] == "c9999" and rows[0]["skills"] == ()


def test_the_vocabulary_and_the_fewest_popups_cover_every_unknown_mechanic() -> None:
    def hero(code: str, name: str, *texts: str) -> object:
        html = "".join(skill(i, f"S{i}", "None", t, []) for i, t in enumerate(texts, 1))
        return audit_hero(code, name, parse_kit(html))

    heroes = [
        hero("c1", "Alpha", "<strong>offering</strong> and <strong>venom</strong>", "<strong>focus</strong>"),
        hero("c2", "Beta", "<strong>venom</strong>"),
        hero("c3", "Gamma", "<strong>offering</strong>", "<strong>erosion</strong> and <strong>focus</strong>"),
    ]
    rows = vocabulary(heroes, {"c2": 9})  # type: ignore[arg-type]
    assert rows[0].keyword == "venom" and rows[0].weight == 9 and rows[0].heroes == 2  # met most
    assert {r.keyword for r in rows} == {"offering", "venom", "focus", "erosion"}
    needs = screens_needed(heroes, seen={"c2": 9})  # type: ignore[arg-type]
    covered = {word for need in needs for word in need.why}
    assert covered == {"offering", "venom", "focus", "erosion"} and len(needs) == 2  # Alpha S1 + Gamma S2
    assert screens_needed(heroes, known={"offering", "venom", "focus", "erosion"}) == []  # type: ignore[arg-type]


def test_the_library_profile_gives_an_opponent_the_fribbels_average_and_its_speed() -> None:
    from e7ac.cli.predict import _Fighter, _from_library

    class Page:
        text = "<h1>Lady of the Scales</h1>" + PAGE

    class Http:
        def get_text(self, *args: object, **kwargs: object) -> Page:
            return Page()

    fighter = _Fighter("Lady of the Scales", "them", "c6005", name="Lady of the Scales")
    http = cast(CachedHttp, Http())
    assert _from_library(http, fighter)
    assert fighter.stats[Stat.SPEED] == 262 and fighter.stats[Stat.EFFECT_RESISTANCE] == 1.82
    assert fighter.speed == 262 and "Fribbels library average" in fighter.spread
    given = _Fighter("Lady of the Scales", "them", "c6005", speed="270", name="Lady of the Scales")
    assert _from_library(http, given) and given.speed == 270.0  # a speed the user gave is kept
    nameless = _Fighter("Nobody", "them", "c9999")
    assert not _from_library(http, nameless)
