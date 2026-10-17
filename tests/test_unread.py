"""A chance that leaves skills out is not shown as a promise (SPEC D87): the match lost after "Win chance 100%"."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from typing import Any, cast

from e7ac.arena.compare import OpponentOdds, chance_text, mark_best, unread_note, verdict
from e7ac.domain.codes import Stat
from e7ac.predict.battle import BattleOdds, Combatant, simulate
from e7ac.predict.damage import SkillPower
from e7ac.predict.effects import Kit, describes_effects, kit_from
from e7ac.predict.overrides import KitOverride, apply_override
from e7ac.predict.worklist import effective_kit, worklist
from e7ac.sources.epic7db import SkillInfo
from e7ac.ui.watch_signal import AMBER, GREEN

ARUNKA_S1 = (
    "Attacks the enemy with a road sign and has a 60% chance to inflict decreased Defense for 1 turn. Damage dealt "
    "increases proportional to the caster's Defense ."
)
BYBLIS_S3 = (
    "After granting increased Effectiveness to the caster for 3 turns, spreads the scent of death, with a 100% chance "
    "to inflict two venom effects on all enemies for 2 turns. At the end of the turn, detonates venom effects."
)


def test_a_skill_that_only_hits_harder_with_a_stat_has_no_effects_to_read() -> None:
    assert describes_effects(ARUNKA_S1)
    assert describes_effects(BYBLIS_S3)
    assert not describes_effects("Attacks the enemy with a road sign. Damage dealt increases proportional to Defense .")
    assert not describes_effects("Attacks the enemy with scented paper.")
    assert describes_effects(
        "Attacks the enemy and recovers Health of all allies proportional to the target's max Health."
    )


def test_skills_with_effects_in_the_text_and_none_read_are_not_read() -> None:
    skills = {
        1: SkillInfo(1, 0, {}, ARUNKA_S1),  # a newer hero: epic7db shows no icons at all
        2: SkillInfo(2, None, {}, "Attacks with a road sign."),
        3: SkillInfo(3, 4, {}, BYBLIS_S3),
    }
    assert kit_from(skills).unread == [1, 3]
    read = {1: SkillInfo(1, 0, {"Decrease Defense": 0.7}, ARUNKA_S1)}
    assert kit_from(read).unread == []  # its effect was read: whatever else is in the text is not guessed at
    counter = kit_from({2: SkillInfo(2, None, {}, "When attacked, has a 30% chance to counterattack.")})
    assert counter.counter == 0.3 and counter.unread == []
    assert kit_from({}).unread == []


def test_a_hand_kit_covers_the_hero_and_a_missing_page_leaves_all_three_skills_unread() -> None:
    kit = kit_from({1: SkillInfo(1, 0, {}, ARUNKA_S1)})
    assert kit.unread == [1]
    done, _ = apply_override(dataclasses.replace(kit), {}, _hand())
    assert done.unread == []
    blank, _ = effective_kit(cast(Any, None), "Nobody", {}, None, lambda http, name: {})
    assert blank.unread == [1, 2, 3] and "(no epic7db page)" in blank.unmodelled


def _hand() -> KitOverride:
    from e7ac.predict.overrides import parse_override

    return parse_override('hero = "c2124"\nsource = "test"\nstatus = "verified"\nreplace = [1]\n')


def _unit(label: str, side: str, unread: list[int] | None = None) -> Combatant:
    stats = {Stat.ATK: 2000.0, Stat.HP: 10000.0, Stat.DEF: 300.0, Stat.CRIT_CHANCE: 0.5, Stat.CRIT_DAMAGE: 2.0}
    return Combatant(
        label, side, stats, 200, {1: SkillPower(rate=1.0, pow=1.0, enhance=0.0)}, kit=Kit(unread=unread or [])
    )


def test_the_result_says_which_heroes_were_made_plain_attackers() -> None:
    odds = simulate([_unit("a", "you"), _unit("b", "them", [2, 3]), _unit("c", "them", [1])], runs=50)
    assert odds.blind == ("b (S2, S3)", "c (S1)")
    assert simulate([_unit("a", "you"), _unit("b", "them")], runs=50).blind == ()


def _row(number: int, win: float, blind: tuple[str, ...] = ()) -> OpponentOdds:
    return OpponentOdds(number, ("c1",) * 4, win, 0.0, "", blind=blind)


def test_among_equal_chances_the_better_known_team_is_the_pick() -> None:
    # all three look like a certain win; the first is two heroes in the dark, the third none: attack the third
    rows = mark_best([_row(1, 1.0, ("a", "b")), _row(2, 1.0, ("c",)), _row(3, 1.0)])
    assert [r.best for r in rows] == [False, False, True]
    # a clearly higher chance still wins over a better known team
    rows = mark_best([_row(1, 0.9, ("a",)), _row(2, 0.6)])
    assert [r.best for r in rows] == [True, False]
    # close (within 2 points) counts as equal
    rows = mark_best([_row(1, 0.80, ("a",)), _row(2, 0.79)])
    assert [r.best for r in rows] == [False, True]


def test_a_chance_is_never_100_and_a_shaky_one_has_a_tilde() -> None:
    assert [chance_text(w) for w in (1.0, 0.996, 0.72, 0.004, 0.0, None)] == ["99%+", "99%+", "72%", "<1%", "<1%", "?"]
    assert unread_note(()) == "" and unread_note(("a",)) == "skills of 1 hero not read"
    assert unread_note(("a", "b")) == "skills of 2 heroes not read"
    rows = mark_best([_row(1, 1.0, ("a",)), _row(2, 0.55)])
    assert verdict(rows) == ("Best: opponent 1 (~99%+)", "1: ~99%+  2: 55%")
    assert _row(1, 0.5).describe() == "50%" and OpponentOdds(1, (), 0.5, 0.03, "").describe() == "50% (+/-3%)"
    assert OpponentOdds(2, (), None, None, "a hero is not recognised").describe() == "a hero is not recognised"


def test_the_badge_does_not_promise_what_the_model_cannot_see(monkeypatch: Any) -> None:
    from e7ac.cli import arena as arena_cli
    from e7ac.cli import predict as predict_cli

    said: list[tuple[str, str, str]] = []
    context = SimpleNamespace(
        heroes={},
        offline=True,
        badge=SimpleNamespace(say=lambda top, bottom, colour: said.append((top, bottom, colour))),
        last=[("", "", "")],
        options=SimpleNamespace(teams=None),
    )
    monkeypatch.setattr(
        predict_cli, "win_odds", lambda *a, **k: BattleOdds(1.0, 0.0, 0.0, 9, 1000, [], ("Byblis (S3)",))
    )
    arena_cli._predict(["c1001"], ["c1005"], cast(Any, context))
    top, bottom, colour = said[-1]
    assert top == "Win chance ~99%+" and "unreliable" in bottom and colour == AMBER  # not green
    assert "skills of 1 hero not read" in context.last[0][1]
    monkeypatch.setattr(predict_cli, "win_odds", lambda *a, **k: BattleOdds(0.7, 0.3, 0.0, 30, 1000))
    arena_cli._predict(["c1001"], ["c1005"], cast(Any, context))
    assert said[-1][0] == "Win chance 70%" and said[-1][2] == GREEN


def test_the_worklist_lists_what_was_never_read() -> None:
    skills = {1: SkillInfo(1, 0, {}, ARUNKA_S1), 3: SkillInfo(3, 3, {}, BYBLIS_S3)}
    heroes = {"c2124": SimpleNamespace(name="Boss Arunka")}
    rows = worklist([("c2124", 5)], cast(Any, heroes), {}, {}, cast(Any, None), lambda http, name: skills)
    assert rows[0].unmodelled == ("S1: effects in its text, none read", "S3: effects in its text, none read")


def test_a_hand_kit_can_keep_a_skill_flagged_as_not_read() -> None:
    import pytest

    from e7ac.predict.overrides import KitError, parse_override

    head = 'hero = "c5154"\nsource = "test"\nstatus = "community"\nreplace = [1]\n'
    hand = parse_override(head + "unread = [3]\n")
    kit = kit_from({1: SkillInfo(1, 0, {}, ARUNKA_S1), 3: SkillInfo(3, 4, {}, BYBLIS_S3)})
    assert kit.unread == [1, 3]
    done, _ = apply_override(kit, {}, hand)
    assert done.unread == [3]  # S1 is written by hand, the venom of S3 is not: the hero stays flagged
    assert apply_override(kit, {}, parse_override(head))[0].unread == []
    with pytest.raises(KitError, match="slots 1, 2 or 3"):
        parse_override(head + "unread = [4]\n")
