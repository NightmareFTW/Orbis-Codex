from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError

from e7ac.domain.codes import Stat, is_artifact_code, is_hero_code, is_set_code
from e7ac.domain.roster import (
    MAX_INT,
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
    StatValue,
    Substat,
)
from e7ac.roster.backup import RosterExport, export_roster, import_roster
from e7ac.roster.store import (
    RosterError,
    add_owned_hero,
    add_snapshot,
    build_from_row,
    current_snapshot,
    history,
)
from e7ac.roster.validation import (
    GEAR_STATS,
    CatalogContext,
    Issue,
    Severity,
    active_sets,
    validate_build,
    validate_gear,
)
from e7ac.storage.db import alembic_config, make_engine, open_database, session_scope
from e7ac.storage.models import GearRow, HeroSnapshotRow
from tests.roster_strategies import SET_CODES, builds, gears

DOCS = Path(__file__).resolve().parents[1] / "docs"
# Rules that are our own data contract (units, positivity): the only ones allowed to be errors (SPEC D31)
DATA_CONTRACT_RULES = {"UNIT-RATE", "UNIT-FLAT", "GEAR-VALUE"}

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


def bbk(**changes: object) -> HeroBuild:
    """Blood Blade Karin as given in the golden fixture description (final stats only)."""
    data: dict[str, object] = {
        "hero_code": "c2011",
        "stars": 6,
        "awakening": 6,
        "level": 60,
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
        "cp": 141750,
        "captured_at": NOW,
        "source": BuildSource.MANUAL,
    }
    data.update(changes)
    return HeroBuild.model_validate(data)


def weapon(**changes: object) -> Gear:
    data: dict[str, object] = {
        "slot": GearSlot.WEAPON,
        "set_code": "set_cri_dmg",
        "grade": GearGrade.EPIC,
        "item_level": 90,
        "enhance": 15,
        "main": StatValue(stat=Stat.ATK, value=525),
        "substats": (
            Substat(stat=Stat.CRIT_CHANCE, value=0.12),
            Substat(stat=Stat.CRIT_DAMAGE, value=0.2),
            Substat(stat=Stat.SPEED, value=8),
            Substat(stat=Stat.ATK_PERCENT, value=0.15),
        ),
    }
    data.update(changes)
    return Gear.model_validate(data)


def rules(issues: list[Issue]) -> set[tuple[str, str]]:
    return {(i.severity.value, i.rule) for i in issues}


# ------------------------------------------------------------------------------------------------ models


def test_domain_models_reject_bad_identifiers_and_ranges() -> None:
    with pytest.raises(ValidationError, match="not a hero code"):
        bbk(hero_code="Blood Blade Karin")
    with pytest.raises(ValidationError, match="not a set code"):
        weapon(set_code="Destruction")
    with pytest.raises(ValidationError, match="not an artifact code"):
        ArtifactRef(code="Hostess", level=18)
    with pytest.raises(ValidationError):
        weapon(enhance=16)
    with pytest.raises(ValidationError, match="timezone-aware"):
        bbk(captured_at=datetime(2026, 10, 3))
    with pytest.raises(ValidationError, match="confidence"):
        bbk(confidence={"atk": 1.5})
    with pytest.raises(ValidationError, match="says it is a"):
        bbk(gear={GearSlot.RING: weapon()})
    with pytest.raises(ValidationError):
        bbk(cp=MAX_INT + 1)  # would overflow the database column: refused up front


@pytest.mark.parametrize("code", ["c2011\n", "c2011 ", " c2011", "c\uff12\uff10\uff11\uff11", "C2011"])
def test_codes_must_match_exactly(code: str) -> None:
    assert not is_hero_code(code)
    assert not is_artifact_code(code.replace("c2011", "efa22"))
    assert not is_set_code(code.replace("c2011", "set_cri_dmg"))
    assert is_hero_code("c2011") and is_artifact_code("efa22") and is_set_code("set_cri_dmg")


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_are_never_accepted(bad: float) -> None:
    with pytest.raises(ValidationError):
        Substat(stat=Stat.SPEED, value=bad)
    with pytest.raises(ValidationError):
        Imprint(grade=ImprintGrade.SSS, stat=Stat.ATK_PERCENT, value=bad)
    with pytest.raises(ValidationError):
        ExclusiveEquipment(stat=Stat.CRIT_CHANCE, value=bad)
    stats = bbk().final_stats
    assert stats is not None
    with pytest.raises(ValidationError):
        FinalStats.model_validate({**stats.model_dump(), "crit_damage": bad})
    with pytest.raises(ValidationError):
        bbk(confidence={"atk": bad})


def test_empty_exclusive_equipment_is_refused() -> None:
    with pytest.raises(ValidationError, match="at least one known field"):
        ExclusiveEquipment()
    assert ExclusiveEquipment(option_code="ek_c201101_01").stat is None


# ------------------------------------------------------------------------------------------------ validation


def test_valid_fixture_like_build_has_no_issues() -> None:
    assert validate_build(bbk(gear={GearSlot.WEAPON: weapon()})) == []


@given(gears())
def test_generated_valid_gear_passes(gear: Gear) -> None:
    assert validate_gear(gear) == []


@given(gears(), st.data())
def test_duplicate_or_main_repeating_substat_is_reported(gear: Gear, data: st.DataObject) -> None:
    if gear.substats:
        dup = gear.model_copy(update={"substats": (*gear.substats, gear.substats[0])})
        assert ("warning", "MECH-GEAR-03") in rules(validate_gear(dup))
    same_as_main = Substat(stat=gear.main.stat, value=data.draw(st.floats(0.02, 0.5)))
    bad = gear.model_copy(update={"substats": (same_as_main,)})
    assert ("warning", "MECH-GEAR-03") in rules(validate_gear(bad))


def test_five_distinct_substats_are_reported() -> None:
    five = weapon(
        substats=(
            Substat(stat=Stat.CRIT_CHANCE, value=0.04),
            Substat(stat=Stat.CRIT_DAMAGE, value=0.06),
            Substat(stat=Stat.SPEED, value=4),
            Substat(stat=Stat.ATK_PERCENT, value=0.08),
            Substat(stat=Stat.HP, value=180),
        )
    )
    assert ("warning", "MECH-GEAR-03") in rules(validate_gear(five))


# MECH-GEAR-08 copied from docs/MECHANICS.md as literals (independent of the validator's own tables)
_FLEX = {"att", "att_rate", "max_hp", "max_hp_rate", "def", "def_rate"}
MAIN_STAT_MATRIX = {
    GearSlot.WEAPON: {"att"},
    GearSlot.HELMET: {"max_hp"},
    GearSlot.ARMOR: {"def"},
    GearSlot.NECKLACE: _FLEX | {"cri", "cri_dmg"},
    GearSlot.RING: _FLEX | {"acc", "res"},
    GearSlot.BOOTS: _FLEX | {"speed"},
}


@pytest.mark.parametrize("slot", list(GearSlot))
@pytest.mark.parametrize("stat", sorted(GEAR_STATS))
def test_main_stat_matrix_matches_mechanics(slot: GearSlot, stat: Stat) -> None:
    value = 0.5 if stat.is_rate else 100.0
    gear = weapon(slot=slot, main=StatValue(stat=stat, value=value), substats=(), enhance=0)
    found = ("warning", "MECH-GEAR-08") in rules(validate_gear(gear))
    assert found == (stat.value not in MAIN_STAT_MATRIX[slot]), (slot, stat)


def test_community_game_rules_never_block() -> None:
    def_on_weapon = weapon(substats=(Substat(stat=Stat.DEF_PERCENT, value=0.08),))
    found = validate_gear(def_on_weapon)
    assert ("warning", "MECH-GEAR-09") in rules(found)
    assert ("warning", "MECH-GEAR-10") in rules(found)  # +15 with a single substat
    assert all(i.severity is Severity.WARNING for i in found)
    hp_weapon = weapon(main=StatValue(stat=Stat.HP, value=2835))
    assert rules(validate_gear(hp_weapon)) == {("warning", "MECH-GEAR-08")}
    dual = weapon(main=StatValue(stat=Stat.DUAL_ATTACK, value=0.05))
    assert ("warning", "MECH-GEAR-08") in rules(validate_gear(dual))


@given(gears(), st.data())
def test_only_data_contract_rules_are_errors(gear: Gear, data: st.DataObject) -> None:
    """Severity policy D31, whatever we break: any ERROR must be a unit/positivity rule, never a game rule."""
    subs = data.draw(st.lists(st.sampled_from(sorted(Stat)), max_size=6))
    broken = gear.model_copy(
        update={
            "substats": tuple(Substat(stat=s, value=data.draw(st.floats(0.001, 5000))) for s in subs),
            "main": StatValue(stat=data.draw(st.sampled_from(sorted(Stat))), value=data.draw(st.floats(0.001, 5000))),
        }
    )
    errors = {i.rule for i in validate_gear(broken) if i.severity is Severity.ERROR}
    assert errors <= DATA_CONTRACT_RULES


def test_every_rule_id_is_documented_and_errors_are_never_game_rules() -> None:
    source = (Path(__file__).resolve().parents[1] / "src/e7ac/roster/validation.py").read_text(encoding="utf-8")
    mechanics = (DOCS / "MECHANICS.md").read_text(encoding="utf-8")
    for rule in sorted(set(re.findall(r"MECH-[A-Z]+-\d+", source))):
        row = next((line for line in mechanics.splitlines() if line.startswith(f"| {rule} ")), None)
        assert row is not None, f"{rule} is used by validation but missing from docs/MECHANICS.md"
    # no game rule is verified yet, so none may block a save (D31); only data-contract rules are errors
    assert re.search(r"Severity\.ERROR,\s*\"MECH-", source) is None


def test_rates_entered_as_percent_are_errors() -> None:
    typical = weapon(
        substats=(
            Substat(stat=Stat.CRIT_CHANCE, value=5),  # 5 typed for 5%: 500%
            Substat(stat=Stat.CRIT_DAMAGE, value=7),
            Substat(stat=Stat.SPEED, value=4),
            Substat(stat=Stat.ATK_PERCENT, value=8),
        )
    )
    unit = [i for i in validate_gear(typical) if i.rule == "UNIT-RATE"]
    assert [i.field for i in unit] == ["gear.substats[0]", "gear.substats[1]", "gear.substats[3]"]
    assert all(i.severity is Severity.ERROR for i in unit)
    necklace = weapon(slot=GearSlot.NECKLACE, main=StatValue(stat=Stat.CRIT_DAMAGE, value=7))
    assert ("error", "UNIT-RATE") in rules(validate_gear(necklace))
    stats = bbk().final_stats
    assert stats is not None
    build = bbk(final_stats=stats.model_copy(update={"crit_damage": 357.0}))
    assert ("error", "UNIT-RATE") in rules(validate_build(build))
    imprint = bbk(imprint=Imprint(grade=ImprintGrade.SSS, stat=Stat.ATK_PERCENT, value=18))
    assert ("error", "UNIT-RATE") in rules(validate_build(imprint))
    ee = bbk(exclusive_equipment=ExclusiveEquipment(stat=Stat.CRIT_CHANCE, value=12))
    assert ("error", "UNIT-RATE") in rules(validate_build(ee))
    flat_imprint = bbk(imprint=Imprint(grade=ImprintGrade.B, stat=Stat.ATK, value=30))
    assert validate_build(flat_imprint) == []


def test_fraction_given_for_a_flat_stat_is_an_error() -> None:
    gear = weapon(substats=(Substat(stat=Stat.HP, value=0.08),))
    assert ("error", "UNIT-FLAT") in rules(validate_gear(gear))


@given(gears(), st.data())
def test_any_gear_rate_typed_as_percent_is_caught(gear: Gear, data: st.DataObject) -> None:
    rates = [i for i, s in enumerate(gear.substats) if s.stat.is_rate]
    if rates:
        index = data.draw(st.sampled_from(rates))
        sub = gear.substats[index]
        typed = sub.model_copy(update={"value": round(sub.value * 100, 2)})
        bad = gear.model_copy(update={"substats": (*gear.substats[:index], typed, *gear.substats[index + 1 :])})
        assert ("error", "UNIT-RATE") in rules(validate_gear(bad))
    if gear.main.stat.is_rate:
        bad = gear.model_copy(update={"main": StatValue(stat=gear.main.stat, value=round(gear.main.value * 100, 2))})
        assert ("error", "UNIT-RATE") in rules(validate_gear(bad))


def test_hero_level_cap_and_catalog_checks() -> None:
    assert ("warning", "MECH-HERO-01") in rules(validate_build(bbk(stars=5, awakening=5)))
    assert ("warning", "MECH-HERO-02") in rules(validate_build(bbk(stars=5, awakening=6, level=50)))
    context = CatalogContext(hero_role="assassin", artifact_role_lock="knight", artifact_role_lock_status="verified")
    found = validate_build(bbk(artifact=ArtifactRef(code="efa22", level=18)), context)
    lock = next(i for i in found if i.rule == "MECH-ART-04")
    assert lock.severity is Severity.WARNING and "(catalog class lock: verified)" in lock.message
    stats = bbk().final_stats
    assert stats is not None
    over = bbk(final_stats=stats.model_copy(update={"crit_chance": 1.12}))
    assert ("warning", "MECH-STAT-05") in rules(validate_build(over))


def test_imprint_and_ee_mismatches_warn() -> None:
    build = bbk(
        imprint=Imprint(grade=ImprintGrade.SSS, stat=Stat.ATK_PERCENT, value=0.16),
        exclusive_equipment=ExclusiveEquipment(stat=Stat.SPEED, value=4),
    )
    context = CatalogContext(imprint_stat=Stat.HP_PERCENT, imprint_values={"SSS": 0.18}, ee_stat=Stat.CRIT_CHANCE)
    found = validate_build(build, context)
    assert [(i.rule, i.field) for i in found] == [
        ("MECH-IMP-01", "imprint.stat"),
        ("MECH-IMP-01", "imprint.value"),
        ("MECH-EE-01", "exclusive_equipment.stat"),
    ]
    assert all(i.severity is Severity.WARNING for i in found)
    right = CatalogContext(imprint_stat=Stat.ATK_PERCENT, imprint_values={"SSS": 0.16}, ee_stat=Stat.SPEED)
    assert validate_build(build, right) == []


def test_a_team_imprint_is_not_checked_against_the_self_imprint_table() -> None:
    context = CatalogContext(imprint_stat=Stat.HP_PERCENT, imprint_values={"B": 0.07})
    team = bbk(imprint=Imprint(grade=None, stat=Stat.EFFECTIVENESS, value=0.06, mode=ImprintMode.TEAM))
    assert [i.rule for i in validate_build(team, context) if i.rule == "MECH-IMP-01"] == []
    unknown = bbk(imprint=Imprint(grade=None, stat=Stat.EFFECTIVENESS, value=0.06))
    assert [i.field for i in validate_build(unknown, context) if i.rule == "MECH-IMP-01"] == ["imprint.stat"]


def test_codes_unknown_to_the_catalog_warn() -> None:
    build = bbk(artifact=ArtifactRef(code="efa99", level=0), gear={GearSlot.WEAPON: weapon(set_code="set_crit")})
    context = CatalogContext(known_heroes={"c1011"}, known_artifacts={"efa22"}, known_sets={"set_cri_dmg"})
    found = [i for i in validate_build(build, context) if i.rule == "CATALOG-UNKNOWN"]
    assert [i.field for i in found] == ["hero_code", "artifact", "gear.weapon.set_code"]
    assert all(i.severity is Severity.WARNING for i in found)


def test_crit_damage_below_base_hints_at_a_unit_mistake() -> None:
    stats = bbk().final_stats
    assert stats is not None
    typed_as_fraction = bbk(final_stats=stats.model_copy(update={"crit_damage": 0.0357}))
    found = validate_build(typed_as_fraction, CatalogContext(base_crit_damage=1.5))
    assert ("warning", "MECH-STAT-03") in rules(found)
    assert validate_build(bbk(), CatalogContext(base_crit_damage=1.5)) == []


def test_active_sets_count_completed_bonuses() -> None:
    health = {
        slot: weapon(slot=slot, set_code="set_max_hp", main=StatValue(stat=Stat.HP, value=1))
        for slot in (GearSlot.WEAPON, GearSlot.HELMET, GearSlot.ARMOR, GearSlot.NECKLACE, GearSlot.RING)
    }
    build = bbk().model_copy(update={"gear": health})
    assert active_sets(build, {"set_max_hp": 2}) == {"set_max_hp": 2}
    assert active_sets(build, {}) == {}


@given(
    st.lists(st.sampled_from(SET_CODES[:3]), min_size=0, max_size=6),  # few codes: many same-set pieces
    st.dictionaries(st.sampled_from(SET_CODES[:3]), st.sampled_from([2, 4])),
)
def test_active_sets_property(set_codes: list[str], pieces: dict[str, int]) -> None:
    """MECH-GEAR-07: a bonus applies once per *completed* group of pieces; partial groups give nothing."""
    slots = list(GearSlot)[: len(set_codes)]
    build = bbk(gear={slot: weapon(slot=slot, set_code=code) for slot, code in zip(slots, set_codes, strict=True)})
    counts = Counter(set_codes)
    expected = {code: counts[code] // need for code, need in pieces.items() if counts[code] // need}
    found = active_sets(build, pieces)
    assert found == expected
    assert sum(times * pieces[code] for code, times in found.items()) <= len(build.gear)
    assert set(found) <= set(pieces)


# ------------------------------------------------------------------------------------------------ storage


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    return open_database(tmp_path / "roster.sqlite3")


def test_editing_creates_a_new_snapshot_and_keeps_history(engine: Engine) -> None:
    with session_scope(engine) as session:
        owned = add_owned_hero(session, bbk(gear={GearSlot.WEAPON: weapon()}), arena_relevant=True)
        add_snapshot(session, owned, bbk(cp=150000, gear={GearSlot.WEAPON: weapon()}))
        rows = history(session, owned.id)
        assert [r.is_current for r in rows] == [False, True]
        current = current_snapshot(session, owned.id)
        assert current is not None and build_from_row(session, current).cp == 150000
        assert build_from_row(session, rows[0]).cp == 141750
        assert session.scalar(select(func.count()).select_from(GearRow)) == 1  # identical weapon stored once


def test_snapshot_for_another_hero_is_refused(engine: Engine) -> None:
    from e7ac.roster.store import RosterError

    with session_scope(engine) as session:
        owned = add_owned_hero(session, bbk())
        with pytest.raises(RosterError):
            add_snapshot(session, owned, bbk(hero_code="c1011"))


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(build=builds())
def test_any_valid_build_round_trips_through_the_database(
    tmp_path_factory: pytest.TempPathFactory, build: HeroBuild
) -> None:
    engine = open_database(tmp_path_factory.mktemp("rt") / "db.sqlite3")
    with session_scope(engine) as session:
        owned = add_owned_hero(session, build)
        row = current_snapshot(session, owned.id)
        assert row is not None
        assert build_from_row(session, row) == build
    engine.dispose()


@settings(max_examples=15, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.lists(builds(), min_size=1, max_size=4))
def test_export_import_is_lossless_and_idempotent(
    tmp_path_factory: pytest.TempPathFactory, items: list[HeroBuild]
) -> None:
    source = open_database(tmp_path_factory.mktemp("src") / "db.sqlite3")
    with session_scope(source) as session:
        for build in items:
            owned = add_owned_hero(session, build)
            add_snapshot(session, owned, build.model_copy(update={"cp": (build.cp or 0) + 1}))
        exported = export_roster(session, NOW)
    target = open_database(tmp_path_factory.mktemp("dst") / "db.sqlite3")
    with session_scope(target) as session:
        first = import_roster(session, exported)
        again = import_roster(session, exported)
        round_trip = export_roster(session, NOW)
    assert first.heroes_added == len(items) and first.snapshots_added == 2 * len(items)
    assert again.heroes_added == 0 and again.snapshots_added == 0
    assert round_trip == exported
    source.dispose()
    target.dispose()


def test_different_pieces_with_the_same_main_are_not_merged(engine: Engine) -> None:
    other = weapon(
        substats=(
            Substat(stat=Stat.HP, value=180),
            Substat(stat=Stat.SPEED, value=4),
            Substat(stat=Stat.CRIT_CHANCE, value=0.05),
            Substat(stat=Stat.ATK_PERCENT, value=0.08),
        )
    )
    with session_scope(engine) as session:
        a = add_owned_hero(session, bbk(gear={GearSlot.WEAPON: weapon()}))
        b = add_owned_hero(session, bbk(hero_code="c1011", gear={GearSlot.WEAPON: other}))
        for owned, expected in ((a, weapon()), (b, other)):
            row = current_snapshot(session, owned.id)
            assert row is not None and build_from_row(session, row).gear[GearSlot.WEAPON] == expected
        assert session.scalar(select(func.count()).select_from(GearRow)) == 2


def test_gear_dedupe_survives_a_score_change(engine: Engine) -> None:
    """Fribbels recomputes scores: each (content, score) is stored once, not once per snapshot."""
    with session_scope(engine) as session:
        owned = add_owned_hero(session, bbk(gear={GearSlot.WEAPON: weapon(score=10)}))
        for cp in (1, 2, 3):
            add_snapshot(session, owned, bbk(cp=cp, gear={GearSlot.WEAPON: weapon(score=20)}))
        assert session.scalar(select(func.count()).select_from(GearRow)) == 2


def test_database_refuses_two_current_snapshots(engine: Engine) -> None:
    with pytest.raises(IntegrityError), session_scope(engine) as session:
        owned = add_owned_hero(session, bbk())
        add_snapshot(session, owned, bbk(cp=1), make_current=False).is_current = True
        session.flush()


def test_migration_repairs_duplicate_current_snapshots(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "old.sqlite3")
    command.upgrade(alembic_config(engine), "0002_roster")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO owned_hero (id, uid, hero_code, arena_relevant, note, created_at) "
            "VALUES (1, 'h', 'c2011', 0, '', '2026-10-03 12:00:00.000000')"
        )
        for snap_id in (1, 2):
            connection.exec_driver_sql(
                "INSERT INTO hero_snapshot (id, uid, owned_hero_id, captured_at, source, is_current, stars, awakening,"
                " level, skill_s1, skill_s2, skill_s3, confidence_json, note) "
                f"VALUES ({snap_id}, 's{snap_id}', 1, '2026-10-03 12:00:00.000000', 'manual', 1, 6, 6, 60, 0, 0, 0,"
                " '{}', '')"
            )
    command.upgrade(alembic_config(engine), "head")
    with session_scope(engine) as session:
        assert [r.is_current for r in history(session, 1)] == [False, True]
    engine.dispose()


def test_imprint_mode_migration_keeps_rows_both_ways(tmp_path: Path) -> None:
    engine = open_database(tmp_path / "db.sqlite3")
    team = Imprint(grade=None, stat=Stat.EFFECTIVENESS, value=0.06, mode=ImprintMode.TEAM)
    with session_scope(engine) as session:
        owned = add_owned_hero(session, bbk(gear={GearSlot.WEAPON: weapon()}, imprint=team))
        row = current_snapshot(session, owned.id)
        assert row is not None and build_from_row(session, row).imprint == team
    command.downgrade(alembic_config(engine), "0003_one_current_snapshot")  # in place: gear links must survive
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM snapshot_gear").scalar() == 1
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM hero_snapshot").scalar() == 1
    command.upgrade(alembic_config(engine), "head")
    with session_scope(engine) as session:
        row = current_snapshot(session, owned.id)
        assert row is not None
        restored = build_from_row(session, row)
        assert restored.imprint == team.model_copy(update={"mode": None}) and GearSlot.WEAPON in restored.gear
    engine.dispose()


# ------------------------------------------------------------------------------------------------ backup / import


def _backup(*snapshots: tuple[str, datetime, bool], hero_uid: str = "hero1", code: str = "c2011") -> RosterExport:
    return RosterExport.model_validate(
        {
            "exported_at": NOW.isoformat(),
            "heroes": [
                {
                    "uid": hero_uid,
                    "hero_code": code,
                    "arena_relevant": False,
                    "note": "",
                    "created_at": NOW.isoformat(),
                    "snapshots": [
                        {
                            "uid": uid,
                            "is_current": current,
                            "build": bbk(hero_code=code, captured_at=when, cp=int(when.timestamp())).model_dump(
                                mode="json"
                            ),
                        }
                        for uid, when, current in snapshots
                    ],
                }
            ],
        }
    )


def _current_uid(engine: Engine) -> str:
    with session_scope(engine) as session:
        row = session.scalars(select(HeroSnapshotRow).where(HeroSnapshotRow.is_current.is_(True))).one()
        return row.uid


def test_import_keeps_the_backups_current_flag_for_a_new_hero(engine: Engine) -> None:
    older, newer = NOW, NOW + timedelta(days=1)
    with session_scope(engine) as session:
        import_roster(session, _backup(("s1", older, True), ("s2", newer, False)))
    assert _current_uid(engine) == "s1"  # restored as it was, not "newest wins"
    with session_scope(engine) as session:
        exported = export_roster(session, NOW)
    assert exported == _backup(("s1", older, True), ("s2", newer, False))  # lossless, flags included


def test_import_merge_moves_current_only_for_newer_snapshots(engine: Engine) -> None:
    day = timedelta(days=1)
    with session_scope(engine) as session:
        import_roster(session, _backup(("s2", NOW + day, True)))
    with session_scope(engine) as session:
        report = import_roster(session, _backup(("s1", NOW, False), ("s2", NOW + day, True)))
    assert (report.snapshots_added, report.snapshots_skipped, report.current_changed) == (1, 1, ())
    assert _current_uid(engine) == "s2"  # an older snapshot never replaces the user's latest build
    with session_scope(engine) as session:
        report = import_roster(session, _backup(("s3", NOW + 2 * day, False)))
    assert report.current_changed == ("hero1",) and _current_uid(engine) == "s3"


def test_import_conflicts_abort_instead_of_dropping_data(engine: Engine) -> None:
    with session_scope(engine) as session:
        import_roster(session, _backup(("s1", NOW, True)))
    with pytest.raises(RosterError, match="already belongs to another hero"), session_scope(engine) as session:
        import_roster(session, _backup(("s1", NOW, True), hero_uid="hero2", code="c1011"))
    with pytest.raises(RosterError, match="roster has that uid as c2011"), session_scope(engine) as session:
        import_roster(session, _backup(("s9", NOW, True), code="c1011"))
    with session_scope(engine) as session:
        assert len(history(session, 1)) == 1  # both imports rolled back entirely


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["heroes"][0].update(hero_code="Blood Blade Karin"), "not a hero code"),
        (lambda d: d["heroes"][0].update(created_at="2026-10-03T12:00:00"), "timezone"),
        (lambda d: d["heroes"][0]["snapshots"][1].update(is_current=True), "more than one current"),
        (lambda d: d["heroes"][0]["snapshots"][1]["build"].update(hero_code="c1011"), "snapshots for another hero"),
        (lambda d: d["heroes"][0]["snapshots"][1].update(uid="s1"), "duplicate snapshot uid"),
    ],
)
def test_inconsistent_backups_are_rejected_before_import(mutate: object, message: str) -> None:
    data = _backup(("s1", NOW, True), ("s2", NOW + timedelta(days=1), False)).model_dump(mode="json")
    assert callable(mutate)
    mutate(data)
    with pytest.raises(ValidationError, match=message):
        RosterExport.model_validate(data)
