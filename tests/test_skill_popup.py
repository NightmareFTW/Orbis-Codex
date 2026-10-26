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
    found = parse_skill_popup(bare)  # no badge and no cost row: the title is the first line above the description
    assert found is not None and found.name == "The Caress of Mercy" and found.level is None
    no_label = [ln for ln in bare if ln.text != "Skill Enhance"]
    assert parse_skill_popup(no_label) is None  # without the popup's own label nothing says where its column is


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


def celine_passive() -> list[TextLine]:
    """The popup of Spirit Eye Celine's Sixth Sense (a passive at +0: no "+N" badge, no "Acquire" row; its note
    "Effect(s) upon death 1 turn" sits right under the title), with the boxes the OCR gave on the user's own capture
    (2026-10-26, 925 x 878)."""
    steps = ["-2%", "-2%", "-2%", "-3%", "-3%", "-3%", "-4%"]
    rows = [
        line("Light", 51, 26, 82, 41),
        line("ThiefSagittarius", 160, 25, 262, 44),
        line("Spirit Eye Celine", 10, 59, 410, 80),
        line("Lv. Max/50", 16, 172, 230, 51),
        line("Sixth Sense", 458, 200, 169, 41),
        line("Effect(s) upon death 1 turn", 482, 266, 302, 37),
        line("Effect", 122, 292, 75, 34),
        line("Resistance + 9%", 122, 319, 188, 34),
        line("When attacked, damage suffered in one", 342, 312, 453, 34),
        line("attack does not exceed 70% of max", 342, 342, 413, 31),
        line("Health. When someone dies, decreases", 341, 371, 450, 30),
        line("skill cooldowns by 1 turn and gains 5", 339, 397, 426, 39),
        line("Soul. Effects upon death are only", 340, 427, 378, 36),
        line("activated upon a Hero's death.", 340, 457, 352, 36),
        line("Skill Enhance", 340, 525, 171, 40),
        line("Move to Grace of Growt", 73, 548, 243, 30),
        line("Skill", 632, 831, 59, 39),
        line("Promotion", 746, 846, 129, 31),
    ]
    for number, step in enumerate(steps, 1):
        y = 565 + 33.5 * (number - 1)
        rows += [line(f"+{number}", 345, y + 5, 32, 27), line(f"{step} damage received limit", 394, y, 292, 36)]
    return rows


def test_a_passive_at_plus_zero_is_found_by_the_line_above_its_description() -> None:
    popup = parse_skill_popup(celine_passive())
    assert popup is not None
    assert (popup.hero, popup.name, popup.level, popup.cooldown) == ("Spirit Eye Celine", "Sixth Sense", None, None)
    assert popup.cooldown_note == "Effect(s) upon death 1 turn" and popup.gains == ()
    assert popup.text.startswith("When attacked, damage suffered in one attack does not exceed 70% of max Health.")
    assert [level for level, _ in popup.enhance] == [1, 2, 3, 4, 5, 6, 7]
    assert popup.enhance[0][1] == "-2% damage received limit" and popup.enhance[6][1] == "-4% damage received limit"


def test_text_to_the_right_of_a_popup_is_never_taken_for_its_title() -> None:
    """On a full window the screen's own buttons are far right and high: still not a title."""
    rows = [*celine_passive(), line("Cancel", 1404, 30, 110, 42)]
    popup = parse_skill_popup(rows)
    assert popup is not None and popup.name == "Sixth Sense"


@pytest.mark.fixtures(f"{SKILLS}/celine_s2_passive.webp")
def test_the_users_own_passive_is_read_end_to_end() -> None:
    popup = popup_of("celine_s2_passive")
    assert popup is not None
    assert (popup.hero, popup.name, popup.level) == ("Spirit Eye Celine", "Sixth Sense", None)
    assert popup.cooldown_note == "Effect(s) upon death 1 turn" and "70% of max Health" in popup.text
    assert [text for _, text in popup.enhance][::3] == [
        "-2% damage received limit",
        "-3% damage received limit",
        "-4% damage received limit",
    ]


def harsetti_passive() -> list[TextLine]:
    """The popup of Harsetti's passive, The Law of Skuggiheim, as laid out in the user's capture (2026-10-26, 1140 x
    746; the boxes are read off the picture, not an OCR dump): a title right of the icon and a paragraph. It cannot
    be enhanced, so it has no "+N" badge, no cost row, no "Skill Enhance" and no soulburn."""
    text = [
        "At the start of battle, Speed of the caster",
        "is fixed going forward, and Speed of all",
        "Heroes except for the caster is limited to",
        "a maximum of 90% of the caster's",
        "Speed. On the caster's turn, Combat",
        "Readiness increase effects of all Heroes",
        "is not applied. The Law of Skuggiheim",
        "only applies to PvP.",
    ]
    return [
        line("Dark", 60, 30, 70, 36),
        line("MageLibra", 180, 28, 170, 40),
        line("Harsetti", 18, 66, 195, 60),
        line("Lv. Max/60", 18, 172, 222, 48),
        line("Health + 21%", 125, 308, 143, 28),
        line("The Law of Skuggiheim", 460, 313, 317, 40),
        *[line(row, 343, 428 + 30.5 * i, 450, 30) for i, row in enumerate(text)],
        line("Imprint Circuit: Takeoff Applied", 75, 550, 240, 26),
        line("Skill", 632, 860, 59, 39),
        line("Promotion", 746, 880, 129, 31),
    ]


def test_a_passive_that_cannot_be_enhanced_is_read_by_its_known_name() -> None:
    assert parse_skill_popup(harsetti_passive()) is None  # nothing in it says where the title is ...
    titles = ["Gambit", "The Law of Skuggiheim", "Checkmate"]
    popup = parse_skill_popup(harsetti_passive(), titles)  # ... but the hero is known, and so are its skills
    assert popup is not None
    assert (popup.hero, popup.name, popup.level, popup.cooldown) == ("Harsetti", "The Law of Skuggiheim", None, None)
    assert popup.text.startswith("At the start of battle, Speed of the caster is fixed going forward")
    assert popup.text.endswith("The Law of Skuggiheim only applies to PvP.")
    assert popup.enhance == () and popup.soulburn == "" and popup.effects == ()
    assert "Promotion" not in popup.text and "Imprint" not in popup.text  # the screen's own text is not the paragraph


def test_a_title_that_is_not_one_of_the_heros_skills_is_not_taken_for_one() -> None:
    assert parse_skill_popup(harsetti_passive(), ["Gambit", "Checkmate"]) is None
    assert parse_skill_popup(harsetti_passive(), []) is None
    no_text = [ln for ln in harsetti_passive() if ln.box.x0 != 343]
    assert parse_skill_popup(no_text, ["The Law of Skuggiheim"]) is None  # a name with no paragraph under it


def journal_specialty() -> list[TextLine]:
    """A hero's page in the Journal (Rhianna and Luciella, the user's capture 2026-10-26, 1919 x 1003): the Specialty
    panel has a title that is also the name of the hero's third skill and a paragraph one icon to its left, the shape of
    a popup, but a label ("Specialty") sits under the title; the Background column is the screen's own text."""
    background = [
        "Some say they were born from the",
        "moonlight, and others say they first",
        "stirred in the darkness. The truth is",
        "unknown, as this is the only record",
        "remaining in an unnamed organization.",
        "The two monsters raised by the",
    ]
    return [
        line("Dark", 95, 125, 55, 36),
        line("ThiefAquarius", 200, 122, 220, 40),
        line("Rhianna and Luciella", 48, 170, 510, 65),
        line("Background", 1425, 120, 205, 45),
        *[line(text, 1426, 215 + 30 * i, 440, 30) for i, text in enumerate(background)],
        line("Attack %", 150, 400, 100, 30),
        line("Pursuit of Death", 1543, 742, 206, 36),
        line("Specialty", 1541, 785, 100, 26),
        line("Rhianna and Luciella never lose a", 1436, 840, 368, 30),
        line("target once they've marked them.", 1436, 868, 375, 30),
        line("Preview Skills", 785, 915, 180, 36),
    ]


def test_the_specialty_panel_of_the_journal_is_not_taken_for_the_skill_named_like_it() -> None:
    titles = ["Half Moon Slash", "Waning Moon Slash", "Hunter's Mark", "Blade of Pursuit", "Pursuit of Death"]
    assert parse_skill_popup(journal_specialty(), titles) is None


def test_text_far_to_the_right_of_a_popup_is_not_one_of_its_effect_panels() -> None:
    plain = parse_skill_popup(synthetic())
    column = [
        line("Background", 2000, 120, 205, 45),
        *[line(f"Some say they were born from the moon {i}", 2000, 215 + 34 * i, 440, 30) for i in range(6)],
    ]
    beside = parse_skill_popup([*synthetic(), *column])
    assert plain is not None and beside is not None
    assert [e.title for e in beside.effects] == [e.title for e in plain.effects]


def lilibet_passive() -> list[TextLine]:
    """Designer Lilibet's passive, Emergency Stitching, as laid out in the user's capture (2026-10-26, 1268 x 873; the
    boxes are read off the picture): no badge and no cost row, a paragraph, "Skill Enhance" with steps whose text
    itself begins with "+3", and two effect panels at the right."""
    description = [
        "Starts the first battle with 30 Fighting",
        "Spirit. When an enemy's turn ends, gains",
        "10 Fighting Spirit for each debuff",
        "inflicted on an ally, and when the",
        "caster's Fighting Spirit is full, consumes",
        "all Fighting Spirit to dispel all debuffs",
        "from the caster and grants increased",
        "Defense and immunity for 1 turn before",
        "increasing Combat Readiness by 40%.",
    ]
    steps = [3, 4, 4, 4, 5]
    rows = [
        line("Dark", 80, 28, 60, 34),
        line("WarriorCancer", 150, 26, 250, 38),
        line("Designer Lilibet", 32, 68, 385, 60),
        line("Health %", 134, 300, 100, 30),
        line("Emergency Stitching", 452, 207, 281, 40),
        *[line(text, 335, 322 + 29.5 * i, 450, 30) for i, text in enumerate(description)],
        line("Skill Enhance", 336, 624, 157, 32),
        line("Increase Defense", 897, 210, 210, 36),
        line("Increases Defense by 60%.", 840, 262, 310, 28),
        line("Immunity", 897, 343, 120, 34),
        line("Cannot be affected by any debuffs", 840, 395, 385, 28),
        line("or some harmful effects.", 840, 423, 250, 28),
        line("Hero Rating", 95, 735, 135, 36),
        line("Preview Skills", 770, 820, 180, 34),
    ]
    for number, spirit in enumerate(steps, 1):
        y = 662 + 33 * (number - 1)
        rows += [
            line(f"+{number}", 336, y + 4, 26, 24),
            line(f"+{spirit} Fighting Spirit at the start of the first battle", 387, y, 405, 30),
        ]
    return rows


def test_a_step_that_begins_with_a_plus_is_not_taken_for_the_title() -> None:
    """Without a badge the first step ("+1", "+3 Fighting Spirit at the start ...") looked like a badge with its title
    and the skill was stored under that text (the user's Designer Lilibet, 2026-10-26)."""
    popup = parse_skill_popup(lilibet_passive())
    assert popup is not None
    assert (popup.hero, popup.name, popup.level, popup.cooldown) == (
        "Designer Lilibet",
        "Emergency Stitching",
        None,
        None,
    )
    assert popup.text.startswith("Starts the first battle with 30 Fighting Spirit.")
    assert popup.text.endswith("increasing Combat Readiness by 40%.")
    assert [level for level, _ in popup.enhance] == [1, 2, 3, 4, 5]
    assert popup.enhance[0][1] == "+3 Fighting Spirit at the start of the first battle" and popup.enhance[4][0] == 5
    assert [e.title for e in popup.effects] == ["Increase Defense", "Immunity"]
