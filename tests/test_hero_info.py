"""Hero Info image readers end to end (gear, stat icons, sets, artifact, EE, imprint icon, stars) on the user's
captures (local fixtures only, never committed; skipped when missing). Each reader has its own unit tests."""

from __future__ import annotations

from functools import cache

import pytest

from e7ac.domain.codes import Stat
from e7ac.domain.roster import GearSlot, ImprintGrade, ImprintMode
from e7ac.vision.hero_info import HeroImageReading, read_hero_images, stat_of
from tests.markers import FIXTURES_DIR

SET_ICONS = "stove/set_icons"
# Transcribed by eye from the user's captures (2026-10-04); sets checked against the CP-row icons and the Equipment
# tab's set names. slot, item level, +N, score, frame colour (None = ambiguous: review), set, main, substats.
type Piece = tuple[str, int, int, int, str | None, str, tuple[str, float], list[tuple[str, float]]]
GOLDEN_GEAR: dict[str, list[Piece]] = {
    "haru": [
        (
            "weapon",
            90,
            15,
            86,
            "red",
            "set_cri_dmg",
            ("att", 525.0),
            [("max_hp", 231.0), ("cri_dmg", 0.09), ("res", 0.15), ("cri", 0.19)],
        ),
        (
            "helmet",
            88,
            15,
            91,
            "red",
            "set_cri_dmg",
            ("max_hp", 2765.0),
            [("max_hp_rate", 0.13), ("def_rate", 0.13), ("speed", 12.0), ("cri_dmg", 0.14)],
        ),
        (
            "armor",
            88,
            15,
            95,
            "red",
            "set_cri_dmg",
            ("def", 310.0),
            [("max_hp_rate", 0.17), ("def_rate", 0.08), ("speed", 15.0), ("cri", 0.09)],
        ),
        (
            "necklace",
            90,
            15,
            84,
            "red",
            "set_cri_dmg",
            ("cri_dmg", 0.7),
            [("max_hp_rate", 0.25), ("speed", 3.0), ("max_hp", 242.0), ("cri", 0.14)],
        ),
        (
            "ring",
            90,
            15,
            95,
            "red",
            "set_max_hp",
            ("max_hp_rate", 0.65),
            [("speed", 12.0), ("cri_dmg", 0.14), ("cri", 0.09), ("res", 0.15)],
        ),
        (
            "boots",
            90,
            15,
            95,
            "red",
            "set_max_hp",
            ("speed", 45.0),
            [("cri_dmg", 0.08), ("cri", 0.09), ("att_rate", 0.32), ("max_hp_rate", 0.14)],
        ),
    ],
    "lots": [
        (
            "weapon",
            88,
            15,
            88,
            "red",
            "set_speed",
            ("att", 515.0),
            [("att_rate", 0.12), ("max_hp_rate", 0.11), ("speed", 17.0), ("acc", 0.06)],
        ),
        (
            "helmet",
            88,
            15,
            88,
            "red",
            "set_speed",
            ("max_hp", 2765.0),
            [("att_rate", 0.12), ("max_hp_rate", 0.11), ("speed", 17.0), ("acc", 0.06)],
        ),
        (
            "armor",
            90,
            15,
            86,
            "red",
            "set_immune",
            ("def", 310.0),
            [("max_hp_rate", 0.17), ("def_rate", 0.2), ("max_hp", 740.0), ("acc", 0.09)],
        ),
        (
            "necklace",
            88,
            15,
            88,
            "red",
            "set_speed",
            ("max_hp_rate", 0.65),
            [("att_rate", 0.11), ("def_rate", 0.11), ("speed", 17.0), ("acc", 0.06)],
        ),
        (
            "ring",
            90,
            15,
            84,
            "red",
            "set_immune",
            ("max_hp_rate", 0.65),
            [("att", 207.0), ("acc", 0.08), ("speed", 4.0), ("cri", 0.14)],
        ),
        (
            "boots",
            88,
            15,
            88,
            "red",
            "set_speed",
            ("speed", 45.0),
            [("att_rate", 0.21), ("max_hp_rate", 0.21), ("def_rate", 0.06), ("acc", 0.14)],
        ),
    ],
    "ainz": [
        (
            "weapon",
            85,
            15,
            72,
            "purple",
            "set_speed",
            ("att", 500.0),
            [("cri", 0.1), ("acc", 0.09), ("att_rate", 0.15), ("max_hp_rate", 0.08)],
        ),
        (
            "helmet",
            85,
            15,
            77,
            "red",
            "set_speed",
            ("max_hp", 2700.0),
            [("acc", 0.04), ("cri", 0.12), ("max_hp_rate", 0.17), ("att_rate", 0.12)],
        ),
        (
            "armor",
            85,
            15,
            74,
            "red",
            "set_acc",
            ("def", 300.0),
            [("max_hp", 375.0), ("cri", 0.09), ("cri_dmg", 0.14), ("acc", 0.11)],
        ),
        (
            "necklace",
            85,
            15,
            71,
            "red",
            "set_speed",
            ("max_hp_rate", 0.6),
            [("acc", 0.06), ("att_rate", 0.21), ("att", 86.0), ("speed", 6.0)],
        ),
        (
            "ring",
            85,
            15,
            84,
            "red",
            "set_speed",
            ("acc", 0.6),
            [("def_rate", 0.13), ("res", 0.25), ("att_rate", 0.14), ("speed", 4.0)],
        ),
        (
            "boots",
            85,
            15,
            65,
            "purple",
            "set_acc",
            ("speed", 40.0),
            [("att_rate", 0.08), ("max_hp_rate", 0.08), ("max_hp", 711.0), ("cri_dmg", 0.1)],
        ),
    ],
    "straze": [
        (
            "weapon",
            90,
            15,
            92,
            "red",
            "set_rage",
            ("att", 525.0),
            [("acc", 0.07), ("cri_dmg", 0.13), ("cri", 0.13), ("att_rate", 0.24)],
        ),
        (
            "helmet",
            90,
            15,
            88,
            "purple",
            "set_rage",
            ("max_hp", 2835.0),
            [("att_rate", 0.24), ("cri", 0.12), ("acc", 0.08), ("cri_dmg", 0.1)],
        ),
        (
            "armor",
            90,
            15,
            100,
            "red",
            "set_rage",
            ("def", 310.0),
            [("cri_dmg", 0.28), ("cri", 0.16), ("acc", 0.08), ("res", 0.09)],
        ),
        (
            "necklace",
            88,
            15,
            91,
            "red",
            "set_cri",
            ("cri_dmg", 0.7),
            [("att", 145.0), ("att_rate", 0.33), ("max_hp_rate", 0.09), ("cri", 0.06)],
        ),
        (
            "ring",
            90,
            15,
            86,
            "red",
            "set_cri",
            ("att_rate", 0.65),
            [("cri", 0.06), ("att", 53.0), ("cri_dmg", 0.3), ("speed", 6.0)],
        ),
        (
            "boots",
            90,
            15,
            81,
            "purple",
            "set_rage",
            ("att_rate", 0.65),
            [("cri", 0.1), ("cri_dmg", 0.14), ("speed", 9.0), ("att", 56.0)],
        ),
    ],
    "politis": [
        (
            "weapon",
            85,
            0,
            24,
            "purple",
            "set_speed",
            ("att", 100.0),
            [("max_hp", 187.0), ("speed", 4.0), ("att_rate", 0.08)],
        ),
        (
            "helmet",
            85,
            15,
            89,
            None,
            "set_chase",
            ("max_hp", 2700.0),
            [("def_rate", 0.13), ("att_rate", 0.06), ("max_hp_rate", 0.07), ("res", 0.38)],
        ),
        (
            "armor",
            85,
            0,
            28,
            "red",
            "set_speed",
            ("def", 60.0),
            [("speed", 3.0), ("max_hp_rate", 0.08), ("res", 0.05), ("max_hp", 201.0)],
        ),
        (
            "necklace",
            85,
            0,
            30,
            "red",
            "set_speed",
            ("max_hp_rate", 0.12),
            [("def_rate", 0.08), ("speed", 3.0), ("res", 0.06), ("acc", 0.06)],
        ),
        (
            "ring",
            85,
            0,
            32,
            "red",
            "set_speed",
            ("max_hp_rate", 0.12),
            [("def_rate", 0.08), ("speed", 4.0), ("res", 0.06), ("acc", 0.06)],
        ),
        (
            "boots",
            88,
            15,
            90,
            "red",
            "set_chase",
            ("speed", 45.0),
            [("att", 99.0), ("max_hp", 229.0), ("att_rate", 0.41), ("max_hp_rate", 0.09)],
        ),
    ],
}
ARTIFACT_NAMES = {
    "Custom-Made Power Anchor": "efw42",
    "Sole Consolation": "efh19",
    "Staff of Ainz Ooal Gown": "efm30",
    "Daydream Joker": "ef317",
    "Light and Darkness": "efr34",
}
# active-set icons of the CP row (sorted), as the completed sets of the pieces
ACTIVE_SETS = {
    "haru": ["set_cri_dmg", "set_max_hp"],
    "lots": ["set_immune", "set_speed"],
    "ainz": ["set_acc", "set_speed"],
    "straze": ["set_cri", "set_rage"],
    "politis": ["set_chase", "set_speed"],
}
# artifact code, +N, EE (stat, value), imprint (mode, grade), stars, awakened
GOLDEN_REST = {
    "haru": ("efw42", 15, None, (ImprintMode.TEAM, ImprintGrade.B), 6, 6),
    "lots": ("efh19", 15, None, (ImprintMode.TEAM, ImprintGrade.SSS), 6, 6),
    "ainz": ("efm30", 4, None, (ImprintMode.SELF, ImprintGrade.SSS), 6, 6),
    "straze": ("ef317", 30, (Stat.CRIT_CHANCE, 0.12), (ImprintMode.SELF, ImprintGrade.SSS), 6, 6),
    "politis": ("efr34", 0, None, (ImprintMode.TEAM, ImprintGrade.B), 5, 1),
}


def test_a_value_means_a_stat_only_when_icon_and_format_agree() -> None:
    assert stat_of(Stat.ATK, percent=True) == (Stat.ATK_PERCENT, "")
    assert stat_of(Stat.HP, percent=False) == (Stat.HP, "")
    assert stat_of(Stat.SPEED, percent=False) == (Stat.SPEED, "")
    assert stat_of(Stat.CRIT_DAMAGE, percent=True) == (Stat.CRIT_DAMAGE, "")
    for family, percent in ((Stat.SPEED, True), (Stat.CRIT_CHANCE, False), (Stat.DUAL_ATTACK, True)):
        stat, why = stat_of(family, percent)
        assert stat is None and why  # sent to review, never remapped


@cache
def _read(name: str) -> HeroImageReading:
    from e7ac.vision.hero_screen import parse_hero_screen
    from e7ac.vision.image import load_image
    from e7ac.vision.ocr import RapidOcrReader
    from e7ac.vision.sets import SetIconMatcher

    icons = {p.stem: p.read_bytes() for p in (FIXTURES_DIR / SET_ICONS).glob("set_*.png")}
    image = load_image(FIXTURES_DIR / f"screenshots/heroinfo_{name}.webp")
    reader = RapidOcrReader()
    lines = reader.read(image)
    screen = parse_hero_screen(lines)
    matcher = SetIconMatcher.from_png(icons)
    return read_hero_images(image, lines, screen, reader, artifact_names=ARTIFACT_NAMES, set_matcher=matcher)


def _marks(name: str) -> pytest.MarkDecorator:
    return pytest.mark.fixtures(f"screenshots/heroinfo_{name}.webp", f"{SET_ICONS}/set_speed.png")


@pytest.mark.parametrize("name", [pytest.param(n, marks=_marks(n)) for n in sorted(GOLDEN_GEAR)])
def test_real_gear(name: str) -> None:
    reading = _read(name)
    for slot_name, level, enhance, score, frame, set_code, main, subs in GOLDEN_GEAR[name]:
        piece = reading.pieces[GearSlot(slot_name)]
        assert (piece.item_level.value, piece.enhance.value, piece.score.value) == (level, enhance, score), slot_name
        assert piece.frame.value == frame, slot_name
        assert piece.set_match is not None and piece.set_match.set_code == set_code, slot_name
        read = [(r.stat.value if r.stat else None, r.value) for r in (piece.main, *piece.subs)]
        assert read == [main, *subs], slot_name


@pytest.mark.parametrize("name", [pytest.param(n, marks=_marks(n)) for n in sorted(GOLDEN_REST)])
def test_real_artifact_ee_imprint_and_stars(name: str) -> None:
    reading = _read(name)
    code, enhance, ee, imprint, stars, awakened = GOLDEN_REST[name]
    artifact = reading.panel.artifact
    assert artifact is not None and (artifact.code.value, artifact.enhance.value) == (code, enhance)
    exclusive = reading.exclusive
    assert (None if exclusive is None else (exclusive.stat, exclusive.value)) == ee
    assert (reading.imprint_icon.mode, reading.imprint_icon.grade) == imprint
    assert (reading.stars.stars, reading.stars.awakened) == (stars, awakened)
    assert sorted(m.set_code or "?" for m in reading.active_sets) == ACTIVE_SETS[name]


@pytest.mark.fixtures("screenshots/heroinfo_haru.webp", f"{SET_ICONS}/set_speed.png")
def test_a_capture_cut_through_the_gear_values_is_never_read_as_whole() -> None:
    from e7ac.vision.hero_screen import parse_hero_screen
    from e7ac.vision.image import load_image
    from e7ac.vision.ocr import RapidOcrReader

    image = load_image(FIXTURES_DIR / "screenshots/heroinfo_haru.webp")
    cut = image[:, : int(image.shape[1] * 0.965)].copy()  # the right column ends at the border: "242" -> "24"
    reader = RapidOcrReader()
    lines = reader.read(cut)
    reading = read_hero_images(cut, lines, parse_hero_screen(lines), reader, artifact_names={}, set_matcher=None)
    for slot in (GearSlot.NECKLACE, GearSlot.RING, GearSlot.BOOTS):
        piece = reading.pieces.get(slot)
        assert piece is None or all(row.value is None for row in (piece.main, *piece.subs)), slot
