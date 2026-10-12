"""Turn order at battle start (MECH-CR-01/02) and `e7 predict order` (synthetic data)."""

from __future__ import annotations

import json
import random

import httpx
import pytest
from typer.testing import CliRunner

from e7ac.cli import catalog as catalog_cli
from e7ac.cli.app import app
from e7ac.domain.codes import Stat
from e7ac.opponents.histogram import LABEL_BOUNDS, StatHistogram
from e7ac.paths import AppPaths, default_paths
from e7ac.predict.turn_order import START_CR_MAX, Unit, first_turns
from e7ac.roster.store import add_owned_hero
from e7ac.storage.db import open_database, session_scope
from tests.test_opponents import _stove, payload
from tests.test_roster import bbk


def test_a_speed_gap_beyond_the_start_cr_spread_always_wins() -> None:
    gap = 200 / (1 - START_CR_MAX) + 1  # faster than any start-CR luck can make up
    result = first_turns([Unit("fast", "you", gap), Unit("slow", "them", 200)], runs=2000)
    assert [o.unit.label for o in result.units] == ["fast", "slow"] and result.your_side_first == 1.0


def test_equal_speeds_are_a_coin_toss_and_the_result_is_reproducible() -> None:
    units = [Unit("a", "you", 200), Unit("b", "them", 200)]
    once, again = first_turns(units, runs=4000), first_turns(units, runs=4000)
    assert once == again and 0.45 < once.your_side_first < 0.55


def test_an_unknown_speed_is_drawn_from_its_histogram() -> None:
    histogram = StatHistogram(Stat.SPEED, (0, 0, 0, 0, 0, 10, 0, 0, 0, 0))  # bin 5: 190-206
    draw = histogram.sampler()
    rng = random.Random(1)
    low, high = LABEL_BOUNDS[Stat.SPEED]
    width = (high - low) / 10
    assert all(low + 5 * width <= draw(rng) < low + 6 * width for _ in range(200))
    result = first_turns([Unit("you", "you", 180), Unit("them", "them", draw)], runs=2000)
    assert result.your_side_first == 0.0  # 180 never beats 190+: more than the 5% start CR can make up


def test_bad_inputs() -> None:
    with pytest.raises(ValueError, match="no units"):
        first_turns([])
    with pytest.raises(ValueError, match="speed must be positive"):
        first_turns([Unit("x", "you", 0)])
    with pytest.raises(ValueError, match="empty histogram"):
        StatHistogram(Stat.SPEED, (0,) * 10).sampler()


def test_predict_order_uses_the_roster_and_stove(monkeypatch: pytest.MonkeyPatch, isolated_home: AppPaths) -> None:
    paths = default_paths()
    paths.ensure()
    with session_scope(open_database(paths.database)) as session:
        add_owned_hero(session, bbk())  # speed 133

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=json.dumps({"code": 0, "message": "OK", "value": payload()}))

    monkeypatch.setattr(catalog_cli, "http_factory", _stove(handler))
    runner = CliRunner()
    result = runner.invoke(app, ["predict", "order", "--team", "c2011", "--vs", "c2011, c1159=140"])
    assert result.exit_code == 0, result.output
    assert "you  c2011" in result.stdout and "~" in result.stdout and "(P10-P90)" in result.stdout
    assert "c1159" in result.stdout and "Your side takes the first turn:" in result.stdout
    missing = runner.invoke(app, ["predict", "order", "--team", "c1159", "--vs", "c2011=150"])
    assert missing.exit_code == 2 and "not in your roster" in missing.stderr
