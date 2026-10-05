"""Fribbels' importer data (`gear.txt`, SPEC D53): the game's own heroes and items as Fribbels' scanner writes them.

Synthetic data shaped like the user's real file (field names from `scanner.js`); never real data.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from typer.testing import CliRunner

from e7ac.cli.app import app
from e7ac.domain.roster import GearSlot
from e7ac.roster.backup import export_roster, import_roster
from e7ac.roster.fribbels_import import ABSENT_AWAKENING, ESTIMATED, FribbelsSave, read_fribbels_save
from e7ac.roster.store import add_owned_hero, list_owned
from e7ac.storage.db import alembic_config, open_database, session_scope
from tests.test_fribbels_import import ARTIFACTS, HEROES, NOW, SUBS, imported, stat

runner = CliRunner()
SLOT_TYPES = {
    "Weapon": "weapon",
    "Helmet": "helm",
    "Armor": "armor",
    "Necklace": "neck",
    "Ring": "ring",
    "Boots": "boot",
}


def unit(game_id: int, code: str, name: str, *, g: int = 6, z: int | None = 6) -> dict[str, Any]:
    """A game unit as the scanner writes it (`g` stars, `z` awakening, left out when 0), with fields not read."""
    data: dict[str, Any] = {
        "id": game_id,
        "code": code,
        "name": name,
        "g": g,
        "exp": 0,
        "ct": 1,
        "st": 0,
        "s": [5, 5, 5],
    }
    if z is not None:
        data["z"] = z
    return data


def raw_item(
    gear: str,
    main: dict[str, Any],
    *,
    wearer: int | None = 270,
    ingame: int = 1,
    set_code: str = "set_speed",
    fribbels_set: str | None = "SpeedSet",
    enhance: int = 15,
    level: int = 90,
) -> dict[str, Any]:
    """A game item as the scanner writes it: the game's fields plus Fribbels' conversions (`set` only for sets
    Fribbels knows; `p` only on worn items, `ingameEquippedId` "undefined" otherwise)."""
    data: dict[str, Any] = {
        "code": "ecq6w",
        "type": SLOT_TYPES[gear],
        "f": set_code,
        "g": 5,
        "id": ingame,
        "ingameId": ingame,
        "ingameEquippedId": "undefined" if wearer is None else str(wearer),
        "gear": gear,
        "rank": "Epic",
        "enhance": enhance,
        "level": level,
        "main": main,
        "substats": SUBS,
        "op": [["att", "105"], ["cri", "0.05"]],
        "name": "Unknown",
    }
    if wearer is not None:
        data["p"] = wearer
    if fribbels_set is not None:
        data["set"] = fribbels_set
    return data


BBK_GEAR = [
    raw_item("Weapon", stat("Attack", 525), ingame=9001),
    raw_item("Helmet", stat("Health", 2835), ingame=9002),
    raw_item("Armor", stat("Defense", 310), ingame=9003),
    raw_item("Necklace", stat("CriticalHitDamagePercent", 70), ingame=9004, set_code="set_cri_dmg", fribbels_set=None),
    raw_item("Ring", stat("AttackPercent", 65), ingame=9005, set_code="set_cri_dmg", fribbels_set="DestructionSet"),
    raw_item("Boots", stat("Speed", 45), ingame=9006, set_code="set_cri_dmg", fribbels_set="DestructionSet"),
]
BBK_UNIT = unit(270, "c2011", "Blood Blade Karin")


def gear_text(units: list[dict[str, Any]], items: list[dict[str, Any]]) -> str:
    return json.dumps({"items": items, "heroes": units})


def read(units: list[dict[str, Any]], items: list[dict[str, Any]]) -> FribbelsSave:
    return read_fribbels_save(gear_text(units, items), HEROES, ARTIFACTS, captured_at=NOW)


# ------------------------------------------------------------------------------------------------ reading


def test_every_game_hero_comes_with_its_code_game_id_stars_awakening_and_gear() -> None:
    save = read([BBK_UNIT, unit(271, "c9001", "Test Hero", g=5, z=None)], BBK_GEAR)
    assert save.importer_data and save.warnings == [] and save.unused_items == 0
    bbk, test = save.heroes
    assert (bbk.hero_code, bbk.game_id, bbk.exact, bbk.problems, bbk.notes) == ("c2011", "270", True, [], [])
    assert bbk.build is not None and (bbk.build.stars, bbk.build.awakening, bbk.build.level) == (6, 6, 60)
    assert bbk.build.confidence == {"level": 0.0}  # stars and awakening are the game's own
    assert sorted(bbk.build.gear) == sorted(GearSlot)
    necklace = bbk.build.gear[GearSlot.NECKLACE]
    assert necklace.set_code == "set_cri_dmg" and necklace.external_id == "ingame:9004"  # a set unknown to Fribbels
    assert [s.rolls for s in bbk.build.gear[GearSlot.WEAPON].substats] == [2, 2, 2, 1]
    assert test.build is not None and (test.build.stars, test.build.awakening) == (5, 0)
    assert test.build.confidence["awakening"] == ABSENT_AWAKENING and test.build.gear == {}
    assert save.locations["ingame:9001"] == ("270", "on Blood Blade Karin")


def test_the_hero_name_is_never_needed() -> None:
    twins = [unit(300, "c9002", "Twin"), unit(301, "c9003", "Twin")]  # a name two catalog heroes share
    entries = read(twins, []).heroes
    assert [(e.hero_code, e.problems) for e in entries] == [("c9002", []), ("c9003", [])]


def test_copies_of_a_hero_stay_apart() -> None:
    copies = [BBK_UNIT, unit(272, "c2011", "Blood Blade Karin", g=3, z=None)]
    save = read(copies, [*BBK_GEAR, raw_item("Weapon", stat("Attack", 100), wearer=272, ingame=50)])
    main, fodder = save.heroes
    assert main.build is not None and fodder.build is not None
    assert (main.game_id, len(main.build.gear)) == ("270", 6) and (fodder.game_id, len(fodder.build.gear)) == ("272", 1)


@pytest.mark.parametrize(
    ("code", "problem"),
    [
        ("m0063", "m0063 is not a hero code (a monster or material): not imported"),
        ("c9999", "c9999 is not in the catalog (run e7 catalog sync)"),
    ],
)
def test_units_that_are_not_catalog_heroes_are_named(code: str, problem: str) -> None:
    (entry,) = read([unit(500, code, "Mighty Scout")], []).heroes
    assert entry.build is None and entry.problems == [problem]


def test_odd_stars_or_awakening_are_reported() -> None:
    save = read([unit(270, "c2011", "Blood Blade Karin", g=7), unit(271, "c9001", "Test Hero", g=5, z=6)], [])
    bad_stars, bad_awakening = save.heroes
    assert bad_stars.build is None and bad_stars.problems == ["stars 7 not usable: not imported"]
    assert bad_awakening.build is not None and bad_awakening.build.awakening == 5
    assert bad_awakening.notes == ["awakening 6 not usable with 5 stars: assumed 5"]


def test_estimates_unknown_values_and_missing_wearers_are_reported() -> None:
    items = [
        raw_item("Weapon", stat("Attack", 470), ingame=1, enhance=12),
        raw_item("Helmet", stat("Health", 1), ingame=2, level=0),  # Fribbels' "unknown"
        raw_item("Ring", stat("Speed", 4), ingame=3, wearer=999),  # worn by a unit missing from the file
        raw_item("Boots", stat("Speed", 45), ingame=4, wearer=None),
    ]
    save = read([BBK_UNIT], items)
    (entry,) = save.heroes
    assert entry.build is not None and entry.build.confidence["gear.weapon"] == ESTIMATED
    assert entry.notes == [
        "helmet: the save's piece is not usable (item level unknown (0 in the save))",
        "weapon: +N below +15 is Fribbels' estimate (a multiple of 3, up to 2 below the real one)",
    ]
    assert save.warnings == ["1 item(s) worn by hero ids missing from the file's heroes: not taken"]
    assert save.locations["ingame:4"] == (None, "in the inventory")
    assert save.locations["ingame:3"] == (None, "worn in the game by a hero missing from the file")
    assert save.unused_items == 2


# ------------------------------------------------------------------------------------------------ CLI


def write_gear(path: Path, units: list[dict[str, Any]], items: list[dict[str, Any]], when: datetime = NOW) -> Path:
    path.write_text(gear_text(units, items), encoding="utf-8")
    os.utime(path, (when.timestamp(), when.timestamp()))
    return path


def game_ids() -> dict[int, str | None]:
    from e7ac.paths import default_paths

    engine = open_database(default_paths().database)
    with session_scope(engine) as session:
        return {owned.id: owned.game_id for owned, _ in list_owned(session)}


def test_cli_imports_importer_data_and_links_every_copy(tmp_path: Path, synthetic_catalog: list[str]) -> None:
    units = [BBK_UNIT, unit(272, "c2011", "Blood Blade Karin", g=3, z=None), unit(500, "m0063", "Mighty Scout")]
    data = write_gear(tmp_path / "gear.txt", units, BBK_GEAR)
    first = runner.invoke(app, ["roster", "import-fribbels", str(data)])
    assert first.exit_code == 0, first.output
    assert first.stdout.count("new       Blood Blade Karin (c2011)") == 2
    assert "skipped   Mighty Scout (?): m0063 is not a hero code" in first.stdout
    assert "Fribbels importer data (file written 2026-10-05 12:00 UTC): 3 hero(es): 2 new" in first.stdout
    assert "Level and the displayed stats are not in the file" in first.stdout
    assert game_ids() == {1: "270", 2: "272"}
    again = runner.invoke(app, ["roster", "import-fribbels", str(data)])
    assert "0 new, 0 updated, 2 unchanged, 1 skipped" in again.stdout


def test_cli_links_the_roster_copy_to_the_main_game_copy(tmp_path: Path, synthetic_catalog: list[str]) -> None:
    assert runner.invoke(app, ["roster", "add", "c2011"]).exit_code == 0  # scanned or typed before: no game id
    units = [unit(272, "c2011", "Blood Blade Karin", g=3, z=None), BBK_UNIT]
    data = write_gear(tmp_path / "gear.txt", units, BBK_GEAR, NOW + timedelta(days=400))
    result = runner.invoke(app, ["roster", "import-fribbels", str(data)])
    assert result.exit_code == 0, result.output
    assert "1 new, 1 updated" in result.stdout
    assert game_ids() == {1: "270", 2: "272"}  # the 6-star geared copy is the roster's one
    again = runner.invoke(app, ["roster", "import-fribbels", str(data)])
    assert "2 unchanged" in again.stdout


def test_cli_never_guesses_between_look_alike_copies(tmp_path: Path, synthetic_catalog: list[str]) -> None:
    assert runner.invoke(app, ["roster", "add", "c2011"]).exit_code == 0
    units = [unit(272, "c2011", "Blood Blade Karin", g=3, z=None), unit(273, "c2011", "Blood Blade Karin", g=3, z=None)]
    data = write_gear(tmp_path / "gear.txt", units, [], NOW + timedelta(days=400))
    result = runner.invoke(app, ["roster", "import-fribbels", str(data)])
    assert result.stdout.count("several copies in the game look alike") == 2
    assert game_ids() == {1: None}


def test_cli_takes_importer_gear_over_screen_gear_but_never_older_data(
    tmp_path: Path, synthetic_catalog: list[str]
) -> None:
    from tests.test_fribbels_import import write_build

    seen = write_build(tmp_path / "seen.json", imported(), external_id=None, score=70)  # read on Hero Info
    assert runner.invoke(app, ["roster", "add", "c2011", "--from-json", str(seen)]).exit_code == 0
    regeared = [raw_item("Weapon", stat("Attack", 400), ingame=77), *BBK_GEAR[1:]]
    stale = write_gear(tmp_path / "old.txt", [BBK_UNIT], regeared, datetime(2020, 1, 1, tzinfo=UTC))
    refused = runner.invoke(app, ["roster", "import-fribbels", str(stale)])
    assert "is newer than the save file (2020-01-01 00:00 UTC)" in refused.stdout
    fresh = write_gear(tmp_path / "gear.txt", [BBK_UNIT], regeared, NOW + timedelta(days=400))
    taken = runner.invoke(app, ["roster", "import-fribbels", str(fresh)])  # written when Fribbels read the game
    assert "updated   Blood Blade Karin (c2011)" in taken.stdout


def test_an_optimizer_save_finds_the_linked_copy_by_its_game_match(
    tmp_path: Path, synthetic_catalog: list[str]
) -> None:
    from tests.test_fribbels_import import BBK, BBK_ITEMS, write_save

    units = [unit(271, "c2011", "Blood Blade Karin", g=3, z=None), {**BBK_UNIT, "id": "g1"}]  # "g1": the export's
    gear = [{**raw, "p": "g1", "ingameEquippedId": "g1"} for raw in BBK_GEAR]  # wearer id in the export test data
    data = write_gear(tmp_path / "gear.txt", units, gear)
    assert "2 new" in runner.invoke(app, ["roster", "import-fribbels", str(data)]).stdout
    save = write_save(tmp_path / "export.json", [BBK], BBK_ITEMS, NOW + timedelta(days=1))
    result = runner.invoke(app, ["roster", "import-fribbels", str(save)])
    assert "several copies" not in result.stdout and "Blood Blade Karin (c2011)" in result.stdout


# ------------------------------------------------------------------------------------------------ storage


def test_game_id_migration_and_backup_round_trip(tmp_path: Path) -> None:
    engine = open_database(tmp_path / "db.sqlite3")
    with session_scope(engine) as session:
        add_owned_hero(session, imported(), game_id="270")
        backup = export_roster(session, NOW)
    assert backup.heroes[0].game_id == "270"
    command.downgrade(alembic_config(engine), "0004_imprint_mode")
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM hero_snapshot").scalar() == 1
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM snapshot_gear").scalar() == 6
    command.upgrade(alembic_config(engine), "head")
    with session_scope(engine) as session:
        assert [owned.game_id for owned, _ in list_owned(session)] == [None]
        import_roster(session, backup)  # the backup's uid is the same hero: nothing new
    other = open_database(tmp_path / "other.sqlite3")
    with session_scope(other) as session:
        import_roster(session, backup)
        assert [owned.game_id for owned, _ in list_owned(session)] == ["270"]
    engine.dispose()
    other.dispose()


# ------------------------------------------------------------------------------------------------ real data


@pytest.mark.fixtures("saves/gear.txt")
def test_the_users_importer_data_is_read() -> None:
    """The user's real gear.txt (git-ignored): every unit and item read, the wearer links resolve, awakening never
    above the stars. Heroes need the real catalog, so only the format is checked."""
    root = Path(__file__).resolve().parents[1] / "fixtures"
    text = (root / "saves" / "gear.txt").read_text(encoding="utf-8-sig")
    save = read_fribbels_save(text, {}, {}, captured_at=NOW)
    assert save.importer_data and save.heroes
    unknown = "(0 in the save)"
    bad = [w for w in save.warnings if w.startswith(("item #", "hero #")) and unknown not in w]
    assert not bad, bad[:5]
    data = json.loads(text)
    worn = [i for i in data["items"] if "p" in i]
    placed = sum(1 for i in worn if save.locations.get(f"ingame:{i['ingameId']}", (None, ""))[0] is not None)
    assert placed > 0.9 * len(worn), (placed, len(worn))  # the wearer of nearly every worn piece is a unit of the file
    assert all(0 <= u.get("z", 0) <= u["g"] for u in data["heroes"])
    assert all(i["ingameEquippedId"] == str(i["p"]) for i in worn)
