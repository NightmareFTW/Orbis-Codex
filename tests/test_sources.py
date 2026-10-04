from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from e7ac.catalog.facts import EntityType, Fact
from e7ac.catalog.names import NameIndex
from e7ac.catalog.sets import parse_effect_text
from e7ac.domain.codes import DataStatus, SourceId, Stat
from e7ac.domain.world import World
from e7ac.sources import e7calc, fribbels, stove
from e7ac.sources.http import CachedHttp, HttpConfig
from tests.catalog_data import (
    E7CALC_HEROES_TS,
    FRIBBELS_ARTIFACTDATA,
    FRIBBELS_HERODATA,
    STOVE_ARTIFACTS,
    make_handler,
)


def http_for(tmp_path: Path, handler: Any) -> CachedHttp:
    return CachedHttp(
        cache_dir=tmp_path / "cache",
        config=HttpConfig(min_interval=0.0),
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
    )


def values(facts: list[Fact], entity_id: str) -> dict[str, Any]:
    return {f.field: f.value for f in facts if f.entity_id == entity_id}


def stove_artifact(code: str) -> dict[str, Any]:
    return next(a for a in STOVE_ARTIFACTS if a["artifact_code"] == code)


# ------------------------------------------------------------------------------------------------ stove


def test_stove_paginates_and_maps_codes(tmp_path: Path) -> None:
    calls: list[str] = []
    catalog = stove.fetch_catalog(http_for(tmp_path, make_handler(page_size=2, calls=calls)), World.EUROPE)
    assert [h.hero_code for h in catalog.heroes] == ["c2011", "c1011", "c9001", "c9002", "c9003", "c9010"]
    assert catalog.errors == []
    hero_pages = [c for c in calls if "hero-list" in c]
    assert len(hero_pages) == 3 and all("world_code=world_eu" in c for c in hero_pages)
    facts, run = stove.to_facts(catalog)
    assert all(f.source is SourceId.STOVE for f in facts)
    hero_facts = [f for f in facts if f.entity_type is EntityType.HERO]
    assert hero_facts and all(f.status is DataStatus.VERIFIED for f in hero_facts)
    bbk = values(facts, "c2011")
    assert bbk == {"name": "Blood Blade Karin", "element": "dark", "role": "assassin", "rarity": 4}
    plain = values(facts, "efz02")
    assert plain["role_lock"] is None and "effect_text" not in plain
    assert any("efz02: no effect text" in w for w in run.warnings)


def _fribbels_data() -> fribbels.FribbelsData:
    heroes, artifacts, warnings = fribbels.parse(FRIBBELS_HERODATA, FRIBBELS_ARTIFACTDATA)
    return fribbels.FribbelsData(heroes=heroes, artifacts=artifacts, results=[_fake_result()], warnings=warnings)


def _artifact_facts(facts: list[Fact], code: str) -> dict[str, Fact]:
    return {f.field: f for f in facts if f.entity_id == code}


def test_stove_artifact_stat_slots_follow_fribbels_values(tmp_path: Path) -> None:
    catalog = stove.fetch_catalog(http_for(tmp_path, make_handler()), World.GLOBAL)
    facts, run = stove.to_facts(catalog, fribbels.artifact_stat_hints(_fribbels_data()))
    expected = {
        "efz01": {"atk_min": 21, "atk_max": 273, "hp_min": 32, "hp_max": 416},
        "efz03": {"atk_min": 21, "atk_max": 273, "def_min": 5, "def_max": 65},  # ATK + DEF
        "efz04": {"def_min": 5, "def_max": 65, "hp_min": 76, "hp_max": 988},  # DEF + HP
    }
    for code, stats in expected.items():
        found = {k: f for k, f in _artifact_facts(facts, code).items() if k.endswith(("_min", "_max"))}
        assert {k: f.value for k, f in found.items()} == stats, code
        assert all(f.status is DataStatus.VERIFIED for f in found.values()), code
    # efz04: Fribbels says DEF 5 / HP 76 and Stove sends (5, 76) in its "attack"/"defense" fields -> confirmed
    # efz02 has no Fribbels entry -> the default ATK/HP reading stays an assumption
    plain = _artifact_facts(facts, "efz02")
    assert plain["atk_min"].status is DataStatus.ASSUMED and plain["hp_min"].value == 10
    assert any("1 artifact(s) whose stat slots are not confirmed" in w and "efz02" in w for w in run.warnings)


def test_stove_without_hints_marks_every_artifact_stat_assumed(tmp_path: Path) -> None:
    catalog = stove.fetch_catalog(http_for(tmp_path, make_handler()), World.GLOBAL)
    facts, _ = stove.to_facts(catalog)
    stat_facts = [f for f in facts if f.entity_type is EntityType.ARTIFACT and f.field.endswith(("_min", "_max"))]
    assert stat_facts and all(f.status is DataStatus.ASSUMED for f in stat_facts)
    assert {f.field for f in stat_facts} == {"atk_min", "atk_max", "hp_min", "hp_max"}


def test_stove_artifact_hint_that_disagrees_is_not_a_confirmation() -> None:
    art = stove.StoveArtifact.model_validate(stove_artifact("efz01"))
    assert stove._artifact_stat_names(art, {"atk": 21, "def": 0, "hp": 33}) == (("atk", "hp"), False)
    assert stove._artifact_stat_names(art, {"atk": 21, "def": 1, "hp": 32}) == (("atk", "hp"), False)
    assert stove._artifact_stat_names(art, {"atk": 21, "def": 0, "hp": 32}) == (("atk", "hp"), True)


def test_stove_effect_levels_keep_present_placeholders_and_flag_unknowns(tmp_path: Path) -> None:
    catalog = stove.fetch_catalog(http_for(tmp_path, make_handler()), World.GLOBAL)
    facts, run = stove.to_facts(catalog)
    dagger = _artifact_facts(facts, "efz01")["effect_levels"]  # '@' and '@@': first value is a 0.0% sentinel
    assert dagger.value == [{"min": None, "max": None}, {"min": "8.0%", "max": "16.0%"}]
    assert dagger.status is DataStatus.ASSUMED
    blade = _artifact_facts(facts, "efz03")["effect_levels"]  # one '@': the 4 padding entries are dropped
    assert blade.value == [{"min": "10.0%", "max": "20.0%"}]
    assert blade.status is DataStatus.VERIFIED
    shield = _artifact_facts(facts, "efz04")["effect_levels"]  # '{}' level entry = unknown
    assert shield.value == [{"min": None, "max": None}] and shield.status is DataStatus.ASSUMED
    assert any("2 artifact(s) with effect values missing" in w and "efz01, efz04" in w for w in run.warnings)


def test_stove_set_facts_only_for_accepted_codes_and_never_verified() -> None:
    good = stove.StoveSet(equip_code="set_speed", equip_name="Speed Set", equip_effect="Speed increases by 25%.")
    bad = stove.StoveSet(equip_code="Speed", equip_name="Speed Set", equip_effect="Speed increases by 25%.")
    catalog = stove.StoveCatalog(heroes=[], sets=[good, bad], artifacts=[], results=[], warnings=[])
    facts, run = stove.to_facts(catalog)
    assert {f.entity_id for f in facts} == {"set_speed"}
    derived = [f for f in facts if f.field in ("kind", "static_bonus", "combat_effects")]
    assert len(derived) == 3 and all(f.status is DataStatus.COMMUNITY for f in derived)
    assert any("unexpected set code 'Speed'" in w for w in run.warnings)


def test_stove_empty_list_and_all_rejected_are_errors(tmp_path: Path) -> None:
    catalog = stove.fetch_catalog(http_for(tmp_path / "a", make_handler(stove_heroes=[])), World.GLOBAL)
    assert catalog.heroes == [] and catalog.sets and catalog.artifacts  # the other endpoints are kept
    assert catalog.errors == ["hero-list: hero-list: no usable item (total_count=0)"]
    bad = [{"hero_code": "c9009", "hero_name": "Bard", "grade": 5, "job_code": "bard", "attribute_code": "fire"}]
    catalog = stove.fetch_catalog(http_for(tmp_path / "b", make_handler(stove_heroes=bad)), World.GLOBAL)
    assert len(catalog.errors) == 1 and "no usable item (c9009:" in catalog.errors[0]


def test_stove_reports_missing_items(tmp_path: Path) -> None:
    inner = make_handler()

    def handler(request: httpx.Request) -> httpx.Response:
        response = inner(request)
        if "hero-list" not in str(request.url):
            return response
        payload = json.loads(response.content)
        payload["value"]["total_count"] += 1  # the API claims one more hero than it ever sends
        return httpx.Response(200, json=payload)

    catalog = stove.fetch_catalog(http_for(tmp_path, handler), World.GLOBAL)
    assert len(catalog.heroes) == 6
    assert any("hero-list: collected 6 items but the API reports 7" in w for w in catalog.warnings)


def test_stove_refetches_when_pages_come_from_different_list_versions(tmp_path: Path) -> None:
    inner = make_handler()
    bumps = {"left": 1}
    hero_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        response = inner(request)
        url = str(request.url)
        if "hero-list" not in url:
            return response
        hero_calls.append(url)
        if "current_page=2" in url and bumps["left"] > 0:
            bumps["left"] -= 1
            payload = json.loads(response.content)
            payload["value"]["total_count"] = 5  # a hero was added/removed between page 1 and page 2
            return httpx.Response(200, json=payload)
        return response

    catalog = stove.fetch_catalog(http_for(tmp_path / "once", handler), World.GLOBAL)
    assert len(catalog.heroes) == 6
    assert not any("different list versions" in w for w in catalog.warnings)
    assert sum("current_page=1" in c for c in hero_calls) == 2  # the whole list was fetched again

    bumps["left"] = 10**6  # never settles: keep the data but say so
    catalog = stove.fetch_catalog(http_for(tmp_path / "always", handler), World.GLOBAL)
    assert any("hero-list: pages come from different list versions" in w for w in catalog.warnings)


def test_stove_error_page_never_replaces_the_cached_good_copy(tmp_path: Path) -> None:
    good = stove.fetch_catalog(http_for(tmp_path, make_handler()), World.GLOBAL)

    def maintenance(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 500, "message": "maintenance", "value": None})

    again = stove.fetch_catalog(http_for(tmp_path, maintenance), World.GLOBAL, refresh=True)
    assert [h.hero_code for h in again.heroes] == [h.hero_code for h in good.heroes]
    assert again.errors == [] and all(r.stale for r in again.results)
    _, run = stove.to_facts(again)
    assert run.stale and any("stale cache served" in w and "maintenance" in w for w in run.warnings)
    third = stove.fetch_catalog(http_for(tmp_path, make_handler()), World.GLOBAL)  # cache still holds good data
    assert [h.hero_code for h in third.heroes] == [h.hero_code for h in good.heroes]


def test_stove_skips_items_with_unknown_schema_but_warns(tmp_path: Path) -> None:
    heroes = [
        {"hero_code": "c9001", "hero_name": "Ok", "grade": 5, "job_code": "warrior", "attribute_code": "fire"},
        {"hero_code": "c9009", "hero_name": "New Class", "grade": 5, "job_code": "bard", "attribute_code": "fire"},
    ]
    catalog = stove.fetch_catalog(http_for(tmp_path, make_handler(stove_heroes=heroes)), World.GLOBAL)
    assert [h.hero_code for h in catalog.heroes] == ["c9001"]
    assert any("hero-list: 1 item(s) skipped" in w and "c9009" in w for w in catalog.warnings)


def test_stove_api_error_is_reported(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 500, "message": "maintenance", "value": None})

    with pytest.raises(stove.StoveError, match="maintenance"):
        stove.fetch_catalog(http_for(tmp_path, handler), World.GLOBAL)


# ------------------------------------------------------------------------------------------------ fribbels


def test_fribbels_facts_statuses_and_duplicates() -> None:
    heroes, artifacts, warnings = fribbels.parse(FRIBBELS_HERODATA, FRIBBELS_ARTIFACTDATA)
    data = fribbels.FribbelsData(heroes=heroes, artifacts=artifacts, results=[], warnings=warnings)
    data_with_result = fribbels.FribbelsData(
        heroes=heroes, artifacts=artifacts, results=[_fake_result()], warnings=warnings
    )
    facts, run = fribbels.to_facts(data_with_result)
    bbk = values(facts, "c2011")
    assert (bbk["base.att"], bbk["base.max_hp"], bbk["base.def"], bbk["base.speed"]) == (1138, 5871, 462, 111)
    assert bbk["base.cri"] == 0.23 and bbk["base.coop"] == 0.03
    assert bbk["imprint.stat"] == Stat.ATK_PERCENT.value
    ee_value = next(f for f in facts if f.entity_id == "c2011" and f.field == "ee.value")
    assert ee_value.status is DataStatus.ASSUMED and "NV-08" in ee_value.note
    assert values(facts, "c2011:s3")["rate"] == 1.2
    assert "c2011:s2" not in {f.entity_id for f in facts}  # passive with nothing numeric
    assert "m0063" not in {f.entity_id for f in facts}
    assert any("non-hero codes" in w and "m0063" in w for w in run.warnings)
    relic = values(facts, "efz09")
    assert "name" not in relic and relic["atk_min"] == 6  # duplicate code: disagreeing name dropped, stats kept
    assert any("efz09" in w and "conflicting fields dropped: name" in w for w in run.warnings)
    assert values(facts, "set_cri_dmg")["pieces"] == 4
    assert len(data.heroes) == 3


def test_fribbels_artifact_defense_and_skill_notes() -> None:
    facts, run = fribbels.to_facts(_fribbels_data())
    assert values(facts, "efz03")["def_min"] == 5 and values(facts, "efz01")["def_min"] == 0
    proc = next(f for f in facts if f.entity_id == "c9001:s2" and f.field == "rate")
    assert proc.value == 0.5 and proc.note == "S1 proc"  # not the plain S2 multiplier: the note must travel
    assert any("unmapped skill fields ignored: c9001:s2:mysteryField" in w for w in run.warnings)
    hints = fribbels.artifact_stat_hints(_fribbels_data())
    assert hints["efz04"] == {"atk": 0, "def": 5, "hp": 76}
    assert hints["efz09"] == {"atk": 6, "def": 0, "hp": 9}  # duplicate entries, but their stats agree
    old_relic = next(a for a in _fribbels_data().artifacts if a.name == "Old Relic II")
    clash = fribbels.FribbelsData(
        heroes=[],
        artifacts=[*_fribbels_data().artifacts, old_relic.model_copy(update={"code": "efz04"})],
        results=[],
        warnings=[],
    )
    assert "efz04" not in fribbels.artifact_stat_hints(clash)  # entries disagree -> no hint at all


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("set_cri_dmg", ("DestructionSet", 4)),
        ("set_speed", ("SpeedSet", 4)),
        ("set_max_hp", ("HealthSet", 2)),
        ("set_cri", ("CriticalSet", 2)),
        ("set_torrent", ("TorrentSet", 2)),
        ("set_immune", ("ImmunitySet", 2)),
        ("set_counter", ("CounterSet", 4)),
    ],
)
def test_fribbels_set_pieces_spot_checks(code: str, expected: tuple[str, int]) -> None:
    """Guard against typos in the hand-copied table (MECH-GEAR-05)."""
    assert fribbels.SET_PIECES[code] == expected
    assert all(pieces in (2, 4) for _, pieces in fribbels.SET_PIECES.values())


def test_fribbels_rejects_wrong_top_level_shape() -> None:
    with pytest.raises(fribbels.FribbelsError):
        fribbels.parse([], {})


# ------------------------------------------------------------------------------------------------ e7calc


def test_e7calc_parser_reads_only_literal_values() -> None:
    heroes, warnings = e7calc.parse_heroes_ts(E7CALC_HEROES_TS)
    assert warnings == []
    by_key = {h.key: h for h in heroes}
    bbk = by_key["blood_blade_karin"]
    assert (bbk.base_atk, bbk.base_hp, bbk.base_def) == (1138, 5871, 462)
    s1, s3, s2 = bbk.skills
    assert (s1.rate, s1.pow, s1.enhance) == (1, 1, (0.05, 0, 0.1, 0, 0.15))
    assert (s3.rate, s3.rate_soulburn, s3.pow) == (1.2, 1.45, 0.95)  # nested AftermathSkill rate 9.9 ignored
    assert (s2.slot, s2.rate, s2.enhance) == (2, None, (0.05, 0.1, 0.1, 0.1, 0.15))  # no trailing comma
    test = by_key["test_hero"]  # misindented, comment and string with braces
    assert test.base_atk == 1300  # the nested `stats: { baseAttack: 1 }` is not the hero's own field
    kanna = by_key["kanna"].skills[0]
    assert (kanna.rate, kanna.rate_soulburn) == (1.1, 1.5)  # parenthesised ternary
    assert (test.skills[0].rate, test.skills[0].pow) == (0.8, None)  # computed pow is never guessed


def test_e7calc_mapping_rules() -> None:
    heroes, warnings = e7calc.parse_heroes_ts(E7CALC_HEROES_TS)
    names = NameIndex()
    known: dict[str, tuple[str, str]] = {}
    for code, name, element, role in [
        ("c2011", "Blood Blade Karin", "dark", "assassin"),
        ("c1011", "Karin", "ice", "assassin"),
        ("c9001", "Test Hero", "fire", "warrior"),
        ("c9002", "Twin", "light", "manauser"),
        ("c9003", "Twin", "light", "manauser"),
        ("c9010", "Bomb Model Kanna", "fire", "ranger"),
    ]:
        names.add(name, code)
        known[code] = (element, role)
    data = e7calc.E7calcData(heroes=heroes, results=[_fake_result()], warnings=warnings)
    facts, run = e7calc.to_facts(data, names, known)
    mapped = {f.entity_id.split(":")[0] for f in facts}
    assert mapped == {"c2011", "c9001", "c9010"}  # c9010 through the hand-checked alias for "kanna"
    base = next(f for f in facts if f.entity_id == "c2011" and f.field == "base.att")
    assert base.status is DataStatus.ASSUMED  # e7calc 'base' stats are corroboration only
    joined = " | ".join(run.warnings)
    assert "1 pre-rework" in joined
    assert "twin (ambiguous name)" in joined and "unknown_person" in joined
    assert "karin->c1011" in joined  # element mismatch: wrong mapping rejected


# ------------------------------------------------------------------------------------------------ sets


@pytest.mark.parametrize(
    ("text", "kind", "static"),
    [
        ("Attack increases by 45%.", "static", [(Stat.ATK, 0.45, True)]),
        ("Critical Hit Chance increases by 12%.", "static", [(Stat.CRIT_CHANCE, 0.12, False)]),
        ("Increases Dual Attack chance by 8%.", "static", [(Stat.DUAL_ATTACK, 0.08, False)]),
        (
            "Decreases Health by 10% and when attacking increases damage dealt by 10%.",
            "mixed",
            [(Stat.HP, -0.10, True)],
        ),
        (
            "Speed increases by 12% and for every 1% of Health lost Speed increases by an additional 0.5%.",
            "mixed",
            [(Stat.SPEED, 0.12, True)],
        ),
        (
            "Increases Speed by 15%. Increases the chance to inflict debuffs by 15%.",
            "mixed",
            [(Stat.SPEED, 0.15, True)],
        ),
        ("Absorbs 20% of damage dealt as Health.", "combat", []),
    ],
)
def test_set_effect_parsing(text: str, kind: str, static: list[tuple[Stat, float, bool]]) -> None:
    parsed = parse_effect_text(text)
    assert parsed.kind == kind
    assert [(b.stat, b.of_base) for b in parsed.static] == [(stat, of_base) for stat, _, of_base in static]
    assert [b.value for b in parsed.static] == pytest.approx([value for _, value, _ in static])


def test_entity_type_values_are_stable() -> None:
    assert json.dumps([t.value for t in EntityType]) == '["hero", "skill", "artifact", "set"]'


def _fake_result() -> Any:
    from e7ac.sources.http import FetchResult

    return FetchResult("https://x.test", "{}", datetime(2026, 10, 3, tzinfo=UTC), from_cache=False, stale=False)
