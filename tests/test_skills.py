"""The skills read from the game (SPEC D91): the store, the merge into epic7db's kit, the worklist, the export and the
commands (synthetic popups; the OCR itself is tested in test_skill_popup)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from typer.testing import CliRunner

from e7ac.cli import roster as roster_cli
from e7ac.cli.app import app
from e7ac.paths import AppPaths
from e7ac.predict.effects import Effect, kit_from
from e7ac.skills.export import export_text
from e7ac.skills.merge import merge, remaining, skill_info, slot_of, to_maximum, unplaced
from e7ac.skills.store import SkillStore, StoredSkill
from e7ac.skills.worklist import progress, worklist
from e7ac.sources.epic7db import SkillInfo, parse_tier_order
from e7ac.vision.ocr import TextLine
from e7ac.vision.skill_popup import EffectPanel, SkillPopup
from tests.test_hero_screen import FakeReader
from tests.test_skill_popup import synthetic

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def popup(
    name: str = "Road Sign Smash",
    level: int | None = 6,
    text: str = "Attacks the enemy and has a 60% chance to inflict decreased Defense for 1 turn.",
    cooldown: int | None = None,
    enhance: tuple[tuple[int, str], ...] = (
        (1, "+5% damage dealt"),
        (2, "+5% effect chance"),
        (5, "+10% effect chance"),
    ),
    effects: tuple[EffectPanel, ...] = (EffectPanel("Decrease Defense", (), "Decreases Defense by 70%."),),
) -> SkillPopup:
    return SkillPopup("Boss Arunka", name, level, (("Soul", 1),), cooldown, "", text, "", None, enhance, effects, 0.97)


def test_a_popup_is_stored_replaced_and_its_effects_learned(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    first = store.save("c2124", popup(), NOW)
    assert first.new and not first.changed and first.new_effects == ("Decrease Defense",)
    again = store.save("c2124", popup(), NOW)
    assert not again.new and not again.changed and again.new_effects == ()
    higher = store.save("c2124", popup(level=3, text="Attacks the enemy and has a 70% chance."), NOW)
    assert not higher.new and higher.changed  # another level or another text: the game says something else now
    assert [s.name for s in store.load("c2124")] == ["Road Sign Smash"]
    store.save("c2124", popup(name="Coruscated Trajectory", cooldown=3), NOW)
    assert [s.name for s in store.load("c2124")] == ["Road Sign Smash", "Coruscated Trajectory"]
    assert store.heroes() == ["c2124"]
    reworded = EffectPanel("Decrease Defense", (), "Decreases Defense by 60%.")
    assert store.save("c2124", popup(effects=(reworded,)), NOW).new_effects == ("Decrease Defense",)  # an update
    assert store.effects()["decrease defense"]["text"] == "Decreases Defense by 60%."
    assert store.effects()["decrease defense"]["heroes"] == ["c2124"]


def test_a_damaged_file_counts_as_nothing_read(tmp_path: Path) -> None:
    store = SkillStore(tmp_path)
    (tmp_path / "c2124.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "effects.json").write_text("[1, 2", encoding="utf-8")
    assert store.load("c2124") == [] and store.effects() == {}
    assert store.save("c2124", popup(), NOW).new  # and it is written again from scratch


def test_the_slot_of_a_popup_is_found_by_its_name_among_epic7db_skills() -> None:
    skills = {
        1: SkillInfo(1, 0, {}, "x", name="Road Sign Smash"),
        3: SkillInfo(3, 3, {}, "y", name="Coruscated Trajectory"),
    }
    assert slot_of("road sign smash", skills) == 1 and slot_of("Coruscated  Trajectory", skills) == 3
    assert slot_of("Unknown", skills) is None


def test_steps_still_to_come_move_the_numbers_of_the_text_forward() -> None:
    steps = ["+5% effect chance", "+3% Combat Readiness", "+15% damage dealt", "-1 turn cooldown", "+5% healing"]
    text, left = to_maximum("Has a 60% chance to inflict fear and increases Combat Readiness by 20%.", steps)
    assert text == "Has a 65% chance to inflict fear and increases Combat Readiness by 23%."
    assert left == ["+5% healing"]  # a number of the text it cannot be applied to
    ambiguous, left = to_maximum("A 50% chance, then a 30% chance.", ["+10% effect chance"])
    assert ambiguous == "A 50% chance, then a 30% chance." and left == [
        "+10% effect chance"
    ]  # two numbers: not guessed


def stored_of(p: SkillPopup) -> StoredSkill:
    return StoredSkill(
        "c2124",
        p.name,
        p.level,
        p.gains,
        p.cooldown,
        p.cooldown_note,
        p.text,
        p.soulburn,
        p.soulburn_cost,
        p.enhance,
        p.effects,
        p.confidence,
        NOW.isoformat(),
    )


def test_a_capture_becomes_the_skill_epic7db_could_not_describe() -> None:
    steps = ((1, "+5% damage dealt"), (3, "-1 turn cooldown"), (5, "+5% effect chance"))
    at_three = popup(name="Aura", level=3, cooldown=3, text="A 90% chance to inflict venom.", enhance=steps)
    assert remaining(stored_of(at_three)) == ["+5% effect chance"]
    info = skill_info(stored_of(at_three), 3)
    assert info.captured and info.level == 3
    assert info.cooldown == 4 and info.cooldown_max == 3 and info.max_cooldown == 3  # the -1 at +3 was applied already
    assert info.text == "A 95% chance to inflict venom."  # the +5% effect chance of +5 still to come
    early = skill_info(stored_of(popup(name="Aura", level=1, cooldown=4, enhance=steps, text="A 90% chance.")), 3)
    assert early.cooldown == 4 and early.max_cooldown == 3 and early.text == "A 95% chance."
    blind = skill_info(stored_of(popup(name="Aura", level=None, cooldown=4, enhance=steps, text="A 90% chance.")), 3)
    assert blind.max_cooldown == 4 and blind.text == "A 90% chance."  # no badge read: nothing is moved forward


def test_the_merged_skill_gives_the_kit_reader_the_effects_it_could_not_see() -> None:
    page = {1: SkillInfo(1, 0, {}, "Attacks the enemy.", name="Road Sign Smash")}  # a newer hero: no icons
    assert kit_from(page).unread == [] or kit_from(page).unread == [1]
    captured = [
        stored_of(popup(text="Attacks the enemy and has a 60% chance to inflict decreased Defense for 1 turn."))
    ]
    merged = merge(page, captured)
    assert merged[1].captured and merged[1].effects == {"Decrease Defense": 0.7}
    kit = kit_from(merged)
    assert kit.on_use[1] == [Effect("def", True, -0.7, 1, 0.6)]  # at +6 every step with an effect chance is behind it
    assert not kit.unread
    assert unplaced(page, [stored_of(popup(name="Someone Else's Skill"))])[0].name == "Someone Else's Skill"


def test_the_worklist_puts_what_is_met_first_then_the_tier_list_then_the_rest(tmp_path: Path) -> None:
    store = SkillStore(tmp_path)
    store.save("c2", popup(name="One"), NOW, slot=1)
    store.save("c2", popup(name="Two"), NOW, slot=3)  # S2 is the one missing
    store.save("c1", popup(name="A"), NOW, slot=1)
    store.save("c1", popup(name="B"), NOW, slot=2, passive=True)
    store.save("c1", popup(name="C"), NOW, slot=3)
    heroes = {"c1": "Alpha", "c2": "Beta", "c3": "Gamma", "c4": "Delta"}
    rows = worklist(heroes, [("c3", 5)], ["c4", "c3", "c2"], store)
    assert [r.code for r in rows] == ["c3", "c4", "c2", "c1"]
    assert [r.why for r in rows] == ["met 5x in your Arena", "RTA tier list #1", "RTA tier list #3", ""]
    assert [(r.read, r.marks, r.complete) for r in rows] == [(0, "- - -", False)] * 2 + [
        (2, "S - S", False),
        (3, "S P S", True),
    ]
    assert progress(rows) == (1, 2, 5, 4)


def test_the_marks_follow_the_heros_skills_and_say_when_a_slot_is_not_known(tmp_path: Path) -> None:
    from e7ac.skills.worklist import slot_marks

    store = SkillStore(tmp_path)
    assert slot_marks([]) == (("-", "-", "-"), 0)
    store.save("c1", popup(name="Third"), NOW, slot=3)
    store.save("c1", popup(name="Not placed"), NOW)  # its slot is not known: it takes the first free one, as "?"
    marks, read = slot_marks(store.load("c1"))
    assert marks == ("?", "-", "S") and read == 2
    store.save("c1", popup(name="Third, misread"), NOW, slot=3)  # another reading of the slot: one skill, not two
    assert slot_marks(store.load("c1")) == (("?", "-", "S"), 2)


def test_a_popup_is_placed_by_its_name_among_epic7dbs_skills_and_the_earlier_ones_follow(tmp_path: Path) -> None:
    from e7ac.skills.merge import assign, place

    kit = {
        1: SkillInfo(1, 0, {}, "", "Mighty Strike"),
        2: SkillInfo(2, None, {}, "", "Sixth Sense"),
        3: SkillInfo(3, 4, {}, "", "Spirit Gate: Burning Possession"),
    }
    assert place("Mighty Strike", kit) == (1, False)
    assert place("Sixth Sense", kit) == (2, True)  # slot 2 without a cooldown: a passive
    assert place("Spirit Gate: Burning Possession", kit) == (3, False)
    assert place("Spirit Gate: Burning Possesion", kit) == (3, False)  # one letter off: the margin rule
    assert place("Something else", kit) == (None, False) and place("Sixth Sense", {}) == (None, False)
    store = SkillStore(tmp_path)
    store.save("c1", popup(name="Sixth Sense"), NOW)  # read before slots existed
    store.save("c1", popup(name="Mighty Strike"), NOW)
    assert assign(store, "c1", kit) == 2
    assert {s.name: (s.slot, s.passive) for s in store.load("c1")} == {
        "Sixth Sense": (2, True),
        "Mighty Strike": (1, False),
    }
    assert assign(store, "c1", kit) == 0  # nothing left to place


def test_a_hero_is_summarised_skill_by_skill_in_its_own_order(tmp_path: Path) -> None:
    from e7ac.skills.summary import hero_summary

    store = SkillStore(tmp_path)
    store.save("c1", popup(name="Third Skill", cooldown=4), NOW, slot=3)
    store.save("c1", popup(name="First Skill", level=None), NOW, slot=1)
    text = hero_summary("Alpha", "c1", store.load("c1"))
    lines = text.splitlines()
    assert lines[0] == "Alpha (c1): 2 of 3 skills read"
    assert lines[1].startswith("S1  First Skill") and "+0" in lines[1] and "no cooldown" in lines[1]
    assert any(line == "S2  - not read yet" for line in lines)  # the skill that is missing is named
    third = next(line for line in lines if line.startswith("S3  Third Skill"))
    assert "cooldown 4" in third and "3 enhancement steps" in third and "effects: Decrease Defense" in third
    store.save("c1", popup(name="Sixth Sense", cooldown=None), NOW, slot=2, passive=True)
    passive = next(line for line in hero_summary("Alpha", "c1", store.load("c1")).splitlines() if "Sixth Sense" in line)
    assert passive.startswith("S2  Sixth Sense (passive)")
    store.save("c2", popup(name="Not placed"), NOW)
    assert "?   Not placed" in hero_summary("Beta", "c2", store.load("c2"))


def test_a_second_reading_keeps_the_effect_panels_the_first_one_caught(tmp_path: Path) -> None:
    store = SkillStore(tmp_path)
    store.save("c1", popup(), NOW)
    again = store.save("c1", popup(effects=()), NOW)
    assert [e.title for e in store.load("c1")[0].effects] == ["Decrease Defense"] and not again.changed
    changed = store.save("c1", popup(text="Another text, 60% now.", effects=()), NOW)
    assert changed.changed and store.load("c1")[0].effects == ()  # the skill itself changed: the old panels do not stay


def test_the_export_is_the_games_own_words_per_hero_then_the_effects(tmp_path: Path) -> None:
    store = SkillStore(tmp_path)
    store.save("c2124", popup(name="Coruscated Trajectory", cooldown=3), NOW)
    text = export_text(store, {"c2124": "Boss Arunka"}, now=NOW)
    assert text.startswith("# Orbis Codex: skills read from the game, 2026-10-18 12:00 UTC: 1 heroes, 1 skills")
    assert "== Boss Arunka (c2124)" in text and "[+6] Coruscated Trajectory | Soul 1, 3 turns" in text
    assert "  Enhance: +1 +5% damage dealt; +2 +5% effect chance; +5 +10% effect chance" in text
    assert "  Effect Decrease Defense: Decreases Defense by 70%." in text
    assert text.rstrip().endswith("Decrease Defense: Decreases Defense by 70%.") and "== Effects (1)" in text


def test_the_rta_tier_list_gives_the_heroes_best_first() -> None:
    page = (
        '<a href="https://epic7db.com/heroes/boss-arunka"><img></a><a href="https://epic7db.com/heroes/boss-arunka">x</a>'
        '<a href="https://epic7db.com/heroes/rinak">r</a><h2>All Epic Seven Heroes</h2>'
        '<a href="https://epic7db.com/heroes/abigail">a</a><a href="https://epic7db.com/heroes/rinak">r</a>'
    )
    assert parse_tier_order(page) == ["boss-arunka", "rinak"]


# --- the commands ---------------------------------------------------------------------------------------------------

runner = CliRunner()


def named(hero: str) -> list[TextLine]:
    """The synthetic popup of test_skill_popup with another hero's name at the top left."""
    return [TextLine(hero, ln.score, ln.box, ln.words) if ln.text == "Lady of the Scales" else ln for ln in synthetic()]


@pytest.fixture
def reading(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    import cv2

    monkeypatch.setattr(roster_cli, "reader_factory", lambda: FakeReader(named("Blood Blade Karin")))
    image = tmp_path / "popup.png"
    cv2.imwrite(str(image), np.full((60, 100, 3), 30, np.uint8))
    return image


def test_skills_read_stores_a_popup_and_show_and_export_say_what_was_read(
    reading: Path, synthetic_catalog: list[str], isolated_home: AppPaths
) -> None:
    result = runner.invoke(app, ["skills", "read", str(reading)])
    assert result.exit_code == 0, result.output
    assert (
        "Blood Blade Karin: The Caress of Mercy +5 (new); effects learned: Offering, Lingering Fragrance"
        in result.output
    )
    assert "1 skill(s) stored of 1 capture(s)." in result.output
    again = runner.invoke(app, ["skills", "read", str(reading)])
    assert "(again)" in again.output
    shown = runner.invoke(app, ["skills", "show", "Blood Blade Karin"])
    assert shown.exit_code == 0 and "The Caress of Mercy" in shown.output and "Effect Offering:" in shown.output
    out = isolated_home.home / "skills_export.txt"
    exported = runner.invoke(app, ["skills", "export", "--out", str(out)])
    assert exported.exit_code == 0 and "== Blood Blade Karin (c2011)" in out.read_text(encoding="utf-8")
    todo = runner.invoke(app, ["skills", "todo", "--offline"])
    assert todo.exit_code == 0 and "0 of" in todo.output and "? - -" in todo.output


def test_a_hero_that_is_not_in_the_catalog_is_not_stored_and_says_why(
    monkeypatch: pytest.MonkeyPatch, reading: Path, synthetic_catalog: list[str], isolated_home: AppPaths
) -> None:
    monkeypatch.setattr(roster_cli, "reader_factory", lambda: FakeReader(named("Nobody Atall")))
    result = runner.invoke(app, ["skills", "read", str(reading)])
    assert "not stored: The Caress of Mercy +5: no catalog hero is called 'Nobody Atall'" in result.output
    assert SkillStore(isolated_home.home / "skills").heroes() == []
    given = runner.invoke(app, ["skills", "read", str(reading), "--hero", "Blood Blade Karin"])
    assert "Blood Blade Karin: The Caress of Mercy" in given.output


def test_the_hero_chosen_wins_over_a_name_cut_short_but_never_over_a_clear_one(
    synthetic_catalog: list[str], isolated_home: AppPaths
) -> None:
    """The name sits on the hero's art and is read cut short ("Spirit Ey"): the hero the user named is taken. A name
    read exactly as another hero means the choice was not changed for the next hero (SPEC D96)."""
    from dataclasses import replace

    from e7ac.cli.roster import _catalog_heroes, _engine
    from e7ac.cli.skills import identify
    from e7ac.storage.db import session_scope

    with session_scope(_engine()) as session:
        heroes = _catalog_heroes(session) or {}
    read = popup()
    cut = replace(read, hero="Blood Bl")
    assert identify(cut, heroes) == ("c2011", "")  # the start of one hero's name and of no other
    assert identify(cut, heroes, "Blood Blade Karin") == ("c2011", "")
    assert identify(replace(read, hero=None), heroes, "c2011") == ("c2011", "")
    assert identify(replace(read, hero="Blood Blade Karin"), heroes, "Blood Blade Karin") == ("c2011", "")
    code, problem = identify(replace(read, hero="Karin"), heroes, "Blood Blade Karin")
    assert code is None and "shows 'Karin' but the hero chosen is 'Blood Blade Karin'" in problem
    assert identify(replace(read, hero="Karin"), heroes) == ("c1011", "")  # nothing chosen: the screen decides
    assert identify(cut, heroes, "Nobody Atall")[0] is None  # a hero that does not exist is not guessed


class Badge:
    """A badge that records what it is told."""

    def __init__(self) -> None:
        self.said: list[tuple[str, str]] = []

    def place(self, area: object) -> None: ...
    def settling(self) -> None: ...
    def captured(self, waiting: int) -> None: ...
    def result(self, name: str, outcome: str, waiting: int) -> None: ...

    def say(self, top: str, bottom: str, colour: str) -> None:
        self.said.append((top, bottom))

    def capturable(self, on: bool) -> bool:
        return False

    def resize(self, size: str) -> None: ...

    def region(self) -> Any:
        return None

    def close(self) -> None: ...


def test_the_watch_reads_a_popup_once_the_screen_has_settled(
    monkeypatch: pytest.MonkeyPatch, synthetic_catalog: list[str], isolated_home: AppPaths
) -> None:
    import threading

    from e7ac.cli import arena as arena_cli
    from e7ac.cli import skills as skills_cli
    from e7ac.vision.window import Rect

    frames = [np.full((36, 64, 3), 40, np.uint8)] * 6 + [np.full((36, 64, 3), 200, np.uint8)] * 6
    monkeypatch.setattr(arena_cli, "game_frames", lambda client, hwnd: iter([(f, Rect(0, 0, 64, 36)) for f in frames]))
    ticks = iter(x * 0.5 for x in range(1000))
    monkeypatch.setattr(skills_cli, "clock", lambda: next(ticks))
    monkeypatch.setattr(roster_cli, "reader_factory", lambda: FakeReader(named("Blood Blade Karin")))
    events: list[skills_cli.ReadEvent] = []
    badge = Badge()
    skills_cli.run_skill_watch(skills_cli.SkillWatchOptions(on_read=events.append), badge, threading.Event())
    assert [e.skill for e in events] == ["The Caress of Mercy", "The Caress of Mercy"]  # one per settled screen
    assert [e.new for e in events] == [True, False]  # the same skill held again: read, not new
    assert ("Read: Blood Blade Karin", "The Caress of Mercy +5") in badge.said
    assert SkillStore(isolated_home.home / "skills").heroes() == ["c2011"]


def test_the_watch_blanks_the_badge_when_it_shows_in_the_picture(
    monkeypatch: pytest.MonkeyPatch,
    synthetic_catalog: list[str],
    isolated_home: AppPaths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The badge is meant to be hidden from the capture; where it is not, its text must not be read as a popup."""
    import threading

    from e7ac.cli import arena as arena_cli
    from e7ac.cli import skills as skills_cli
    from e7ac.vision.window import Rect
    from tests.test_badge_view import _badge_picture

    place = Rect(100, 8, 220, 60)
    frames = [_badge_picture(np.full((400, 640, 3), v, np.uint8), place) for v in [40] * 6 + [200] * 6]
    monkeypatch.setattr(
        arena_cli, "game_frames", lambda client, hwnd: iter([(f, Rect(0, 0, 640, 400)) for f in frames])
    )
    ticks = iter(x * 0.5 for x in range(1000))
    monkeypatch.setattr(skills_cli, "clock", lambda: next(ticks))
    seen: list[np.ndarray] = []

    class Recording(FakeReader):
        def read(self, image: object) -> list[TextLine]:
            seen.append(np.array(image))
            return super().read(image)

    monkeypatch.setattr(roster_cli, "reader_factory", lambda: Recording(named("Blood Blade Karin")))

    class Shown(Badge):
        def region(self) -> Any:
            return place

    skills_cli.run_skill_watch(skills_cli.SkillWatchOptions(), Shown(), threading.Event())
    assert seen and all(not image[8:68, 100:320].any() for image in seen)  # the badge's place is blank
    assert seen[0][100:200, 100:200].all()  # the rest of the game is as it was
    assert "the badge shows in the screen capture on this PC" in capsys.readouterr().out


def test_a_skill_that_changes_form_is_found_by_either_of_its_names() -> None:
    from e7ac.skills.merge import place

    kit = {
        1: SkillInfo(1, 0, {}, "", "Half Moon Slash / Waning Moon Slash"),
        2: SkillInfo(2, 3, {}, "", "Hunter's Mark / Blade of Pursuit"),
        3: SkillInfo(3, 5, {}, "", "Pursuit of Death"),
    }
    assert kit[1].names == ("Half Moon Slash", "Waning Moon Slash") and kit[3].names == ("Pursuit of Death",)
    assert slot_of("Waning Moon Slash", kit) == 1 and slot_of("Half Moon Slash", kit) == 1
    assert slot_of("Blade of Pursuit", kit) == 2 and slot_of("Pursuit of Death", kit) == 3
    assert place("Hunter's Mark", kit) == (2, False) and place("Half Moon Slash", kit) == (1, False)


def test_a_wrong_reading_can_be_forgotten_with_the_effects_only_it_had_shown(tmp_path: Path) -> None:
    store = SkillStore(tmp_path)
    store.save("c1001", popup(name="First", effects=(EffectPanel("Junk", (), "Not an effect."),)), NOW, slot=1)
    store.save("c1001", popup(name="Second"), NOW, slot=2)
    store.save("c1002", popup(name="Other"), NOW, slot=1)  # shows Decrease Defense too
    assert set(store.effects()) == {"junk", "decrease defense"}
    assert store.forget("c1001", "first") == 1  # by name, once normalised
    assert [s.name for s in store.load("c1001")] == ["Second"] and set(store.effects()) == {"decrease defense"}
    assert store.forget("c1001", "first") == 0  # nothing left to forget
    assert store.forget("c1001") == 1 and store.load("c1001") == [] and store.heroes() == ["c1002"]
    assert set(store.effects()) == {"decrease defense"} and store.effects()["decrease defense"]["heroes"] == ["c1002"]


def test_skills_forget_drops_a_hero_reading_from_the_command_line(
    reading: Path, synthetic_catalog: list[str], isolated_home: AppPaths
) -> None:
    runner.invoke(app, ["skills", "read", str(reading)])
    store = SkillStore(isolated_home.home / "skills")
    assert store.heroes() == ["c2011"] and "lingering fragrance" in store.effects()
    one = runner.invoke(app, ["skills", "forget", "Blood Blade Karin", "--skill", "No Such Skill"])
    assert one.exit_code == 0 and "Forgot 0 skill(s)" in one.output and store.heroes() == ["c2011"]
    gone = runner.invoke(app, ["skills", "forget", "Blood Blade Karin"])
    assert gone.exit_code == 0 and "Forgot 1 skill(s): Blood Blade Karin." in gone.output
    assert store.heroes() == [] and store.effects() == {}
    assert runner.invoke(app, ["skills", "forget", "Nobody Atall"]).exit_code != 0


def test_readings_that_are_not_skills_are_told_from_real_ones_and_cleaned(tmp_path: Path) -> None:
    from e7ac.skills.clean import clean, find, why_junk

    store = SkillStore(tmp_path)
    real = "Attacks the enemy with a sheath, and grants stealth to the caster for 1 turn."
    store.save("c1001", popup(name="Mighty Strike", text=real), NOW, slot=1)
    store.save("c1001", popup(name="S3 Sword of Requiem |", text="Spirit Eye Celine Hero of the day", enhance=()), NOW)
    store.save("c1001", popup(name="God's Might", text="Read: Notos -", enhance=(), effects=()), NOW)
    store.save("c1001", popup(name="+3 Fighting Spirit at the start of the first battle", text="Preview Stats"), NOW)
    store.save("c1001", popup(name="Sixth Sense", text="Short.", enhance=(), effects=()), NOW)
    store.save(
        "c1001",
        popup(name="Pursuit of Death", text="Rhianna and Luciella never lose a target once they've marked them."),
        NOW,
    )
    assert [j.name for j in find(store)] == [
        "S3 Sword of Requiem |",
        "God's Might",
        "+3 Fighting Spirit at the start of the first battle",
        "Sixth Sense",
    ]
    assert why_junk(store.load("c1001")[0]) is None
    assert "Orbis window" in (why_junk(store.load("c1001")[1]) or "")
    assert len(clean(store)) == 4
    assert [s.name for s in store.load("c1001")] == ["Mighty Strike", "Pursuit of Death"]
    assert find(store) == []


def test_skills_clean_lists_then_drops_them_from_the_command_line(
    reading: Path, synthetic_catalog: list[str], isolated_home: AppPaths
) -> None:
    runner.invoke(app, ["skills", "read", str(reading)])
    store = SkillStore(isolated_home.home / "skills")
    store.save("c2011", popup(name="S2  Something |", text="ORBIS CODEX", enhance=(), effects=()), NOW)
    assert len(store.load("c2011")) == 2
    shown = runner.invoke(app, ["skills", "clean", "--dry-run"])
    assert shown.exit_code == 0 and "1 reading(s) would go." in shown.output and len(store.load("c2011")) == 2
    done = runner.invoke(app, ["skills", "clean"])
    assert done.exit_code == 0 and "1 reading(s) dropped." in done.output and len(store.load("c2011")) == 1
    assert "Nothing to clean." in runner.invoke(app, ["skills", "clean"]).output


def test_an_export_is_read_back_into_a_store_and_the_junk_in_it_is_left_out(tmp_path: Path) -> None:
    from e7ac.skills.importer import import_export, parse

    source = SkillStore(tmp_path / "source")
    real = "Attacks the enemy with a sheath, and grants stealth to the caster for 1 turn."
    source.save("c1001", popup(name="Mighty Strike", text=real), NOW)
    source.save("c1001", popup(name="Sixth Sense", level=None, text=real + " Passive.", enhance=(), effects=()), NOW)
    source.save("c1001", popup(name="S3 Sword of Requiem |", text="ORBIS CODEX", enhance=(), effects=()), NOW)
    note = SkillPopup(
        "Hero", "Blink", 3, (), None, "Blink 2 turns", "Moves, and grants +3 Speed.", "", None, (), (), 0.9
    )
    source.save("c1002", note, NOW)
    steps = ((1, "+5% damage"),)
    burn = SkillPopup(
        "Hero", "Burst", 2, (("Soul", 1),), 3, "", "Attacks all enemies.", "Hits harder.", 25, steps, (), 0.9
    )
    source.save("c1002", burn, NOW)
    text = export_text(source, {"c1001": "Alpha", "c1002": "Beta"}, now=NOW)

    heroes = parse(text)
    assert [(k, [p.name for p in v]) for k, v in heroes.items()] == [
        (("Alpha", "c1001"), ["Mighty Strike", "Sixth Sense", "S3 Sword of Requiem |"]),
        (("Beta", "c1002"), ["Blink", "Burst"]),
    ]
    mine = SkillStore(tmp_path / "mine")
    done = import_export(mine, text, lambda _name: {1: SkillInfo(1, 0, {}, "", "Mighty Strike")})
    assert (done.heroes, done.skills) == (2, 4) and done.left_out == ["Alpha: S3 Sword of Requiem |"]
    alpha = {s.name: s for s in mine.load("c1001")}
    assert alpha["Mighty Strike"].slot == 1 and alpha["Mighty Strike"].level == 6
    assert alpha["Mighty Strike"].enhance == source.load("c1001")[0].enhance
    assert [e.title for e in alpha["Mighty Strike"].effects] == ["Decrease Defense"]
    assert alpha["Sixth Sense"].level is None and alpha["Sixth Sense"].slot is None  # +0: numbers as shown, not placed
    beta = {s.name: s for s in mine.load("c1002")}
    assert beta["Blink"].cooldown is None and beta["Blink"].cooldown_note == "Blink 2 turns"
    assert beta["Burst"].cooldown == 3 and beta["Burst"].gains == (("Soul", 1),)
    assert beta["Burst"].soulburn == "Hits harder." and beta["Burst"].soulburn_cost == 25
    again = export_text(mine, {"c1001": "Alpha", "c1002": "Beta"}, now=NOW)
    assert again.split("== Beta")[1] == text.split("== Beta")[1]  # the heroes without junk come back word for word


def test_an_enhancement_step_that_starts_with_a_plus_is_not_cut_in_two() -> None:
    from e7ac.skills.importer import STEP

    line = "+1 +5% damage dealt; +2 +3 Fighting Spirit at the start of the battle; +3 Recovers 10% Health"
    assert STEP.findall(line) == [
        ("1", "+5% damage dealt"),
        ("2", "+3 Fighting Spirit at the start of the battle"),
        ("3", "Recovers 10% Health"),
    ]


def test_skills_import_loads_an_export_from_the_command_line(
    tmp_path: Path, isolated_home: AppPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    from e7ac.cli import skills as skills_cli

    source = SkillStore(tmp_path / "source")
    source.save("c2011", popup(name="Mighty Strike"), NOW)
    file = tmp_path / "export.txt"
    file.write_text(export_text(source, {"c2011": "Celine"}, now=NOW), encoding="utf-8")
    monkeypatch.setattr(skills_cli, "make_kit_of", lambda _paths: lambda _name: {})
    done = runner.invoke(app, ["skills", "import", str(file)])
    assert done.exit_code == 0 and "1 skill(s) of 1 hero(es) imported from export.txt." in done.output
    assert [s.name for s in SkillStore(isolated_home.home / "skills").load("c2011")] == ["Mighty Strike"]
    assert runner.invoke(app, ["skills", "import", str(tmp_path / "missing.txt")]).exit_code != 0


def test_a_text_that_stops_where_a_sentence_cannot_looks_cut() -> None:
    from e7ac.skills.completeness import fuller, looks_cut

    for cut in (
        "Decreases Attack and Speed by",
        "Increases damage suffered by 15% and",
        "Increases Attack by 50",
        "Grants a barrier, and",
        "Cannot be affected by any debuffs or",
    ):
        assert looks_cut(cut), cut
    for whole in (
        "Decreases Attack and Speed by 30%.",
        "Cannot die for a certain number of turns.",
        "Poison, Decrease Speed, Decrease Defense",  # the effect lists end on a word, without a stop
        "",
    ):
        assert not looks_cut(whole), whole
    old, new = "Decreases Evasion by 50%.", "Decreases Evasion by"
    assert fuller(old, new) == old  # a reading that is only the start of the old one does not replace it
    assert fuller(new, old) == old  # the whole one replaces the cut one
    assert fuller("Decreases Defense by 70%.", "Decreases Defense by 60%.") == "Decreases Defense by 60%."  # an update
    assert fuller("Removes it.", "Removes the debuff and") == "Removes it."  # a cut reading never beats a whole one


def test_a_worse_reading_of_a_skill_never_replaces_a_better_one(tmp_path: Path) -> None:
    from dataclasses import replace

    store = SkillStore(tmp_path)
    whole = EffectPanel("Vigor", (), "Increases Attack and Defense by 30%.")
    store.save("c1001", popup(effects=(whole,)), NOW)
    cut = replace(whole, text="Increases Attack and Defense by")
    again = store.save("c1001", popup(effects=(cut,)), NOW)
    assert store.load("c1001")[0].effects[0].text == whole.text and not again.new_effects
    assert store.effects()["vigor"]["text"] == whole.text
    # and the other way round: a skill first read cut is completed by the next reading
    other = SkillStore(tmp_path / "other")
    other.save("c1001", popup(effects=(cut,)), NOW)
    other.save("c1001", popup(effects=(whole,)), NOW)
    assert other.load("c1001")[0].effects[0].text == whole.text and other.effects()["vigor"]["text"] == whole.text
    first = SkillStore(tmp_path / "text")
    first.save(
        "c1001", popup(text="Attacks the enemy and has a 60% chance to inflict decreased Defense for 1 turn."), NOW
    )
    first.save("c1001", popup(text="Attacks the enemy and has a 60% chance to inflict decreased Defense for"), NOW)
    assert first.load("c1001")[0].text.endswith("for 1 turn.")


def test_skills_check_lists_the_readings_that_look_cut_and_the_event_says_so(
    reading: Path, synthetic_catalog: list[str], isolated_home: AppPaths
) -> None:
    from e7ac.skills.completeness import cut_parts

    store = SkillStore(isolated_home.home / "skills")
    cut = EffectPanel("Vigor", (), "Increases Attack and Defense by")
    store.save("c2011", popup(name="Cut Skill", effects=(cut,)), NOW)
    assert cut_parts(store.load("c2011")[0]) == ["effect Vigor"]
    listed = runner.invoke(app, ["skills", "check"])
    assert listed.exit_code == 0 and "Cut Skill" in listed.output and "effect Vigor looks cut" in listed.output
    assert "stored cut" in listed.output and "2 problem(s)." in listed.output  # cut, and its place is not known
    store.save("c2011", popup(name="Cut Skill", effects=(EffectPanel("Vigor", (), "Increases Attack by 30%."),)), NOW)
    again = runner.invoke(app, ["skills", "check"]).output
    assert "effect Vigor" not in again and "1 problem(s)." in again  # whole now: only the place is not known


def test_a_popup_whose_effect_stops_short_is_stored_and_the_event_says_it_looks_cut(
    synthetic_catalog: list[str], isolated_home: AppPaths
) -> None:
    from dataclasses import replace

    from e7ac.cli.roster import _catalog_heroes, _engine
    from e7ac.cli.skills import describe, store_popup
    from e7ac.storage.db import session_scope

    with session_scope(_engine()) as session:
        heroes = _catalog_heroes(session) or {}
    store = SkillStore(isolated_home.home / "skills")
    cut = EffectPanel("Vigor", (), "Increases Attack and Defense by")
    event = store_popup(store, heroes, replace(popup(effects=(cut,)), hero="Blood Blade Karin"))
    assert event.cut == (
        "effect Vigor looks cut",
    ) and "PROBLEM (effect Vigor looks cut): hold the skill again" in describe(event)
    whole = EffectPanel("Vigor", (), "Increases Attack and Defense by 30%.")
    again = store_popup(store, heroes, replace(popup(effects=(whole,)), hero="Blood Blade Karin"))
    assert again.cut == () and "PROBLEM" not in describe(again)


def test_every_kind_of_problem_is_found_and_the_ones_that_can_be_read_again_are_cleared(tmp_path: Path) -> None:
    from e7ac.skills.problems import clear_problems, find_problems, hero_problems

    store = SkillStore(tmp_path)
    good = "Attacks the enemy with a sheath, and grants stealth to the caster for 1 turn."
    store.save("c1001", popup(name="Whole", text=good), NOW, slot=1)
    store.save(
        "c1001", popup(name="Cut", text=good, effects=(EffectPanel("Vigor", (), "Increases Attack by"),)), NOW, slot=2
    )
    store.save("c1001", popup(name="S3 Window |", text="Spirit Eye Celine Hero of the day", enhance=()), NOW)
    store.save("c1002", popup(name="Loose", text=good), NOW)  # read, but its place is not known
    found = {(p.skill, p.kind) for p in find_problems(store)}
    assert found == {("Cut", "cut"), ("S3 Window |", "junk"), ("Loose", "unplaced")}
    assert [p.kind for p in hero_problems(store.load("c1002"))] == ["unplaced"]
    gone = clear_problems(store)
    assert {(p.skill, p.kind) for p in gone} == {("Cut", "cut"), ("S3 Window |", "junk")}  # "Loose" is only shown
    assert [s.name for s in store.load("c1001")] == ["Whole"] and [s.name for s in store.load("c1002")] == ["Loose"]
    assert "vigor" not in store.effects()  # the effect only the dropped skill had shown goes with it
    assert clear_problems(store) == []


def test_a_panel_that_swallowed_the_next_one_is_a_problem() -> None:
    from e7ac.skills.problems import holds_title

    glued = "Decreases Attack and Speed by 50%. XV Bomb At the end of the duration, suffers damage."
    assert holds_title(glued, ["Bomb", "Bleed"]) == "Bomb"
    assert holds_title("Decreases Attack and Speed by 50%.", ["Bomb"]) is None
    assert holds_title("Bomb damage is unaffected by the number of turns.", ["Bomb"]) is None  # not a title: "damage"
    assert holds_title("Poison, Bleed, Burn, Venom, and Erosion damages are proportional.", ["Bleed"]) is None


def test_a_badge_read_into_the_end_of_a_description_is_a_problem() -> None:
    from e7ac.skills.completeness import stray_badge
    from e7ac.skills.problems import skill_problems

    assert stray_badge("... detonates bleedingeffects inflicted on the target.+3") == "+3"
    assert stray_badge("Increases Attack by 50%.") is None and stray_badge("Grants +3 Speed.") is None
    skill = StoredSkill(
        "c1001", "This'll Sting!", 6, (), None, "", "Attacks the enemy with a scalpel, then it ends.+3", "", None, (),
        (), 0.9, "", 1, False,
    )  # fmt: skip
    assert [p.detail for p in skill_problems(skill)] == ["its description ends with a stray '+3' (a badge)"]
