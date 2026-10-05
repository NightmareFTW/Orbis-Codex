"""Fribbels save import (M4, path A of SPEC D45, D52): synthetic saves shaped like Fribbels' Gson output.

Never real data: a real save holds the user's whole account (fixtures/saves/ is git-ignored; see fixtures/README.md).
"""

from __future__ import annotations

import json
import os
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
    USER_ENTERED,
    FribbelsFileError,
    FribbelsItem,
    FribbelsSave,
    gear_of,
    hero_names,
    merge_with_current,
    read_fribbels_save,
)

runner = CliRunner()
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def fact(kind: EntityType, code: str, field: str, value: Any, source: SourceId = SourceId.FRIBBELS) -> Fact:
    return Fact(entity_type=kind, entity_id=code, field=field, value=value, source=source, status=DataStatus.COMMUNITY)


def hero(code: str, name: str, *, fribbels_name: str | None = None, **fields: Any) -> ResolvedEntity:
    facts = [fact(EntityType.HERO, code, "name", name, SourceId.STOVE)]
    if fribbels_name is not None:
        facts.append(fact(EntityType.HERO, code, "name", fribbels_name))
    facts += [fact(EntityType.HERO, code, key.replace("__", "."), value) for key, value in fields.items()]
    return resolve(facts)[0]


def artifact(code: str, name: str) -> ResolvedEntity:
    return resolve([fact(EntityType.ARTIFACT, code, "name", name)])[0]


HEROES = {
    "c2011": hero(
        "c2011",
        "Blood Blade Karin",
        imprint__stat="att_rate",
        imprint__values={"C": 0.06, "SSS": 0.18},
        ee__stat="cri",
    ),
    "c9001": hero("c9001", "Test Hero", fribbels_name="Test Hero (Fribbels)", imprint__stat="att"),
    "c9002": hero("c9002", "Twin"),
    "c9003": hero("c9003", "Twin"),
}
ARTIFACTS = {"efz01": artifact("efz01", "Test Dagger"), "efz09": artifact("efz09", "Old Relic")}


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


def item(
    gear: str,
    main: dict[str, Any],
    *,
    hero_id: str | None = "h1",
    set_name: str = "SpeedSet",
    rank: str = "Epic",
    ingame: str | None = None,
    subs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """An item as Fribbels saves it (`model/Item.java`, Gson field names), with fields we do not read."""
    data: dict[str, Any] = {
        "gear": gear,
        "rank": rank,
        "set": set_name,
        "enhance": 15,
        "level": 90,
        "main": main,
        "substats": SUBS if subs is None else subs,
        "id": f"fid-{gear}-{hero_id}",
        "wss": 70,  # Fribbels' own score: ignored
        "locked": False,
    }
    if ingame is not None:
        data["ingameId"] = ingame
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
    item("Boots", stat("Speed", 45), set_name="DestructionSet", rank="Heroic"),
]


def save_text(heroes: list[dict[str, Any]], items: list[dict[str, Any]]) -> str:
    return json.dumps({"heroes": heroes, "items": items})


def read(heroes: list[dict[str, Any]], items: list[dict[str, Any]]) -> FribbelsSave:
    return read_fribbels_save(save_text(heroes, items), HEROES, ARTIFACTS, captured_at=NOW)


# ------------------------------------------------------------------------------------------------ reading


def test_a_fribbels_hero_becomes_a_build_with_its_gear() -> None:
    save = read([BBK], BBK_ITEMS)
    assert save.warnings == [] and save.unequipped_items == 0
    (entry,) = save.heroes
    assert (entry.hero_code, entry.problems, entry.notes) == ("c2011", [], [])
    build = entry.build
    assert build is not None and build.source is BuildSource.FRIBBELS and build.captured_at == NOW
    assert sorted(build.gear) == sorted(GearSlot)
    weapon = build.gear[GearSlot.WEAPON]
    assert weapon == Gear(
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
    boots = build.gear[GearSlot.BOOTS]
    assert boots.grade is GearGrade.HEROIC and boots.external_id == "fribbels:fid-Boots-h1"  # no game id: Fribbels'
    assert build.final_stats is None and build.cp is None  # Fribbels' computed stats are never taken


def test_typed_bonuses_are_mapped_with_the_catalog_and_a_lower_confidence() -> None:
    build = read([BBK], BBK_ITEMS).heroes[0].build
    assert build is not None
    assert build.artifact == ArtifactRef(code="efz01", level=15)
    assert build.imprint == Imprint(grade=ImprintGrade.SSS, stat=Stat.ATK_PERCENT, value=0.18, mode=ImprintMode.SELF)
    assert build.exclusive_equipment == ExclusiveEquipment(stat=Stat.CRIT_CHANCE, value=0.12)
    for key in ("artifact", "imprint", "imprint.mode", "imprint.grade", "exclusive_equipment"):
        assert build.confidence[key] == USER_ENTERED, key


def test_level_and_awakening_are_assumed_from_the_stars() -> None:
    build = read([{**BBK, "stars": 5}], []).heroes[0].build
    assert build is not None and (build.stars, build.awakening, build.level) == (5, 5, 50)
    assert build.confidence["level"] == build.confidence["awakening"] == ASSUMED
    assert "stars" not in build.confidence and build.gear == {}


def test_unusable_stars_are_assumed_and_reported() -> None:
    (entry,) = read([{**BBK, "stars": 9}], []).heroes
    assert entry.build is not None and entry.build.confidence["stars"] == ASSUMED
    assert entry.notes == ["stars 9 not usable: assumed 6"]


def test_unset_bonuses_are_left_empty() -> None:
    unset = {"artifactName": "None", "artifactLevel": "None", "imprintNumber": "None", "eeNumber": ""}
    build = read([{**BBK, **unset}], []).heroes[0].build
    assert build is not None and (build.artifact, build.imprint, build.exclusive_equipment) == (None, None, None)
    assert not {"artifact", "imprint", "exclusive_equipment"} & set(build.confidence)


def test_an_imprint_value_off_the_grade_table_keeps_no_grade() -> None:
    build = read([{**BBK, "imprintNumber": "12.5"}], []).heroes[0].build
    assert build is not None and build.imprint is not None
    assert (build.imprint.grade, build.imprint.value) == (None, 0.125)
    assert build.confidence["imprint.grade"] == ASSUMED


def test_unusable_bonuses_are_reported_never_guessed() -> None:
    odd = {"artifactName": "Test Daggr", "artifactLevel": "15"}
    (entry,) = read([{**BBK, **odd}], []).heroes
    assert entry.build is not None and entry.build.artifact is None
    assert entry.notes == ["artifact 'Test Daggr' +15 not usable: not stored"]
    (high,) = read([{**BBK, "artifactLevel": "31"}], []).heroes
    assert high.build is not None and high.build.artifact is None and "not usable" in high.notes[0]
    (no_stats,) = read([{"id": "h2", "name": "Test Hero", "stars": 6, "eeNumber": "5"}], []).heroes
    assert no_stats.build is not None and no_stats.build.exclusive_equipment is None
    assert no_stats.notes == ["exclusive equipment '5': the catalog has no EE stat for this hero"]


def test_flat_imprint_values_are_not_divided() -> None:
    (entry,) = read([{"id": "h2", "name": "Test Hero", "stars": 6, "imprintNumber": 30}], []).heroes
    assert entry.build is not None and entry.build.imprint is not None
    assert (entry.build.imprint.stat, entry.build.imprint.value) == (Stat.ATK, 30)


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


def test_a_fribbels_name_shared_with_another_hero_stays_ambiguous() -> None:
    names = hero_names({**HEROES, "c9004": hero("c9004", "Other", fribbels_name="Test Hero")})
    assert names.is_ambiguous("Test Hero") and names.lookup("Test Hero") is None


def test_bad_entries_are_named_never_dropped_silently() -> None:
    items = [
        *BBK_ITEMS[:1],
        {"gear": "Weapon"},  # incomplete
        item("Helmet", stat("Dac", 3)),  # never a gear stat
        item("Armor", stat("Defense", 310), set_name="MysterySet"),
        item("Ring", stat("AttackPercent", 65), hero_id="ghost"),
        item("Boots", stat("Speed", 45), hero_id=None),  # in the inventory
    ]
    save = read([BBK, {"name": "no id"}], items)
    assert save.warnings == [
        "item #2: not read (rank: Field required)",
        "item #3: not read (unknown stat type 'Dac')",
        "item #4: not read (unknown set 'MysterySet')",
        "hero #2: not read (id: Field required)",
        "1 item(s) equipped by an unknown hero id ghost: ignored",
    ]
    assert save.unequipped_items == 1
    assert save.heroes[0].build is not None and list(save.heroes[0].build.gear) == [GearSlot.WEAPON]


def test_two_pieces_in_one_slot_keep_the_first_and_say_so() -> None:
    items = [BBK_ITEMS[0], item("Weapon", stat("Attack", 500), ingame="77")]
    items[1]["id"] = "second"
    (entry,) = read([BBK], items).heroes
    assert entry.notes == ["two weapon pieces equipped in the save: second ignored"]
    assert entry.build is not None and entry.build.gear[GearSlot.WEAPON].external_id == "ingame:9001"


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


def imported(**changes: Any) -> HeroBuild:
    build = read([{**BBK, **changes}], BBK_ITEMS).heroes[0].build
    assert build is not None
    return build


def screen_build(base: HeroBuild, **changes: Any) -> HeroBuild:
    """The hero as an earlier Hero Info scan stored it: same gear, read stats, real level and awakening."""
    data: dict[str, Any] = {
        "level": 60,
        "awakening": 6,
        "skills": SkillEnhancements(s1=5),
        "final_stats": FinalStats(
            atk=4116,
            defense=829,
            hp=12819,
            speed=133,
            crit_chance=1.0,
            crit_damage=3.57,
            effectiveness=0.0,
            effect_resistance=0.21,
            dual_attack=0.03,
        ),
        "cp": 120000,
        "source": BuildSource.OCR,
        "captured_at": NOW - timedelta(days=1),
        "confidence": {"level": 1.0, "awakening": 0.9, "final_stats.atk": 0.97, "artifact": 0.9, "cp": 0.95},
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
    assert merged.final_stats == current.final_stats and merged.cp == 120000  # nothing they depend on changed
    assert merged.confidence["level"] == 1.0 and merged.confidence["awakening"] == 0.9
    assert merged.confidence["final_stats.atk"] == 0.97 and merged.confidence["cp"] == 0.95
    assert merged.source is BuildSource.FRIBBELS and merged.captured_at == NOW


def test_changed_gear_drops_the_displayed_stats_with_a_note() -> None:
    current = screen_build(imported())
    changed = read([BBK], [item("Weapon", stat("Attack", 500), ingame="1"), *BBK_ITEMS[1:]]).heroes[0].build
    assert changed is not None
    merged, notes = merge_with_current(changed, current)
    assert merged.final_stats is None and merged.cp is None
    assert not any(key.startswith("final_stats.") or key == "cp" for key in merged.confidence)
    assert notes == ["the build changed: the displayed stats and CP of the last screen reading are dropped (rescan it)"]


def test_a_slot_missing_from_the_save_keeps_the_roster_piece() -> None:
    current = screen_build(imported(), confidence={"gear.boots": 0.8})
    partial = read([BBK], BBK_ITEMS[:5]).heroes[0].build
    assert partial is not None
    merged, notes = merge_with_current(partial, current)
    assert merged.gear[GearSlot.BOOTS] == current.gear[GearSlot.BOOTS]
    assert merged.confidence["gear.boots"] == 0.8
    assert notes == ["boots: not in the save (Fribbels imports only items from a chosen +N up): kept"]
    assert merged.final_stats == current.final_stats  # the build is still the same


def test_awakening_never_exceeds_the_save_stars() -> None:
    current = screen_build(imported())
    merged, notes = merge_with_current(imported(stars=5), current)
    assert (merged.stars, merged.awakening, merged.level) == (5, 5, 60)
    assert notes[0] == "awakening 6 is above the save's 5 stars: lowered to 5"


def test_a_typed_bonus_never_silently_replaces_a_different_one() -> None:
    current = screen_build(imported(), artifact=ArtifactRef(code="efz09", level=30))
    merged, notes = merge_with_current(imported(), current)
    assert merged.artifact == ArtifactRef(code="efz09", level=30) and merged.confidence["artifact"] == 0.9
    assert notes == [
        "artifact: the save says efz01 +15, the roster efz09 +30: kept the roster's (Fribbels' bonus stats are typed "
        "by hand; use e7 roster edit if the save is right)"
    ]
    doubtful = screen_build(imported(), artifact=ArtifactRef(code="efz09", level=30), confidence={"artifact": 0.5})
    taken, why = merge_with_current(imported(), doubtful)
    assert taken.artifact == ArtifactRef(code="efz01", level=15) and taken.confidence["artifact"] == USER_ENTERED
    assert why[0] == "artifact: the save's efz01 +15 replaces the roster's efz09 +30 (read with confidence 0.50)"
    assert taken.final_stats is None  # the artifact changed, so the displayed stats are stale


def test_a_bonus_the_save_lacks_is_kept() -> None:
    current = screen_build(imported())
    merged, notes = merge_with_current(imported(artifactName="None", eeNumber="None"), current)
    assert merged.artifact == current.artifact and merged.exclusive_equipment == current.exclusive_equipment
    assert merged.confidence["artifact"] == 0.9 and notes == []


def test_the_same_imprint_fills_only_what_the_roster_did_not_know() -> None:
    unknown = Imprint(grade=None, stat=Stat.ATK_PERCENT, value=0.18, mode=None)
    current = screen_build(imported(), imprint=unknown, confidence={"imprint": 0.9})
    merged, notes = merge_with_current(imported(), current)
    assert merged.imprint == Imprint(grade=ImprintGrade.SSS, stat=Stat.ATK_PERCENT, value=0.18, mode=ImprintMode.SELF)
    assert merged.confidence["imprint"] == 0.9
    assert merged.confidence["imprint.grade"] == merged.confidence["imprint.mode"] == USER_ENTERED
    assert notes == ["the build changed: the displayed stats and CP of the last screen reading are dropped (rescan it)"]


def test_a_team_imprint_read_on_screen_is_kept() -> None:
    team = Imprint(grade=ImprintGrade.SSS, stat=Stat.ATK_PERCENT, value=0.18, mode=ImprintMode.TEAM)
    current = screen_build(imported(), imprint=team, confidence={"imprint": 0.9, "imprint.mode": 0.9})
    merged, notes = merge_with_current(imported(), current)
    assert merged.imprint == team and merged.confidence["imprint.mode"] == 0.9
    assert notes == [] and merged.final_stats == current.final_stats


# ------------------------------------------------------------------------------------------------ CLI


def write_save(path: Path, heroes: list[dict[str, Any]], items: list[dict[str, Any]], when: datetime = NOW) -> Path:
    path.write_text(save_text(heroes, items), encoding="utf-8")
    os.utime(path, (when.timestamp(), when.timestamp()))
    return path


def test_cli_imports_then_reports_unchanged(tmp_path: Path, synthetic_catalog: list[str]) -> None:
    unknown = {"id": "h9", "name": "Nobody"}
    spare = item("Ring", stat("Speed", 4), hero_id=None)
    save = write_save(tmp_path / "fribbels.json", [BBK, unknown], [*BBK_ITEMS, spare])
    dry = runner.invoke(app, ["roster", "import-fribbels", str(save), "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert "dry run" in dry.stdout and "0 hero(es)" in runner.invoke(app, ["roster", "list"]).stdout
    first = runner.invoke(app, ["roster", "import-fribbels", str(save)])
    assert first.exit_code == 0, first.output
    assert "new       Blood Blade Karin (c2011): 6 gear piece(s)" in first.stdout
    assert "skipped Nobody (?): no catalog hero is called 'Nobody'" in first.stdout
    assert "2 hero(es): 1 new, 0 updated, 0 unchanged, 1 skipped; 1 unequipped item(s)" in first.stdout
    shown = runner.invoke(app, ["roster", "show", "1", "--json"])
    build = json.loads(shown.stdout)["build"]
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
    assert "0 new, 0 updated, 1 unchanged, 1 skipped" in again.stdout
    history = runner.invoke(app, ["roster", "history", "1"])
    assert history.stdout.count("fribbels") == 1


def test_cli_updates_the_current_build_and_keeps_its_level(tmp_path: Path, synthetic_catalog: list[str]) -> None:
    assert runner.invoke(app, ["roster", "add", "c2011", "--level", "60", "--awakening", "6"]).exit_code == 0
    later = NOW + timedelta(days=400)  # the manual entry is stamped now; the save must be newer
    save = write_save(tmp_path / "fribbels.json", [BBK], BBK_ITEMS, later)
    result = runner.invoke(app, ["roster", "import-fribbels", str(save)])
    assert result.exit_code == 0, result.output
    assert "updated   Blood Blade Karin (c2011)" in result.stdout
    build = json.loads(runner.invoke(app, ["roster", "show", "1", "--json"]).stdout)["build"]
    assert (build["level"], build["awakening"], len(build["gear"])) == (60, 6, 6)


def test_cli_never_puts_an_older_save_over_a_newer_build(tmp_path: Path, synthetic_catalog: list[str]) -> None:
    assert runner.invoke(app, ["roster", "add", "c2011"]).exit_code == 0
    save = write_save(tmp_path / "old.json", [BBK], BBK_ITEMS, datetime(2020, 1, 1, tzinfo=UTC))
    result = runner.invoke(app, ["roster", "import-fribbels", str(save)])
    assert result.exit_code == 0 and "is newer than the save (2020-01-01 00:00 UTC)" in result.stdout
    assert "0 updated" in result.stdout


def test_cli_never_guesses_between_copies(tmp_path: Path, synthetic_catalog: list[str]) -> None:
    for _ in range(2):
        assert runner.invoke(app, ["roster", "add", "c2011"]).exit_code == 0
    save = write_save(tmp_path / "fribbels.json", [BBK], BBK_ITEMS, NOW + timedelta(days=400))
    result = runner.invoke(app, ["roster", "import-fribbels", str(save)])
    assert "skipped Blood Blade Karin (c2011): you own several copies (#1, #2)" in result.stdout


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
    assert "skipped Blood Blade Karin (c2011): not valid" in refused.stdout and "0 new" in refused.stdout
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
def test_the_users_save_is_read(synthetic_catalog: list[str]) -> None:
    """A real save (git-ignored) must parse without a single unreadable item; heroes need the real catalog."""
    root = Path(__file__).resolve().parents[1] / "fixtures"
    text = (root / "saves" / "fribbels.json").read_text(encoding="utf-8-sig")
    save = read_fribbels_save(text, {}, {}, captured_at=NOW)
    assert not [w for w in save.warnings if w.startswith("item")], save.warnings[:5]
