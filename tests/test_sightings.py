"""Opponent sightings (SPEC D81): the file, what it says about the Arena, and how the watch records the Opponents
list (every refresh, with or without a known team)."""

from __future__ import annotations

import queue
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import cv2
import numpy as np
import pytest
from typer.testing import CliRunner

from e7ac.arena.sightings import Sighting, SightingStore, popularity, sightings_path
from e7ac.vision.arena_badges import ArenaScreenError
from e7ac.vision.arena_guide import Face
from e7ac.vision.arena_list import ListBadge

T0 = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def _team(*codes: str | None, row: int = 1, when: datetime = T0) -> Sighting:
    return Sighting(when, "list", row, tuple(codes))


def test_sightings_round_trip_and_bad_lines_are_counted_never_dropped_silently(tmp_path: Path) -> None:
    store = SightingStore(sightings_path(tmp_path))
    first, second = _team("c1001", "c1002", None, "c1004"), _team("c1001", "c1003", "c1005", "c1006", row=2)
    store.add([first, second])
    with store.path.open("a", encoding="utf-8") as handle:
        handle.write('{"when": "2026-10-18T12:00:00+00:00", "source": "list", "row": 1, "codes": ["c1001"]}\n')  # short
        handle.write("not json\n")
        handle.write('{"when": "2026-10-18T12:00:00", "source": "list", "row": 1, "codes": [null,null,null,null]}\n')
    found, skipped = store.read()
    assert found == [first, second] and skipped == 3
    assert SightingStore(tmp_path / "none.jsonl").read() == ([], 0)


def test_popularity_counts_teams_pairs_and_unknown_faces() -> None:
    later = T0 + timedelta(hours=1)
    seen = [
        _team("c1001", "c1002", "c1003", None),
        _team("c1001", "c1002", "c1004", "c1005", row=2),
        _team("c1001", "c1006", "c1007", "c1008", row=3),
        _team("c1002", "c1009", "c1010", "c1011", when=later),
    ]
    result = popularity(seen)
    assert (result.teams, result.refreshes, result.faces, result.unknown_faces) == (4, 2, 16, 1)
    assert [(h.code, h.teams) for h in result.heroes[:2]] == [("c1001", 3), ("c1002", 3)]
    assert result.heroes[0].share == pytest.approx(0.75)
    assert result.pairs[0] == (("c1001", "c1002"), 2)
    recent = popularity(seen, since=T0 + timedelta(minutes=30))
    assert recent.teams == 1 and recent.refreshes == 1


def _faces(*codes: str | None) -> list[Face]:
    return [Face(0.0, 0.0, 10.0, c, 0.9, 0.5, c or "c1001") for c in codes]


def _badges(*shapes: tuple[str, str] | None) -> list[ListBadge]:
    from e7ac.domain.codes import Element, HeroClass

    return [
        ListBadge(Element(s[0]), 1.0, HeroClass(s[1]), 1.0) if s else ListBadge(None, 0.0, None, 0.0) for s in shapes
    ]


def _catalog() -> dict[str, Any]:
    """Heroes c1001..c1009 with shapes: c1001-c1003 light knight, c1004 dark mage, c1005.. fire ranger."""
    shapes = {"c1001": ("light", "knight"), "c1002": ("light", "knight"), "c1003": ("light", "knight")}
    shapes["c1004"] = ("dark", "mage")
    for i in range(5, 10):
        shapes[f"c100{i}"] = ("fire", "ranger")
    return {
        code: SimpleNamespace(name=f"Hero {code[-1]}", value=lambda key, s=shape: s[0] if key == "element" else s[1])
        for code, shape in shapes.items()
    }


def _context(
    tmp_path: Path, rows: list[list[Face]], shapes: list[list[ListBadge]], collect_only: bool, team: list[str]
) -> tuple[Any, Any, list[Any]]:
    from e7ac.cli.arena import WatchOptions

    said: list[Any] = []
    context = SimpleNamespace(
        list_faces=SimpleNamespace(read_rows=lambda image, found: rows, among=lambda face, group: (None, 0.0, 0.0)),
        templates={},
        heroes=_catalog(),
        sightings=SightingStore(sightings_path(tmp_path)),
        options=WatchOptions(collect_only=collect_only),
        badge=SimpleNamespace(say=lambda top, bottom, colour: said.append((top, bottom))),
        last=[("", "", "")],
        offline=True,
        shapes=shapes,
    )
    return context, SimpleNamespace(team=team), said


@pytest.mark.parametrize(("collect_only", "team"), [(True, ["c1001", "c1002", "c1003", "c1004"]), (False, [])])
def test_the_list_is_recorded_with_no_prediction_and_with_no_known_team(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, collect_only: bool, team: list[str]
) -> None:
    from e7ac.cli import arena as arena_cli

    rows = [_faces("c1001", "c1002", "c1003", "c1004"), _faces("c1005", None, "c1006", "c1007")]
    shapes = [
        _badges(("light", "knight"), ("light", "knight"), ("light", "knight"), ("dark", "mage")),
        _badges(("fire", "ranger"), ("fire", "ranger"), ("fire", "ranger"), ("fire", "ranger")),
    ]
    context, match, said = _context(tmp_path, rows, shapes, collect_only, team)
    monkeypatch.setattr(arena_cli, "list_rows", lambda image: [[(0.0, 0.0, 1.0)] * 4] * 2)
    monkeypatch.setattr(arena_cli, "read_list_badges", lambda image, found, templates: context.shapes)
    arena_cli._opponents(cast(Any, None), match, context)
    found, _ = context.sightings.read()
    assert [(s.row, s.codes) for s in found] == [
        (1, ("c1001", "c1002", "c1003", "c1004")),
        (2, ("c1005", None, "c1006", "c1007")),
    ]
    assert found[1].shapes == (("fire", "ranger"),) * 4  # the unknown face still has its element and class
    assert len({s.when for s in found}) == 1  # one instant for the whole refresh
    assert said[0][0] == ("Saved 2 opponents" if collect_only else "Your team is not known")


def test_a_face_and_its_badge_check_each_other() -> None:
    from e7ac.arena.list_read import resolve_list

    heroes = _catalog()
    seen: list[tuple[str, list[str]]] = []

    def among(face: Face, group: Any) -> tuple[str | None, float, float]:
        group = list(group)
        seen.append((face.candidate, group))
        return ("c1003", 0.8, 0.3) if face.candidate == "c1009" and "c1003" in group else (None, 0.4, 0.0)

    knight, mage, fire = ("light", "knight"), ("dark", "mage"), ("fire", "ranger")
    faces = [
        _faces("c1001", "c1005", "c1005", None),  # agree, disagree (a light knight, not a fire ranger), agree, unknown
        [Face(0.0, 0.0, 10.0, "c1009", 0.9, 0.5, "c1009"), *_faces("c1004", None, "c1002")],
    ]
    badges = [_badges(knight, knight, fire, mage), _badges(knight, mage, None, None)]
    first, second = resolve_list(faces, badges, heroes, among, lambda code: heroes[code].name)
    assert [h.code for h in first] == ["c1001", None, "c1005", None]  # the wrong face is left unknown, never kept
    assert "badge says light knight" in first[1].note and first[0].note == "" and first[3].shape == mage
    assert second[0].code == "c1003" and "Hero 3" in second[0].note  # corrected from among the light knights
    assert second[1].code == "c1004" and second[2].code is None and second[2].shape is None  # no badge: as read
    assert seen[0][1] == ["c1001", "c1002", "c1003"]  # the group is the badge's shape, nothing else
    low = ListBadge(None, 0.2, None, 0.0)  # an unclear badge never overrules a face
    assert resolve_list([faces[0][:1]], [[low]], heroes, among)[0][0].code == "c1001"


def test_sightings_keep_shapes_and_popularity_counts_them(tmp_path: Path) -> None:
    store = SightingStore(sightings_path(tmp_path))
    first = Sighting(T0, "list", 1, ("c1001", None, None, None), (("light", "knight"), ("dark", "mage"), None, None))
    store.add([first, _team("c1001", None, None, None)])  # the second has no shapes (older line / Attack Guide)
    found, skipped = store.read()
    assert found[0] == first and found[1].shapes == () and skipped == 0
    assert '"shapes"' not in found[1].to_json()
    with store.path.open("a", encoding="utf-8") as handle:
        handle.write(
            '{"when":"2026-10-18T12:00:00+00:00","source":"list","row":1,"codes":[null,null,null,null],"shapes":[1]}\n'
        )
    assert store.read()[1] == 1
    stats = popularity(found)
    assert stats.elements == (("dark", 1), ("light", 1)) and stats.classes == (("knight", 1), ("mage", 1))


def _screen(seed: int, shift: int = 0) -> tuple[np.ndarray[Any, Any], list[list[tuple[float, float, float]]]]:
    """A synthetic Opponents list: three rows of four round faces (random texture per seed) on a dark ground."""
    rng = np.random.default_rng(seed)
    image = np.full((740, 1000, 3), 20, np.uint8)
    rows = []
    for row in range(3):
        faces = []
        for col in range(4):
            x, y, r = 300 + 90 * col, 150 + 150 * row, 40
            blobs = rng.integers(0, 255, (5, 5, 3), dtype=np.uint8)  # a smooth, hero-like picture
            image[y - r : y + r, x - r : x + r] = cv2.resize(blobs, (2 * r, 2 * r), interpolation=cv2.INTER_CUBIC)
            faces.append((float(x), float(y), float(r)))
        rows.append(faces)
    noise = np.random.default_rng(shift).integers(-3, 4, image.shape)
    return np.clip(image.astype(int) + noise, 0, 255).astype(np.uint8), rows


def test_the_same_faces_are_recognised_through_noise_and_other_heroes_are_not() -> None:
    from e7ac.cli.arena import _list_signature, _same_faces

    a, rows = _screen(1, shift=1)
    again, _ = _screen(1, shift=2)
    other, _ = _screen(2)
    assert _same_faces(_list_signature(a, rows), _list_signature(again, rows))
    assert not _same_faces(_list_signature(a, rows), _list_signature(other, rows))
    assert not _same_faces(_list_signature(a, rows), _list_signature(a, rows[:2]))  # another size
    assert not _same_faces(None, _list_signature(a, rows))


def test_a_refresh_is_read_again_but_a_list_that_stays_is_not(monkeypatch: pytest.MonkeyPatch) -> None:
    from e7ac.cli import arena as arena_cli

    screens = {1: _screen(1), 2: _screen(2)}
    reads: list[int] = []

    def list_rows(image: Any) -> Any:
        if int(image[0, 0, 0]) == 99:  # a frame that is not the list
            raise ArenaScreenError("not the list")
        return next(rows for img, rows in screens.values() if img is image)

    def not_the_list(image: Any, templates: Any) -> Any:
        raise ArenaScreenError("nothing")

    def no_faces(image: Any) -> Any:
        raise ArenaScreenError("nothing")

    monkeypatch.setattr(arena_cli, "list_rows", list_rows)
    monkeypatch.setattr(arena_cli, "read_badges", not_the_list)
    monkeypatch.setattr(arena_cli, "_opponents", lambda image, match, c: reads.append(1))
    match = arena_cli._Match()
    context = cast(
        "arena_cli._Context",
        SimpleNamespace(answers=queue.Queue(), templates={}, faces=SimpleNamespace(read=no_faces)),
    )
    away = np.full((10, 10, 3), 99, np.uint8)

    def feed(*names: int | str) -> list[int]:
        reads.clear()
        for name in names:
            arena_cli._step(away if name == "away" else screens[int(name)][0], match, context)
        return list(reads)

    assert len(feed(1)) == 0  # one frame is not "still"
    assert len(feed(1)) == 1  # the second frame alike: read once
    assert len(feed(1, 1, 1)) == 0  # the list stays: no new reading
    assert len(feed(2)) == 0 and len(feed(2)) == 1  # a refresh: other faces, read once they are still
    assert len(feed(2, "away", 2, 2)) == 1  # leaving and coming back reads the list again


def test_kit_todo_counts_the_teams_the_watch_recorded(
    synthetic_catalog: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from e7ac.cli import kit as kit_cli
    from e7ac.cli.app import app
    from e7ac.paths import default_paths

    store = SightingStore(sightings_path(default_paths().home))
    store.add([_team("c9001", "c2011", None, None), _team("c9001", None, None, None, row=2)])
    monkeypatch.setattr(kit_cli, "fetch_kit", lambda http, name: {})
    result = CliRunner().invoke(app, ["kit", "todo", "--offline"])
    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if "x  " in line]
    assert "Test Hero" in lines[0] and " 2x" in lines[0] and "Blood Blade Karin" in lines[1] and " 1x" in lines[1]


def test_check_again_reads_the_opponents_list_again() -> None:
    from e7ac.cli import arena as arena_cli

    said: list[tuple[str, str]] = []
    badge = SimpleNamespace(say=lambda top, bottom, colour: said.append((top, bottom)))
    match = arena_cli._Match(list_armed=False, list_read=np.zeros((12, 144), np.float32))
    arena_cli._again(match, cast(Any, badge), collect_only=True)
    assert match.list_armed and match.list_read is None and said == [("Checking again...", "the Opponents list")]
    arena_cli._again(match, cast(Any, badge))
    assert "VS screen" in said[1][1]


@pytest.mark.fixtures("screenshots/arena/list_2.webp", "stove/class_icons/icon_spr.png")
def test_golden_opponents_list_badges() -> None:
    """The user's full-screen Opponents list (2026-10-18): 12 faces, read by their badges. The expected shapes agree,
    face by face, with the 12 heroes the Stove face icons gave independently (Boss Arunka ... Notos): 12 of 12."""
    from e7ac.vision.arena_badges import class_templates, normalise
    from e7ac.vision.arena_guide import list_rows
    from e7ac.vision.arena_list import read_list_badges
    from e7ac.vision.image import load_image

    root = Path(__file__).parent.parent / "fixtures"
    image = normalise(np.asarray(load_image(root / "screenshots/arena/list_2.webp"), np.uint8))
    read = read_list_badges(
        image, list_rows(image), class_templates((root / "stove/class_icons/icon_spr.png").read_bytes())
    )
    assert [[b.shape for b in row] for row in read] == [
        [("light", "knight"), ("dark", "assassin"), ("dark", "mage"), ("light", "knight")],
        [("ice", "assassin"), ("ice", "ranger"), ("fire", "ranger"), ("dark", "mage")],
        [("fire", "assassin"), ("dark", "assassin"), ("light", "manauser"), ("light", "knight")],
    ]
    assert all(min(b.element_confidence, b.class_confidence) >= 0.9 for row in read for b in row)
