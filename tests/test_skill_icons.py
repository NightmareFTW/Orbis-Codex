"""The skill's own icon, cut from the picture it was read from (SPEC D134)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from e7ac.skills.icons import crop_icon, icon_path, save_icon


def _picture() -> np.ndarray:
    """A 1920 x 1010 game picture with a bright 98 px icon where the game puts it: left of a 38 px high title."""
    image = np.full((1010, 1920, 3), 30, np.uint8)
    image[295:393, 595:693] = (200, 120, 40)  # the icon, 17 px left of the title at x 710
    return image


TITLE = (710.0, 305.0, 870.0, 343.0)


def test_the_icon_is_cut_left_of_the_title_at_the_size_of_the_windows_text() -> None:
    icon = crop_icon(_picture(), TITLE)
    assert icon is not None and icon.shape[0] == icon.shape[1] == round(2.6 * 38)
    assert (icon[10:-10, 10:-10] == (200, 120, 40)).all()  # the icon is in the cut, nothing else of note
    small = crop_icon(np.full((500, 800, 3), 30, np.uint8), (300.0, 100.0, 380.0, 119.0))
    assert small is not None and small.shape[0] == round(2.6 * 19)  # a smaller window cuts a smaller icon


def test_no_icon_is_cut_when_it_would_fall_outside_the_picture_or_be_tiny(tmp_path: Path) -> None:
    assert crop_icon(_picture(), (20.0, 305.0, 100.0, 343.0)) is None  # not enough room at the left
    assert crop_icon(_picture(), (710.0, 305.0, 760.0, 312.0)) is None  # a tiny title
    assert save_icon(tmp_path, "c2053", "Bastet Roar", _picture(), (20.0, 305.0, 100.0, 343.0)) is None


def test_the_icon_is_kept_under_the_heros_code_and_the_skills_name(tmp_path: Path) -> None:
    path = save_icon(tmp_path, "c2053", "Bastet Roar", _picture(), TITLE)
    assert path == icon_path(tmp_path, "c2053", "Bastet Roar") and path is not None and path.is_file()
    assert path.parent == tmp_path / "skills" / "icons" and path.name == "c2053-bastetroar.png"
    read = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
    assert read is not None and read.shape[0] == read.shape[1]


def test_a_skill_without_its_icon_is_a_problem_until_it_is_held_again(tmp_path: Path) -> None:
    """SPEC D135: a hero is not done while a skill read has no icon kept (here or among the program's own)."""
    from e7ac.skills.icons import find_icon
    from e7ac.skills.problems import hero_problems
    from e7ac.skills.store import SkillStore
    from tests.test_skills import popup

    store = SkillStore(tmp_path / "skills")
    store.save("c2053", popup("Bastet Roar", 5, "Dispels all debuffs.", cooldown=5), slot=3)
    skills = store.load("c2053")
    kept = lambda s: find_icon(tmp_path, s.hero, s.name) is not None  # noqa: E731
    assert [p.kind for p in hero_problems(skills, (), kept)] == ["icon"]
    assert hero_problems(skills) == ()  # asked for no icons: none is wanted
    save_icon(tmp_path, "c2053", "Bastet Roar", _picture(), TITLE)
    assert hero_problems(skills, (), kept) == ()
