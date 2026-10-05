"""Gear panel of the Hero Info screen: synthetic layouts (always run) and the user's captures (local fixtures only).

The synthetic panel copies the measured layout of the user's captures (2000-px copies, unit h = 44 px): OCR lines are
built by hand, the image only carries the colour cues (frame, gold ornament, '+N' pill, text ink) drawn as flat
patches, and a fake reader stands in for the second OCR passes.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from functools import cache
from typing import Final

import cv2
import numpy as np
import numpy.typing as npt
import pytest

from e7ac.domain.roster import GearSlot
from e7ac.settings import GameLanguage
from e7ac.vision.gear_panel import (
    MIN_UPSCALE,
    PROBE_HALF_HEIGHT,
    PROBE_LEFT,
    RENDER_BORDER,
    RENDER_TARGET,
    ROW_PITCH,
    GearPanel,
    GearPiece,
    bright_red_share,
    decide_frame,
    frame_votes,
    gold_share,
    parse_gear_panel,
)
from e7ac.vision.ocr import Box, RapidOcrReader, TextLine
from tests.markers import FIXTURES_DIR

type Image = npt.NDArray[np.uint8]

H: Final = 44.0
"""Unit of the synthetic layout (h / UI scale measured 43.4-44.5 px on the user's 2000-px copies)."""
ANCHOR: Final = Box(1365.0, 331.0, 1750.0, 375.0)
EDGES: Final = {"L": 1592.0, "R": 1944.0}
"""Right edges of the value columns (measured at UI scale 1.0)."""
COLUMN: Final = {
    GearSlot.WEAPON: ("L", 0),
    GearSlot.HELMET: ("L", 1),
    GearSlot.ARMOR: ("L", 2),
    GearSlot.NECKLACE: ("R", 0),
    GearSlot.RING: ("R", 1),
    GearSlot.BOOTS: ("R", 2),
}
# measured layout (not the module's constants, so the tests do not just restate them)
FIRST_ROW, PIECE_PITCH, FIRST_SUB, ICON_OFFSET = 1.52, 4.54, 0.79, 4.74


@dataclass(frozen=True, slots=True)
class PieceSpec:
    main: str
    subs: tuple[str, ...]
    level: str
    enhance: int
    score: int
    frame: str = "red"
    """'red' (with the gold ornament), 'purple' (without), 'red-plain' (red without ornament)."""


STRAZE: Final = {  # values of the user's Straze capture
    GearSlot.WEAPON: PieceSpec("525", ("7%", "13%", "13%", "24%"), "90", 15, 92),
    GearSlot.HELMET: PieceSpec("2,835", ("24%", "12%", "8%", "10%"), "90", 15, 88, "purple"),
    GearSlot.ARMOR: PieceSpec("310", ("28%", "16%", "8%", "9%"), "90", 15, 100),
    GearSlot.NECKLACE: PieceSpec("70%", ("145", "33%", "9%", "6%"), "88", 15, 91),
    GearSlot.RING: PieceSpec("65%", ("6%", "53", "30%", "6"), "90", 15, 86),
    GearSlot.BOOTS: PieceSpec("65%", ("10%", "14%", "9", "56"), "90", 15, 81, "purple"),
}
ARTIFACT_NAMES: Final = {
    "Daydream Joker": "ef317",
    "Dream Scroll": "ef103",
    "Custom-Made Power Anchor": "efw42",
    "Light and Darkness": "efr34",
    "Fan of Light and Dark": "efh18",
}


def row_y(slot: GearSlot) -> float:
    return ANCHOR.cy + (FIRST_ROW + COLUMN[slot][1] * PIECE_PITCH) * H


def icon_cx(slot: GearSlot) -> float:
    return EDGES[COLUMN[slot][0]] - ICON_OFFSET * H


def sub_y(slot: GearSlot, index: int) -> float:
    return row_y(slot) + (FIRST_SUB + index * ROW_PITCH) * H


def text_line(
    text: str, x0: float, cy: float, height: float, *, x1: float | None = None, score: float = 0.99
) -> TextLine:
    right = x1 if x1 is not None else x0 + max(1, len(text)) * 0.42 * height
    return TextLine(text, score, Box(x0, cy - height / 2, right, cy + height / 2), ())


def value_line(text: str, edge: float, cy: float, height: float) -> TextLine:
    """Right-aligned value: its right edge is the column's."""
    return text_line(text, edge - len(text) * 0.42 * height, cy, height, x1=edge)


def hsv_colour(hue: int, saturation: int, value: int) -> tuple[int, int, int]:
    bgr = cv2.cvtColor(np.array([[[hue, saturation, value]]], dtype=np.uint8), cv2.COLOR_HSV2BGR)[0, 0]
    return int(bgr[0]), int(bgr[1]), int(bgr[2])


RED_FRAME: Final = hsv_colour(1, 215, 72)
"""Measured on red pieces: mean hue 0.6-1.8, V 66-77."""
PURPLE_FRAME: Final = hsv_colour(158, 200, 90)
"""Measured on purple pieces: mean hue 157.5-159.0, V 87-93."""
GOLD: Final = hsv_colour(22, 180, 200)
PILL_RED: Final = hsv_colour(0, 220, 230)
PILL_ORANGE: Final = hsv_colour(15, 220, 235)
BACKGROUND: Final = (20, 20, 20)


def fill(image: Image, x0: float, y0: float, x1: float, y1: float, colour: tuple[int, int, int]) -> None:
    image[int(y0) : int(y1), int(x0) : int(x1)] = colour


def paint_piece(image: Image, slot: GearSlot, spec: PieceSpec) -> None:
    """Item icon background, the gold corner ornament of red-style frames and the '+N' pill (measured geometry)."""
    cx, ry = icon_cx(slot), row_y(slot)
    fill(
        image,
        cx - 1.2 * H,
        ry - 0.1 * H,
        cx + 1.2 * H,
        ry + 2.2 * H,
        PURPLE_FRAME if spec.frame == "purple" else RED_FRAME,
    )
    if spec.frame == "red":
        fill(image, cx - 1.3 * H, ry - 0.5 * H, cx - 0.95 * H, ry + 0.2 * H, GOLD)
    if spec.enhance:
        paint_pill(image, slot)


def paint_pill(image: Image, slot: GearSlot) -> None:
    cx, ry = icon_cx(slot), row_y(slot)
    fill(image, cx + 0.37 * H, ry - 0.45 * H, cx + 1.44 * H, ry + 0.23 * H, PILL_RED)


def paint_ink(image: Image, slot: GearSlot, rows: Iterable[int]) -> None:
    """Light grey text-like bars in substat cells (what the missed-row probe looks for)."""
    edge = EDGES[COLUMN[slot][0]]
    for index in rows:
        cy = sub_y(slot, index)
        fill(image, edge - 1.0 * H, cy - 0.2 * H, edge, cy + 0.2 * H, (190, 190, 190))


@dataclass(slots=True)
class Screen:
    image: Image
    lines: dict[str, TextLine] = field(default_factory=dict)

    def without(self, *keys: str) -> list[TextLine]:
        for key in keys:
            assert key in self.lines, key
        return [line for key, line in self.lines.items() if key not in keys]

    def replaced(self, key: str, text: str) -> list[TextLine]:
        return [replace(line, text=text) if k == key else line for k, line in self.lines.items()]


def make_screen(
    pieces: dict[GearSlot, PieceSpec] = STRAZE,
    *,
    average: str = "89",
    artifact: tuple[str, str, str] | None = ("Lv.Max/11", "Daydream Joker", "+30"),
    ee: tuple[str, str] | None = ("12%", "Star Extinction"),
) -> Screen:
    image = np.full((1167, 2000, 3), BACKGROUND, dtype=np.uint8)
    screen = Screen(image)
    lines = screen.lines
    lines["anchor"] = text_line(
        f"Average Equipment Score: {average}", ANCHOR.x0, ANCHOR.cy, ANCHOR.height, x1=ANCHOR.x1
    )
    for label, value, y in (("Attack", "5063", 600.0), ("Critical Hit Chance", "100.0%", 747.0)):  # left stats panel
        lines[f"stats.{label}"] = text_line(label, 98, y, 38)
        lines[f"stats.{label}.value"] = value_line(value, 500, y, 38)
    for slot, spec in pieces.items():
        edge, cx, ry = EDGES[COLUMN[slot][0]], icon_cx(slot), row_y(slot)
        lines[f"{slot}.main"] = value_line(spec.main, edge, ry, 0.9 * H)
        for index, sub in enumerate(spec.subs):
            lines[f"{slot}.sub{index + 1}"] = value_line(sub, edge, sub_y(slot, index), 0.7 * H)
        lines[f"{slot}.level"] = text_line(spec.level, cx - 1.03 * H, ry + 0.15 * H, 0.5 * H, x1=cx - 0.35 * H)
        if spec.enhance:
            lines[f"{slot}.enhance"] = text_line(f"+{spec.enhance}", cx + 0.38 * H, ry - 0.12 * H, 0.7 * H)
        lines[f"{slot}.score"] = text_line(str(spec.score), cx - 0.42 * H, ry + 2.73 * H, 0.75 * H, x1=cx + 0.47 * H)
        lines[f"{slot}.junk"] = text_line("义", cx + 2.1 * H, ry + 1.6 * H, 2.9 * H, score=0.7)  # icons read as glyphs
        paint_piece(image, slot, spec)
    if artifact is not None:
        level, name, plus = artifact
        x0, cy = ANCHOR.x0 + 8.57 * H, ANCHOR.cy - 3.45 * H
        lines["artifact.level"] = text_line(level, x0, cy, 0.65 * H, x1=x0 + 2.5 * H)
        lines["artifact.name"] = text_line(name, x0, cy + 0.52 * H, 0.55 * H)
        if plus:
            lines["artifact.enhance"] = text_line(plus, x0 - 0.73 * H, cy - 0.72 * H, 0.52 * H, x1=x0 + 0.07 * H)
            fill(image, x0 - 0.78 * H, cy - 0.94 * H, x0, cy - 0.41 * H, PILL_RED)
    if ee is not None:
        value, name = ee
        x0, cy = ANCHOR.x0 + 0.8 * H, ANCHOR.cy - 3.6 * H
        lines["ee.value"] = text_line(value, x0, cy, 0.73 * H, x1=x0 + 1.8 * H)
        lines["ee.name"] = text_line(name, x0 - 0.05 * H, cy + 0.52 * H, 0.55 * H)
    return screen


class FakeReader:
    """Stands in for the second OCR passes: answers each call from a script or a function of the call number."""

    def __init__(self, answer: Callable[[int, Image], list[TextLine]] | None = None) -> None:
        self.answer = answer
        self.calls: list[Image] = []

    def read(self, image: Image) -> list[TextLine]:
        self.calls.append(image)
        return self.answer(len(self.calls) - 1, image) if self.answer is not None else []


def scripted(*texts: Sequence[str]) -> FakeReader:
    """Call i returns the texts of texts[i] (nothing after the script ends)."""

    def answer(call: int, _image: Image) -> list[TextLine]:
        chosen = texts[call] if call < len(texts) else ()
        return [text_line(t, 50, 50, 40) for t in chosen]

    return FakeReader(answer)


def parse(
    lines: Sequence[TextLine], image: Image, reader: FakeReader | None = None, language: GameLanguage = GameLanguage.EN
) -> GearPanel:
    return parse_gear_panel(image, lines, reader or FakeReader(), ARTIFACT_NAMES, language)


def texts(piece: GearPiece) -> tuple[str, list[str]]:
    return piece.main.text, [row.text for row in piece.subs]


# ------------------------------------------------------------------------------------------------ synthetic layouts


def test_full_panel_is_read_without_second_passes() -> None:
    screen = make_screen()
    reader = FakeReader()
    panel = parse(list(screen.lines.values()), screen.image, reader)
    assert reader.calls == []  # everything was on the first pass
    assert panel.present and panel.warnings == ()
    assert panel.unit == pytest.approx(H, rel=0.01)
    assert (panel.average_score.value, panel.anchor) == (89, ANCHOR)
    assert set(panel.pieces) == set(GearSlot)
    for slot, spec in STRAZE.items():
        piece = panel.pieces[slot]
        assert texts(piece) == (spec.main, list(spec.subs))
        assert (piece.item_level.value, piece.enhance.value, piece.score.value) == (int(spec.level), 15, spec.score)
        assert piece.frame.value == spec.frame
        assert piece.warnings == ()
        side = COLUMN[slot][0]
        assert piece.main.group == f"{side}.main" and {r.group for r in piece.subs} == {f"{side}.sub"}
    artifact = panel.artifact
    assert artifact is not None
    assert (artifact.code.value, artifact.enhance.value, artifact.level_text.value) == ("ef317", 30, "Lv.Max/11")
    assert artifact.name_text == "Daydream Joker"
    exclusive = panel.exclusive
    assert exclusive is not None and exclusive.name_text == "Star Extinction"
    assert (exclusive.value.text, exclusive.value.value, exclusive.value.percent) == ("12%", 0.12, True)
    assert exclusive.value.group == "ee"


def test_values_are_converted_and_glued_icon_glyphs_dropped() -> None:
    screen = make_screen()
    lines = screen.replaced(f"{GearSlot.RING}.sub1", "义6%")  # the stat icon read as a glyph in front of the value
    panel = parse(lines, screen.image)
    helmet, ring = panel.pieces[GearSlot.HELMET], panel.pieces[GearSlot.RING]
    assert (helmet.main.value, helmet.main.percent) == (2835.0, False)
    assert (ring.main.value, ring.main.percent) == (0.65, True)
    assert (ring.subs[0].text, ring.subs[0].value) == ("6%", 0.06)
    assert ring.subs[0].confidence <= 0.6 < ring.subs[1].confidence  # the glyph may hide a misread digit
    assert [r.value for r in ring.subs[1:]] == [53.0, 0.30, 6.0]
    junk = parse(screen.replaced(f"{GearSlot.RING}.sub2", "1,34"), screen.image).pieces[GearSlot.RING]
    assert [r.text for r in junk.subs] == ["6%", "30%", "6"]  # digits before the value: not a glued glyph


def test_icon_windows_follow_the_stat_icon_contract() -> None:
    screen = make_screen()
    panel = parse(list(screen.lines.values()), screen.image)
    p = ROW_PITCH * H
    for slot, piece in panel.pieces.items():
        edge = EDGES[COLUMN[slot][0]]
        for row in (piece.main, *piece.subs):
            window = row.icon_window
            assert window.x0 == pytest.approx(edge - 4.6 * p, abs=0.5)
            assert window.x1 == pytest.approx(min(row.box.x0, edge - 1.5 * p), abs=0.5)
            assert window.cy == pytest.approx(row.box.cy) and window.height == pytest.approx(1.5 * p, abs=0.5)
        icon = piece.icon_box
        assert icon.x0 < icon_cx(slot) < icon.x1 and icon.y0 < row_y(slot) + H < icon.y1
    exclusive = panel.exclusive
    assert exclusive is not None
    window, box = exclusive.value.icon_window, exclusive.value.box
    assert window.x0 < box.x0 < window.x1 < box.x1  # the EE icon is inside the OCR line box


@pytest.mark.parametrize(
    "empty",
    [
        (GearSlot.WEAPON,),
        (GearSlot.WEAPON, GearSlot.NECKLACE),
        (GearSlot.WEAPON, GearSlot.HELMET, GearSlot.ARMOR),
        (GearSlot.NECKLACE, GearSlot.RING, GearSlot.BOOTS),
        (GearSlot.WEAPON, GearSlot.HELMET, GearSlot.ARMOR, GearSlot.NECKLACE, GearSlot.RING),
    ],
)
def test_empty_slots_are_missing_keys_and_never_shift_the_others(empty: tuple[GearSlot, ...]) -> None:
    pieces = {slot: spec for slot, spec in STRAZE.items() if slot not in empty}
    screen = make_screen(pieces, average="72")
    panel = parse(list(screen.lines.values()), screen.image)
    assert set(panel.pieces) == set(pieces)
    for slot, spec in pieces.items():
        assert texts(panel.pieces[slot]) == (spec.main, list(spec.subs))
    one_column = {COLUMN[slot][0] for slot in pieces} != {"L", "R"}
    assert any("one value column" in w for w in panel.warnings) == one_column


def test_a_missed_main_stat_is_never_replaced_by_a_substat() -> None:
    screen = make_screen()
    reader = FakeReader()  # the second pass reads nothing
    panel = parse(screen.without(f"{GearSlot.HELMET}.main"), screen.image, reader)
    helmet = panel.pieces[GearSlot.HELMET]
    assert (helmet.main.value, helmet.main.confidence) == (None, 0.0)
    assert [r.text for r in helmet.subs] == list(STRAZE[GearSlot.HELMET].subs)
    assert any("main stat unreadable" in w for w in helmet.warnings)
    assert len(reader.calls) == 2  # one cell, two renderings
    assert texts(panel.pieces[GearSlot.ARMOR]) == ("310", list(STRAZE[GearSlot.ARMOR].subs))


def rendered_box(box: Box, edge: float, cy: float, left: float) -> Box:
    """Where an image box lands in the second-pass rendering of the value cell at (edge, cy)."""
    ox, oy = int(edge - left * H), int(cy - PROBE_HALF_HEIGHT * H)
    factor, border = max(MIN_UPSCALE, RENDER_TARGET / H), round(RENDER_BORDER * RENDER_TARGET)
    return Box(
        *((v - o) * factor + border for v, o in zip((box.x0, box.y0, box.x1, box.y1), (ox, oy, ox, oy), strict=True))
    )


def cell_reader(text: str, slot: GearSlot, cy: float, left: float, height: float) -> FakeReader:
    edge = EDGES[COLUMN[slot][0]]
    original = value_line(text, edge, cy, height).box
    box = rendered_box(original, edge, cy, left)
    return FakeReader(lambda _call, _image: [TextLine(text, 0.95, box, ())])


def test_a_missed_main_stat_is_recovered_by_a_second_pass_on_its_cell() -> None:
    screen = make_screen()
    reader = cell_reader("2,835", GearSlot.HELMET, row_y(GearSlot.HELMET), PROBE_LEFT[0], 0.9 * H)
    panel = parse(screen.without(f"{GearSlot.HELMET}.main"), screen.image, reader)
    helmet = panel.pieces[GearSlot.HELMET]
    assert texts(helmet) == ("2,835", list(STRAZE[GearSlot.HELMET].subs))
    assert helmet.main.confidence == pytest.approx(0.7)  # 2 agreeing renderings
    assert any("recovered by a second OCR pass" in w for w in helmet.warnings)


def test_a_substat_cell_without_ink_is_empty_and_not_read() -> None:
    """Heroic +0 pieces have 3 substats (Politis' weapon): the 4th cell has no text ink, no row is invented."""
    screen = make_screen()
    paint_ink(screen.image, GearSlot.RING, range(3))  # the three read rows have ink, the 4th cell none
    reader = FakeReader()
    panel = parse(screen.without(f"{GearSlot.RING}.sub4"), screen.image, reader)
    ring = panel.pieces[GearSlot.RING]
    assert [r.text for r in ring.subs] == ["6%", "53", "30%"] and ring.warnings == ()
    assert reader.calls == []


def test_a_substat_cell_with_ink_is_reread_or_kept_as_review() -> None:
    screen = make_screen()
    paint_ink(screen.image, GearSlot.ARMOR, range(4))
    lines = screen.without(f"{GearSlot.ARMOR}.sub2")
    unreadable = parse(lines, screen.image, FakeReader()).pieces[GearSlot.ARMOR]
    assert [r.value for r in unreadable.subs] == [0.28, None, 0.08, 0.09]
    assert any("substat 2" in w and "REVIEW" in w for w in unreadable.warnings)
    reader = cell_reader("16%", GearSlot.ARMOR, sub_y(GearSlot.ARMOR, 1), PROBE_LEFT[1], 0.7 * H)
    recovered = parse(lines, screen.image, reader).pieces[GearSlot.ARMOR]
    assert [r.text for r in recovered.subs] == ["28%", "16%", "8%", "9%"]
    assert any("substat 2 (16%) recovered" in w for w in recovered.warnings)


def test_item_level_second_pass_needs_two_agreeing_renderings() -> None:
    screen = make_screen()
    lines = screen.without(f"{GearSlot.WEAPON}.level")
    voted = parse(lines, screen.image, scripted(["90"], ["90."], ["70"], [], [])).pieces[GearSlot.WEAPON]
    assert voted.item_level.value == 90 and voted.item_level.confidence == pytest.approx(0.7)
    tie = parse(lines, screen.image, scripted(["90"], ["70"], ["90"], ["70"], [])).pieces[GearSlot.WEAPON]
    assert tie.item_level.value is None and any("item level" in w for w in tie.warnings)
    single = parse(lines, screen.image, scripted(["90"])).pieces[GearSlot.WEAPON]
    assert single.item_level.value is None


def test_odd_first_pass_item_level_is_never_accepted() -> None:
    screen = make_screen()
    lines = screen.replaced(f"{GearSlot.BOOTS}.level", "901")  # the item art read as a trailing 1
    reader = FakeReader()
    boots = parse(lines, screen.image, reader).pieces[GearSlot.BOOTS]
    assert boots.item_level.value is None and "901" in boots.item_level.note
    assert len(reader.calls) == 5  # all five level renderings were tried
    fixed = parse(lines, screen.image, scripted(*[["90"]] * 5)).pieces[GearSlot.BOOTS]
    assert (fixed.item_level.value, fixed.item_level.confidence) == (90, 1.0)


def test_plus_zero_only_when_no_plus_n_is_read_and_no_pill_is_seen() -> None:
    plain = {**STRAZE, GearSlot.WEAPON: replace(STRAZE[GearSlot.WEAPON], enhance=0)}
    screen = make_screen(plain)
    zero = parse(list(screen.lines.values()), screen.image).pieces[GearSlot.WEAPON]
    assert (zero.enhance.value, zero.enhance.confidence) == (0, 0.9)
    paint_pill(screen.image, GearSlot.WEAPON)  # a pill is there but its text was not read
    unread = parse(list(screen.lines.values()), screen.image).pieces[GearSlot.WEAPON]
    assert unread.enhance.value is None and any("'+N'" in w and "REVIEW" in w for w in unread.warnings)
    read = parse(list(screen.lines.values()), screen.image, scripted(["+15"], [], ["+ 15"])).pieces[GearSlot.WEAPON]
    assert read.enhance.value == 15


def test_score_second_pass() -> None:
    screen = make_screen()
    panel = parse(screen.without(f"{GearSlot.RING}.score"), screen.image, scripted(["86"], ["86"]))
    assert panel.pieces[GearSlot.RING].score.value == 86 and panel.warnings == ()


def test_frame_colour_needs_the_colour_and_the_ornament_to_agree() -> None:
    plain_red = {**STRAZE, GearSlot.ARMOR: replace(STRAZE[GearSlot.ARMOR], frame="red-plain")}
    screen = make_screen(plain_red)
    panel = parse(list(screen.lines.values()), screen.image)
    armor = panel.pieces[GearSlot.ARMOR]
    assert armor.frame.value is None and "disagree" in armor.frame.note
    assert any("frame colour" in w for w in armor.warnings)
    assert panel.pieces[GearSlot.HELMET].frame.value == "purple"


@pytest.mark.parametrize(
    ("purple", "coverage", "gold", "expected"),
    [
        (1.0, 0.9, 0.0, "purple"),
        (0.0, 0.9, 0.2, "red"),
        (0.08, 0.8, 0.16, "red"),
        (0.97, 0.9, 0.01, "purple"),
        (0.52, 0.9, 0.2, None),  # Politis' helmet: mixed background with a red-style ornament
        (0.0, 0.9, 0.0, None),  # red background without the ornament
        (1.0, 0.9, 0.2, None),  # purple background with the ornament
        (0.0, 0.9, 0.06, None),  # ornament neither clearly present nor absent
        (0.0, 0.1, 0.2, None),  # background not measurable
    ],
)
def test_decide_frame(purple: float, coverage: float, gold: float, expected: str | None) -> None:
    assert decide_frame(purple, coverage, gold).value == expected


def test_colour_probes_on_drawn_patches() -> None:
    patch = np.full((40, 40, 3), BACKGROUND, dtype=np.uint8)
    assert bright_red_share(patch) == 0.0 and gold_share(patch) == 0.0
    patch[:20] = PILL_RED
    assert bright_red_share(patch) == pytest.approx(0.5)
    orange = np.full((10, 10, 3), PILL_ORANGE, dtype=np.uint8)
    assert bright_red_share(orange) == 0.0 and bright_red_share(orange, hue_max=22) == 1.0
    assert gold_share(np.full((10, 10, 3), GOLD, dtype=np.uint8)) == 1.0
    icon = np.full((104, 107, 3), RED_FRAME, dtype=np.uint8)
    assert frame_votes(icon, H) == pytest.approx((0.0, 1.0))
    icon[:] = PURPLE_FRAME
    assert frame_votes(icon, H) == pytest.approx((1.0, 1.0))
    assert frame_votes(icon[:0], H) == (0.0, 0.0)


def test_average_score_rule_only_warns() -> None:
    screen = make_screen(average="88")  # floor(538 / 6) = 89
    panel = parse(list(screen.lines.values()), screen.image)
    assert panel.average_score.value == 88
    assert [w for w in panel.warnings if "average score 88" in w]


def test_average_score_read_as_a_separate_line() -> None:
    screen = make_screen()
    lines = screen.replaced("anchor", "Average Equipment Score:")
    lines.append(text_line("89", ANCHOR.x1 + 10, ANCHOR.cy, ANCHOR.height))
    panel = parse(lines, screen.image)
    assert panel.average_score.value == 89 and "separate line" in panel.average_score.note
    glued = parse(screen.replaced("anchor", "Average Equipment Score: 1234"), screen.image)
    assert glued.present and glued.average_score.value is None


def test_artifact_name_matching_follows_the_margin_rule() -> None:
    screen = make_screen(artifact=("Lv.Max/6", "Custom-Made PowerAnci", "+15"))  # name cut by the artwork
    artifact = parse(list(screen.lines.values()), screen.image).artifact
    assert artifact is not None and artifact.code.value == "efw42" and 0.85 <= artifact.code.confidence < 1.0
    names = {"Staff of Wisdom A": "ef901", "Staff of Wisdom B": "ef902"}
    screen = make_screen(artifact=("Lv.Max/6", "Staff of Wisdom", "+15"))
    panel = parse_gear_panel(screen.image, list(screen.lines.values()), FakeReader(), names)
    assert panel.artifact is not None and panel.artifact.code.value is None  # two equally close names: REVIEW
    assert any("artifact code" in w for w in panel.warnings)


def test_artifact_plus_n_second_pass_and_plus_zero() -> None:
    screen = make_screen(artifact=("Lv.2/10", "Fan of Light and Dark", ""))
    reader = scripted(["7+4"], ["Z|+4"], [], ["+4"])  # the art in front of the pill read as glyphs
    artifact = parse(list(screen.lines.values()), screen.image, reader).artifact
    assert artifact is not None and (artifact.enhance.value, artifact.code.value) == (4, "efh18")
    zero_screen = make_screen(artifact=("Lv.1/6", "Light and Darkness", ""))
    zero = parse(list(zero_screen.lines.values()), zero_screen.image).artifact
    assert zero is not None and (zero.enhance.value, zero.enhance.confidence) == (0, 0.8)
    x0, cy = ANCHOR.x0 + 8.57 * H, ANCHOR.cy - 3.45 * H
    fill(zero_screen.image, x0 - 0.78 * H, cy - 0.94 * H, x0, cy - 0.41 * H, PILL_ORANGE)
    unread = parse(list(zero_screen.lines.values()), zero_screen.image).artifact
    assert unread is not None and unread.enhance.value is None


def test_artifact_level_text_inconsistent_with_plus_n_lowers_confidence_only() -> None:
    screen = make_screen(artifact=("Lv.Max/8", "Daydream Joker", "+15"))  # the real cap is 6: misread on the art
    panel = parse(list(screen.lines.values()), screen.image)
    artifact = panel.artifact
    assert artifact is not None
    assert (artifact.enhance.value, artifact.level_text.value) == (15, "Lv.Max/8")  # values are never "fixed"
    assert artifact.enhance.confidence <= 0.4 and artifact.level_text.confidence <= 0.2
    assert any("inconsistent" in w for w in panel.warnings)
    cut = make_screen(artifact=("Lv.Max/", "Daydream Joker", "+15"))
    cut_artifact = parse(list(cut.lines.values()), cut.image).artifact
    assert cut_artifact is not None and cut_artifact.level_text.confidence < 0.5 and cut_artifact.enhance.value == 15


def test_missing_artifact_is_a_warning() -> None:
    screen = make_screen(artifact=None)
    panel = parse(list(screen.lines.values()), screen.image)
    assert panel.artifact is None and any("artifact not found" in w for w in panel.warnings)


def test_exclusive_equipment_absence_is_never_silent() -> None:
    no_ee = make_screen(ee=None)
    clean = parse(list(no_ee.lines.values()), no_ee.image)
    assert clean.exclusive is None and clean.warnings == ()
    screen = make_screen()
    missed_value = parse(screen.without("ee.value"), screen.image)
    assert missed_value.exclusive is None
    assert any("Exclusive Equipment area" in w and "Star Extinction" in w for w in missed_value.warnings)
    missed_name = parse(screen.without("ee.name"), screen.image)
    assert missed_name.exclusive is None and any("12%" in w for w in missed_name.warnings)


def test_no_anchor_is_not_a_silent_no_gear() -> None:
    screen = make_screen()
    panel = parse(screen.without("anchor"), screen.image)
    assert not panel.present and panel.pieces == {} and panel.unit == 0.0
    assert any("gear-like text" in w for w in panel.warnings)
    left_only = [line for key, line in screen.lines.items() if key.startswith("stats.")]
    nothing = parse(left_only, screen.image)  # a hero without gear (Closer Charles): only the stats panel
    assert not nothing.present and nothing.warnings == ()


def test_unknown_game_language_is_reported() -> None:
    screen = make_screen()
    panel = parse(list(screen.lines.values()), screen.image, language=GameLanguage.PT)
    assert not panel.present and any("'pt'" in w for w in panel.warnings)


def stray_values(side: str, top: float) -> list[TextLine]:
    """Two value rows of a stat list starting at `top`."""
    return [value_line(t, EDGES[side], top + i * ROW_PITCH * H, 0.7 * H) for i, t in enumerate(("11%", "12%"))]


def test_values_off_the_slot_grid_are_not_placed() -> None:
    screen = make_screen()
    below = parse(
        [*screen.lines.values(), *stray_values("L", row_y(GearSlot.ARMOR) + 2 * PIECE_PITCH * H)], screen.image
    )
    assert set(below.pieces) == set(GearSlot)  # a 4th "row" below the armor
    assert any("not on the slot grid" in w for w in below.warnings)
    no_boots = make_screen({s: p for s, p in STRAZE.items() if s is not GearSlot.BOOTS}, average="91")
    between = stray_values("R", row_y(GearSlot.RING) + 1.45 * PIECE_PITCH * H)  # 0.45 pitch off the boots row
    panel = parse([*no_boots.lines.values(), *between], no_boots.image)
    assert GearSlot.BOOTS not in panel.pieces and any("not on the slot grid" in w for w in panel.warnings)


def test_grey_image_is_accepted() -> None:
    screen = make_screen()
    grey = np.asarray(cv2.cvtColor(screen.image, cv2.COLOR_BGR2GRAY), dtype=np.uint8)
    panel = parse_gear_panel(grey, list(screen.lines.values()), FakeReader(), ARTIFACT_NAMES)
    assert set(panel.pieces) == set(GearSlot)


# ------------------------------------------------------------------------------------------------ the user's captures


@dataclass(frozen=True, slots=True)
class Golden:
    average: int
    artifact: tuple[str, int, str]
    """Code, '+N', level text (Lady of the Scales: 'Lv.Max/6', checked on a 6x zoom)."""
    ee: tuple[str, str] | None
    frames: str
    """Weapon ... boots: R red, P purple, X neither (REVIEW expected); judged by eye on zoomed icons."""
    pieces: tuple[tuple[str, tuple[str, ...], int, int, int], ...]
    """Weapon ... boots: main, substats, item level, '+N', score (ground truth transcribed by eye)."""


GOLDEN: Final = {
    "haru": Golden(
        91,
        ("efw42", 15, "Lv.Max/6"),
        None,
        "RRRRRR",
        (
            ("525", ("231", "9%", "15%", "19%"), 90, 15, 86),
            ("2,765", ("13%", "13%", "12", "14%"), 88, 15, 91),
            ("310", ("17%", "8%", "15", "9%"), 88, 15, 95),
            ("70%", ("25%", "3", "242", "14%"), 90, 15, 84),
            ("65%", ("12", "14%", "9%", "15%"), 90, 15, 95),
            ("45", ("8%", "9%", "32%", "14%"), 90, 15, 95),
        ),
    ),
    "lots": Golden(
        87,
        ("efh19", 15, "Lv.Max/6"),
        None,
        "RRRRRR",
        (
            ("515", ("12%", "11%", "17", "6%"), 88, 15, 88),
            ("2,765", ("12%", "11%", "17", "6%"), 88, 15, 88),
            ("310", ("17%", "20%", "740", "9%"), 90, 15, 86),
            ("65%", ("11%", "11%", "17", "6%"), 88, 15, 88),
            ("65%", ("207", "8%", "4", "14%"), 90, 15, 84),
            ("45", ("21%", "21%", "6%", "14%"), 88, 15, 88),
        ),
    ),
    "ainz": Golden(
        73,
        ("efm30", 4, "Lv.2/10"),
        None,
        "PRRRRP",
        (
            ("500", ("10%", "9%", "15%", "8%"), 85, 15, 72),
            ("2,700", ("4%", "12%", "17%", "12%"), 85, 15, 77),
            ("300", ("375", "9%", "14%", "11%"), 85, 15, 74),
            ("60%", ("6%", "21%", "86", "6"), 85, 15, 71),
            ("60%", ("13%", "25%", "14%", "4"), 85, 15, 84),
            ("40", ("8%", "8%", "711", "10%"), 85, 15, 65),
        ),
    ),
    "straze": Golden(
        89,
        ("ef317", 30, "Lv.Max/11"),
        ("12%", "Star Extinction"),
        "RPRRRP",
        (
            ("525", ("7%", "13%", "13%", "24%"), 90, 15, 92),
            ("2,835", ("24%", "12%", "8%", "10%"), 90, 15, 88),
            ("310", ("28%", "16%", "8%", "9%"), 90, 15, 100),
            ("70%", ("145", "33%", "9%", "6%"), 88, 15, 91),
            ("65%", ("6%", "53", "30%", "6"), 90, 15, 86),
            ("65%", ("10%", "14%", "9", "56"), 90, 15, 81),
        ),
    ),
    "politis": Golden(
        48,
        ("efr34", 0, "Lv.1/6"),
        None,
        "PXRRRR",
        (
            ("100", ("187", "4", "8%"), 85, 0, 24),  # heroic +0: 3 substats (negative control: no 4th row invented)
            ("2,700", ("13%", "6%", "7%", "38%"), 85, 15, 89),
            ("60", ("3", "8%", "5%", "201"), 85, 0, 28),
            ("12%", ("8%", "3", "6%", "6%"), 85, 0, 30),
            ("12%", ("8%", "4", "6%", "6%"), 85, 0, 32),
            ("45", ("99", "229", "41%", "9%"), 88, 15, 90),
        ),
    ),
}
GOLDEN_ARTIFACTS: Final = {  # the five equipped artifacts and their closest catalog names (codes from the catalog)
    "Custom-Made Power Anchor": "efw42",
    "Sole Consolation": "efh19",
    "Staff of Ainz Ooal Gown": "efm30",
    "Daydream Joker": "ef317",
    "Light and Darkness": "efr34",
    "Fan of Light and Dark": "efh18",
    "Wings of Light and Shadow": "efk16",
    "Staff of Wisdom": "ef203",
    "Spear of a New Dawn": "efk15",
    "Dream Scroll": "ef103",
    "Love Potion": "ef402",
    "Cutie Pando": "ef434",
    "Prelude to a New Era": "ef432",
    "XIV. Temperance": "ef422",
}
LEVEL_ABSTENTIONS: Final = {("straze", 0.64, GearSlot.WEAPON)}
"""Item levels the reader may leave to review (never a wrong value): cream digits on bright gold weapon art at the
smallest UI scale, 1 of 5 second-pass renderings agreed."""
SCALES: Final = (0.64, 1.0, 1.28)  # 1.28 x the 2000-px copies = the user's 2560-px window


class CachingReader:
    """The real OCR, memoised per image content: the panels and the line-deletion variants share their crops."""

    def __init__(self) -> None:
        self._reader = RapidOcrReader()
        self._seen: dict[tuple[tuple[int, ...], str], list[TextLine]] = {}

    def read(self, image: Image) -> list[TextLine]:
        key = (image.shape, hashlib.sha1(np.ascontiguousarray(image).tobytes()).hexdigest())
        if key not in self._seen:
            self._seen[key] = self._reader.read(image)
        return self._seen[key]


@cache
def caching_reader() -> CachingReader:
    return CachingReader()


@cache
def capture(name: str, scale: float) -> tuple[Image, tuple[TextLine, ...]]:
    from e7ac.vision.image import load_image

    image = load_image(FIXTURES_DIR / f"screenshots/heroinfo_{name}.webp")
    if scale != 1.0:
        interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=interpolation)
    return image, tuple(caching_reader().read(image))


@cache
def golden_panel(name: str, scale: float) -> GearPanel:
    image, lines = capture(name, scale)
    return parse_gear_panel(image, lines, caching_reader(), GOLDEN_ARTIFACTS)


def _golden_params() -> list[object]:
    return [
        pytest.param(name, scale, marks=pytest.mark.fixtures(f"screenshots/heroinfo_{name}.webp"), id=f"{name}-{scale}")
        for name in GOLDEN
        for scale in SCALES
    ]


@pytest.mark.parametrize(("name", "scale"), _golden_params())
def test_real_capture(name: str, scale: float) -> None:
    golden, panel = GOLDEN[name], golden_panel(name, scale)
    assert panel.present and panel.average_score.value == golden.average
    assert panel.unit / scale == pytest.approx(44.0, abs=1.0)
    assert list(panel.pieces) == list(GearSlot)
    for slot, (main, subs, level, enhance, score), frame in zip(GearSlot, golden.pieces, golden.frames, strict=True):
        piece = panel.pieces[slot]
        assert texts(piece) == (main, list(subs)), slot
        assert (piece.enhance.value, piece.score.value) == (enhance, score), slot
        if (name, scale, slot) in LEVEL_ABSTENTIONS:
            assert piece.item_level.value in (None, level), slot
        else:
            assert piece.item_level.value == level, slot
        assert piece.frame.value == {"R": "red", "P": "purple", "X": None}[frame], (slot, piece.frame.note)
        review = piece.frame.value is None or piece.item_level.value is None
        assert bool(piece.warnings) == review, (slot, piece.warnings)  # a warning exactly when a field is open
    code, enhance, level_text = golden.artifact
    artifact = panel.artifact
    assert artifact is not None
    assert (artifact.code.value, artifact.enhance.value) == (code, enhance)
    assert artifact.level_text.value == level_text or artifact.level_text.confidence < 0.5  # cap drawn on the art
    if golden.ee is None:
        assert panel.exclusive is None
    else:
        assert panel.exclusive is not None
        assert (panel.exclusive.value.text, panel.exclusive.name_text) == golden.ee
        assert panel.exclusive.value.value == pytest.approx(0.12)
    unexpected = [w for w in panel.warnings if not ("inconsistent" in w and name == "lots")]
    assert unexpected == []


@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.fixtures("screenshots/heroinfo_charles.webp")
def test_real_capture_without_gear(scale: float) -> None:
    panel = golden_panel("charles", scale)
    assert (panel.present, panel.pieces, panel.artifact, panel.exclusive, panel.warnings) == (False, {}, None, None, ())


def _piece_lines(panel: GearPanel, slot: GearSlot, lines: Sequence[TextLine]) -> set[int]:
    """Indices of the OCR lines of one piece: its values and its icon block (level, '+N', score)."""
    piece, h = panel.pieces[slot], panel.unit
    icon = piece.icon_box
    region = Box(icon.x0 - 0.3 * h, icon.y0 - 0.6 * h, piece.main.box.x1 + 0.2 * h, icon.y1 + 1.3 * h)
    return {
        i
        for i, line in enumerate(lines)
        if region.x0 <= (line.box.x0 + line.box.x1) / 2 <= region.x1 and region.y0 <= line.box.cy <= region.y1
    }


def _index(lines: Sequence[TextLine], box: Box) -> int:
    return next(i for i, line in enumerate(lines) if line.box == box)


@pytest.mark.parametrize(
    "name", [pytest.param(n, marks=pytest.mark.fixtures(f"screenshots/heroinfo_{n}.webp")) for n in GOLDEN]
)
def test_real_capture_with_ocr_misses(name: str) -> None:
    """OCR lines deleted (the image is unchanged): empty slots never shift the others, a missed main stat is re-read
    or left to review (never a substat), a missed substat is re-read or kept as a REVIEW row."""
    golden, base = GOLDEN[name], golden_panel(name, 1.0)
    image, lines = capture(name, 1.0)
    truth = dict(zip(GearSlot, golden.pieces, strict=True))

    def run(drop: set[int]) -> GearPanel:
        return parse_gear_panel(
            image, [ln for i, ln in enumerate(lines) if i not in drop], caching_reader(), GOLDEN_ARTIFACTS
        )

    empty = run(_piece_lines(base, GearSlot.WEAPON, lines) | _piece_lines(base, GearSlot.NECKLACE, lines))
    assert set(empty.pieces) == set(GearSlot) - {GearSlot.WEAPON, GearSlot.NECKLACE}
    for slot, piece in empty.pieces.items():
        assert texts(piece) == (truth[slot][0], list(truth[slot][1])), slot

    missed_main = run({_index(lines, base.pieces[GearSlot.HELMET].main.box)}).pieces[GearSlot.HELMET]
    assert missed_main.main.text in (truth[GearSlot.HELMET][0], "")
    assert [r.text for r in missed_main.subs] == list(truth[GearSlot.HELMET][1]) and missed_main.warnings

    ring = base.pieces[GearSlot.RING]
    missed_sub = run({_index(lines, ring.subs[-1].box)}).pieces[GearSlot.RING]
    full = list(truth[GearSlot.RING][1])
    assert [r.text for r in missed_sub.subs] in (full, [*full[:-1], ""]) and missed_sub.warnings
