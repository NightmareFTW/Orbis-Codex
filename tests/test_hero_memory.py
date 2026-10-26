"""Which hero a skill popup belongs to (SPEC D97): the hero is read on its own screen, where the name is whole, and
remembered for the popups that cover it."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any, cast

import numpy as np
import pytest
from PIL import Image

from e7ac.catalog.resolve import ResolvedEntity
from e7ac.cli import roster as roster_cli
from e7ac.paths import AppPaths
from e7ac.skills.hero import REMEMBER, HeroMemory, decide, hero_starting_with, starts
from e7ac.skills.store import SkillStore
from e7ac.vision.ocr import RapidOcrReader
from e7ac.vision.skill_popup import looks_like_popup, parse_skill_popup, read_hero_name
from tests.markers import FIXTURES_DIR
from tests.test_skill_popup import line
from tests.test_skills import Badge, named

HEROES = cast(
    dict[str, ResolvedEntity],
    {
        code: SimpleNamespace(name=name)
        for code, name in {
            "c2103": "Spirit Eye Celine",
            "c1018": "Celine",
            "c1153": "Harsetti",
            "c1104": "Mort",
            "c1162": "Young Senya",
            "c2011": "Blood Blade Karin",
            "c1011": "Karin",
        }.items()
    },
)


def test_the_start_of_a_name_is_told_from_a_name_that_merely_resembles_it() -> None:
    assert starts("Spirit Ey", "Spirit Eye Celine") and starts("Harsett", "Harsetti") and starts("Mort", "Mort")
    assert not starts("Celine", "Spirit Eye Celine")  # the end of a name is not what a popup leaves visible
    assert not starts("Mo", "Mort")  # too short to mean anything
    assert hero_starting_with("Spirit Ey", HEROES) == "c2103"
    assert hero_starting_with("Harsett", HEROES) == "c1153"
    assert hero_starting_with("Blood", HEROES) == "c2011"
    assert hero_starting_with("Mort", HEROES) is None  # under five letters: only a whole name counts
    assert hero_starting_with("Xyzzy", HEROES) is None


def test_a_name_cut_short_by_the_popup_still_finds_its_hero() -> None:
    assert decide("Spirit Ey", HEROES).code == "c2103"  # nothing remembered: the start of the name is enough
    assert decide("Spirit Ey", HEROES, remembered="c2103") == decide("Spirit Ey", HEROES).__class__(
        "c2103", "", "hero screen"
    )
    assert decide("Spirit Ey", HEROES).how == "start of the name"
    assert decide("Harsett", HEROES).code == "c1153"  # one letter short: the margin rule or the start, either way
    assert decide("Mort", HEROES).how == "screen name"


def test_the_hero_remembered_serves_when_the_popup_shows_no_name() -> None:
    assert decide(None, HEROES, remembered="c2103").code == "c2103"
    assert decide("Xq", HEROES, remembered="c2103").code == "c2103"  # noise that names nobody contradicts nothing
    nothing = decide(None, HEROES)
    assert nothing.code is None and "open the hero's own screen first" in nothing.problem


def test_a_stale_memory_gives_way_to_a_name_that_names_another_hero() -> None:
    assert decide("Harsett", HEROES, remembered="c2103").code == "c1153"  # the next hero was opened
    assert decide("Mort", HEROES, remembered="c2103").code == "c1104"
    assert decide("Karin", HEROES, remembered="c2011").code == "c1011"  # "Karin" is a hero, not the start of the other


def test_the_hero_named_by_the_user_wins_unless_the_screen_shows_another_clearly() -> None:
    assert decide("Spirit Ey", HEROES, chosen="Spirit Eye Celine", remembered="c1153").code == "c2103"
    assert decide(None, HEROES, chosen="c1153").code == "c1153"
    refused = decide("Mort", HEROES, chosen="Spirit Eye Celine")
    assert refused.code is None and "shows 'Mort' but the hero chosen is 'Spirit Eye Celine'" in refused.problem
    assert decide("Mort", HEROES, chosen="Mort").how == "chosen"
    assert decide("Mort", HEROES, chosen="Nobody Atall").code is None


def test_the_memory_forgets_after_a_while() -> None:
    memory = HeroMemory()
    assert memory.recall(0.0) is None
    memory.see("c2103", 100.0)
    assert memory.recall(100.0 + REMEMBER) == "c2103" and memory.recall(100.0 + REMEMBER + 1) is None


# ------------------------------------------------------------------------------------------------ the hero's screen


def hero_screen(name: str = "Spirit Eye Celine") -> list[Any]:
    """The hero's own screen laid out like the game's (1354 x 916): the name large at the top left, the element and
    class line small above it, the level, the buttons at the bottom."""
    return [
        line("Light", 66, 29, 80, 43),
        line("ThiefSagittarius", 179, 26, 258, 46),
        line(name, 23, 61, 411, 82),
        line("Lv. Max/50", 29, 176, 232, 49),
        line("Effect", 138, 295, 75, 33),
        line("Move to Grace of Growth", 87, 551, 262, 29),
        line("Skill", 648, 835, 56, 35),
        line("Awaken", 1077, 852, 96, 31),
    ]


def test_the_name_on_a_hero_screen_is_the_largest_text_at_its_top_left() -> None:
    assert read_hero_name(hero_screen()) == "Spirit Eye Celine"
    assert read_hero_name(hero_screen("Harsetti")) == "Harsetti"
    assert read_hero_name(hero_screen()[:2]) is None  # too little on the screen to say
    assert read_hero_name([line("Lv. 60", 0, 0, 200, 90), *hero_screen()[3:]]) is None  # a level is not a name
    small = [line(t.text, t.box.x0, t.box.y0, t.box.x1 - t.box.x0, 20) for t in hero_screen()]
    assert read_hero_name(small) is None  # nothing stands out as a name


@pytest.mark.fixtures("screenshots/skills/hero_screen_celine.webp")
def test_the_users_own_hero_screen_gives_the_whole_name() -> None:
    """The screen the user sent (2026-10-26): the name is clear here and covered by the popup afterwards."""
    picture = np.array(Image.open(FIXTURES_DIR / "screenshots/skills/hero_screen_celine.webp").convert("RGB"))
    assert read_hero_name(RapidOcrReader().read(picture)) == "Spirit Eye Celine"


# ------------------------------------------------------------------------------------------------ the watch


class Script:
    """A reader that gives the lines of one screen after the other."""

    def __init__(self, screens: list[list[Any]]) -> None:
        self.screens = iter(screens)

    def read(self, image: object) -> list[Any]:
        return next(self.screens, [])


def test_the_watch_remembers_the_hero_of_its_screen_and_files_the_popups_under_it(
    monkeypatch: pytest.MonkeyPatch, synthetic_catalog: list[str], isolated_home: AppPaths
) -> None:
    from e7ac.cli import arena as arena_cli
    from e7ac.cli import skills as skills_cli
    from e7ac.vision.window import Rect

    frames = [np.full((36, 64, 3), shade, np.uint8) for shade in (40, 200, 90)]
    sequence = [f for f in frames for _ in range(6)]
    monkeypatch.setattr(
        arena_cli, "game_frames", lambda client, hwnd: iter([(f, Rect(0, 0, 64, 36)) for f in sequence])
    )
    ticks = iter(x * 0.5 for x in range(1000))
    monkeypatch.setattr(skills_cli, "clock", lambda: next(ticks))
    popup_lines = named("Blood Bl")  # the popup covers the name: only its start is left
    screens = [hero_screen("Blood Blade Karin"), popup_lines, named("Qq")]
    monkeypatch.setattr(roster_cli, "reader_factory", lambda: Script(screens))
    events: list[skills_cli.ReadEvent] = []
    badge = Badge()
    skills_cli.run_skill_watch(skills_cli.SkillWatchOptions(on_read=events.append), badge, threading.Event())
    assert ("Hero: Blood Blade Karin", "now hold each skill icon ~2 s") in badge.said
    assert [(e.hero, e.how) for e in events] == [
        ("Blood Blade Karin", "hero screen"),
        ("Blood Blade Karin", "hero screen"),
    ]
    assert SkillStore(isolated_home.home / "skills").heroes() == ["c2011"]
    assert skills_cli.describe(events[0]).endswith("[hero from its own screen]")


# ---------------------------------------------------------------------------------------- a popup not understood


def passive_without_title() -> list[Any]:
    """A popup the reader cannot place yet: a skill at +0 that gains no Souls shows no "+N" badge and no "Acquire" row
    (the user's Celine, Sixth Sense, 2026-10-26), so no title is found."""
    return [
        line("Damage suffered in one attack does not exceed", 455, 262, 650),
        line("51% of max Health. Decreases skill cooldown by 1", 455, 305, 640),
        line("turn when somebody dies.", 456, 348, 520),
        line("Skill Enhance", 456, 640, 240),
        line("+1", 456, 700, 50, 38),
        line("+5% Combat Readiness", 530, 695, 420),
        line("Promotion", 800, 1000, 150),
    ]


def test_a_popup_with_its_own_labels_is_told_from_a_screen_that_is_not_one() -> None:
    assert parse_skill_popup(passive_without_title()) is None and looks_like_popup(passive_without_title())
    assert not looks_like_popup(hero_screen())  # the buttons "Skill" and "Enhance" are two lines, not the label
    assert not looks_like_popup([line("Arena", 10, 10), line("Shop", 10, 60), line("Heroes", 10, 110)])


def test_the_watch_keeps_the_picture_of_a_popup_it_does_not_understand(
    monkeypatch: pytest.MonkeyPatch, synthetic_catalog: list[str], isolated_home: AppPaths
) -> None:
    from e7ac.cli import arena as arena_cli
    from e7ac.cli import skills as skills_cli
    from e7ac.vision.window import Rect

    frames = [np.full((36, 64, 3), shade, np.uint8) for shade in (40, 200)]
    sequence = [f for f in frames for _ in range(6)]
    monkeypatch.setattr(
        arena_cli, "game_frames", lambda client, hwnd: iter([(f, Rect(0, 0, 64, 36)) for f in sequence])
    )
    ticks = iter(x * 0.5 for x in range(1000))
    monkeypatch.setattr(skills_cli, "clock", lambda: next(ticks))
    monkeypatch.setattr(roster_cli, "reader_factory", lambda: Script([hero_screen("Karin"), passive_without_title()]))
    events: list[skills_cli.ReadEvent] = []
    badge = Badge()
    skills_cli.run_skill_watch(skills_cli.SkillWatchOptions(on_read=events.append), badge, threading.Event())
    assert [e.problem.startswith("a skill popup was seen but not understood") for e in events] == [True]
    kept = sorted((isolated_home.captures_dir / "skills").glob("*-unread.png"))
    assert len(kept) == 1 and str(kept[0]) in events[0].problem
    assert ("Skills: popup not understood", "its picture was kept - send it") in badge.said
    assert SkillStore(isolated_home.home / "skills").heroes() == []  # nothing guessed


def test_the_watch_reads_a_passive_with_no_enhance_section_by_the_names_of_the_heros_skills(
    monkeypatch: pytest.MonkeyPatch, synthetic_catalog: list[str], isolated_home: AppPaths
) -> None:
    """Harsetti's The Law of Skuggiheim (2026-10-26) has nothing a title can be found by but its own name."""
    from e7ac.cli import arena as arena_cli
    from e7ac.cli import skills as skills_cli
    from e7ac.sources.epic7db import SkillInfo
    from e7ac.vision.window import Rect
    from tests.test_skill_popup import harsetti_passive

    kit = {
        1: SkillInfo(1, 0, {}, "", "Gambit"),
        2: SkillInfo(2, None, {}, "", "The Law of Skuggiheim"),
        3: SkillInfo(3, 5, {}, "", "Checkmate"),
    }
    monkeypatch.setattr(skills_cli, "make_kit_of", lambda paths, offline=False: lambda name: kit)
    frames = [np.full((36, 64, 3), shade, np.uint8) for shade in (40, 200)]
    sequence = [f for f in frames for _ in range(6)]
    monkeypatch.setattr(
        arena_cli, "game_frames", lambda client, hwnd: iter([(f, Rect(0, 0, 64, 36)) for f in sequence])
    )
    ticks = iter(x * 0.5 for x in range(1000))
    monkeypatch.setattr(skills_cli, "clock", lambda: next(ticks))
    popup_lines = [
        line("Blood Blade Karin", t.box.x0, t.box.y0, t.box.x1 - t.box.x0, t.box.y1 - t.box.y0)
        if t.text == "Harsetti"
        else t
        for t in harsetti_passive()
    ]
    monkeypatch.setattr(roster_cli, "reader_factory", lambda: Script([hero_screen("Blood Blade Karin"), popup_lines]))
    events: list[skills_cli.ReadEvent] = []
    skills_cli.run_skill_watch(skills_cli.SkillWatchOptions(on_read=events.append), Badge(), threading.Event())
    assert [(e.skill, e.slot, e.passive, e.how, e.problem) for e in events] == [
        ("The Law of Skuggiheim", 2, True, "hero screen", "")
    ]
    stored = SkillStore(isolated_home.home / "skills").load("c2011")
    assert [(s.name, s.slot, s.passive) for s in stored] == [("The Law of Skuggiheim", 2, True)]
    assert stored[0].text.startswith("At the start of battle, Speed of the caster is fixed")


# ------------------------------------------------------------------------------------ the program's own window


def test_the_orbis_window_and_the_badge_are_not_the_game() -> None:
    from e7ac.skills.own_ui import shows_window, without_badge

    window = [line("ORBIS CODEX", 20, 20), line("Spirit Eye Celine", 20, 900), line("Hero of the day", 20, 940)]
    assert shows_window(window) and not shows_window(hero_screen())
    badge = [
        line("Read: Notos", 700, 10, 300, 36),
        line("God's Might +0", 700, 52, 300, 30),  # the badge's second line, right under the first
        line("Divinity Release", 455, 105, 330, 60),  # the game's own text, further down
    ]
    kept = [t.text for t in without_badge(badge)]
    assert kept == ["Divinity Release"]
    assert [t.text for t in without_badge(hero_screen())] == [t.text for t in hero_screen()]  # nothing to take out


def test_the_watch_reads_nothing_from_a_picture_that_shows_the_orbis_window_or_its_badge(
    monkeypatch: pytest.MonkeyPatch, synthetic_catalog: list[str], isolated_home: AppPaths
) -> None:
    """The user's export held "S3 Sword of Requiem |" with the text "Spirit Eye Celine Hero of the day" (the detail
    panel of the Orbis window over the game) and "God's Might" with "Read: Notos -" (the badge): not the game's."""
    from e7ac.cli import arena as arena_cli
    from e7ac.cli import skills as skills_cli
    from e7ac.vision.window import Rect

    frames = [np.full((36, 64, 3), shade, np.uint8) for shade in (40, 200)]
    sequence = [f for f in frames for _ in range(6)]
    monkeypatch.setattr(
        arena_cli, "game_frames", lambda client, hwnd: iter([(f, Rect(0, 0, 64, 36)) for f in sequence])
    )
    ticks = iter(x * 0.5 for x in range(1000))
    monkeypatch.setattr(skills_cli, "clock", lambda: next(ticks))
    over_the_game = [
        line("S3  Sword of Requiem |", 455, 105, 330, 60),
        line("Spirit Eye Celine Hero of the day", 455, 200, 400, 40),
        line("ORBIS CODEX", 20, 20, 300, 40),
        *named("Karin")[3:],
    ]
    monkeypatch.setattr(roster_cli, "reader_factory", lambda: Script([hero_screen("Karin"), over_the_game]))
    events: list[skills_cli.ReadEvent] = []
    badge = Badge()
    skills_cli.run_skill_watch(skills_cli.SkillWatchOptions(on_read=events.append), badge, threading.Event())
    assert events == [] and SkillStore(isolated_home.home / "skills").heroes() == []
    assert ("Skills: the Orbis window is over the game", "move it beside the game") in badge.said
