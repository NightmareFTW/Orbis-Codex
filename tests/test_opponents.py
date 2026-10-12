"""Opponent model (M10): Stove usage histograms, assumed builds, `e7 opponent show` (synthetic data only)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from e7ac.cli import catalog as catalog_cli
from e7ac.cli.app import app
from e7ac.domain.codes import Stat
from e7ac.domain.world import World
from e7ac.opponents.assumed import CONFIDENCE, OPEN_END_CONFIDENCE, Profile, assumed_build
from e7ac.opponents.histogram import StatHistogram, parse_counts
from e7ac.paths import AppPaths
from e7ac.sources.http import CachedHttp, HttpConfig
from e7ac.sources.stove_usage import last_weekly_update, parse_detail
from tests.markers import FIXTURES_DIR

# The worked example of docs/DATA_SOURCES.md §1 (Blood Blade Karin, Global, n = 68,898).
BBK_ATK = "0,13,189,477,3675,16443,31217,14989,1855,40"
BBK_CD = "166,212,207,540,1689,3024,6085,7051,9040,40884"
NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


def payload(**changes: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "seq": 1,
        "hero_code": "c2011",
        "hero_name": "Blood Blade Karin",
        "reg_date": "2026-10-08 10:38:26.287",
        "attack_stats": BBK_ATK,
        "defense_stats": "20774,43737,3815,502,70,0,0,0,0,0",
        "vitality_statistics": "5803,33275,23786,5157,580,241,47,0,0,9",
        "speed_statistics": "0,7455,18572,20471,7975,9259,3475,1556,128,7",
        "critical_statistics": "0,7,75,138,604,1408,3295,8248,10814,44309",
        "critical_hit_statistics": BBK_CD,
        "effective_statistics": "59663,6926,1572,359,307,42,26,3,0,0",
        "effect_resistance_statistics": "49666,6869,2493,1831,2627,2654,1754,903,94,7",
        "hero_eqips": [{"rank_sets": "cri_dmg,torrent", "eqip_rank_share": 27.5}],
        "hero_dedicated_eqip": [
            {"rank_skill": "ek_c201101_01", "enhancemen_skill": "2", "skill_rank_share": 84.92},
            {"rank_skill": "", "enhancemen_skill": "", "skill_rank_share": 0.0},
        ],
        "hero_arti": [{"rank_arti": "efa15", "arti_rank_share": 35.6, "rank_arti_name": "Shepherd of the Hollow"}],
    }
    value.update(changes)
    return value


def test_percentiles_match_the_documented_worked_example() -> None:
    atk = parse_counts(Stat.ATK, BBK_ATK)
    found = [atk.percentile(p) for p in (0.5, 0.75, 0.9)]
    assert [round(f.value) for f in found if f] == [3775, 3996, 4267]
    assert not any(f.open_end for f in found if f)
    median_cd = parse_counts(Stat.CRIT_DAMAGE, BBK_CD).percentile(0.5)
    assert median_cd is not None and median_cd.open_end and median_cd.value == pytest.approx(3.33, abs=0.005)


def test_bad_or_empty_histograms() -> None:
    with pytest.raises(ValueError, match="10 non-negative counts"):
        parse_counts(Stat.ATK, "1,2,3")
    with pytest.raises(ValueError, match="not integers"):
        parse_counts(Stat.ATK, "a,b")
    assert StatHistogram(Stat.ATK, (0,) * 10).percentile(0.5) is None


@pytest.mark.parametrize(
    ("now", "update"),
    [
        (datetime(2026, 10, 7, 12, 0, tzinfo=UTC), datetime(2026, 10, 1, 3, 0, tzinfo=UTC)),  # Wednesday
        (datetime(2026, 10, 8, 2, 59, tzinfo=UTC), datetime(2026, 10, 1, 3, 0, tzinfo=UTC)),  # just before
        (datetime(2026, 10, 8, 3, 0, tzinfo=UTC), datetime(2026, 10, 8, 3, 0, tzinfo=UTC)),  # the refresh itself
    ],
)
def test_the_weekly_refresh_before_now(now: datetime, update: datetime) -> None:
    assert last_weekly_update(now) == update


def test_a_stove_payload_becomes_usage() -> None:
    usage = parse_detail(payload(), World.GLOBAL, NOW)
    assert usage is not None and usage.sample_size == 68898 and usage.warnings == []
    assert [(c.codes, c.share) for c in usage.sets] == [(("set_cri_dmg", "set_torrent"), 0.275)]
    assert [(c.codes, c.skill) for c in usage.ee] == [(("ek_c201101_01",), 2)]  # the empty EE slot is dropped
    assert usage.artifacts[0].name == "Shepherd of the Hollow"
    assert parse_detail(payload(seq=0), World.GLOBAL, NOW) is None  # "Gathering Hero Information"
    uneven = parse_detail(payload(attack_stats="1,0,0,0,0,0,0,0,0,0"), World.GLOBAL, NOW)
    assert uneven is not None and uneven.sample_size is None and uneven.warnings


def test_profiles_and_their_confidence() -> None:
    usage = parse_detail(payload(), World.GLOBAL, NOW)
    assert usage is not None
    strong = assumed_build(usage, Profile.STRONG, NOW)
    assert round(strong.stats[Stat.ATK].value) == 3996 and strong.stats[Stat.ATK].confidence == CONFIDENCE
    assert strong.stats[Stat.CRIT_DAMAGE].open_end and strong.stats[Stat.CRIT_DAMAGE].confidence == OPEN_END_CONFIDENCE
    late = assumed_build(usage, Profile.TYPICAL, datetime(2026, 10, 20, tzinfo=UTC))
    assert any("missed a weekly refresh" in w for w in late.warnings)
    tiny = parse_detail(payload(**{name: "1,0,0,0,0,0,0,0,0,0" for name in _STAT_FIELDS}), World.GLOBAL, NOW)
    assert tiny is not None
    rough = assumed_build(tiny, Profile.TYPICAL, NOW)
    assert any("only 1 builds" in w for w in rough.warnings)
    assert all(s.confidence == OPEN_END_CONFIDENCE for s in rough.stats.values())


_STAT_FIELDS = [
    "attack_stats",
    "defense_stats",
    "vitality_statistics",
    "speed_statistics",
    "critical_statistics",
    "critical_hit_statistics",
    "effective_statistics",
    "effect_resistance_statistics",
]


def _stove(handler: Callable[[httpx.Request], httpx.Response]) -> Callable[[AppPaths, bool], CachedHttp]:
    def factory(paths: AppPaths, offline: bool) -> CachedHttp:
        config = HttpConfig(min_interval=0.0)
        return CachedHttp(paths.cache_dir, config, offline, httpx.MockTransport(handler), sleep=lambda s: None)

    return factory


def test_opponent_show(monkeypatch: pytest.MonkeyPatch, isolated_home: AppPaths) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text=json.dumps({"code": 0, "message": "OK", "value": payload()}))

    monkeypatch.setattr(catalog_cli, "http_factory", _stove(handler))
    runner = CliRunner()
    result = runner.invoke(app, ["opponent", "show", "c2011", "--profile", "strong"])
    assert result.exit_code == 0, result.output
    assert "Blood Blade Karin (c2011) - Stove world_global, 68,898 builds, updated 2026-10-08" in result.stdout
    assert "att 3996" in result.stdout and "cri_dmg 341.6%*" in result.stdout
    assert "Shepherd of the Hollow 35.6%" in result.stdout and "(skill 2) 84.9%" in result.stdout
    assert "hero_code=c2011" in seen[0] and "strategy_type=0" in seen[0]
    again = runner.invoke(app, ["opponent", "show", "c2011"])
    assert again.exit_code == 0 and len(seen) == 1  # this week's copy comes from the cache


def test_opponent_show_without_data(monkeypatch: pytest.MonkeyPatch, isolated_home: AppPaths) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=json.dumps({"code": 0, "message": "OK", "value": payload(seq=0)}))

    monkeypatch.setattr(catalog_cli, "http_factory", _stove(handler))
    result = CliRunner().invoke(app, ["opponent", "show", "c2011"])
    assert result.exit_code == 2 and "no usage data for c2011" in result.stderr


@pytest.mark.fixtures("stove/detail/c2011.json")
def test_a_real_stove_response_parses() -> None:
    value = json.loads(Path(FIXTURES_DIR / "stove/detail/c2011.json").read_text())["value"]
    usage = parse_detail(value, World.GLOBAL, NOW)
    assert usage is not None and usage.sample_size and len(usage.histograms) == 8 and usage.sets
