"""The Opponents list as the watch lives it (SPEC D83): circle detection that jitters, rows that fade in or go missing,
a list read once, the faces nobody could name offered with suggestions, and the questions answered on the window."""

from __future__ import annotations

import queue
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import cv2
import numpy as np
import pytest

from e7ac.arena.identify import Slot
from e7ac.arena.questions import FaceQuestion
from e7ac.arena.sightings import Sighting, SightingStore, sightings_path
from e7ac.domain.codes import Element, HeroClass
from e7ac.vision.arena_badges import ArenaScreenError, Badge
from e7ac.vision.arena_guide import FaceMatcher, ListLayout, fit_rows
from tests.test_arena import _face_icon, _guide
from tests.test_sightings import _badges, _catalog, _context, _faces

XS, YS, R = (300.0, 390.0, 480.0, 570.0), (150.0, 300.0, 450.0), 40.0


def _list_image(rows: tuple[int, ...] = (0, 1, 2), seed: int = 1) -> Any:
    """A list screen: a face (a smooth random picture) at each place of the rows given, the dark panel elsewhere."""
    rng = np.random.default_rng(seed)
    image = np.full((740, 1000, 3), 20, np.uint8)
    for row in rows:
        for x in XS:
            blobs = rng.integers(0, 255, (5, 5, 3), dtype=np.uint8)
            y0, x0 = int(YS[row] - R), int(x - R)
            image[y0 : y0 + 2 * int(R), x0 : x0 + 2 * int(R)] = cv2.resize(
                blobs, (2 * int(R), 2 * int(R)), interpolation=cv2.INTER_CUBIC
            )
    return image


def _found(
    rows: tuple[int, ...] = (0, 1, 2), jitter: float = 0.0, seed: int = 0
) -> list[list[tuple[float, float, float]]]:
    rng = np.random.default_rng(seed)
    return [
        [(x + float(rng.uniform(-jitter, jitter)), YS[row] + float(rng.uniform(-jitter, jitter)), R) for x in XS]
        for row in rows
    ]


def test_the_layout_removes_jitter_and_adds_a_row_the_detection_missed() -> None:
    image = _list_image()
    rows, layout = fit_rows(_found(jitter=1.5), None, image)
    assert len(rows) == 3 and layout.ys == pytest.approx(YS, abs=1.5) and layout.height == 740
    again, same = fit_rows(_found(jitter=1.5, seed=9), layout, image)
    assert again == rows and same == layout  # the places do not move from frame to frame
    middle_missing, _ = fit_rows(_found((0, 2), jitter=1.0, seed=3), layout, image)
    assert len(middle_missing) == 3 and middle_missing[1] == rows[1]  # its four places hold pictures: added
    blank = _list_image((0, 2))
    assert len(fit_rows(_found((0, 2)), layout, blank)[0]) == 2  # nothing there: nothing invented
    taller = np.zeros((900, 1000, 3), np.uint8)
    assert fit_rows(_found((0, 1)), layout, taller)[1].ys == pytest.approx(YS[:2], abs=0.5)  # another size: new layout
    elsewhere = ListLayout(740, tuple(x + 80 for x in XS), YS, R)
    assert fit_rows(_found(), elsewhere, image)[1].xs == pytest.approx(XS)  # columns that do not fit: replaced


def _feed(monkeypatch: pytest.MonkeyPatch, frames: list[tuple[Any, list[Any]]]) -> list[int]:
    """Run `_step` over frames (an image and the circles the detection found in it): the rows of every list read."""
    from e7ac.cli import arena as arena_cli

    current: list[Any] = []

    def no_rows(image: Any) -> Any:
        if not current[0]:
            raise ArenaScreenError("nothing")
        return current[0]

    reads: list[int] = []
    monkeypatch.setattr(arena_cli, "list_rows", no_rows)
    monkeypatch.setattr(arena_cli, "_opponents", lambda image, match, c, rows, signature: reads.append(len(rows)))
    match = arena_cli._Match()
    context = cast(
        "arena_cli._Context",
        SimpleNamespace(answers=queue.Queue(), templates={}, options=arena_cli.WatchOptions(collect_only=True)),
    )
    for image, found in frames:
        current[:] = [found]
        arena_cli._step(image, match, context)
    return reads


def test_a_list_is_read_once_although_circle_detection_jitters(monkeypatch: pytest.MonkeyPatch) -> None:
    image = _list_image()
    frames = [(image, _found(jitter=1.5, seed=i)) for i in range(8)]
    assert _feed(monkeypatch, frames) == [3]  # exact positions never repeat; the list was read once


def test_a_refresh_is_read_again_and_a_list_coming_back_too(monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = _list_image(seed=1), _list_image(seed=2)
    away = np.zeros((740, 1000, 3), np.uint8)
    frames = [(first, _found(jitter=1, seed=i)) for i in range(4)]
    frames += [(second, _found(jitter=1, seed=10 + i)) for i in range(4)]
    frames += [(away, [])] + [(second, _found(jitter=1, seed=20 + i)) for i in range(4)]
    assert _feed(monkeypatch, frames) == [3, 3, 3]


def test_rows_still_fading_in_are_waited_for_then_read_as_they_are(monkeypatch: pytest.MonkeyPatch) -> None:
    from e7ac.cli.arena import CALM_FRAMES, PARTIAL_WAIT

    image = _list_image()
    two = [(image, _found((0, 1), seed=i)) for i in range(CALM_FRAMES + PARTIAL_WAIT)]
    assert _feed(monkeypatch, two) == []  # two of three rows: a moment more
    assert _feed(monkeypatch, [*two, (image, _found((0, 1)))]) == [2]  # the third never came: the two are read
    assert _feed(monkeypatch, two[:3] + [(image, _found()) for _ in range(4)]) == [3]  # it came: all three, at once


def test_a_row_recorded_before_is_not_recorded_twice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from e7ac.cli import arena as arena_cli

    rows3 = [_faces("c1001", "c1002", "c1003", "c1004"), _faces("c1005", None, "c1006", "c1007")]
    shapes = [
        _badges(("light", "knight")) * 1 + _badges(("light", "knight"), ("light", "knight"), ("dark", "mage"))
    ] * 2
    context, match, _ = _context(tmp_path, rows3, shapes, True, [])
    sig = np.random.default_rng(1).normal(size=(8, 144)).astype(np.float32)  # two rows of four faces
    monkeypatch.setattr(arena_cli, "read_list_badges", lambda image, found, templates: context.shapes)
    grid = [[(0.0, 0.0, 1.0)] * 4] * 2
    match = arena_cli._Match()
    arena_cli._opponents(cast(Any, None), match, context, grid, sig)
    arena_cli._opponents(cast(Any, None), match, context, grid, sig)  # "Read now" on the same list
    assert len(context.sightings.read()[0]) == 2  # still two teams, not four
    more = np.vstack([sig, np.random.default_rng(2).normal(size=(4, 144)).astype(np.float32)])
    context.list_faces.read_rows = lambda image, found: [*rows3, _faces("c1001", "c1002", "c1003", "c1004")]
    context.shapes = [*shapes, _badges(("light", "knight")) * 4]
    arena_cli._opponents(cast(Any, None), match, context, [*grid, [(0.0, 0.0, 1.0)] * 4], more)  # the third row came
    assert [s.row for s in context.sightings.read()[0]] == [1, 2, 3]  # only the new row was added


def test_a_face_is_suggested_by_how_much_it_looks_like_each_hero() -> None:
    icons = {f"c100{i}": _face_icon(60 + i) for i in range(1, 9)}
    matcher = FaceMatcher.from_png(icons)
    faces = matcher.read(_guide([icons["c1003"], icons["c1006"]]))
    assert matcher.suggest(faces[0], None, 3)[0][0] == "c1003"
    assert matcher.suggest(faces[1], None, 3)[0][0] == "c1006"
    assert {code for code, _ in matcher.suggest(faces[0], ["c1001", "c1002"], 6)} == {"c1001", "c1002"}  # only those
    assert matcher.suggest(faces[0], [], 3) == [] and matcher.suggest(replace(faces[0], crop=None)) == []


def test_a_face_named_afterwards_corrects_its_record_once(tmp_path: Path) -> None:
    store = SightingStore(sightings_path(tmp_path))
    when = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
    store.add([Sighting(when, "list", 2, ("c1001", None, None, "c1004"))])
    key = when.isoformat()
    assert store.name_face(key, 2, 1, "c1002")
    assert store.read()[0][0].codes == ("c1001", "c1002", None, "c1004")
    assert not store.name_face(key, 2, 1, "c1003")  # a name is never overwritten
    assert not store.name_face(key, 1, 2, "c1003") and not store.name_face(key, 2, 2, "not a code")  # no such line
    assert store.name_face(key, 2, 2, "c1003") and store.read()[0][0].codes == ("c1001", "c1002", "c1003", "c1004")


def test_unknown_faces_are_offered_with_suggestions(tmp_path: Path) -> None:
    from e7ac.arena.list_read import ListHero
    from e7ac.arena.references import ReferenceStore
    from e7ac.cli import arena as arena_cli

    icons = {f"c100{i}": _face_icon(70 + i) for i in range(1, 6)}
    matcher = FaceMatcher.from_png(icons)
    face = matcher.read(_guide([icons["c1002"], icons["c1004"]]))[0]
    offered: list[list[FaceQuestion]] = []
    when = datetime.now(UTC)
    context = SimpleNamespace(
        options=arena_cli.WatchOptions(unknown=offered.append),
        heroes=_catalog(),
        list_faces=matcher.for_list(),
        faces=matcher,
        face_store=ReferenceStore(tmp_path / "faces"),
        sightings=None,
    )
    read = [[ListHero(None, ("light", "knight"), "", face)] + [ListHero("c1001", None, "", face)] * 3]
    arena_cli._offer_unknown(cast(Any, context), read, [0], when.isoformat())
    (question,) = offered[0]
    assert question.kind == "list" and question.key == (when.isoformat(), 1, 0) and question.detail == "light knight"
    assert set(question.candidates) == {"c1001", "c1002", "c1003"}  # the heroes with its badge
    assert question.suggestions[0] == "c1002" and set(question.suggestions) <= set(question.candidates)
    arena_cli._offer_unknown(cast(Any, context), read, [0], "")  # nothing was recorded: nothing is offered
    assert len(offered) == 1


def test_teaching_a_face_teaches_both_readers_and_corrects_its_record(tmp_path: Path) -> None:
    from e7ac.arena.references import ReferenceStore
    from e7ac.cli import arena as arena_cli

    icons = {f"c100{i}": _face_icon(70 + i) for i in range(1, 6)}
    others = {code: icon for code, icon in icons.items() if code != "c1002"}  # this hero's icon is missing
    matcher = FaceMatcher.from_png(others)
    twin = matcher.for_list()
    picture = _guide([icons["c1002"], icons["c1004"]])
    face = matcher.read(picture)[0]
    assert face.code is None  # not known yet
    store = SightingStore(sightings_path(tmp_path))
    when = datetime.now(UTC)
    store.add([Sighting(when, "list", 1, (None, "c1001", "c1003", "c1004"))])
    taught: queue.Queue[tuple[FaceQuestion, str]] = queue.Queue()
    options = arena_cli.WatchOptions(taught=taught, recorded=lambda n: None)
    context = SimpleNamespace(
        options=options,
        heroes=_catalog(),
        list_faces=twin,
        faces=matcher,
        face_store=ReferenceStore(tmp_path / "faces"),
        sightings=store,
    )
    question = FaceQuestion(
        "list", "Opponent 1, hero 1", "light knight", face.crop, None, (), (), (when.isoformat(), 1, 0)
    )
    taught.put((question, "c1002"))
    arena_cli._learn_taught(cast(Any, context))
    assert store.read()[0][0].codes[0] == "c1002"  # the record is corrected
    assert "c1002" in context.face_store.codes()  # kept for the next run
    assert matcher.read(picture)[0].code == "c1002"  # known at once by the guide reader...
    assert twin.suggest(face, None, 1)[0][0] == "c1002"  # ... and by the list reader (the twin learned it)


def _slot(code: str | None, candidates: list[str], scores: dict[str, int] | None = None) -> Slot:
    badge = Badge("you", 0, 0, 30, 29, Element.LIGHT, 1.0, HeroClass.KNIGHT, 1.0)
    return Slot(badge, candidates, code, "review", scores or {})


def test_a_hero_of_the_vs_screen_is_asked_on_the_window_never_on_the_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    from e7ac.cli import arena as arena_cli

    def never(*args: Any, **kwargs: Any) -> str:
        raise AssertionError("the terminal was asked")

    import typer

    monkeypatch.setattr(typer, "prompt", never)
    asked: list[FaceQuestion] = []

    def choose(question: FaceQuestion) -> str | None:
        asked.append(question)
        return "c1003"

    slots = [_slot("c1001", ["c1001"]), _slot(None, ["c1001", "c1002", "c1003", "c1004"], {"c1004": 30, "c1003": 12})]
    crops = [np.zeros((20, 20, 3), np.uint8)] * 2
    arena_cli._ask(slots, cast(Any, _catalog()), ("you",), choose, crops)
    (question,) = asked
    assert question.kind == "slot" and question.title == "Your team, slot 2" and question.crop is crops[1]
    assert question.suggestions[:2] == ("c1004", "c1003") and question.candidates == (
        "c1002",
        "c1003",
        "c1004",
    )  # c1001 taken
    assert (slots[1].code, slots[1].method) == ("c1003", "confirmed")
    skipped = [_slot(None, ["c1002"])]
    arena_cli._ask(skipped, cast(Any, _catalog()), ("you",), lambda q: None, [crops[0]])
    assert (skipped[0].code, skipped[0].method) == (None, "review")  # skipped: left for later, never guessed


def test_collecting_never_reads_the_vs_screen_or_asks_anything(monkeypatch: pytest.MonkeyPatch) -> None:
    from e7ac.cli import arena as arena_cli

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the VS screen was read while only collecting")

    monkeypatch.setattr(arena_cli, "list_rows", lambda image: (_ for _ in ()).throw(ArenaScreenError("no list")))
    monkeypatch.setattr(arena_cli, "read_badges", boom)
    context = SimpleNamespace(answers=queue.Queue(), templates={}, options=arena_cli.WatchOptions(collect_only=True))
    arena_cli._step(np.zeros((740, 1000, 3), np.uint8), arena_cli._Match(), cast(Any, context))
