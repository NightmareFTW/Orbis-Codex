"""Synthetic source payloads for catalog tests (shapes mirror docs/DATA_SOURCES.md; no real API responses).

Blood Blade Karin's base stats are the documented acceptance values (docs/ROADMAP.md M2); everything else is
made up (hero codes c9xxx, artifact codes efz..) so tests never depend on real game data.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

STOVE_HEROES: list[dict[str, Any]] = [
    {
        "hero_code": "c2011",
        "hero_name": "Blood Blade Karin",
        "grade": 4,
        "job_code": "assassin",
        "attribute_code": "dark",
    },
    {"hero_code": "c1011", "hero_name": "Karin", "grade": 4, "job_code": "assassin", "attribute_code": "ice"},
    {"hero_code": "c9001", "hero_name": "Test Hero", "grade": 5, "job_code": "warrior", "attribute_code": "fire"},
    {"hero_code": "c9002", "hero_name": "Twin", "grade": 3, "job_code": "mage", "attribute_code": "light"},
    {"hero_code": "c9003", "hero_name": "Twin", "grade": 3, "job_code": "mage", "attribute_code": "light"},
]
STOVE_SETS: list[dict[str, Any]] = [
    {
        "equip_code": "set_cri_dmg",
        "equip_name": "Destruction Set",
        "equip_effect": "Critical Hit Damage increases by 60%.",
    },
    {"equip_code": "set_speed", "equip_name": "Speed Set", "equip_effect": "Speed increases by 25%."},
    {
        "equip_code": "set_torrent",
        "equip_name": "Torrent Set",
        "equip_effect": "Decreases Health by 10% and when attacking increases damage dealt by 10%.",
    },
]
STOVE_ARTIFACTS: list[dict[str, Any]] = [
    {
        "artifact_code": "efz01",
        "artifact_name": "Test Dagger",
        "job_code": "assassin",
        "grade": 5,
        "ability_attack": 21,
        "ability_defense": 32,
        "enhance_ability_attack": 273,
        "enhance_ability_defense": 416,
        "info_text": "Increases damage dealt by @.",
        "level_list": [{"lv01": "8.0%", "lv_max": "16.0%"}, {}],
    },
    {
        "artifact_code": "efz02",
        "artifact_name": "Plain Charm",
        "job_code": "NN",
        "grade": 3,
        "ability_attack": 5,
        "ability_defense": 10,
        "enhance_ability_attack": 65,
        "enhance_ability_defense": 130,
        "level_list": [],
    },
]


def _stat_block(atk: float, hp: float, spd: float, def_: float, chc: float = 0.15) -> dict[str, float]:
    return {
        "cp": 1,
        "atk": atk,
        "hp": hp,
        "spd": spd,
        "def": def_,
        "chc": chc,
        "chd": 1.5,
        "dac": 0.05,
        "eff": 0,
        "efr": 0,
    }


FRIBBELS_HERODATA: dict[str, Any] = {
    "Blood Blade Karin": {
        "code": "c2011",
        "_id": "blood-blade-karin",
        "name": "Blood Blade Karin",
        "rarity": 4,
        "attribute": "dark",
        "role": "assassin",
        "zodiac": "scorpion",
        "self_devotion": {"type": "att_rate", "grades": {"C": 0.06, "SSS": 0.18}},
        "ex_equip": [{"stat": {"type": "cri", "value": 0.06}}],
        "skills": {
            "S1": {"hitTypes": ["crit", "normal"], "rate": 1, "pow": 1, "targets": 1, "options": []},
            "S2": {"hitTypes": [], "options": []},
            "S3": {"hitTypes": ["crit"], "rate": 1.2, "pow": 0.95, "targets": 3, "options": []},
        },
        "calculatedStatus": {
            "lv50FiveStarFullyAwakened": _stat_block(913, 4436, 111, 373, 0.23),
            "lv60SixStarFullyAwakened": {**_stat_block(1138, 5871, 111, 462, 0.23), "dac": 0.03},
        },
    },
    "Test Hero": {
        "code": "c9001",
        "_id": "test-hero",
        "name": "Test Hero",
        "rarity": 5,
        "attribute": "fire",
        "role": "warrior",
        "zodiac": "lion",
        "self_devotion": {"type": "att", "grades": {"B": 30}},
        "ex_equip": [],
        "skills": {"S1": {"hitTypes": ["normal"], "rate": 0.9, "pow": 1, "targets": 1, "options": []}},
        "calculatedStatus": {"lv60SixStarFullyAwakened": _stat_block(1000, 6000, 100, 600)},
    },
    "Mighty Scout": {
        "code": "m0063",
        "_id": "mighty-scout",
        "name": "Mighty Scout",
        "rarity": 3,
        "attribute": "wind",
        "role": "ranger",
        "zodiac": "ram",
        "skills": {},
        "calculatedStatus": {"lv60SixStarFullyAwakened": _stat_block(500, 3000, 90, 300)},
    },
}
FRIBBELS_ARTIFACTDATA: dict[str, Any] = {
    "Test Dagger": {
        "name": "Test Dagger",
        "rarity": 5,
        "role": "assassin",
        "stats": {"attack": 21, "health": 32},
        "code": "efz01",
    },
    "Old Relic": {"name": "Old Relic", "rarity": 3, "role": "", "stats": {"attack": 6, "health": 9}, "code": "efz09"},
    "Old Relic II": {
        "name": "Old Relic II",
        "rarity": 3,
        "role": "",
        "stats": {"attack": 6, "health": 9},
        "code": "efz09",
    },
}

E7CALC_HEROES_TS = """
import { Hero } from "src/app/models/hero";

export const Heroes: Record<string, Hero> = {
  blood_blade_karin: new Hero({
    element: HeroElement.dark,
    class: HeroClass.thief,
    baseAttack: 1138,
    baseHP: 5871,
    baseDefense: 462,
    skills: {
      s1: new Skill({
        id: 's1',
        rate: () => 1,
        pow: () => 1,
        enhance: [0.05, 0, 0.1, 0, 0.15],
        isSingle: () => true,
      }),
      s3: new Skill({
        id: 's3',
        soulburn: true,
        rate: (soulburn: boolean) => soulburn ? 1.45 : 1.2,
        pow: () => 0.95,
        afterMath: (hitType: HitType) => new AftermathSkill({ rate: () => 9.9 }),
        enhance: [0.05, 0.05, 0, 0.1, 0.15],
      }),
    }
  }),
    test_hero: new Hero({ // misindented entry with a comment containing { braces }
    element: HeroElement.fire,
    class: HeroClass.warrior,
    baseAttack: 1300,
    baseHP: 6000,
    baseDefense: 600,
    skills: {
      s1: new Skill({
        id: 's1',
        name: 'brace } in a string',
        rate: () => 0.8,
        pow: (soulburn: boolean, inputValues: DamageFormData) => inputValues.x ? 1.1 : 1,
      }),
    }
  }),
  test_hero_old: new Hero({
    element: HeroElement.fire,
    class: HeroClass.warrior,
    baseAttack: 1,
    baseHP: 1,
    baseDefense: 1,
    skills: {}
  }),
  twin: new Hero({
    element: HeroElement.light,
    class: HeroClass.mage,
    baseAttack: 700,
    baseHP: 4000,
    baseDefense: 500,
    skills: {}
  }),
  karin: new Hero({
    element: HeroElement.fire,
    class: HeroClass.thief,
    baseAttack: 1000,
    baseHP: 5000,
    baseDefense: 500,
    skills: {}
  }),
  unknown_person: new Hero({
    element: HeroElement.ice,
    class: HeroClass.knight,
    baseAttack: 800,
    baseHP: 7000,
    baseDefense: 700,
    skills: {}
  }),
};
"""


def _envelope(value: dict[str, Any]) -> dict[str, Any]:
    return {"code": 0, "message": "OK", "value": value}


def _paged(items: list[dict[str, Any]], page: int, size: int) -> dict[str, Any]:
    chunk = items[(page - 1) * size : page * size]
    return _envelope({"total_count": len(items), "current_page": page, "guide_list": chunk})


def make_handler(
    *,
    page_size: int = 2,
    calls: list[str] | None = None,
    fail: Callable[[str], int | None] | None = None,
    stove_heroes: list[dict[str, Any]] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    """httpx MockTransport handler serving the synthetic payloads (small pages to exercise pagination)."""
    heroes = STOVE_HEROES if stove_heroes is None else stove_heroes

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if calls is not None:
            calls.append(url)
        status = fail(url) if fail is not None else None
        if status is not None:
            return httpx.Response(status)
        parts = urlsplit(url)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        page = int(query.get("current_page", "1"))
        if parts.path.endswith("/hero-list"):
            return httpx.Response(200, json=_paged(heroes, page, page_size))
        if parts.path.endswith("/equip-list"):
            return httpx.Response(200, json=_paged(STOVE_SETS, page, page_size))
        if parts.path.endswith("/artifact-list"):
            return httpx.Response(200, json=_paged(STOVE_ARTIFACTS, page, page_size))
        if parts.path.endswith("/herodata.json"):
            return httpx.Response(200, text=json.dumps(FRIBBELS_HERODATA), headers={"ETag": '"hero-v1"'})
        if parts.path.endswith("/artifactdata.json"):
            return httpx.Response(200, text=json.dumps(FRIBBELS_ARTIFACTDATA))
        if parts.path.endswith("/heroes.ts"):
            return httpx.Response(200, text=E7CALC_HEROES_TS)
        return httpx.Response(404)

    return handler
