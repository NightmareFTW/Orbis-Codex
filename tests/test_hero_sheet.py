"""The hero sheet (SPEC D133): what the app knows about one hero, in sections, from what is on this PC."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from e7ac.catalog.resolve import ResolvedEntity
from e7ac.domain.codes import Stat
from e7ac.paths import default_paths
from e7ac.skills.store import SkillStore, skills_dir
from e7ac.ui.hero_sheet import build_sheet, stat_text
from tests.test_skills import popup

HEROES = cast(
    dict[str, ResolvedEntity],
    {
        "c2053": SimpleNamespace(
            name="Desert Jewel Basar",
            value=lambda key: {"element": "light", "role": "soul-weaver", "rarity": 5}.get(key),
        )
    },
)


def test_stat_text_shows_rates_as_percentages_and_flat_stats_as_numbers() -> None:
    assert stat_text(Stat.SPEED, 45) == "Speed 45"
    assert stat_text(Stat.CRIT_CHANCE, 0.62) == "Crit Chance 62%"


def test_a_sheet_says_what_is_missing_instead_of_leaving_it_out() -> None:
    paths = default_paths()
    paths.ensure()
    sheet = build_sheet(paths, HEROES, "c2053")
    assert sheet.name == "Desert Jewel Basar" and sheet.tags[:2] == ("Light", "Soul-Weaver")
    titles = [s.title for s in sheet.sections]
    assert titles == ["Your hero", "Skills as read from the game", "In the simulation", "In the Arena"]
    assert all(s.empty or s.rows for s in sheet.sections)
    assert "Not read yet" in sheet.sections[1].empty


def test_the_skills_read_and_the_hand_kit_show_on_the_sheet() -> None:
    paths = default_paths()
    paths.ensure()
    store = SkillStore(skills_dir(paths.home))
    store.save("c2053", popup("Bastet Roar", 5, "Dispels all debuffs.", cooldown=5), slot=3)
    sheet = build_sheet(paths, HEROES, "c2053")
    skills, kit = sheet.sections[1], sheet.sections[2]
    assert skills.rows == (("S3", "Bastet Roar +5 (cooldown 5)"),)
    assert any("Dispels all debuffs." in p for p in skills.paragraphs)
    assert ("Skills read", "1 of 3") in kit.rows and ("Kit in the simulation", "written by hand (verified)") in kit.rows
    assert any(p.startswith("Not simulated:") for p in kit.paragraphs)
