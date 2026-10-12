"""Battle simulation (SPEC D61): synthetic fighters."""

from __future__ import annotations

import pytest

from e7ac.domain.codes import Stat
from e7ac.predict.battle import Combatant, simulate
from e7ac.predict.damage import SkillPower

S1 = {1: SkillPower(rate=1.0, pow=1.0, enhance=0.0)}


def fighter(label: str, side: str, atk: float, hp: float = 10000, speed: float = 200) -> Combatant:
    stats = {Stat.ATK: atk, Stat.HP: hp, Stat.DEF: 300.0, Stat.CRIT_CHANCE: 0.5, Stat.CRIT_DAMAGE: 2.0}
    return Combatant(label, side, stats, speed, S1)


def test_mirror_match_is_even_and_reproducible() -> None:
    units = [fighter("a", "you", 2000), fighter("b", "them", 2000)]
    once, again = simulate(units, runs=2000), simulate(units, runs=2000)
    assert once == again and 0.4 < once.win < 0.6 and once.win + once.loss + once.draw == pytest.approx(1)


def test_a_much_stronger_and_faster_side_wins() -> None:
    odds = simulate([fighter("a", "you", 6000, speed=250), fighter("b", "them", 1000)], runs=500)
    assert odds.win == 1.0 and odds.mean_actions < 10


def test_both_sides_are_needed_and_skillless_heroes_are_warned() -> None:
    with pytest.raises(ValueError, match="both sides"):
        simulate([fighter("a", "you", 1000)])
    mute = Combatant("m", "them", fighter("m", "them", 1).stats, 200, {})
    odds = simulate([fighter("a", "you", 1000), mute], runs=50)
    assert odds.win == 1.0 and odds.warnings == ["m: no damaging skill known"]


def test_s3_comes_back_after_its_cooldown() -> None:
    strong = {1: SkillPower(rate=0.1, pow=1.0, enhance=0.0), 3: SkillPower(rate=3.0, pow=1.0, enhance=0.0)}
    you = fighter("a", "you", 3000, speed=300)
    tank = fighter("t", "them", 1, hp=200000, speed=100)

    def battle(cooldowns: dict[int, int]) -> float:
        unit = Combatant("a", "you", you.stats, 300, strong, cooldowns)
        return simulate([unit, tank], runs=200).mean_actions

    assert battle({3: 0}) < battle({3: 2}) < battle({3: 50})  # the shorter the cooldown, the sooner the tank falls


def test_cooldowns_are_read_from_an_epic7db_page() -> None:
    from e7ac.sources.epic7db import parse_cooldowns, slug

    page = "".join(
        f'<img src="/images/skills/x_skill_{n}.webp"><div class="top"><div class="cooldown"> {text} </div>'
        for n, text in ((1, "None"), (2, "Passive"), (3, "5 Turns"))
    )
    assert parse_cooldowns(page) == {1: 0, 3: 5}
    assert slug("Alexa's Basket") == "alexas-basket" and slug("Arbiter Vildred") == "arbiter-vildred"
