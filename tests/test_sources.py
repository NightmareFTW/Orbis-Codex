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
from tests.catalog_data import E7CALC_HEROES_TS, FRIBBELS_ARTIFACTDATA, FRIBBELS_HERODATA, make_handler


def http_for(tmp_path: Path, handler: Any) -> CachedHttp:
    return CachedHttp(
        cache_dir=tmp_path / "cache",
        config=HttpConfig(min_interval=0.0),
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
    )


def values(facts: list[Fact], entity_id: str) -> dict[str, Any]:
    return {f.field: f.value for f in facts if f.entity_id == entity_id}


# ------------------------------------------------------------------------------------------------ stove


def test_stove_paginates_and_maps_codes(tmp_path: Path) -> None:
    calls: list[str] = []
    catalog = stove.fetch_catalog(http_for(tmp_path, make_handler(page_size=2, calls=calls)), World.EUROPE)
    assert [h.hero_code for h in catalog.heroes] == ["c2011", "c1011", "c9001", "c9002", "c9003"]
    hero_pages = [c for c in calls if "hero-list" in c]
    assert len(hero_pages) == 3 and all("world_code=world_eu" in c for c in hero_pages)
    facts, run = stove.to_facts(catalog)
    assert all(f.status is DataStatus.VERIFIED and f.source is SourceId.STOVE for f in facts)
    bbk = values(facts, "c2011")
    assert bbk == {"name": "Blood Blade Karin", "element": "dark", "role": "assassin", "rarity": 4}
    art = values(facts, "efz01")
    assert (art["atk_min"], art["hp_min"], art["atk_max"], art["hp_max"]) == (21, 32, 273, 416)
    assert art["effect_levels"] == [{"min": "8.0%", "max": "16.0%"}, {"min": None, "max": None}]
    plain = values(facts, "efz02")
    assert plain["role_lock"] is None and "effect_text" not in plain
    assert any("efz02: no effect text" in w for w in run.warnings)


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
    s1, s3 = bbk.skills
    assert (s1.rate, s1.pow, s1.enhance) == (1, 1, (0.05, 0, 0.1, 0, 0.15))
    assert (s3.rate, s3.rate_soulburn, s3.pow) == (1.2, 1.45, 0.95)  # nested AftermathSkill rate 9.9 ignored
    test = by_key["test_hero"]  # misindented, comment and string with braces
    assert test.base_atk == 1300
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
    ]:
        names.add(name, code)
        known[code] = (element, role)
    data = e7calc.E7calcData(heroes=heroes, results=[_fake_result()], warnings=warnings)
    facts, run = e7calc.to_facts(data, names, known)
    mapped = {f.entity_id.split(":")[0] for f in facts}
    assert mapped == {"c2011", "c9001"}
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
