"""Arena VS screen (M17a, SPEC D64): badges on synthetic screens, identification rules, learned references, CLI.
Golden test on the user's capture skips when the local fixtures are missing."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import cv2
import numpy as np
import pytest
from typer.testing import CliRunner

from e7ac.arena.identify import MIN_INLIERS, identify
from e7ac.arena.references import Match, ReferenceStore
from e7ac.catalog.resolve import ResolvedEntity
from e7ac.domain.codes import Element, HeroClass
from e7ac.paths import default_paths
from e7ac.vision.arena_badges import ArenaScreenError, Badge, Template, read_badges

FIXTURES = Path(__file__).parent.parent / "fixtures"
HUES = {Element.EARTH: 40, Element.LIGHT: 22, Element.DARK: 150, Element.FIRE: 2, Element.ICE: 105}


def _glyphs() -> dict[HeroClass, Template]:
    """Six distinct white shapes standing in for the class icons (the real ones are game art, never committed)."""
    shapes: dict[HeroClass, Any] = {}
    size = 40
    for hero_class in HeroClass:
        g = np.zeros((size, size), np.uint8)
        if hero_class is HeroClass.KNIGHT:
            cv2.circle(g, (20, 20), 15, 255, -1)
        elif hero_class is HeroClass.MAGE:
            cv2.fillPoly(g, [np.array([[20, 3], [37, 37], [3, 37]], np.int32)], 255)
        elif hero_class is HeroClass.THIEF:
            cv2.rectangle(g, (4, 4), (36, 36), 255, 5)
        elif hero_class is HeroClass.RANGER:
            cv2.line(g, (5, 5), (35, 35), 255, 7)
            cv2.line(g, (35, 5), (5, 35), 255, 7)
        elif hero_class is HeroClass.SOUL_WEAVER:
            for y in (8, 20, 32):
                cv2.line(g, (4, y), (36, y), 255, 4)
        else:
            cv2.fillPoly(g, [np.array([[20, 2], [38, 20], [20, 38], [2, 20]], np.int32)], 255)
        shapes[hero_class] = g
    return {k: (v.astype(np.float32), (v > 0).astype(np.float32)) for k, v in shapes.items()}


def _screen(
    badges: list[tuple[int, int, Element, HeroClass]], scale: float = 1.0, *, icons: bool = True
) -> np.ndarray[Any, np.dtype[np.uint8]]:
    """Dark 1326x740 screen with badges at (x, y) of the level square (29 px tall at scale 1)."""
    image = np.full((740, 1326, 3), 25, np.uint8)
    glyphs = _glyphs()
    for x, y, element, hero_class in badges:
        colour = cv2.cvtColor(np.array([[[HUES[element], 210, 170]]], np.uint8), cv2.COLOR_HSV2BGR)[0, 0]
        cv2.rectangle(image, (x, y), (x + 33, y + 29), tuple(int(c) for c in colour), -1)
        cv2.putText(image, "60", (x + 4, y + 23), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        if icons:
            icon = cv2.resize(glyphs[hero_class][0], (38, 38)).astype(np.uint8)
            region = image[y - 5 : y + 33, x - 44 : x - 6]
            region[icon > 127] = 255
    return np.asarray(cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA), np.uint8)


LAYOUT = [
    (150, 170, Element.EARTH, HeroClass.SOUL_WEAVER),
    (275, 72, Element.DARK, HeroClass.THIEF),
    (900, 137, Element.FIRE, HeroClass.WARRIOR),
    (1150, 155, Element.ICE, HeroClass.RANGER),
]


@pytest.mark.parametrize("scale", [0.7, 1.0, 1.5])
def test_badges_give_side_element_and_class_at_any_size(scale: float) -> None:
    badges = read_badges(_screen(LAYOUT, scale), _glyphs())
    assert [(b.side, b.element, b.hero_class) for b in badges] == [
        ("you", Element.EARTH, HeroClass.SOUL_WEAVER),
        ("you", Element.DARK, HeroClass.THIEF),
        ("them", Element.FIRE, HeroClass.WARRIOR),
        ("them", Element.ICE, HeroClass.RANGER),
    ]
    by_element = {b.element: b for b in badges}
    assert by_element[Element.DARK].element_confidence > by_element[Element.FIRE].element_confidence  # fire unseen


def test_squares_without_class_icon_are_not_badges() -> None:
    with pytest.raises(ArenaScreenError):
        read_badges(_screen(LAYOUT, icons=False), _glyphs())
    with pytest.raises(ArenaScreenError):
        read_badges(np.full((740, 1326, 3), 25, np.uint8), _glyphs())


@pytest.mark.fixtures("screenshots/arena/prep_1.webp", "stove/class_icons/icon_spr.png")
def test_golden_vs_screen() -> None:
    from e7ac.vision.arena_badges import class_templates
    from e7ac.vision.image import load_image

    templates = class_templates((FIXTURES / "stove/class_icons/icon_spr.png").read_bytes())
    badges = read_badges(np.asarray(load_image(FIXTURES / "screenshots/arena/prep_1.webp"), np.uint8), templates)
    assert [(b.side, b.element.value, b.hero_class.value) for b in badges] == [
        ("you", "wind", "manauser"),
        ("you", "dark", "assassin"),
        ("you", "dark", "mage"),
        ("you", "wind", "knight"),
        ("them", "wind", "knight"),
        ("them", "light", "manauser"),
        ("them", "light", "mage"),
        ("them", "light", "manauser"),
    ]


class _Hero:
    def __init__(self, name: str, element: str, role: str) -> None:
        self.name = name
        self._values = {"element": element, "role": role}

    def value(self, key: str) -> object:
        return self._values.get(key)


HEROES = cast(
    dict[str, ResolvedEntity],
    {
        "c1001": _Hero("Alpha", "light", "manauser"),
        "c1002": _Hero("Beta", "light", "manauser"),
        "c1003": _Hero("Gamma", "light", "manauser"),
        "c1004": _Hero("Delta", "dark", "mage"),
    },
)


def _badge(side: str, element: Element = Element.LIGHT, hero_class: HeroClass = HeroClass.SOUL_WEAVER) -> Badge:
    return Badge(cast(Any, side), 0, 0, 30, 29, element, 1.0, hero_class, 1.0)


def _scores(table: dict[tuple[int, str], int]) -> Any:
    return lambda index, code: Match(table.get((index, code), 0), 0.8)


def test_only_candidate_and_owned_heroes_on_your_side() -> None:
    slots = identify(
        [_badge("you"), _badge("them", Element.DARK, HeroClass.MAGE)], HEROES, {"c1002"}, [], set(), _scores({})
    )
    assert [(s.code, s.method) for s in slots] == [("c1002", "only candidate"), ("c1004", "only candidate")]


def test_your_side_uses_the_whole_catalog_when_you_own_none_of_them() -> None:
    (slot,) = identify([_badge("you")], HEROES, set(), [], set(), _scores({}))
    assert slot.code is None and slot.candidates == ["c1001", "c1002", "c1003"]


def test_last_team_decides_your_side_only() -> None:
    owned = {"c1001", "c1002", "c1003"}
    slots = identify([_badge("you"), _badge("them")], HEROES, owned, ["c1003"], set(), _scores({}))
    assert [(s.code, s.method) for s in slots] == [("c1003", "last team"), (None, "review")]


def test_reference_needs_enough_points_and_a_margin() -> None:
    known = {"c1001", "c1002"}
    clear = identify([_badge("them")], HEROES, set(), [], known, _scores({(0, "c1001"): MIN_INLIERS, (0, "c1002"): 10}))
    assert (clear[0].code, clear[0].method) == ("c1001", "reference")
    close = identify([_badge("them")], HEROES, set(), [], known, _scores({(0, "c1001"): 90, (0, "c1002"): 40}))
    weak = identify([_badge("them")], HEROES, set(), [], known, _scores({(0, "c1001"): MIN_INLIERS - 1}))
    assert close[0].code is None and weak[0].code is None
    assert close[0].candidates[:2] == ["c1001", "c1002"]  # best first for the question


def test_unseen_hero_is_never_taken_for_a_known_one() -> None:
    (slot,) = identify([_badge("them")], HEROES, set(), [], {"c1001"}, _scores({(0, "c1001"): 12}))
    assert slot.code is None


def test_same_hero_twice_on_one_side_goes_to_review() -> None:
    table = {(0, "c1001"): 200, (1, "c1001"): 200}
    slots = identify([_badge("them"), _badge("them")], HEROES, set(), [], {"c1001"}, _scores(table))
    assert [s.code for s in slots] == [None, None]


def _textured(seed: int) -> np.ndarray[Any, np.dtype[np.uint8]]:
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 255, (35, 23, 3), dtype=np.uint8)
    return np.asarray(cv2.resize(small, (180, 280), interpolation=cv2.INTER_NEAREST), np.uint8)


def test_references_match_the_same_hero_mirrored_and_resized(tmp_path: Path) -> None:
    store = ReferenceStore(tmp_path)
    when = datetime(2026, 10, 12, tzinfo=UTC)
    store.add("c1001", _textured(1), when)
    store.add("c1002", _textured(2), when)
    query = np.asarray(cv2.resize(cv2.flip(_textured(1), 1), None, fx=1.4, fy=1.4), np.uint8)
    same, other = store.best("c1001", query), store.best("c1002", query)
    assert same.inliers >= MIN_INLIERS and same.inliers > 3 * other.inliers
    assert store.codes() == {"c1001", "c1002"} and store.best("c1003", query).inliers == 0


def test_reference_store_keeps_the_newest_crops_and_the_last_team(tmp_path: Path) -> None:
    store = ReferenceStore(tmp_path)
    for minute in range(14):
        store.add("c1001", _textured(minute), datetime(2026, 10, 12, 10, minute, tzinfo=UTC))
    assert len(list((tmp_path / "c1001").glob("*.png"))) == 12
    (tmp_path / "c1001" / "20990101-000000-broken.png").write_bytes(b"not a png")
    assert store.best("c1001", _textured(13)).inliers >= MIN_INLIERS  # a damaged file counts as missing
    with pytest.raises(ValueError):
        store.add("Alpha", _textured(1), datetime(2026, 10, 12, tzinfo=UTC))
    store.save_last_team(["c1001", "c1002"])
    assert ReferenceStore(tmp_path).last_team() == ["c1001", "c1002"]


def test_scan_asks_learns_and_prints_the_predict_command(
    synthetic_catalog: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from e7ac.cli import arena as arena_cli
    from e7ac.cli.app import app

    badges = [
        Badge("you", 100, 100, 33, 29, Element.DARK, 1.0, HeroClass.THIEF, 1.0),
        Badge("them", 900, 100, 33, 29, Element.FIRE, 1.0, HeroClass.WARRIOR, 1.0),
    ]
    frame = _textured(5)
    big = np.asarray(cv2.resize(frame, (1326, 740)), np.uint8)
    monkeypatch.setattr(arena_cli, "load_class_sprite", lambda cache: b"png")
    monkeypatch.setattr(arena_cli, "class_templates", lambda sprite: {})
    monkeypatch.setattr(arena_cli, "read_badges", lambda image, templates: badges)
    monkeypatch.setattr(arena_cli, "load_image", lambda path: big)
    runner = CliRunner()
    first = runner.invoke(app, ["arena", "scan", "vs.png", "--confirm"], input="\n")
    assert first.exit_code == 0, first.output
    assert "Blood Blade Karin (c2011) [only candidate]" in first.output
    assert 'Run: e7 predict win --team "c2011" --vs "c9001"' in first.output
    root = default_paths().home / "references" / "arena"
    assert len(list((root / "c2011").glob("*.png"))) == 1 and ReferenceStore(root).last_team() == ["c2011"]
    quiet = runner.invoke(app, ["arena", "scan", "vs.png", "--no-confirm"])
    assert quiet.exit_code == 0 and len(list((root / "c2011").glob("*.png"))) == 1  # nothing stored without asking


def test_watch_reads_each_vs_screen_once_and_predicts(
    synthetic_catalog: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from e7ac.cli import arena as arena_cli
    from e7ac.cli import predict as predict_cli
    from e7ac.cli.app import app

    vs = np.full((740, 1326, 3), 7, np.uint8)
    blank = np.zeros((740, 1326, 3), np.uint8)
    badges = [Badge("you", 100 + 40 * i, 100, 33, 29, Element.DARK, 1.0, HeroClass.THIEF, 1.0) for i in range(4)] + [
        Badge("them", 900 + 40 * i, 100, 33, 29, Element.FIRE, 1.0, HeroClass.WARRIOR, 1.0) for i in range(4)
    ]

    def frames(client: object, hwnd: int | None) -> Any:
        yield from [None, blank, vs, vs, vs, blank, vs, vs]
        raise KeyboardInterrupt

    def read(image: Any, templates: object) -> list[Badge]:
        if image[0, 0, 0] != 7:
            raise ArenaScreenError("not VS")
        return badges

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(arena_cli, "game_frames", frames)
    monkeypatch.setattr(arena_cli, "_templates", lambda paths, offline: {})
    monkeypatch.setattr(arena_cli, "read_badges", read)
    monkeypatch.setattr(predict_cli, "win", lambda team, vs, world, offline: calls.append((team, vs)))
    result = CliRunner().invoke(app, ["arena", "watch"], input="\n" * 30)
    assert result.exit_code == 0, result.output
    # the same hero 4 times on a side is never accepted: asked (skipped here), so no prediction yet
    assert "Not identified" in result.output and calls == []
    assert len(list((default_paths().captures_dir / "arena").glob("*.png"))) == 2  # one per VS screen shown
