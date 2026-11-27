"""The badge keeps a result on screen long enough to be read, and says what to do next (SPEC D136)."""

from __future__ import annotations

from pathlib import Path

from e7ac.skills.guide import next_step
from e7ac.skills.store import SkillStore
from e7ac.ui.sticky_badge import HOLD, StickyBadge
from tests.test_skills import popup


class Inner:
    def __init__(self) -> None:
        self.said: list[str] = []
        self.settled = 0

    def say(self, top: str, bottom: str, colour: str) -> None:
        self.said.append(top)

    def settling(self) -> None:
        self.settled += 1

    def region(self) -> str:
        return "region"


def test_a_result_is_not_wiped_by_the_idle_hint_and_the_hint_comes_back_after_it() -> None:
    now = [0.0]
    inner = Inner()
    badge = StickyBadge(inner, lambda: now[0])
    badge.say("Read: Muwi", "next: hold S1", "#2fa84f", hold=HOLD)
    for second in (0.5, 1.0, 3.0):  # frames without a popup keep saying the idle hint
        now[0] = second
        badge.say("Skills: hold a skill icon", "no skill popup on this screen", "#bbb")
        badge.settling()
        badge.tick()
    assert inner.said == ["Read: Muwi"] and inner.settled == 0  # the result stayed, nothing wiped it
    now[0] = HOLD + 0.1
    badge.tick()
    assert inner.said == ["Read: Muwi", "Skills: hold a skill icon"]  # the last hint, once the hold is over
    badge.tick()
    assert len(inner.said) == 2  # said once


def test_a_new_reading_breaks_the_hold_and_everything_else_goes_straight_through() -> None:
    now = [0.0]
    inner = Inner()
    badge = StickyBadge(inner, lambda: now[0])
    badge.say("Read: Muwi", "x", "#2fa84f", hold=HOLD)
    now[0] = 1.0
    badge.say("Skills: reading...", "keep holding", "#bbb", urgent=True)
    assert inner.said[-1] == "Skills: reading..."
    badge.say("Read: Ivana", "y", "#2fa84f", hold=HOLD)  # a result may replace a result at once
    assert inner.said[-1] == "Read: Ivana" and badge.region() == "region"


def test_the_next_step_says_which_skill_to_hold_and_when_a_hero_is_done(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    store.save("c1001", popup("One", 5, "Attacks the enemy."), slot=1)
    assert next_step(store, "c1001", "Muwi") == ("Muwi 1 of 3 - next: hold S2 and S3", False)
    store.save("c1001", popup("Two", 5, "Attacks the enemy."), slot=2)
    store.save("c1001", popup("Three", 5, "Attacks the enemy."), slot=3)
    assert next_step(store, "c1001", "Muwi") == ("Muwi: all 3 skills read, icons kept", True)
    line, done = next_step(store, "c1001", "Muwi", lambda skill: False)  # no icon kept: not done
    assert not done and line.startswith("Muwi: hold again One, Three, Two") and "icon" in line
