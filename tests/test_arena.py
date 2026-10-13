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
    assert len(list((root / "c2011").glob("*.png"))) == 1 and ReferenceStore(root).last_team() == []  # not 4 heroes
    quiet = runner.invoke(app, ["arena", "scan", "vs.png", "--no-confirm"])
    assert quiet.exit_code == 0 and len(list((root / "c2011").glob("*.png"))) == 1  # nothing stored without asking


def _face_icon(seed: int) -> bytes:
    """A synthetic RGBA face icon (the real ones are game art, never committed)."""
    rgba = np.zeros((112, 112, 4), np.uint8)
    rgba[:, :, :3] = cv2.resize(_textured(seed), (112, 112), interpolation=cv2.INTER_NEAREST)
    alpha = np.zeros((112, 112), np.uint8)
    cv2.circle(alpha, (56, 56), 50, 255, -1)
    rgba[:, :, 3] = alpha
    return cv2.imencode(".png", rgba)[1].tobytes()


def _guide(icons: list[bytes]) -> np.ndarray[Any, np.dtype[np.uint8]]:
    """An Attack Guide: a row of round face icons in gold frames on a dark panel."""
    image = np.full((740, 1151, 3), (40, 25, 10), np.uint8)  # the size the watch reads at: no resampling
    r = 58
    for i, data in enumerate(icons):
        cx, cy = 324 + 170 * i, 282
        decoded = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
        assert decoded is not None
        icon = cv2.resize(decoded, (2 * r, 2 * r), interpolation=cv2.INTER_AREA).astype(np.float32)
        alpha = icon[:, :, 3:] / 255
        face = icon[:, :, :3] * alpha + np.array([110, 60, 20], np.float32) * (1 - alpha)
        disc = np.zeros((2 * r, 2 * r), np.uint8)
        cv2.circle(disc, (r, r), r - 4, 1, -1)
        region = image[cy - r : cy + r, cx - r : cx + r]
        region[disc > 0] = face.astype(np.uint8)[disc > 0]
        cv2.circle(image, (cx, cy), r, (60, 170, 210), 6)
    return image


def test_attack_guide_faces_are_recognised_and_unknown_ones_never_guessed() -> None:
    from e7ac.vision.arena_guide import FaceMatcher

    icons = {f"c100{i}": _face_icon(10 + i) for i in range(1, 4)}
    matcher = FaceMatcher.from_png(icons)
    faces = matcher.read(_guide([*icons.values(), _face_icon(99)]))
    assert [f.code for f in faces] == ["c1001", "c1002", "c1003", None]
    with pytest.raises(ArenaScreenError):
        matcher.read(np.full((832, 1151, 3), 30, np.uint8))


TEAMS = cast(
    dict[str, ResolvedEntity],
    {
        "c1001": _Hero("Ally One", "dark", "assassin"),
        "c1002": _Hero("Ally Two", "fire", "warrior"),
        "c1003": _Hero("Ally Three", "wind", "ranger"),
        "c1004": _Hero("Ally Four", "ice", "mage"),
        "c1005": _Hero("Foe One", "light", "manauser"),
        "c1006": _Hero("Foe Two", "dark", "knight"),
        "c1007": _Hero("Foe Three", "fire", "mage"),
        "c1008": _Hero("Foe Four", "wind", "warrior"),
        **{
            f"c20{i}": _Hero(f"Lookalike {i}", e, r)
            for i, (e, r) in enumerate(
                [("light", "manauser"), ("dark", "knight"), ("fire", "mage"), ("wind", "warrior")], 10
            )
        },  # the VS screen alone cannot tell the opponents from these
    },
)


def test_watch_reads_your_team_then_the_attack_guide_and_shows_the_odds(monkeypatch: pytest.MonkeyPatch) -> None:
    from e7ac.cli import arena as arena_cli
    from e7ac.cli import predict as predict_cli
    from e7ac.cli.app import app
    from e7ac.predict.battle import BattleOdds
    from e7ac.vision.arena_guide import FaceMatcher

    def badge_of(side: str, x: int, code: str) -> Badge:
        hero = TEAMS[code]
        element, role = Element(str(hero.value("element"))), HeroClass(str(hero.value("role")))
        return Badge(cast(Any, side), x, 150, 33, 29, element, 1.0, role, 1.0)

    badges = [badge_of("you", 100 + 120 * i, f"c100{i + 1}") for i in range(4)]
    badges += [badge_of("them", 760 + 120 * i, f"c100{i + 5}") for i in range(4)]
    vs = np.asarray(cv2.resize(_textured(3), (1326, 740)), np.uint8)
    vs[0, 0] = 7
    foes = {f"c100{i}": _face_icon(20 + i) for i in range(5, 9)}
    guide = _guide(list(foes.values()))
    said: list[tuple[str, str]] = []

    class FakeBadge:
        def place(self, area: object) -> None: ...
        def say(self, top: str, bottom: str, colour: str) -> None:
            said.append((top, bottom))

        def close(self) -> None: ...

    def frames(client: object, hwnd: int | None) -> Any:
        yield from [(vs, None), (vs, None), None, (guide, None), (guide, None)]
        raise KeyboardInterrupt

    def read(image: Any, templates: object) -> list[Badge]:
        if image.shape[1] != 1326 or image[0, 0, 0] != 7:
            raise ArenaScreenError("not VS")
        return badges

    predicted: list[tuple[str, str]] = []

    def odds(team: str, theirs: str, world: object, offline: bool) -> BattleOdds:
        predicted.append((team, theirs))
        return BattleOdds(0.7, 0.3, 0.0, 30, 100)

    owned = {f"c100{i}" for i in range(1, 5)}
    monkeypatch.setattr(arena_cli, "game_frames", frames)
    monkeypatch.setattr(arena_cli, "_templates", lambda paths, offline: {})
    monkeypatch.setattr(arena_cli, "_context", lambda: (TEAMS, owned))
    monkeypatch.setattr(arena_cli, "_faces", lambda paths, heroes, offline, store: FaceMatcher.from_png(foes))
    monkeypatch.setattr(arena_cli, "read_badges", read)
    monkeypatch.setattr(arena_cli, "signal_factory", lambda on_retry, on_share: (FakeBadge(), ""))
    monkeypatch.setattr(predict_cli, "win_odds", odds)
    result = CliRunner().invoke(app, ["arena", "watch"])
    assert result.exit_code == 0, result.output
    assert predicted == [("c1001, c1002, c1003, c1004", "c1005, c1006, c1007, c1008")]  # once, no question asked
    assert ("Tap the magnifier", "to read the opponent's team") in said
    assert said[-1] == ("Win chance 70%", "vs Foe One, Foe Two, Foe Three, Foe Four")
    learned = ReferenceStore(default_paths().home / "references" / "arena").codes()
    assert learned == set(foes)  # the VS models of the opponent, named by their faces


def test_a_swapped_out_hero_of_your_last_team_is_not_kept() -> None:
    owned = {"c1001", "c1002", "c1003"}
    known = {"c1001", "c1002"}
    table = {(0, "c1001"): 12, (0, "c1002"): 15}  # neither stored hero matches: the remembered one was swapped out
    (slot,) = identify([_badge("you")], HEROES, owned, ["c1001"], known, _scores(table))
    assert slot.code is None
    (kept,) = identify([_badge("you")], HEROES, owned, ["c1001"], known, _scores({(0, "c1001"): 50, (0, "c1002"): 30}))
    assert (kept.code, kept.method) == ("c1001", "last team")


def test_check_again_reads_another_opponent(monkeypatch: pytest.MonkeyPatch) -> None:
    from e7ac.cli import arena as arena_cli
    from e7ac.cli import predict as predict_cli
    from e7ac.cli.app import app
    from e7ac.predict.battle import BattleOdds
    from e7ac.vision.arena_guide import FaceMatcher

    def badge_of(side: str, x: int, code: str) -> Badge:
        hero = TEAMS[code]
        element, role = Element(str(hero.value("element"))), HeroClass(str(hero.value("role")))
        return Badge(cast(Any, side), x, 150, 33, 29, element, 1.0, role, 1.0)

    team = [badge_of("you", 100 + 120 * i, f"c100{i + 1}") for i in range(4)]
    foes = [badge_of("them", 760 + 120 * i, f"c100{i + 5}") for i in range(4)]
    first_foes = [f"c100{i}" for i in range(5, 9)]
    other_foes = [f"c20{i}" for i in range(10, 14)]  # the same elements and classes: only their faces tell them apart
    icons = {code: _face_icon(30 + i) for i, code in enumerate(first_foes + other_foes)}
    first = np.asarray(cv2.resize(_textured(3), (1326, 740)), np.uint8)
    other = np.asarray(cv2.resize(_textured(4), (1326, 740)), np.uint8)
    first_guide, other_guide = _guide([icons[c] for c in first_foes]), _guide([icons[c] for c in other_foes])
    clicks: list[Any] = []

    def frames(client: object, hwnd: int | None) -> Any:
        yield from [(first, None), (first, None), (first_guide, None), (first_guide, None)]
        yield from [(first, None), (first, None)]  # back on the VS screen: its opponent models are now known
        yield from [(other, None), (other, None)]  # another opponent; the VS screen never went away
        clicks[0]()
        yield from [(other, None), (other, None), (other_guide, None), (other_guide, None)]
        raise KeyboardInterrupt

    def read(image: Any, templates: object) -> list[Badge]:
        if image.shape[1] != 1326:
            raise ArenaScreenError("not VS")
        return team + foes

    said: list[str] = []

    class FakeBadge:
        def place(self, area: object) -> None: ...
        def say(self, top: str, bottom: str, colour: str) -> None:
            said.append(top)

        def close(self) -> None: ...

    def factory(on_click: Any, on_share: Any) -> tuple[FakeBadge, str]:
        clicks.append(on_click)
        return FakeBadge(), ""

    predicted: list[str] = []

    def odds(team_: str, vs_: str, world: object, offline: bool) -> BattleOdds:
        predicted.append(vs_)
        return BattleOdds(0.5, 0.5, 0.0, 30, 100)

    owned = {f"c100{i}" for i in range(1, 5)}
    monkeypatch.setattr(arena_cli, "game_frames", frames)
    monkeypatch.setattr(arena_cli, "_templates", lambda paths, offline: {})
    monkeypatch.setattr(arena_cli, "_context", lambda: (TEAMS, owned))
    monkeypatch.setattr(arena_cli, "_faces", lambda paths, heroes, offline, store: FaceMatcher.from_png(icons))
    monkeypatch.setattr(arena_cli, "read_badges", read)
    monkeypatch.setattr(arena_cli, "signal_factory", factory)
    monkeypatch.setattr(predict_cli, "win_odds", odds)
    result = CliRunner().invoke(app, ["arena", "watch"])
    assert result.exit_code == 0, result.output
    # the guide; the VS screen again (learned models); after the button the new opponent, with the same elements and
    # classes, is never taken for the old one: the magnifier is asked for and its faces name it
    assert predicted == [", ".join(first_foes), ", ".join(first_foes), ", ".join(other_foes)], result.output
    assert said.count("Tap the magnifier") == 2


def _watch_run(
    monkeypatch: pytest.MonkeyPatch,
    frames: list[Any],
    icons: dict[str, bytes],
    typed: list[str],
    heroes: dict[str, ResolvedEntity] = TEAMS,
) -> tuple[Any, list[str]]:
    """Run `e7 arena watch` on these frames (VS screens: 1326 wide), with these known face icons and typed lines."""
    from e7ac.cli import arena as arena_cli
    from e7ac.cli import predict as predict_cli
    from e7ac.cli.app import app
    from e7ac.predict.battle import BattleOdds
    from e7ac.vision.arena_guide import FaceMatcher

    def badge_of(side: str, x: int, code: str) -> Badge:
        hero = TEAMS[code]
        element, role = Element(str(hero.value("element"))), HeroClass(str(hero.value("role")))
        return Badge(cast(Any, side), x, 150, 33, 29, element, 1.0, role, 1.0)

    badges = [badge_of("you", 100 + 120 * i, f"c100{i + 1}") for i in range(4)]
    badges += [badge_of("them", 760 + 120 * i, f"c100{i + 5}") for i in range(4)]

    def read(image: Any, templates: object) -> list[Badge]:
        if image.shape[1] != 1326:
            raise ArenaScreenError("not VS")
        return badges

    class FakeBadge:
        def place(self, area: object) -> None: ...
        def say(self, top: str, bottom: str, colour: str) -> None: ...
        def close(self) -> None: ...

    predicted: list[str] = []

    def odds(team: str, vs_: str, world: object, offline: bool) -> BattleOdds:
        predicted.append(vs_)
        return BattleOdds(0.5, 0.5, 0.0, 30, 100)

    def stream(client: object, hwnd: int | None) -> Any:
        yield from [(f, None) for f in frames]
        raise KeyboardInterrupt

    monkeypatch.setattr(arena_cli, "game_frames", stream)
    monkeypatch.setattr(arena_cli, "_templates", lambda paths, offline: {})
    monkeypatch.setattr(arena_cli, "_context", lambda: (heroes, {f"c100{i}" for i in range(1, 5)}))
    monkeypatch.setattr(
        arena_cli, "_faces", lambda paths, heroes, offline, store: FaceMatcher.from_png(icons, store.crops())
    )
    monkeypatch.setattr(arena_cli, "read_badges", read)
    monkeypatch.setattr(arena_cli, "signal_factory", lambda on_retry, on_share: (FakeBadge(), ""))
    monkeypatch.setattr(arena_cli, "listen", lambda answers: answers.put(typed.pop(0)) if typed else None)
    monkeypatch.setattr(predict_cli, "win_odds", odds)
    result = CliRunner().invoke(app, ["arena", "watch"])
    assert result.exit_code == 0, result.output
    return result, predicted


def test_a_face_unlike_its_icon_is_named_by_elimination_and_learned(monkeypatch: pytest.MonkeyPatch) -> None:
    foes = [f"c100{i}" for i in range(5, 9)]
    icons = {code: _face_icon(40 + i) for i, code in enumerate(foes)}
    shown = {**icons, "c1007": _face_icon(77)}  # in game, Foe Three's face is not its icon
    vs = np.asarray(cv2.resize(_textured(6), (1326, 740)), np.uint8)
    guide = _guide([shown[c] for c in foes])
    unique = {code: hero for code, hero in TEAMS.items() if not code.startswith("c20")}  # the VS screen names them
    result, predicted = _watch_run(monkeypatch, [vs, vs, guide, guide], icons, [], unique)
    assert "is Foe Three (from the VS screen): learned" in result.output
    assert predicted == [", ".join(foes), ", ".join(foes)]  # from the VS screen, then from the guide
    again, _ = _watch_run(monkeypatch, [guide, guide], icons, [])  # learned: recognised directly, nothing asked
    assert "Foe Three" in again.output and "not recognised" not in again.output


def test_an_unknown_face_is_asked_without_stopping_the_watch(monkeypatch: pytest.MonkeyPatch) -> None:
    foes = [f"c100{i}" for i in range(5, 9)]
    icons = {code: _face_icon(50 + i) for i, code in enumerate(foes)}
    shown = {**icons, "c1006": _face_icon(88)}
    guide = _guide([shown[c] for c in foes])
    blank = np.zeros((740, 1151, 3), np.uint8)
    result, predicted = _watch_run(monkeypatch, [guide, guide, blank, blank], icons, ["Foe Two"])
    assert "Opponent hero 2 not recognised" in result.output and "face 2 is Foe Two: learned" in result.output
    # your team was never shown (no VS screen): the opponent is named, the prediction waits for your team
    assert "Your team is not known yet" in result.output and predicted == []


def test_forget_removes_what_was_learned_for_a_hero(synthetic_catalog: list[str]) -> None:
    from e7ac.cli.app import app

    home = default_paths().home / "references"
    when = datetime(2026, 10, 13, tzinfo=UTC)
    ReferenceStore(home / "faces").add("c2011", _textured(1), when)
    ReferenceStore(home / "arena").add("c2011", _textured(2), when)
    ReferenceStore(home / "arena").add("c9001", _textured(3), when)
    ReferenceStore(home / "arena").save_last_team(["c2011", "c9001"])
    result = CliRunner().invoke(app, ["arena", "forget", "Blood Blade Karin"])
    assert result.exit_code == 0 and "2 learned picture(s) forgotten" in result.output
    assert ReferenceStore(home / "arena").codes() == {"c9001"} and ReferenceStore(home / "faces").codes() == set()
    assert ReferenceStore(home / "arena").last_team() == ["c9001"]


def test_an_unclear_face_is_decided_among_the_heroes_its_vs_badge_allows(monkeypatch: pytest.MonkeyPatch) -> None:
    foes = [f"c100{i}" for i in range(5, 9)]
    icons = {code: _face_icon(60 + i) for i, code in enumerate(foes + [f"c20{i}" for i in range(10, 14)])}
    own = cv2.imdecode(np.frombuffer(icons["c1006"], np.uint8), cv2.IMREAD_UNCHANGED)
    other = cv2.imdecode(np.frombuffer(_face_icon(99), np.uint8), cv2.IMREAD_UNCHANGED)
    assert own is not None and other is not None
    own[:, :, :3] = (0.6 * own[:, :, :3] + 0.4 * other[:, :, :3]).astype(np.uint8)  # ~0.73: below the global rule
    shown = {**icons, "c1006": cv2.imencode(".png", own)[1].tobytes()}
    vs = np.asarray(cv2.resize(_textured(7), (1326, 740)), np.uint8)
    guide = _guide([shown[c] for c in foes])
    result, predicted = _watch_run(monkeypatch, [vs, vs, guide, guide], icons, [])
    assert "face 2 is Foe Two (" in result.output and "not recognised" not in result.output
    assert predicted == [", ".join(foes)]


def test_the_attack_guide_is_read_even_when_no_face_is_known(monkeypatch: pytest.MonkeyPatch) -> None:
    foes = [f"c100{i}" for i in range(5, 9)]
    icons = {code: _face_icon(70 + i) for i, code in enumerate(foes + [f"c20{i}" for i in range(10, 14)])}
    other = cv2.imdecode(np.frombuffer(_face_icon(98), np.uint8), cv2.IMREAD_UNCHANGED)
    assert other is not None
    shown = {}
    for code in foes:  # every face differs from its icon (~0.73): none is known among all heroes
        face = cv2.imdecode(np.frombuffer(icons[code], np.uint8), cv2.IMREAD_UNCHANGED)
        assert face is not None
        face[:, :, :3] = (0.6 * face[:, :, :3] + 0.4 * other[:, :, :3]).astype(np.uint8)
        shown[code] = cv2.imencode(".png", face)[1].tobytes()
    vs = np.asarray(cv2.resize(_textured(8), (1326, 740)), np.uint8)
    guide = _guide([shown[c] for c in foes])
    result, predicted = _watch_run(monkeypatch, [vs, vs, guide, guide], icons, [])
    assert "tap the magnifier" in result.output and "Attack Guide: ?" in result.output
    assert predicted == [", ".join(foes)]  # all four decided among the heroes the VS badges allow


def test_save_picture_writes_the_last_result_on_the_game(monkeypatch: pytest.MonkeyPatch) -> None:
    from e7ac.cli import arena as arena_cli

    said: list[str] = []

    class FakeBadge:
        def place(self, area: object) -> None: ...
        def say(self, top: str, bottom: str, colour: str) -> None:
            said.append(top)

        def close(self) -> None: ...

    paths = default_paths()
    game = np.asarray(cv2.resize(_textured(9), (1600, 900)), np.uint8)
    arena_cli._share(None, ("", "", ""), paths, cast(Any, FakeBadge()))
    arena_cli._share(game, ("Win chance 62%", "vs Foe One, Foe Two", "#2fa84f"), paths, cast(Any, FakeBadge()))
    saved = list((paths.captures_dir / "arena" / "shared").glob("*.png"))
    assert said == ["Nothing to save yet", "Picture saved"] and len(saved) == 1
    picture = cv2.imread(str(saved[0]))
    assert picture is not None and picture.shape == game.shape and not np.array_equal(picture[:60], game[:60])
