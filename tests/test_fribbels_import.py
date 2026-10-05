"""Fribbels save import (M4, path A of SPEC D45, D52): synthetic saves shaped like Fribbels' Gson output.

Never real data: a real save holds the user's whole account (fixtures/saves/ is git-ignored; see fixtures/README.md).
"""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from e7ac.catalog.facts import EntityType, Fact
from e7ac.catalog.resolve import ResolvedEntity, resolve
from e7ac.cli.app import app
from e7ac.domain.codes import DataStatus, SourceId, Stat
from e7ac.domain.roster import (
    ArtifactRef,
    BuildSource,
    ExclusiveEquipment,
    FinalStats,
    Gear,
    GearGrade,
    GearSlot,
    HeroBuild,
    Imprint,
    ImprintGrade,
    ImprintMode,
    SkillEnhancements,
    StatValue,
    Substat,
)
from e7ac.paths import AppPaths
from e7ac.roster.fribbels_import import (
    ASSUMED,
    ESTIMATED,
    USER_ENTERED,
    FribbelsFileError,
    FribbelsItem,
    FribbelsSave,
    HeroImport,
    catalog_names,
    gear_of,
    merge_with_current,
    read_fribbels_save,
)

runner = CliRunner()
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def fact(kind: EntityType, code: str, field: str, value: Any, source: SourceId = SourceId.FRIBBELS) -> Fact:
    return Fact(entity_type=kind, entity_id=code, field=field, value=value, source=source, status=DataStatus.COMMUNITY)


def entity(kind: EntityType, code: str, name: str, fribbels_name: str | None, **fields: Any) -> ResolvedEntity:
    facts = [fact(kind, code, "name", name, SourceId.STOVE)]
    if fribbels_name is not None:
        facts.append(fact(kind, code, "name", fribbels_name))
    facts += [fact(kind, code, key.replace("__", "."), value) for key, value in fields.items()]
    return resolve(facts)[0]


def hero(code: str, name: str, *, fribbels_name: str | None = None, **fields: Any) -> ResolvedEntity:
    return entity(EntityType.HERO, code, name, fribbels_name, **fields)


def artifact(code: str, name: str, *, fribbels_name: str | None = None) -> ResolvedEntity:
    return entity(EntityType.ARTIFACT, code, name, fribbels_name)


DENSE = {"D": 10, "C": 15, "B": 20, "A": 25, "S": 30, "SS": 35, "SSS": 40}
HEROES = {
    "c2011": hero(
        "c2011",
        "Blood Blade Karin",
        imprint__stat="att_rate",
        imprint__values={"C": 0.06, "SSS": 0.18},
        ee__stat="cri",
    ),
    "c9001": hero(
        "c9001", "Test Hero", fribbels_name="Test Hero (Fribbels)", imprint__stat="att", imprint__values=DENSE
    ),
    "c9002": hero("c9002", "Twin"),
    "c9003": hero("c9003", "Twin"),
    "c9005": hero("c9005", "Plain Hero"),
    "c9006": hero("c9006", "Echo", imprint__stat="att_rate", imprint__values={"B": 0.1, "A": 0.1}, ee__stat="speed"),
    "c9007": hero("c9007", "Rate Hero", imprint__stat="att_rate", imprint__values={"A": 0.15, "S": 0.172, "SS": 0.194}),
}
ARTIFACTS = {
    "efz01": artifact("efz01", "Test Dagger"),
    "efz03": artifact("efz03", "Guard Blade"),
    "efz05": artifact("efz05", "Twin Blade"),
    "efz06": artifact("efz06", "Twin Blade"),
    "efz07": artifact("efz07", "Queen\u2019s Charm", fribbels_name="Queen's Charm"),
    "efz09": artifact("efz09", "Old Relic"),
}


def stat(kind: str, value: float, rolls: int | None = None, modified: bool | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"type": kind, "value": value}
    if rolls is not None:
        out["rolls"] = rolls
    if modified is not None:
        out["modified"] = modified
    return out


SUBS = [
    stat("CriticalHitChancePercent", 10, 2),
    stat("CriticalHitDamagePercent", 14, 2, True),
    stat("Speed", 8, 2),
    stat("AttackPercent", 9, 1),
]
NOT_SET = object()


def item(
    gear: str,
    main: dict[str, Any],
    *,
    hero_id: str | None = "h1",
    wearer: object = "g1",
    ingame: object = NOT_SET,
    set_name: str = "SpeedSet",
    rank: str = "Epic",
    enhance: int = 15,
    level: int = 90,
    op: bool = True,
    subs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """An item as Fribbels saves it (`model/Item.java`, Gson field names), with fields we do not read.

    By default a piece imported from the game (`op`, `ingameId`, the game wearer "g1") and equipped in Fribbels on h1.
    `wearer=None` leaves out `ingameEquippedId` (no game data); `ingame=None` leaves out `ingameId`."""
    data: dict[str, Any] = {
        "gear": gear,
        "rank": rank,
        "set": set_name,
        "enhance": enhance,
        "level": level,
        "main": main,
        "substats": SUBS if subs is None else subs,
        "id": f"fid-{gear}-{hero_id}-{wearer}",
        "wss": 70,  # Fribbels' own score: ignored
        "locked": False,
    }
    if op:
        data["op"] = [["att", 525], ["cri", 0.05]]
    game_id = f"{hero_id}-{gear}-{wearer}" if ingame is NOT_SET else ingame
    if game_id is not None:
        data["ingameId"] = game_id
    if wearer is not None:
        data["ingameEquippedId"] = wearer
    if hero_id is not None:
        data["equippedById"] = hero_id
    return data


BBK = {
    "id": "h1",
    "name": "Blood Blade Karin",
    "stars": 6,
    "artifactName": "Test Dagger",
    "artifactLevel": "15",
    "imprintNumber": "18",
    "eeNumber": "12",
    "atk": 4116,  # Fribbels' computed stats: never taken
    "cp": 99999,
}
BBK_ITEMS = [
    item("Weapon", stat("Attack", 525), ingame="9001"),
    item("Helmet", stat("Health", 2835), ingame="9002"),
    item("Armor", stat("Defense", 310), ingame="9003"),
    item("Necklace", stat("CriticalHitDamagePercent", 70), set_name="DestructionSet", ingame="9004"),
    item("Ring", stat("AttackPercent", 65), set_name="DestructionSet", ingame="9005"),
    item("Boots", stat("Speed", 45), set_name="DestructionSet", rank="Heroic", ingame="9006"),
]


def save_text(heroes: list[dict[str, Any]], items: list[dict[str, Any]]) -> str:
    return json.dumps({"heroes": heroes, "items": items})


def read(heroes: list[dict[str, Any]], items: list[dict[str, Any]]) -> FribbelsSave:
    return read_fribbels_save(save_text(heroes, items), HEROES, ARTIFACTS, captured_at=NOW)


def only(heroes: list[dict[str, Any]], items: list[dict[str, Any]] | None = None) -> HeroImport:
    (entry,) = read(heroes, [] if items is None else items).heroes
    return entry


# ------------------------------------------------------------------------------------------------ reading


def test_a_fribbels_hero_becomes_a_build_with_its_game_gear() -> None:
    save = read([BBK], BBK_ITEMS)
    assert save.warnings == [] and save.unused_items == 0
    (entry,) = save.heroes
    assert (entry.hero_code, entry.problems, entry.notes) == ("c2011", [], [])
    build = entry.build
    assert build is not None and build.source is BuildSource.FRIBBELS and build.captured_at == NOW
    assert sorted(build.gear) == sorted(GearSlot)
    assert build.gear[GearSlot.WEAPON] == Gear(
        slot=GearSlot.WEAPON,
        set_code="set_speed",
        grade=GearGrade.EPIC,
        item_level=90,
        enhance=15,
        main=StatValue(stat=Stat.ATK, value=525),
        substats=(
            Substat(stat=Stat.CRIT_CHANCE, value=0.1, rolls=2),
            Substat(stat=Stat.CRIT_DAMAGE, value=0.14, rolls=2, modified=True),
            Substat(stat=Stat.SPEED, value=8, rolls=2),
            Substat(stat=Stat.ATK_PERCENT, value=0.09, rolls=1),
        ),
        external_id="ingame:9001",
    )
    assert build.gear[GearSlot.NECKLACE].set_code == "set_cri_dmg"
    assert build.gear[GearSlot.NECKLACE].main == StatValue(stat=Stat.CRIT_DAMAGE, value=0.7)
    assert build.gear[GearSlot.BOOTS].grade is GearGrade.HEROIC
    assert build.final_stats is None and build.cp is None  # Fribbels' computed stats are never taken
    assert not any(key.startswith("gear.") for key in build.confidence)  # all +15 pieces read from the game
    assert save.locations["ingame:9001"] == ("h1", "on Blood Blade Karin")


def test_typed_bonuses_and_stars_have_a_lower_confidence() -> None:
    build = only([BBK], BBK_ITEMS).build
    assert build is not None
    assert build.artifact == ArtifactRef(code="efz01", level=15)
    assert build.imprint == Imprint(grade=ImprintGrade.SSS, stat=Stat.ATK_PERCENT, value=0.18, mode=ImprintMode.SELF)
    assert build.exclusive_equipment == ExclusiveEquipment(stat=Stat.CRIT_CHANCE, value=0.12)
    for key in ("artifact", "imprint", "imprint.mode", "imprint.grade", "exclusive_equipment", "stars"):
        assert build.confidence[key] == USER_ENTERED, key


def test_level_and_awakening_are_assumed_from_the_stars() -> None:
    build = only([{**BBK, "stars": 5}]).build
    assert build is not None and (build.stars, build.awakening, build.level) == (5, 5, 50)
    assert build.confidence["level"] == build.confidence["awakening"] == ASSUMED
    assert build.gear == {}


@pytest.mark.parametrize("stars", [0, 9, None])
def test_unusable_stars_are_assumed_and_reported(stars: int | None) -> None:
    entry = only([{**BBK, "stars": stars}])
    assert entry.build is not None and entry.build.confidence["stars"] == ASSUMED and entry.build.stars == 6
    assert entry.notes == [f"stars {stars!r} not usable: assumed 6"]


def test_unset_bonuses_are_left_empty() -> None:
    unset = {"artifactName": "None", "artifactLevel": "None", "imprintNumber": "None", "eeNumber": ""}
    build = only([{**BBK, **unset}]).build
    assert build is not None and (build.artifact, build.imprint, build.exclusive_equipment) == (None, None, None)
    assert not {"artifact", "imprint", "exclusive_equipment"} & set(build.confidence)


@pytest.mark.parametrize(
    ("number", "grade"),
    [("25", ImprintGrade.A), ("26", None), (40, ImprintGrade.SSS), ("12.5", None)],
)
def test_an_imprint_grade_needs_an_exact_unique_match(number: str | int, grade: ImprintGrade | None) -> None:
    build = only([{"id": "h2", "name": "Test Hero", "stars": 6, "imprintNumber": number}]).build
    assert build is not None and build.imprint is not None
    assert (build.imprint.stat, build.imprint.value, build.imprint.grade) == (Stat.ATK, float(number), grade)  # flat
    assert build.confidence["imprint.grade"] == (USER_ENTERED if grade else ASSUMED)


@pytest.mark.parametrize(
    ("number", "grade"), [("17.2", ImprintGrade.S), ("19.4", ImprintGrade.SS), ("16", None), ("18.5", None)]
)
def test_a_rate_imprint_grade_is_never_taken_from_a_near_value(number: str, grade: ImprintGrade | None) -> None:
    build = only([{"id": "h7", "name": "Rate Hero", "stars": 6, "imprintNumber": number}]).build
    assert build is not None and build.imprint is not None and build.imprint.grade == grade
    assert build.confidence["imprint.grade"] == (USER_ENTERED if grade else ASSUMED)


def test_a_value_shared_by_two_grades_gives_no_grade_and_flat_ee_values_are_kept() -> None:
    build = only([{"id": "h3", "name": "Echo", "stars": 6, "imprintNumber": "10", "eeNumber": "4"}]).build
    assert build is not None and build.imprint is not None
    assert (build.imprint.value, build.imprint.grade) == (0.1, None)
    assert build.exclusive_equipment == ExclusiveEquipment(stat=Stat.SPEED, value=4)  # flat: not divided by 100


@pytest.mark.parametrize(
    ("name", "level", "expected", "note"),
    [
        (" Test Dagger ", "15", ArtifactRef(code="efz01", level=15), None),
        ("Queen's Charm", "30", ArtifactRef(code="efz07", level=30), None),  # Fribbels' straight apostrophe
        ("Test Daggr", "15", None, "artifact 'Test Daggr' +15 not usable: not stored"),
        ("Test Dagger", "31", None, "artifact 'Test Dagger' +31 not usable: not stored"),
        ("Test Dagger", "15.5", None, "artifact 'Test Dagger' +15.5 not usable: not stored"),
        ("Test Dagger", "-1", None, "artifact 'Test Dagger' +-1 not usable: not stored"),
        ("Test Dagger", "x", None, "artifact 'Test Dagger' +x not usable: not stored"),
        ("Twin Blade", "3", None, "artifact 'Twin Blade' +3: several catalog artifacts have this name: not stored"),
    ],
)
def test_typed_artifacts_are_matched_exactly(
    name: str, level: str, expected: ArtifactRef | None, note: str | None
) -> None:
    entry = only([{**BBK, "artifactName": name, "artifactLevel": level}])
    assert entry.build is not None and entry.build.artifact == expected
    assert entry.notes == ([note] if note else [])


@pytest.mark.parametrize(
    ("field", "value", "note"),
    [
        ("imprintNumber", "18,5", "imprint '18,5' is not a number: not stored"),
        ("imprintNumber", "NaN", "imprint 'NaN' is not a finite number: not stored"),
        ("eeNumber", "inf", "exclusive equipment 'inf' is not a finite number: not stored"),
        ("eeNumber", "12 pct", "exclusive equipment '12 pct' is not a number: not stored"),
    ],
)
def test_unreadable_typed_values_are_reported_never_dropped_or_fatal(field: str, value: str, note: str) -> None:
    save = read([{**BBK, field: value}, {**BBK, "id": "h2", "name": "Test Hero"}], [])
    first = save.heroes[0]
    assert first.build is not None and first.notes == [note]
    assert save.heroes[1].build is not None  # the other heroes are imported anyway


def test_bonuses_the_catalog_cannot_place_are_reported() -> None:
    entry = only([{"id": "h5", "name": "Plain Hero", "stars": 6, "imprintNumber": "5", "eeNumber": "5"}])
    assert entry.build is not None and entry.build.imprint is None and entry.build.exclusive_equipment is None
    assert entry.notes == [
        "imprint '5': the catalog has no imprint stat for this hero",
        "exclusive equipment '5': the catalog has no EE stat for this hero",
    ]


def test_heroes_are_matched_by_exact_name_never_by_a_near_miss() -> None:
    heroes = [
        {"id": "a", "name": "Twin"},
        {"id": "b", "name": "Blood Blade Karn"},
        {"id": "c", "name": "blood blade karin"},  # normalised: the same name
        {"id": "d", "name": "Test Hero (Fribbels)"},  # the name Fribbels gives the hero
    ]
    entries = read(heroes, []).heroes
    assert [e.hero_code for e in entries] == [None, None, "c2011", "c9001"]
    assert "several catalog heroes" in entries[0].problems[0] and entries[0].build is None
    assert entries[1].problems == ["no catalog hero is called 'Blood Blade Karn' (run e7 catalog sync)"]


def test_a_fribbels_name_shared_with_another_entity_stays_ambiguous() -> None:
    names = catalog_names({**HEROES, "c9004": hero("c9004", "Other", fribbels_name="Test Hero")})
    assert names.is_ambiguous("Test Hero") and names.lookup("Test Hero") is None


def test_bad_entries_are_named_never_dropped_silently() -> None:
    items = [
        *BBK_ITEMS[:1],
        {"gear": "Weapon"},  # incomplete
        item("Helmet", stat("Dac", 3), hero_id=None, wearer="0"),  # never a gear stat
        item("Armor", stat("Defense", 310), set_name="MysterySet", hero_id=None, wearer="0"),
        item("Ring", stat("AttackPercent", 65), hero_id="ghost", wearer="g9"),
        item("Boots", stat("Speed", 45), hero_id=None, wearer="0"),  # in the inventory
        {**item("Necklace", stat("CriticalHitChancePercent", 60)), "enhance": "NaN"},
    ]
    save = read([BBK, {"name": "no id"}], items)
    assert save.warnings == [
        "item #2: not read (rank: Field required)",
        "item #7: not read (enhance: Input should be a valid integer, unable to parse string as an integer)",
        "hero #2: not read (id: Field required)",
        "1 item(s) equipped by an unknown hero id ghost: ignored",
        "item #3 (None-Helmet-0): not read (unknown stat type 'Dac')",
        "item #4 (None-Armor-0): not read (unknown set 'MysterySet')",
        "1 item(s) worn in the game by 1 hero(es) the save does not match to a Fribbels hero (not imported by "
        "Fribbels, or all their gear moved in it): not taken",
    ]
    assert save.unused_items == 2  # the inventory boots and the ghost's ring
    assert save.locations["ingame:None-Boots-0"] == (None, "in the inventory")
    build = save.heroes[0].build
    assert build is not None and list(build.gear) == [GearSlot.WEAPON]


@pytest.mark.parametrize(
    ("change", "reason"),
    [({"level": 0}, "item level unknown (0 in the save)"), ({"main": stat("Attack", 0)}, "main stat value unknown")],
)
def test_fribbels_unknown_values_make_only_that_piece_unusable(change: dict[str, Any], reason: str) -> None:
    entry = only([BBK], [{**BBK_ITEMS[0], **change}, *BBK_ITEMS[1:]])
    assert entry.build is not None and GearSlot.WEAPON not in entry.build.gear and len(entry.build.gear) == 5
    assert entry.unusable[GearSlot.WEAPON].startswith(reason)
    assert any(n.startswith(f"weapon: the save's piece is not usable ({reason}") for n in entry.notes)


PLAN_NOTE = (
    "matched to its game hero by most of its pieces, but Fribbels' equipment differs from the game (an optimizer "
    "plan?): its game gear has confidence 0.7"
)


def test_an_optimizer_plan_is_never_taken_as_game_gear() -> None:
    other = {"id": "h2", "name": "Test Hero", "stars": 6}
    items = [
        *BBK_ITEMS[:4],
        item("Ring", stat("AttackPercent", 65), wearer="g2"),  # Fribbels equipped Test Hero's ring on BBK
        item("Boots", stat("Speed", 45), wearer="0"),  # and boots from the game's inventory
        item("Ring", stat("HealthPercent", 60), hero_id="h2", wearer="g1"),  # BBK's ring, moved to Test Hero
        item("Weapon", stat("Attack", 500), hero_id="h2", wearer="g2"),
        item("Helmet", stat("Health", 2000), hero_id="h2", wearer="g2"),
    ]
    save = read([BBK, other], items)
    bbk, test = save.heroes
    assert bbk.build is not None and test.build is not None
    assert bbk.build.gear[GearSlot.RING].main.stat is Stat.HP_PERCENT  # the ring BBK wears in the game
    assert GearSlot.BOOTS not in bbk.build.gear
    assert bbk.notes == [
        "ring: worn in the game, moved in Fribbels: the game's piece taken",
        PLAN_NOTE,
        "boots, ring: equipped in Fribbels only (worn by another hero or in the inventory in the game, "
        "e.g. an optimizer result): not taken",
    ]
    assert all(bbk.build.confidence[f"gear.{slot.value}"] == USER_ENTERED for slot in bbk.build.gear)
    assert test.build.gear[GearSlot.RING].main.stat is Stat.ATK_PERCENT  # back on its game wearer
    assert set(test.build.gear) == {GearSlot.WEAPON, GearSlot.HELMET, GearSlot.RING}
    assert save.locations["ingame:h1-Boots-0"] == (None, "in the inventory")
    assert (bbk.game_id, test.game_id) == ("g1", "g2")


def test_a_clean_match_keeps_full_confidence_and_strays_are_located() -> None:
    stray = item("Ring", stat("Speed", 4), wearer="g9", ingame="555")  # moved in the game to a hero Fribbels lacks
    save = read([BBK], [*BBK_ITEMS[:4], BBK_ITEMS[5], stray])
    (entry,) = save.heroes
    assert entry.build is not None and GearSlot.RING not in entry.build.gear
    assert entry.notes == [  # a stray of another game hero makes the match uncertain: confidence 0.7
        PLAN_NOTE,
        "ring: equipped in Fribbels only (worn by another hero or in the inventory in the game, e.g. an optimizer "
        "result): not taken",
    ]
    assert save.locations["ingame:555"] == (None, "worn in the game by another hero")
    assert save.locations["fribbels:fid-Ring-h1-g9"] == (None, "worn in the game by another hero")
    assert save.elsewhere(entry)["ingame:555"] == "worn in the game by another hero"
    clean = read([BBK], BBK_ITEMS).heroes[0]
    assert clean.build is not None and not any(k.startswith("gear.") for k in clean.build.confidence)


@pytest.mark.parametrize(
    ("items", "why"),
    [
        (  # one piece of a game hero Fribbels does not have, equipped by the optimizer
            [
                item("Weapon", stat("Attack", 500), wearer="gY", ingame=f"y{n}")
                if n == 0
                else item(g, stat("Health", 1), hero_id=None, wearer="gY", ingame=f"y{n}")
                for n, g in enumerate(("Weapon", "Helmet", "Armor", "Necklace", "Ring", "Boots"))
            ],
            "its Fribbels equipment holds only 1 of the 6 pieces worn by the game hero most of them come from",
        ),
        (
            [item("Weapon", stat("Attack", 525)), item("Helmet", stat("Health", 2835), wearer="g2")],
            "its Fribbels equipment mixes pieces worn by 2 game heroes",
        ),
    ],
    ids=["one-piece-plan", "even-split"],
)
def test_a_hero_the_save_cannot_match_to_a_game_hero_gets_no_game_gear(items: list[dict[str, Any]], why: str) -> None:
    entry = only([BBK], items)
    assert entry.build is not None and entry.build.gear == {} and entry.game_id is None
    assert entry.notes[0] == (
        f"the save cannot tell which game hero this is ({why}): game gear not taken; import your account again in "
        "Fribbels before saving"
    )
    assert "equipped in Fribbels only" in entry.notes[1]


def test_a_game_hero_is_never_matched_to_two_fribbels_heroes() -> None:
    items = [item("Weapon", stat("Attack", 525)), item("Helmet", stat("Health", 2835), hero_id="h2")]
    save = read([BBK, {"id": "h2", "name": "Test Hero", "stars": 6}], items)
    for entry in save.heroes:  # each holds 1 of g1's 2 pieces: neither has most of them
        assert entry.build is not None and entry.build.gear == {} and entry.game_id is None
    assert save.warnings == [
        "2 item(s) worn in the game by 1 hero(es) the save does not match to a Fribbels hero "
        "(not imported by Fribbels, or all their gear moved in it): not taken"
    ]


def test_a_hero_whose_fribbels_gear_is_all_a_plan_gets_none() -> None:
    entry = only([BBK], [item(g, stat("Attack", 500), wearer="0") for g in ("Weapon", "Helmet")])
    assert entry.build is not None and entry.build.gear == {}
    assert entry.notes == [
        "helmet, weapon: equipped in Fribbels only (worn by another hero or in the inventory in the game, "
        "e.g. an optimizer result): not taken"
    ]


def test_gear_without_game_data_is_taken_from_fribbels_with_a_lower_confidence() -> None:
    items = [
        item("Weapon", stat("Attack", 525), wearer=None, ingame=None, op=False),  # added by hand
        item("Helmet", stat("Health", 2835), wearer=None),  # an old import: no wearer recorded
    ]
    entry = only([BBK], items)
    build = entry.build
    assert build is not None and set(build.gear) == {GearSlot.WEAPON, GearSlot.HELMET}
    assert all(s.rolls is None for s in build.gear[GearSlot.WEAPON].substats)  # Fribbels' guesses: not taken
    assert [s.rolls for s in build.gear[GearSlot.HELMET].substats] == [2, 2, 2, 1]  # from the game's data
    assert build.gear[GearSlot.WEAPON].external_id == "fribbels:fid-Weapon-h1-None"
    assert build.confidence["gear.weapon"] == build.confidence["gear.helmet"] == USER_ENTERED
    assert entry.notes == [
        "helmet, weapon: equipped in Fribbels, and the save does not say who wears it in the game (added by hand or "
        "an old import): confidence 0.7",
        "weapon: added or edited by hand in Fribbels: confidence 0.7, rolls not taken",
    ]


def test_an_edited_game_piece_keeps_its_place_but_not_its_rolls() -> None:
    entry = only([BBK], [item("Weapon", stat("Attack", 525), op=False), *BBK_ITEMS[1:]])
    assert entry.build is not None and entry.build.confidence["gear.weapon"] == USER_ENTERED
    assert all(s.rolls is None for s in entry.build.gear[GearSlot.WEAPON].substats)
    assert entry.notes == ["weapon: added or edited by hand in Fribbels: confidence 0.7, rolls not taken"]


def test_a_derived_plus_n_below_15_is_an_estimate() -> None:
    entry = only([BBK], [item("Weapon", stat("Attack", 470), enhance=12), *BBK_ITEMS[1:]])
    assert entry.build is not None and entry.build.confidence["gear.weapon"] == ESTIMATED
    assert entry.notes == ["weapon: +N below +15 is Fribbels' estimate (a multiple of 3, up to 2 below the real one)"]


def test_two_pieces_in_one_slot_are_settled_by_fribbels_equipment_or_refused() -> None:
    second = item("Weapon", stat("Attack", 500), hero_id="h2", ingame="77")  # also worn by g1, equipped on h2
    entry = only([BBK], [*BBK_ITEMS, second])
    assert entry.build is not None and entry.build.gear[GearSlot.WEAPON].external_id == "ingame:9001"
    assert "weapon: 2 pieces in the save: item #1 (9001) taken" in entry.notes
    neither = [{**BBK_ITEMS[0], "equippedById": None}, {**second, "equippedById": None}]
    shown = {**BBK, "equipment": {"Weapon": {"id": second["id"]}}}
    (by_equipment,) = read([shown], [*neither, *BBK_ITEMS[1:]]).heroes
    assert by_equipment.build is not None and by_equipment.build.gear[GearSlot.WEAPON].external_id == "ingame:77"
    (refused,) = read([BBK], [*neither, *BBK_ITEMS[1:]]).heroes
    assert refused.build is not None and GearSlot.WEAPON not in refused.build.gear
    assert "weapon: 2 pieces in the save (item #1 (9001), item #2 (77)): none taken" in refused.notes


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("{", "not a JSON file"),
        ("[]", "not a Fribbels save"),
        ('{"heroes": []}', "not a Fribbels save"),
    ],
)
def test_other_files_are_refused(text: str, message: str) -> None:
    with pytest.raises(FribbelsFileError, match=message):
        read_fribbels_save(text, HEROES, ARTIFACTS, captured_at=NOW)


def test_gear_of_refuses_unknown_names() -> None:
    raw = FribbelsItem.model_validate(item("Weapon", stat("Attack", 1), rank="Mythic"))
    with pytest.raises(ValueError, match="unknown rank 'Mythic'"):
        gear_of(raw)


# ------------------------------------------------------------------------------------------------ merging


def imported(*, items: list[dict[str, Any]] | None = None, **changes: Any) -> HeroBuild:
    build = only([{**BBK, **changes}], BBK_ITEMS if items is None else items).build
    assert build is not None
    return build


FINAL = FinalStats(
    atk=4116,
    defense=829,
    hp=12819,
    speed=133,
    crit_chance=1.0,
    crit_damage=3.57,
    effectiveness=0.0,
    effect_resistance=0.21,
    dual_attack=0.03,
)
STATS_DROPPED = "the build changed: the displayed stats and CP of the last screen reading are dropped (rescan it)"


def screen_build(base: HeroBuild, **changes: Any) -> HeroBuild:
    """The hero as an earlier Hero Info scan stored it: same gear, read stats, real level and awakening."""
    data: dict[str, Any] = {
        "level": 60,
        "awakening": 6,
        "skills": SkillEnhancements(s1=5),
        "final_stats": FINAL,
        "cp": 120000,
        "source": BuildSource.OCR,
        "captured_at": NOW - timedelta(days=1),
        "confidence": {
            "level": 1.0,
            "awakening": 0.9,
            "stars": 0.9,
            "final_stats.atk": 0.97,
            "artifact": 0.9,
            "cp": 0.95,
        },
    }
    return base.model_copy(update={**data, **changes})


def test_without_a_current_build_the_import_is_taken_as_is() -> None:
    build = imported()
    assert merge_with_current(build, None) == (build, [])


def test_what_the_save_lacks_is_kept_from_the_roster() -> None:
    build = imported()
    current = screen_build(build)
    merged, notes = merge_with_current(build, current)
    assert notes == []
    assert (merged.level, merged.awakening, merged.skills) == (60, 6, SkillEnhancements(s1=5))
    assert merged.final_stats == FINAL and merged.cp == 120000  # nothing they depend on changed
    assert merged.confidence["level"] == 1.0 and merged.confidence["awakening"] == 0.9
    assert merged.confidence["stars"] == 0.9  # the roster's reading is more certain than the save's
    assert merged.confidence["final_stats.atk"] == 0.97 and merged.confidence["cp"] == 0.95
    assert merged.source is BuildSource.FRIBBELS and merged.captured_at == NOW


CHANGES: list[tuple[str, dict[str, Any] | list[dict[str, Any]]]] = [
    ("weapon main", [item("Weapon", stat("Attack", 500), ingame="9001"), *BBK_ITEMS[1:]]),
    ("substat", [item("Weapon", stat("Attack", 525), ingame="9001", subs=SUBS[:3]), *BBK_ITEMS[1:]]),
    ("set", [item("Weapon", stat("Attack", 525), ingame="9001", set_name="TorrentSet"), *BBK_ITEMS[1:]]),
    ("enhance", [item("Weapon", stat("Attack", 525), ingame="9001", enhance=12), *BBK_ITEMS[1:]]),
    ("ee", {"eeNumber": "8"}),
]


@pytest.mark.parametrize(("what", "change"), CHANGES, ids=[c[0] for c in CHANGES])
def test_any_change_of_the_stat_inputs_drops_the_displayed_stats(
    what: str, change: dict[str, Any] | list[dict[str, Any]]
) -> None:
    current = screen_build(imported(), confidence={"exclusive_equipment": 0.5})
    new = imported(items=change) if isinstance(change, list) else imported(**change)
    merged, notes = merge_with_current(new, current)
    assert merged.final_stats is None and merged.cp is None
    assert not any(key.startswith("final_stats.") or key == "cp" for key in merged.confidence)
    assert notes[-1] == STATS_DROPPED


@pytest.mark.parametrize(
    ("roster", "save"),
    [
        ({"stars": 5, "level": 50, "awakening": 5, "confidence": {"stars": ASSUMED}}, {}),
        ({"artifact": ArtifactRef(code="efz09", level=30), "confidence": {"artifact": 0.5}}, {}),
        (
            {
                "imprint": Imprint(grade=None, stat=Stat.ATK_PERCENT, value=0.06, mode=ImprintMode.SELF),
                "confidence": {"imprint": 0.5},
            },
            {},
        ),
        ({"level": 50, "awakening": 5, "confidence": {"stars": USER_ENTERED}}, {"stars": 5}),
    ],
    ids=["stars", "artifact", "imprint", "stars-cap-level"],
)
def test_a_value_the_save_wins_drops_the_displayed_stats(roster: dict[str, Any], save: dict[str, Any]) -> None:
    current = screen_build(imported(), **roster)
    merged, notes = merge_with_current(imported(**save), current)
    assert merged.final_stats is None and merged.cp is None and notes[-1] == STATS_DROPPED


def test_the_roster_note_and_skill_confidence_are_kept() -> None:
    current = screen_build(imported(), note="speed tuned for Arena", confidence={"skills": 0.4})
    merged, _ = merge_with_current(imported(), current)
    assert merged.note == "speed tuned for Arena" and merged.confidence["skills"] == 0.4


def test_a_roster_piece_the_save_shows_on_an_unmatched_game_hero_is_removed() -> None:
    stray = item("Ring", stat("AttackPercent", 65), set_name="DestructionSet", wearer="g9", ingame="9005")
    save = read([BBK], [*BBK_ITEMS[:4], BBK_ITEMS[5], stray])
    (entry,) = save.heroes
    assert entry.build is not None
    current = screen_build(imported())
    merged, notes = merge_with_current(entry.build, current, elsewhere=save.elsewhere(entry))
    assert GearSlot.RING not in merged.gear
    assert "ring: the roster's piece is worn in the game by another hero in the save: removed from this hero" in notes
    unmatched = read([BBK], [item("Weapon", stat("Attack", 1), wearer="gZ"), stray]).heroes[0]
    assert unmatched.game_id is None and "ingame:9005" not in save.elsewhere(unmatched)  # it may be this very hero


def test_stars_from_the_save_never_override_a_more_certain_reading() -> None:
    current = screen_build(imported(stars=5), level=50, awakening=5)
    merged, notes = merge_with_current(imported(stars=6), current)
    assert (merged.stars, merged.awakening, merged.level) == (5, 5, 50) and merged.confidence["stars"] == 0.9
    assert notes == [
        "stars: the save says 6, the roster 5: kept the roster's (Fribbels' stars can be stale or set in its "
        "bonus dialog)"
    ]
    assumed, quiet = merge_with_current(imported(stars=0), current)
    assert assumed.stars == 5 and quiet == []  # an assumed 6 never replaces anything


def test_a_newer_save_replaces_the_stars_of_an_earlier_import_and_caps_the_rest() -> None:
    earlier = imported().model_copy(update={"level": 60, "awakening": 6, "confidence": {"stars": USER_ENTERED}})
    merged, notes = merge_with_current(imported(stars=5), earlier)
    assert (merged.stars, merged.awakening, merged.level) == (5, 5, 50)
    assert merged.confidence["level"] == merged.confidence["awakening"] == ASSUMED
    assert notes == [
        "stars: the save's 5 replaces the roster's 6",
        "level 60 is above the 5-star cap: lowered to 50",
        "awakening 6 is above 5 stars: lowered to 5",
    ]


def test_a_slot_missing_from_the_save_keeps_the_roster_piece_unless_the_save_puts_it_elsewhere() -> None:
    current = screen_build(imported(), confidence={"gear.boots": 0.8})
    partial = imported(items=BBK_ITEMS[:5])
    merged, notes = merge_with_current(partial, current)
    assert merged.gear[GearSlot.BOOTS] == current.gear[GearSlot.BOOTS] and merged.confidence["gear.boots"] == 0.8
    assert notes == [
        "boots: not in the file (Fribbels leaves out items below its import +N and sets it does not know): kept the "
        "roster's piece"
    ]
    assert merged.final_stats == FINAL  # the build is still the same
    unusable, why = merge_with_current(partial, current, unusable={GearSlot.BOOTS: "item level unknown"})
    assert unusable.gear[GearSlot.BOOTS] == current.gear[GearSlot.BOOTS]
    assert why == ["boots: the save's piece is not usable (item level unknown): kept the roster's piece"]
    moved, where = merge_with_current(partial, current, elsewhere={"ingame:9006": "on Test Hero"})
    assert GearSlot.BOOTS not in moved.gear and "gear.boots" not in moved.confidence
    assert where == ["boots: the roster's piece is on Test Hero in the save: removed from this hero", STATS_DROPPED]


def test_a_piece_read_on_screen_and_in_the_save_is_combined() -> None:
    estimated = item("Weapon", stat("Attack", 525), ingame="9001", enhance=12)
    save_build = imported(items=[estimated, *BBK_ITEMS[1:]])
    weapon = save_build.gear[GearSlot.WEAPON]
    seen = weapon.model_copy(
        update={
            "enhance": 14,
            "score": 70,
            "external_id": None,
            "substats": tuple(s.model_copy(update={"rolls": None}) for s in weapon.substats),
        }
    )
    current = screen_build(imported(), gear={**save_build.gear, GearSlot.WEAPON: seen}, confidence={"gear.weapon": 0.9})
    merged, notes = merge_with_current(save_build, current)
    combined = merged.gear[GearSlot.WEAPON]
    assert (combined.enhance, combined.score, combined.external_id) == (14, 70, "ingame:9001")
    assert [s.rolls for s in combined.substats] == [2, 2, 2, 1]
    assert merged.confidence["gear.weapon"] == 0.9 and merged.final_stats == FINAL and notes == []


@pytest.mark.parametrize(
    ("field", "roster", "note"),
    [
        ("artifact", ArtifactRef(code="efz09", level=30), "the save says efz01 +15, the roster efz09 +30"),
        ("artifact", ArtifactRef(code="efz01", level=30), "the save says efz01 +15, the roster efz01 +30"),
        (
            "imprint",
            Imprint(grade=ImprintGrade.C, stat=Stat.ATK_PERCENT, value=0.06, mode=ImprintMode.SELF),
            "the save says att_rate 18%, the roster att_rate 6%",
        ),
        (
            "exclusive_equipment",
            ExclusiveEquipment(stat=Stat.CRIT_CHANCE, value=0.08),
            "the save says cri 12%, the roster cri 8%",
        ),
    ],
)
def test_a_typed_bonus_never_silently_replaces_a_different_one(field: str, roster: Any, note: str) -> None:
    label = field.replace("_", " ")
    entered = imported().model_copy(update={field: roster, "confidence": {}})  # entered here: no confidence key
    merged, notes = merge_with_current(imported(), entered)
    assert getattr(merged, field) == roster and field not in merged.confidence
    assert notes == [
        f"{label}: {note}: kept the roster's (Fribbels' bonus stats are typed by hand; use e7 roster edit if the "
        "save is right)"
    ]
    doubtful = entered.model_copy(update={"confidence": {field: 0.5}})
    taken, why = merge_with_current(imported(), doubtful)
    assert getattr(taken, field) == getattr(imported(), field) and taken.confidence[field] == USER_ENTERED
    assert why[0].startswith(f"{label}: the save's ") and why[0].endswith("(confidence 0.50)")


def test_a_newer_save_replaces_a_bonus_typed_in_an_earlier_one() -> None:
    earlier = imported(artifactName="Old Relic", artifactLevel="30")
    merged, notes = merge_with_current(imported(), earlier)
    assert merged.artifact == ArtifactRef(code="efz01", level=15)
    assert notes == ["artifact: the save's efz01 +15 replaces the roster's efz09 +30 (confidence 0.70)"]


def test_a_bonus_the_save_lacks_is_kept() -> None:
    current = screen_build(imported())
    merged, notes = merge_with_current(imported(artifactName="None", eeNumber="None"), current)
    assert merged.artifact == current.artifact and merged.exclusive_equipment == current.exclusive_equipment
    assert merged.confidence["artifact"] == 0.9 and notes == []


def test_an_imprint_read_as_locked_is_not_replaced_by_a_typed_one() -> None:
    locked = screen_build(imported(), imprint=None, confidence={"imprint": 1.0})
    merged, notes = merge_with_current(imported(), locked)
    assert merged.imprint is None and merged.confidence["imprint"] == 1.0
    assert notes[0].startswith("imprint: the save says att_rate 18%, the roster none: kept the roster's")
    unsure = locked.model_copy(update={"confidence": {"imprint": ASSUMED}})
    taken, _ = merge_with_current(imported(), unsure)
    assert taken.imprint is not None and taken.imprint.value == 0.18


def test_the_same_imprint_fills_only_what_the_roster_did_not_know() -> None:
    unknown = Imprint(grade=None, stat=Stat.ATK_PERCENT, value=0.18, mode=None)
    current = screen_build(imported(), imprint=unknown, confidence={"imprint": 0.9})
    merged, notes = merge_with_current(imported(), current)
    assert merged.imprint == Imprint(grade=ImprintGrade.SSS, stat=Stat.ATK_PERCENT, value=0.18, mode=ImprintMode.SELF)
    assert merged.confidence["imprint"] == 0.9
    assert merged.confidence["imprint.grade"] == merged.confidence["imprint.mode"] == USER_ENTERED
    assert notes == [] and merged.final_stats == FINAL  # learning the mode changes nothing in the game


def test_a_team_imprint_read_on_screen_keeps_its_mode_and_gets_no_self_grade() -> None:
    team = Imprint(grade=None, stat=Stat.ATK_PERCENT, value=0.18, mode=ImprintMode.TEAM)
    current = screen_build(imported(), imprint=team, confidence={"imprint": 0.9, "imprint.mode": 0.9})
    merged, notes = merge_with_current(imported(), current)
    assert merged.imprint == team and merged.confidence["imprint.mode"] == 0.9
    assert notes == ["imprint: the save treats it as self, the roster read team: kept"]
    assert merged.final_stats == FINAL


# ------------------------------------------------------------------------------------------------ CLI


def write_save(path: Path, heroes: list[dict[str, Any]], items: list[dict[str, Any]], when: datetime = NOW) -> Path:
    path.write_text(save_text(heroes, items), encoding="utf-8")
    os.utime(path, (when.timestamp(), when.timestamp()))
    return path


def show(owned_id: int = 1) -> dict[str, Any]:
    result = runner.invoke(app, ["roster", "show", str(owned_id), "--json"])
    assert result.exit_code == 0, result.output
    build: dict[str, Any] = json.loads(result.stdout)["build"]
    return build


def test_cli_imports_then_reports_unchanged(tmp_path: Path, synthetic_catalog: list[str]) -> None:
    unknown = {"id": "h9", "name": "Nobody"}
    spare = item("Ring", stat("Speed", 4), hero_id=None, wearer="0")
    save = write_save(tmp_path / "fribbels.json", [BBK, unknown], [*BBK_ITEMS, spare])
    dry = runner.invoke(app, ["roster", "import-fribbels", str(save), "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert "dry run" in dry.stdout and "0 hero(es)" in runner.invoke(app, ["roster", "list"]).stdout
    first = runner.invoke(app, ["roster", "import-fribbels", str(save)])
    assert first.exit_code == 0, first.output
    assert "new       Blood Blade Karin (c2011): 6 gear piece(s)" in first.stdout
    assert "skipped   Nobody (?): no catalog hero is called 'Nobody'" in first.stdout
    summary = "Fribbels save (file written 2026-10-05 12:00 UTC): 2 hero(es): 1 new, 0 updated, 0 unchanged, 1 skipped"
    assert summary in first.stdout
    assert "1 item(s) worn by no hero of the file" in first.stdout
    build = show()
    assert build["source"] == "fribbels" and build["artifact"] == {"code": "efz01", "level": 15}
    assert build["gear"]["weapon"]["external_id"] == "ingame:9001"
    assert build["gear"]["weapon"]["substats"][1] == {
        "stat": "cri_dmg",
        "value": 0.14,
        "rolls": 2,
        "modified": True,
        "reforged": False,
    }
    again = runner.invoke(app, ["roster", "import-fribbels", str(save)])
    assert "0 new, 0 updated, 1 unchanged, 1 skipped" in again.stdout and "unchanged Blood" not in again.stdout
    assert runner.invoke(app, ["roster", "history", "1"]).stdout.count("fribbels") == 1


def test_cli_prints_every_warning_note_and_validation_issue(tmp_path: Path, synthetic_catalog: list[str]) -> None:
    items = [
        *BBK_ITEMS[:5],
        item("Boots", stat("Speed", 45), wearer="0"),  # an optimizer plan from the inventory
        {"gear": "Weapon"},
        item("Ring", stat("Speed", 4), hero_id="ghost", wearer="g7"),
    ]
    save = write_save(tmp_path / "fribbels.json", [{**BBK, "artifactName": "Test Daggr"}], items)
    result = runner.invoke(app, ["roster", "import-fribbels", str(save)])
    assert result.exit_code == 0, result.output
    assert "    note: artifact 'Test Daggr' +15 not usable: not stored" in result.stdout
    assert "    note: boots: equipped in Fribbels only" in result.stdout
    assert "  warning: item #7: not read (rank: Field required)" in result.stderr
    assert "  warning: 1 item(s) equipped by an unknown hero id ghost: ignored" in result.stderr
    again = runner.invoke(app, ["roster", "import-fribbels", str(save)])
    assert "unchanged Blood Blade Karin (c2011)" in again.stdout  # notes are shown for unchanged heroes too
    assert "    note: artifact 'Test Daggr' +15 not usable: not stored" in again.stdout


def test_cli_fills_a_manual_build_and_keeps_its_level(tmp_path: Path, synthetic_catalog: list[str]) -> None:
    assert runner.invoke(app, ["roster", "add", "c2011", "--level", "60", "--awakening", "6"]).exit_code == 0
    later = NOW + timedelta(days=400)  # the manual entry is stamped now; the save must be newer
    save = write_save(tmp_path / "fribbels.json", [BBK], BBK_ITEMS, later)
    result = runner.invoke(app, ["roster", "import-fribbels", str(save)])
    assert result.exit_code == 0, result.output
    assert "updated   Blood Blade Karin (c2011)" in result.stdout
    build = show()
    assert (build["level"], build["awakening"], len(build["gear"])) == (60, 6, 6)


def write_build(path: Path, build: HeroBuild, **piece_changes: Any) -> Path:
    """A build JSON for `roster add --from-json`, with every piece changed (e.g. no source id, a screen score)."""
    gear = {
        slot.value: json.loads(piece.model_copy(update=piece_changes).model_dump_json())
        for slot, piece in build.gear.items()
    }
    path.write_text(json.dumps({"gear": gear}), encoding="utf-8")
    return path


def test_cli_never_replaces_gear_entered_here_unless_trusted(tmp_path: Path, synthetic_catalog: list[str]) -> None:
    other = imported(items=[item("Weapon", stat("Attack", 500), ingame="1"), *BBK_ITEMS[1:]])
    entered = write_build(tmp_path / "entered.json", other, external_id=None)  # typed by the user: no source id
    assert runner.invoke(app, ["roster", "add", "c2011", "--from-json", str(entered)]).exit_code == 0
    save = write_save(tmp_path / "fribbels.json", [BBK], BBK_ITEMS, NOW + timedelta(days=400))
    refused = runner.invoke(app, ["roster", "import-fribbels", str(save)])
    assert (
        "skipped   Blood Blade Karin (c2011): the save's weapon differ from the roster's piece read on screen or "
        "entered here (build of " in refused.stdout
    )
    assert "use --trust-save" in refused.stdout and show()["gear"]["weapon"]["main"]["value"] == 500
    trusted = runner.invoke(app, ["roster", "import-fribbels", str(save), "--trust-save"])
    assert "updated   Blood Blade Karin (c2011)" in trusted.stdout and show()["gear"]["weapon"]["main"]["value"] == 525


def test_cli_keeps_protecting_screen_gear_after_an_import_confirmed_it(
    tmp_path: Path, synthetic_catalog: list[str]
) -> None:
    seen = write_build(tmp_path / "seen.json", imported(), external_id=None, score=70)  # as Hero Info reads it
    assert runner.invoke(app, ["roster", "add", "c2011", "--from-json", str(seen)]).exit_code == 0
    fresh = write_save(tmp_path / "fresh.json", [BBK], BBK_ITEMS, NOW + timedelta(days=400))
    confirmed = runner.invoke(app, ["roster", "import-fribbels", str(fresh)])
    assert "updated   Blood Blade Karin (c2011)" in confirmed.stdout  # the same pieces: combined, nothing replaced
    weapon = show()["gear"]["weapon"]
    assert (weapon["score"], weapon["external_id"], weapon["substats"][0]["rolls"]) == (70, "ingame:9001", 2)
    stale_items = [item("Weapon", stat("Attack", 400), ingame="4"), *BBK_ITEMS[1:]]
    stale = write_save(tmp_path / "autosave.json", [BBK], stale_items, NOW + timedelta(days=401))
    refused = runner.invoke(app, ["roster", "import-fribbels", str(stale)])
    assert "skipped   Blood Blade Karin (c2011): the save's weapon differ" in refused.stdout
    assert show()["gear"]["weapon"]["main"]["value"] == 525


def test_cli_refuses_a_save_whose_game_match_contradicts_the_roster(
    tmp_path: Path, synthetic_catalog: list[str]
) -> None:
    other = {"id": "h2", "name": "Karin", "stars": 6}
    karin_items = [
        item(g, stat("Health", 1000 + n), hero_id="h2", wearer="g2", ingame=f"k{n}")
        for n, g in enumerate(("Weapon", "Helmet", "Armor", "Necklace", "Ring", "Boots"))
    ]
    first = write_save(tmp_path / "a.json", [BBK, other], [*BBK_ITEMS, *karin_items])
    assert "2 new" in runner.invoke(app, ["roster", "import-fribbels", str(first)]).stdout
    swapped = [  # whole builds swapped in Fribbels' planner: the game wearers did not change
        *({**raw, "equippedById": "h2"} for raw in BBK_ITEMS),
        *({**raw, "equippedById": "h1"} for raw in karin_items),
    ]
    second = write_save(tmp_path / "b.json", [BBK, other], swapped, NOW + timedelta(days=1))
    refused = runner.invoke(app, ["roster", "import-fribbels", str(second)])
    assert refused.stdout.count("are worn in the save by another game hero than the one its Fribbels equipment") == 2
    assert "0 updated" in refused.stdout and show()["gear"]["weapon"]["external_id"] == "ingame:9001"
    trusted = runner.invoke(app, ["roster", "import-fribbels", str(second), "--trust-save"])
    assert "2 updated" in trusted.stdout


def test_cli_warns_when_one_piece_ends_up_on_two_heroes(tmp_path: Path, synthetic_catalog: list[str]) -> None:
    worn = tmp_path / "worn.json"
    weapon = imported().gear[GearSlot.WEAPON]
    worn.write_text(json.dumps({"gear": {"weapon": json.loads(weapon.model_dump_json())}}), encoding="utf-8")
    assert runner.invoke(app, ["roster", "add", "c1011", "--from-json", str(worn)]).exit_code == 0
    save = write_save(tmp_path / "fribbels.json", [BBK], BBK_ITEMS, NOW + timedelta(days=400))
    result = runner.invoke(app, ["roster", "import-fribbels", str(save)])
    assert result.exit_code == 0, result.output
    assert (
        "warning: the same piece (ingame:9001) is in the builds of #1 Karin and new Blood Blade Karin (c2011)"
        in result.stderr
    )


def test_cli_never_puts_an_older_save_over_a_newer_build(tmp_path: Path, synthetic_catalog: list[str]) -> None:
    assert runner.invoke(app, ["roster", "add", "c2011"]).exit_code == 0
    save = write_save(tmp_path / "old.json", [BBK], BBK_ITEMS, datetime(2020, 1, 1, tzinfo=UTC))
    result = runner.invoke(app, ["roster", "import-fribbels", str(save)])
    assert result.exit_code == 0 and "is newer than the save file (2020-01-01 00:00 UTC)" in result.stdout
    assert "0 updated" in result.stdout


def test_cli_replaces_the_bonus_of_an_earlier_import(tmp_path: Path, synthetic_catalog: list[str]) -> None:
    first = write_save(tmp_path / "a.json", [BBK], BBK_ITEMS)
    assert runner.invoke(app, ["roster", "import-fribbels", str(first)]).exit_code == 0
    retyped = {**BBK, "artifactName": "Guard Blade", "artifactLevel": "30"}
    second = write_save(tmp_path / "b.json", [retyped], BBK_ITEMS, NOW + timedelta(days=1))
    result = runner.invoke(app, ["roster", "import-fribbels", str(second)])
    assert "updated   Blood Blade Karin (c2011)" in result.stdout
    assert "note: artifact: the save's efz03 +30 replaces the roster's efz01 +15 (confidence 0.70)" in result.stdout
    assert show()["artifact"] == {"code": "efz03", "level": 30}


def test_cli_never_guesses_between_copies(tmp_path: Path, synthetic_catalog: list[str]) -> None:
    for _ in range(2):
        assert runner.invoke(app, ["roster", "add", "c2011"]).exit_code == 0
    save = write_save(tmp_path / "fribbels.json", [BBK], BBK_ITEMS, NOW + timedelta(days=400))
    result = runner.invoke(app, ["roster", "import-fribbels", str(save)])
    assert "skipped   Blood Blade Karin (c2011): you own several copies (#1, #2)" in result.stdout


def test_cli_skips_two_save_heroes_with_one_code(tmp_path: Path, synthetic_catalog: list[str]) -> None:
    twins = [BBK, {**BBK, "id": "h2", "name": "blood blade karin"}]
    save = write_save(tmp_path / "fribbels.json", twins, [])
    result = runner.invoke(app, ["roster", "import-fribbels", str(save)])
    assert result.stdout.count("2 heroes of the save are this hero") == 2
    assert "0 new" in result.stdout


def test_cli_blocks_invalid_builds_unless_forced(tmp_path: Path, synthetic_catalog: list[str]) -> None:
    bad = [item("Weapon", stat("Attack", -5))]  # a negative stat breaks the data contract
    save = write_save(tmp_path / "fribbels.json", [BBK], bad)
    refused = runner.invoke(app, ["roster", "import-fribbels", str(save)])
    assert "skipped   Blood Blade Karin (c2011): not valid" in refused.stdout and "0 new" in refused.stdout
    assert "ERROR" in refused.stderr and "gear.weapon" in refused.stderr
    forced = runner.invoke(app, ["roster", "import-fribbels", str(save), "--force"])
    assert "1 new" in forced.stdout


def test_cli_reads_a_utf16_save_and_refuses_other_files(tmp_path: Path, synthetic_catalog: list[str]) -> None:
    utf16 = tmp_path / "utf16.json"
    utf16.write_text(save_text([BBK], BBK_ITEMS), encoding="utf-16")
    assert "1 new" in runner.invoke(app, ["roster", "import-fribbels", str(utf16), "--dry-run"]).stdout
    other = tmp_path / "other.json"
    other.write_text('{"version": 1}', encoding="utf-8")
    refused = runner.invoke(app, ["roster", "import-fribbels", str(other)])
    assert refused.exit_code == 2 and "not a Fribbels save" in refused.stderr
    missing = runner.invoke(app, ["roster", "import-fribbels", str(tmp_path / "nope.json")])
    assert missing.exit_code == 2 and "cannot read" in missing.stderr


def test_cli_needs_a_catalog(tmp_path: Path, isolated_home: AppPaths) -> None:
    save = write_save(tmp_path / "fribbels.json", [BBK], BBK_ITEMS)
    result = runner.invoke(app, ["roster", "import-fribbels", str(save)])
    assert result.exit_code == 2 and "needs the catalog" in result.stderr


# ------------------------------------------------------------------------------------------------ real save


@pytest.mark.fixtures("saves/fribbels.json")
def test_the_users_save_is_read() -> None:
    """A real save (git-ignored) must match the format the importer models: every hero and item read, equipped
    items linked to a hero, the game's wearer recorded, values in plausible units. Heroes need the real catalog, so
    only the format is checked here."""
    root = Path(__file__).resolve().parents[1] / "fixtures"
    text = (root / "saves" / "fribbels.json").read_text(encoding="utf-8-sig")
    save = read_fribbels_save(text, {}, {}, captured_at=NOW)
    assert save.heroes, "no hero read"
    unknown = "(0 in the save)"  # Fribbels' own "unknown" values: reported, not a format problem
    bad = [
        w for w in save.warnings if (w.startswith(("item #", "hero #")) and unknown not in w) or "unknown hero id" in w
    ]
    assert not bad, bad[:5]
    items = [FribbelsItem.model_validate(raw) for raw in json.loads(text)["items"]]  # all read, as checked above
    assert any(i.wearer is not None for i in items), "no game wearer recorded (what does 'not worn' look like?)"
    with_game_pieces = {i.equippedById for i in items if i.equippedById and i.ingameEquippedId is not None}
    matched = [h for h in save.heroes if h.game_id is not None]
    assert len(matched) * 2 > len(with_game_pieces), (len(matched), len(with_game_pieces))  # the wearer rule holds
    mains: Counter[tuple[Stat, bool]] = Counter()
    for i in items:
        try:
            gear = gear_of(i)
        except ValueError:
            continue  # Fribbels' "unknown" values: reported by the importer
        mains[(gear.main.stat, gear.main.value <= 1)] += 1
    wrong_units = {key: n for key, n in mains.items() if key[0].is_rate is not key[1]}
    assert not wrong_units, wrong_units  # rates are fractions after /100, flat stats are game units
