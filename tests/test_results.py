"""The results log (SPEC D93): predictions and outcomes of Arena fights, paired and compared; the Victory / Defeat title
read from the game picture (synthetic lines, and the user's own capture where it exists)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from e7ac.arena.results import Outcome, Prediction, ResultStore, calibrate, pair, results_path
from e7ac.cli import arena as arena_cli
from e7ac.cli import roster as roster_cli
from e7ac.cli.app import app
from e7ac.paths import AppPaths
from e7ac.vision.arena_result import read_result
from e7ac.vision.ocr import Box, TextLine
from tests.markers import FIXTURES_DIR
from tests.test_hero_screen import FakeReader

NOW = datetime(2026, 10, 18, 20, 0, tzinfo=UTC)


def prediction(minutes: float, win: float = 0.99, blind: tuple[str, ...] = ()) -> Prediction:
    return Prediction(
        NOW + timedelta(minutes=minutes),
        ("c1", "c2", "c3", "c4"),
        ("c5", "c6", "c7", "c8"),
        win,
        1 - win,
        "typical",
        blind,
    )


def test_the_log_round_trips_and_counts_the_lines_it_cannot_read(tmp_path: Path) -> None:
    store = ResultStore(tmp_path / "arena" / "results.jsonl")
    store.add(prediction(0, blind=("Lady (S1, S3)",)))
    store.add(Outcome(NOW + timedelta(minutes=4), "defeat"))
    with store.path.open("a", encoding="utf-8") as handle:
        handle.write("{broken\n" + '{"kind":"outcome","when":"2026-10-18T20:09:00+00:00","result":"draw"}\n')
    predictions, outcomes, skipped = store.read()
    assert [(p.win, p.blind, p.profile) for p in predictions] == [(0.99, ("Lady (S1, S3)",), "typical")]
    assert [(o.result, o.when) for o in outcomes] == [("defeat", NOW + timedelta(minutes=4))] and skipped == 2
    assert store.remove_last() and ResultStore(tmp_path / "none.jsonl").read() == ([], [], 0)


def test_an_outcome_belongs_to_the_last_prediction_before_it_and_a_prediction_is_used_once() -> None:
    predictions = [prediction(0, 0.9), prediction(30, 0.7), prediction(31, 0.2), prediction(120, 0.5)]
    outcomes = [Outcome(NOW + timedelta(minutes=34), "victory"), Outcome(NOW + timedelta(minutes=36), "defeat")]
    fights = pair(predictions, outcomes)
    assert [(f.prediction.win, f.outcome.result) for f in fights] == [(0.2, "victory"), (0.7, "defeat")]
    assert pair(predictions, [Outcome(NOW + timedelta(minutes=300), "victory")]) == []  # nothing within 20 minutes
    assert pair([], outcomes) == []


def test_the_calibration_says_how_far_the_chances_were_from_the_results() -> None:
    fights = pair(
        [prediction(0, 1.0), prediction(10, 0.5), prediction(20, 0.9)],
        [Outcome(NOW + timedelta(minutes=m), r) for m, r in ((5, "defeat"), (15, "victory"), (25, "victory"))],
    )
    cal = calibrate(fights)
    assert (cal.fights, cal.wins, cal.sure_but_lost) == (3, 2, 1)
    assert cal.expected == pytest.approx(2.4) and cal.brier == pytest.approx((1.0 + 0.25 + 0.01) / 3)
    assert calibrate([]).fights == 0


def title(text: str, score: float = 1.0) -> list[TextLine]:
    return [TextLine("S.Rank.18", 1.0, Box(0, 0, 100, 30), ()), TextLine(text, score, Box(300, 100, 700, 200), ())]


def test_the_title_of_the_result_screen_is_read_in_both_languages() -> None:
    picture = np.zeros((740, 1316, 3), np.uint8)
    assert read_result(picture, FakeReader(title("Defeat"))) == "defeat"
    assert read_result(picture, FakeReader(title("VICTORY"))) == "victory"
    assert read_result(picture, FakeReader(title("Derrota"))) == "defeat"
    assert read_result(picture, FakeReader(title("Vitória"))) == "victory"
    assert read_result(picture, FakeReader(title("Defeat", 0.4))) is None  # a shaky reading is not an outcome
    assert read_result(picture, FakeReader(title("Hero Info"))) is None


@pytest.mark.fixtures("screenshots/arena/defeat_1.webp")
def test_the_users_defeat_screen_is_read() -> None:
    from PIL import Image

    from e7ac.vision.ocr import RapidOcrReader

    picture = np.array(Image.open(FIXTURES_DIR / "screenshots/arena/defeat_1.webp").convert("RGB"))
    assert read_result(picture, RapidOcrReader()) == "defeat"


class Badge:
    def __init__(self) -> None:
        self.said: list[tuple[str, str]] = []

    def say(self, top: str, bottom: str, colour: str) -> None:
        self.said.append((top, bottom))


def test_the_watch_logs_the_outcome_once_the_screen_has_settled(
    monkeypatch: pytest.MonkeyPatch, isolated_home: AppPaths
) -> None:
    store = ResultStore(results_path(isolated_home.home))
    store.add(Prediction(datetime.now(UTC) - timedelta(minutes=3), ("c1",) * 4, ("c2",) * 4, 0.99, 0.01, "typical"))
    badge = Badge()
    context = arena_cli._Context.__new__(arena_cli._Context)  # only what `_outcome_step` uses
    for name, value in (("results", store), ("reader", lambda: FakeReader(title("Defeat"))), ("badge", badge)):
        object.__setattr__(context, name, value)
    match = arena_cli._Match()
    ticks = iter(x * 0.5 for x in range(100))
    monkeypatch.setattr(arena_cli, "arena_clock", lambda: next(ticks))
    frame = np.full((74, 132, 3), 30, np.uint8)
    for _ in range(6):
        arena_cli._outcome_step(frame, match, context)
    predictions, outcomes, _ = store.read()
    assert [o.result for o in outcomes] == ["defeat"] and len(predictions) == 1  # one outcome, however long it stays
    assert ("Recorded: Defeat", "you were given 99%") in badge.said
    monkeypatch.setattr(roster_cli, "reader_factory", lambda: FakeReader([]))


def test_arena_results_lists_the_fights_and_how_far_off_the_chances_were(isolated_home: AppPaths) -> None:
    runner = CliRunner()
    assert "No fight seen to the end yet" in runner.invoke(app, ["arena", "results"]).output
    store = ResultStore(results_path(isolated_home.home))
    store.add(Prediction(NOW, ("c1",) * 4, ("c2",) * 4, 0.994, 0.004, "typical", ("Lady (S1)",)))
    store.add(Outcome(NOW + timedelta(minutes=5), "defeat"))
    shown = runner.invoke(app, ["arena", "results"])
    assert shown.exit_code == 0, shown.output
    assert "defeat" in shown.output and "99%" in shown.output and "skills not read: Lady (S1)" in shown.output
    assert "1 fight(s): 0 won, the chances expected 1.0" in shown.output and "lost with 85% or more: 1" in shown.output
    assert "Removed the last line." in runner.invoke(app, ["arena", "results", "--undo"]).output
