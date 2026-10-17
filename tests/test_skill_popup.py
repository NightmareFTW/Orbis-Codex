"""The skill popup reader (SPEC D91): synthetic OCR lines for the parser, and the user's own captures (cropped and full
window, local fixtures only, never committed; skipped when missing) read end to end."""

from __future__ import annotations

from functools import cache

import numpy as np
import pytest
from PIL import Image

from e7ac.vision.ocr import Box, RapidOcrReader, TextLine
from e7ac.vision.skill_popup import SkillPopup, clean, parse_skill_popup, read_skill_popup
from tests.markers import FIXTURES_DIR

SKILLS = "screenshots/skills"


def line(text: str, x: float, y: float, w: float = 300, h: float = 48, score: float = 1.0) -> TextLine:
    return TextLine(text, score, Box(x, y, x + w, y + h), ())


def synthetic() -> list[TextLine]:
    """A popup laid out like the game's: title right of its +N badge, cost row, description, soulburn, enhancement
    steps, and two effect panels on the right (one with an indented title and a wrapped mark, one without an icon)."""
    return [
        line("Lady of the Scales", 0, 0, 650, 100),
        line("Lv. Max/60", 0, 150, 300, 70),
        line("+5", 560, 100, 45, 32),
        line("The Caress of Mercy", 620, 105, 330, 60),
        line("Acquire 3 Soul", 620, 200, 240, 50),
        line("4 turns", 960, 200, 120, 46),
        line("With the Goddess's authority, dispels all", 455, 262, 650),
        line("debuffs from all allies and increases", 455, 305, 640),
        line("Combat Readiness by 25%.", 456, 348, 520),
        line("Soulburn Effect", 455, 480, 240),
        line("Consume 10 Soul", 840, 480, 260),
        line("Greatly recovers Health.", 456, 530, 500),
        line("Skill Enhance", 456, 640, 240),
        line("+1", 456, 700, 50, 38),
        line("+2% Combat Readiness", 530, 695, 420),
        line("+2", 456, 745, 50, 38),
        line("-1 turn cooldown", 530, 740, 380),
        line("+3 +3% Combat Readiness", 456, 790, 560),  # a row the OCR read as one line
        line("Promotion", 800, 1000, 150),  # a tab label of the screen
        line("Offering (Unique Effect)", 1262, 110, 410, 56),
        line("Grants 70% damage sharing of", 1178, 184, 500),
        line("damage received by all allies.", 1179, 226, 480),
        line("Lingering Fragrance", 1262, 330, 360, 54),
        line("(Undispellable)", 1264, 370, 270, 50),
        line("Increases max Health by 5%.", 1180, 430, 560),
        line("Penetrate", 1180, 560, 180),
        line("Ignores the target's Defense when", 1180, 612, 560),
        line("inflicting damage.", 1180, 652, 300),
        line("Cancel", 1404, 25, 110, 42),  # UI of the screen, far above the panels
    ]


def test_the_parser_reads_every_part_of_a_popup() -> None:
    popup = parse_skill_popup(synthetic())
    assert popup is not None
    assert (popup.hero, popup.name, popup.level) == ("Lady of the Scales", "The Caress of Mercy", 5)
    assert popup.gains == (("Soul", 3),) and popup.cooldown == 4 and popup.cooldown_note == ""
    assert popup.text == (
        "With the Goddess's authority, dispels all debuffs from all allies and increases Combat Readiness by 25%."
    )
    assert popup.soulburn == "Greatly recovers Health." and popup.soulburn_cost == 10
    assert popup.enhance == ((1, "+2% Combat Readiness"), (2, "-1 turn cooldown"), (3, "+3% Combat Readiness"))
    assert [(p.title, p.marks) for p in popup.effects] == [
        ("Offering", ("Unique Effect",)),
        ("Lingering Fragrance", ("Undispellable",)),
        ("Penetrate", ()),
    ]
    assert popup.effects[0].text == "Grants 70% damage sharing of damage received by all allies."
    assert popup.effects[2].text == "Ignores the target's Defense when inflicting damage."  # no icon, no indent
    assert popup.confidence == 1.0


def test_too_little_text_is_not_a_popup() -> None:
    assert parse_skill_popup(synthetic()[:4]) is None
    bare = [ln for ln in synthetic() if ln.text not in ("+5", "Acquire 3 Soul")]
    assert (
        parse_skill_popup(bare) is None
    )  # no badge and no cost row to find the title by: an enhancement step is not one


def test_a_skill_without_a_level_badge_is_found_by_its_cost_row() -> None:
    popup = parse_skill_popup([ln for ln in synthetic() if ln.text != "+5"])
    assert popup is not None and popup.name == "The Caress of Mercy" and popup.level is None
    assert popup.gains == (("Soul", 3),) and popup.cooldown == 4 and len(popup.enhance) == 3


def test_a_passive_with_a_named_cooldown_has_no_cooldown_of_its_own() -> None:
    lines = [ln for ln in synthetic() if ln.text != "4 turns"]
    lines.append(line("La Vie en Rose 3 Turns", 700, 200, 400, 46))
    popup = parse_skill_popup(lines)
    assert popup is not None and popup.cooldown is None and popup.cooldown_note == "La Vie en Rose 3 Turns"


def test_a_step_whose_badge_the_ocr_missed_is_numbered_after_the_one_before() -> None:
    lines = [ln for ln in synthetic() if ln.text not in ("+2", "-1 turn cooldown")]
    lines.append(line("+15% damage dealt", 530, 740, 380))
    popup = parse_skill_popup(lines)
    assert popup is not None
    assert popup.enhance == ((1, "+2% Combat Readiness"), (2, "+15% damage dealt"), (3, "+3% Combat Readiness"))


def test_the_ocr_diacritics_are_removed_and_quotes_straightened() -> None:
    assert clean("Îmmortality can only be activated  once") == "Immortality can only be activated once"
    assert clean(f"caster{chr(0x2019)}s max Health") == "caster's max Health"


# Transcribed by eye from the user's captures (2026-10-17/18): file -> hero, skill, level, cooldown, effect panels.
GOLDEN: dict[str, tuple[str | None, str, int, int | None, tuple[str, ...]]] = {
    "lady_s1_crop": (None, "Divine Punishment", 5, None, ("Fear",)),
    "lady_s2_crop": ("Lady of the Scales", "Scales of Equity", 5, None, ("Offering", "Immortal")),
    "lady_s3_crop": ("Lady of the Scales", "The Caress of Mercy", 5, 4, ("Offering", "Abundance")),
    "arunka_s1_crop": ("Boss Arunka", "Road Sign Smash", 6, None, ("Decrease Defense",)),
    "arunka_s2_crop": ("Boss Arunka", "Vanguard of the Silent Expanse", 3, None, ("Ferocious Stand",)),
    "arunka_s3_crop": (
        "Boss Arunka",
        "Coruscated Trajectory",
        6,
        3,
        ("Decrease Defense", "Increase Defense", "Barrier"),
    ),
    "byblis_s1_crop": ("Perfumer Byblis", "Rose Breeze", 5, None, ("Decrease Defense",)),
    "byblis_s2_crop": ("Perfumer Byblis", "Elixir de Belle Nuit", 5, None, ("Lingering Fragrance", "Barrier")),
    "byblis_s3_crop": (
        "Perfumer Byblis",
        "Aura de la Mort",
        5,
        3,
        ("Increase Effectiveness", "Redirected Provoke", "Venom", "Detonate"),
    ),
    "rinak_s1_crop": ("Rinak", "Vital Point Slash", 5, None, ("Penetrate",)),
    "rinak_s2_crop": ("Rinak", "Pickpocketing", 5, 3, ("Pilfer", "Spoils", "Increase Attack", "Extra Turn")),
    "rinak_s3_crop": ("Rinak", "Clumsy Finish", 5, 4, ("Penetrate", "Stun")),
    "chloe_full_a": ("Maid Chloe", "VIP Treatment", 1, 4, ("Revive", "Increase Defense")),
    "mort_full_b": ("Mort", "Advent: Mortelix", 5, 4, ("Fear", "Penetrate")),
    "harsetti_full_s1": ("Harsett", "Gambit", 7, None, ("Decrease Defense", "Decrease Attack")),
    "harsetti_full_s3": (
        "Harsetti",
        "Checkmate",
        8,
        4,
        ("Cannot Counterattack", "Cannot Buff", "Penetrate"),
    ),
}


@cache
def popup_of(name: str) -> SkillPopup | None:
    path = FIXTURES_DIR / SKILLS / f"{name}.webp"
    return read_skill_popup(np.array(Image.open(path).convert("RGB")), RapidOcrReader())


@pytest.mark.fixtures(f"{SKILLS}/lady_s2_crop.webp")
@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_the_users_captures_are_read_end_to_end(name: str) -> None:
    if not (FIXTURES_DIR / SKILLS / f"{name}.webp").exists():
        pytest.skip(f"fixture {name} missing")
    hero, skill, level, cooldown, effects = GOLDEN[name]
    popup = popup_of(name)
    assert popup is not None
    assert (popup.hero, popup.name, popup.level, popup.cooldown) == (hero, skill, level, cooldown)
    assert tuple(p.title for p in popup.effects) == effects
    assert popup.text and popup.confidence > 0.7


@pytest.mark.fixtures(f"{SKILLS}/lady_s3_crop.webp", f"{SKILLS}/rinak_s3_crop.webp", f"{SKILLS}/harsetti_full_s3.webp")
def test_numbers_and_enhancement_steps_come_out_exact() -> None:
    lady = popup_of("lady_s3_crop")
    assert (
        lady is not None
        and "increases Combat Readiness by 25%" in lady.text
        and "20% of the current Health" in lady.text
    )
    assert [e.text for e in lady.effects][1] == "After attacking, inflicts 2,000 additional damage on the target."
    assert lady.enhance[2] == (3, "-1 turn cooldown") and len(lady.enhance) == 5
    rinak = popup_of("rinak_s3_crop")
    assert rinak is not None and "deals 7,000 additional damage to all enemies" in rinak.text
    harsetti = popup_of("harsetti_full_s3")  # its last rows lost their "+N" badges in the OCR
    assert harsetti is not None and [level for level, _ in harsetti.enhance] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert harsetti.enhance[3] == (4, "-1 turn cooldown") and harsetti.enhance[7] == (8, "+10% damage dealt")
    assert harsetti.gains == (("Focus", 2), ("Soul", 3))


@pytest.mark.fixtures(f"{SKILLS}/harsetti_full_c.webp")
def test_a_screen_without_a_popup_is_not_read_as_one() -> None:
    assert popup_of("harsetti_full_c") is None
