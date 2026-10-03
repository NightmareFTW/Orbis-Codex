from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError
from sqlalchemy import Engine, func, select

from e7ac.domain.codes import Stat
from e7ac.domain.roster import (
    ArtifactRef,
    BuildSource,
    FinalStats,
    Gear,
    GearGrade,
    GearSlot,
    HeroBuild,
    StatValue,
    Substat,
)
from e7ac.roster.backup import export_roster, import_roster
from e7ac.roster.store import add_owned_hero, add_snapshot, build_from_row, current_snapshot, history
from e7ac.roster.validation import Issue, Severity, active_sets, validate_build, validate_gear
from e7ac.storage.db import open_database, session_scope
from e7ac.storage.models import GearRow
from tests.roster_strategies import builds, gears

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


# ------------------------------------------------------------------------------------------------ validation


def test_valid_fixture_like_build_has_no_issues() -> None:
    assert validate_build(bbk(gear={GearSlot.WEAPON: weapon()})) == []


@given(gears())
def test_generated_valid_gear_passes(gear: Gear) -> None:
    assert validate_gear(gear) == []


@given(gears(), st.data())
def test_duplicate_or_main_repeating_substat_is_an_error(gear: Gear, data: st.DataObject) -> None:
    if gear.substats:
        dup = gear.model_copy(update={"substats": (*gear.substats, gear.substats[0])})
        assert ("error", "MECH-GEAR-03") in rules(validate_gear(dup))
    same_as_main = Substat(stat=gear.main.stat, value=data.draw(st.floats(0.01, 0.5)))
    bad = gear.model_copy(update={"substats": (same_as_main,)})
    assert ("error", "MECH-GEAR-03") in rules(validate_gear(bad))


def test_slot_main_stat_rules() -> None:
    assert ("error", "MECH-GEAR-08") in rules(validate_gear(weapon(main=StatValue(stat=Stat.HP, value=2835))))
    boots = weapon(slot=GearSlot.BOOTS, main=StatValue(stat=Stat.SPEED, value=45), substats=())
    assert ("error", "MECH-GEAR-08") not in rules(validate_gear(boots))
    ring_cd = weapon(slot=GearSlot.RING, main=StatValue(stat=Stat.CRIT_DAMAGE, value=0.7))
    assert ("error", "MECH-GEAR-08") in rules(validate_gear(ring_cd))
    dual = weapon(main=StatValue(stat=Stat.DUAL_ATTACK, value=0.05))
    assert ("error", "GEAR-STAT") in rules(validate_gear(dual))


def test_community_rules_are_only_warnings() -> None:
    def_on_weapon = weapon(substats=(Substat(stat=Stat.DEF_PERCENT, value=0.08),))
    found = validate_gear(def_on_weapon)
    assert ("warning", "MECH-GEAR-09") in rules(found)
    assert ("warning", "MECH-GEAR-10") in rules(found)  # +15 with a single substat
    assert all(i.severity is Severity.WARNING for i in found)


def test_rates_entered_as_percent_are_errors() -> None:
    gear = weapon(substats=(Substat(stat=Stat.CRIT_CHANCE, value=12),))
    assert ("error", "UNIT-RATE") in rules(validate_gear(gear))
    stats = bbk().final_stats
    assert stats is not None
    build = bbk(final_stats=stats.model_copy(update={"crit_damage": 357.0}))
    assert ("error", "UNIT-RATE") in rules(validate_build(build))


def test_hero_level_cap_and_catalog_checks() -> None:
    assert ("warning", "MECH-HERO-01") in rules(validate_build(bbk(stars=5, awakening=5)))
    found = validate_build(
        bbk(artifact=ArtifactRef(code="efa22", level=18)),
        hero_role="assassin",
        artifact_role_lock="knight",
        ee_stat=Stat.CRIT_CHANCE,
    )
    assert ("error", "ARTIFACT-CLASS-LOCK") in rules(found)
    stats = bbk().final_stats
    assert stats is not None
    over = bbk(final_stats=stats.model_copy(update={"crit_chance": 1.12}))
    assert ("warning", "MECH-STAT-05") in rules(validate_build(over))


def test_active_sets_count_completed_bonuses() -> None:
    health = {
        slot: weapon(slot=slot, set_code="set_max_hp", main=StatValue(stat=Stat.HP, value=1))
        for slot in (GearSlot.WEAPON, GearSlot.HELMET, GearSlot.ARMOR, GearSlot.NECKLACE, GearSlot.RING)
    }
    build = bbk().model_copy(update={"gear": health})
    assert active_sets(build, {"set_max_hp": 2}) == {"set_max_hp": 2}
    assert active_sets(build, {}) == {}


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
