"""Choosing among the three opponents of the Opponents list (SPEC D85)."""

from __future__ import annotations

import queue
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import numpy as np
import pytest

from e7ac.arena.compare import OpponentOdds, Unavailable, band, compare, mark_best, verdict
from e7ac.arena.questions import FaceQuestion
from e7ac.predict.battle import BattleOdds

TEAM = ("c1001", "c1002", "c1003", "c1004")


def _row(number: int, win: float | None) -> OpponentOdds:
    return OpponentOdds(number, TEAM, win, None if win is None else 0.02, "" if win is not None else "?")


def test_the_best_opponent_is_the_one_with_the_highest_chance() -> None:
    rows = mark_best([_row(1, 0.55), _row(2, 0.72), _row(3, None)])
    assert [r.best for r in rows] == [False, True, False] and not any(r.tie for r in rows)
    assert verdict(rows) == ("Best: opponent 2 (72%)", "1: 55%  2: 72%  3: ?")


def test_a_close_call_is_said_and_a_tie_goes_to_the_first() -> None:
    rows = mark_best([_row(1, 0.70), _row(2, 0.71), _row(3, 0.2)])
    assert [r.best for r in rows] == [False, True, False] and rows[1].tie
    assert verdict(rows)[0] == "Best: opponent 2 (71%) - close"
    same = mark_best([_row(1, 0.6), _row(2, 0.6)])
    assert [r.best for r in same] == [True, False]


def test_nothing_is_marked_when_nothing_could_be_compared() -> None:
    rows = mark_best([_row(1, None), _row(2, None)])
    assert not any(r.best for r in rows) and verdict(rows) == ("Opponents not compared", "1: ?  2: ?")


def test_chances_are_banded_for_their_colour() -> None:
    assert [band(w) for w in (0.9, 0.6, 0.5, 0.4, 0.1, None)] == ["good", "good", "even", "bad", "bad", "unknown"]


def test_unknown_heroes_are_never_guessed_and_the_same_teams_are_simulated_once() -> None:
    calls: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

    def predict(team: tuple[str, ...], theirs: tuple[str, ...]) -> tuple[float, float, tuple[str, ...]]:
        calls.append((team, theirs))
        return (0.8 if theirs[0] == "c2001" else 0.3), 0.02, ()

    known: dict[Any, tuple[float, float, tuple[str, ...]]] = {}
    defences = [
        ("c2001", "c2002", "c2003", "c2004"),
        ("c2005", None, "c2007", "c2008"),
        ("c2005", "c2006", "c2007", "c2008"),
    ]
    rows = compare(TEAM, defences, predict, known)
    assert [r.win for r in rows] == [0.8, None, 0.3] and [r.best for r in rows] == [True, False, False]
    assert rows[1].note == "a hero is not recognised" and len(calls) == 2
    compare(TEAM, defences, predict, known)  # looked at again: the same numbers, nothing simulated
    assert len(calls) == 2


def test_without_your_team_nothing_is_compared_and_a_failure_says_why() -> None:
    def never(team: tuple[str, ...], theirs: tuple[str, ...]) -> tuple[float, float, tuple[str, ...]]:
        raise AssertionError("no simulation without a team")

    rows = compare([], [("c2001", "c2002", "c2003", "c2004")], never, {})
    assert rows[0].win is None and "VS screen" in rows[0].note

    def broken(team: tuple[str, ...], theirs: tuple[str, ...]) -> tuple[float, float, tuple[str, ...]]:
        raise Unavailable("Foe: Stove has no usage data")

    known: dict[Any, tuple[float, float, tuple[str, ...]]] = {}
    rows = compare(TEAM, [("c2001", "c2002", "c2003", "c2004")], broken, known)
    assert rows[0].note == "Foe: Stove has no usage data" and not known  # a failure is not remembered


# --- the watch --------------------------------------------------------------------------------------------------


def _watch_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, chances: dict[str, float]
) -> tuple[Any, Any, list[Any], list[Any], list[tuple[str, str]]]:
    from e7ac.arena.sightings import SightingStore, sightings_path
    from e7ac.cli import arena as arena_cli
    from e7ac.cli import predict as predict_cli
    from tests.test_sightings import _badges, _catalog, _faces

    rows = [
        _faces("c1001", "c1002", "c1003", "c1004"),
        _faces("c1005", None, "c1006", "c1007"),
        _faces("c1008", "c1009", "c1005", "c1006"),
    ]
    fire = ("fire", "ranger")
    shapes = [
        _badges(("light", "knight"), ("light", "knight"), ("light", "knight"), ("dark", "mage")),
        _badges(fire, fire, fire, fire),
        _badges(fire, fire, fire, fire),
    ]
    said: list[tuple[str, str]] = []
    shown: list[list[OpponentOdds]] = []
    simulated: list[tuple[str, str, int]] = []
    taught: queue.Queue[tuple[FaceQuestion, str]] = queue.Queue()
    options = arena_cli.WatchOptions(opponents=shown.append, compare_runs=1234, teams=lambda a, b: None, taught=taught)
    context = SimpleNamespace(
        list_faces=SimpleNamespace(read_rows=lambda image, found: rows, among=lambda face, group: (None, 0.0, 0.0)),
        templates={},
        heroes=_catalog(),
        sightings=SightingStore(sightings_path(tmp_path)),
        options=options,
        badge=SimpleNamespace(say=lambda top, bottom, colour: said.append((top, bottom))),
        last=[("", "", "")],
        offline=True,
        shapes=shapes,
        compared={},
    )

    def odds(team: str, vs: str, world: object, offline: bool, runs: int = 0, quiet: bool = False) -> BattleOdds:
        assert quiet and runs == 1234  # silent, and with the number of battles of the settings
        simulated.append((team, vs, runs))
        return BattleOdds(chances[vs.split(", ")[0]], 1 - chances[vs.split(", ")[0]], 0.0, 30, runs)

    monkeypatch.setattr(predict_cli, "win_odds", odds)
    monkeypatch.setattr(arena_cli, "find_rows", lambda image: [[(0.0, 0.0, 1.0)] * 4] * 3)
    monkeypatch.setattr(arena_cli, "read_list_badges", lambda image, found, templates: context.shapes)
    return context, arena_cli._Match(team=["c1001", "c1002", "c1003", "c1004"]), shown, simulated, said


def test_the_watch_compares_the_three_opponents_and_names_the_best(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from e7ac.cli import arena as arena_cli

    context, match, shown, simulated, said = _watch_context(
        tmp_path, monkeypatch, {"c1001": 0.55, "c1005": 0.4, "c1008": 0.82}
    )
    arena_cli._opponents(cast(Any, None), match, context)
    # the opponent with an unrecognised face is not simulated; the other two, quietly, with the settings' battles
    assert [(s[1].split(", ")[0], s[2]) for s in simulated] == [("c1001", 1234), ("c1008", 1234)]
    (rows,) = shown
    assert [r.win for r in rows] == [0.55, None, 0.82] and [r.best for r in rows] == [False, False, True]
    assert said[-1] == ("Best: opponent 3 (82%)", "1: 55%  2: ?  3: 82%")
    assert context.last[0][:2] == said[-1]
    # looking at the same list again changes nothing and simulates nothing
    arena_cli._opponents(cast(Any, None), match, context)
    assert len(simulated) == 2 and said[-1] == ("Best: opponent 3 (82%)", "1: 55%  2: ?  3: 82%")


def test_naming_the_unknown_face_compares_the_list_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:

    from e7ac.arena.references import ReferenceStore
    from e7ac.cli import arena as arena_cli

    context, match, shown, _, said = _watch_context(tmp_path, monkeypatch, {"c1001": 0.55, "c1008": 0.3, "c1005": 0.9})
    context.face_store = ReferenceStore(tmp_path / "faces")
    context.faces = SimpleNamespace(learn=lambda code, crop: None)
    context.list_faces.learn = lambda code, crop: None
    arena_cli._opponents(cast(Any, None), match, context)
    assert said[-1][0] == "Best: opponent 1 (55%)"
    when = match.list_when
    assert when  # the reading was recorded: that is what a taught face comes back with
    crop = np.zeros((8, 8, 3), np.uint8)
    question = FaceQuestion("list", "Opponent 2, hero 2", "", crop, None, (), (), (when, 2, 1))
    context.options.taught.put((question, "c1008"))
    arena_cli._learn_taught(context, match)
    assert match.list_codes[1] == ["c1005", "c1008", "c1006", "c1007"]
    assert said[-1][0] == "Best: opponent 2 (90%)"  # the second opponent can be compared now, and it is the best
    assert len(shown) == 2 and shown[1][1].win == 0.9


def test_a_face_named_for_a_list_that_has_gone_changes_no_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from e7ac.cli import arena as arena_cli

    context, match, shown, _, _said = _watch_context(tmp_path, monkeypatch, {"c1001": 0.5, "c1008": 0.5, "c1005": 0.5})
    arena_cli._opponents(cast(Any, None), match, context)
    old = list(match.list_codes[1])
    assert not arena_cli._rename(match, FaceQuestion("list", "", "", None, None, (), (), ("1999-01-01", 2, 1)), "c1002")
    assert match.list_codes[1] == old and len(shown) == 1
    assert not arena_cli._rename(None, FaceQuestion("list", "", "", None, None, (), (), None), "c1002")
