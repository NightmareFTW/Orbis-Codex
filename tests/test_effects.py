"""Automatic skill effects (SPEC D63): epic7db kit parsing, effect rules, and their use in battles (synthetic)."""

from __future__ import annotations

from e7ac.domain.codes import Stat
from e7ac.predict.battle import Combatant, simulate
from e7ac.predict.damage import SkillPower
from e7ac.predict.effects import Effect, Kit, kit_from
from e7ac.sources.epic7db import SkillInfo, parse_kit

PAGE = (
    '<img src="/images/skills/x_skill_1.webp"><div class="cooldown"> None </div><div class="status-effects">'
    '<img src="/images/status_effects/decrease-defense.png" alt="Decrease Defense" title="Decreases Defense by 70%.">'
    '</div><div class="bottom"><p>Attacks with a 60% chance to decrease Defense for 2 turns.</p></div>'
    '<img src="/images/skills/x_skill_2.webp"><div class="cooldown"> Passive </div><div class="status-effects">'
    '<img src="/images/status_effects/increase-cr.png" alt="Increase Combat Readiness" title="Increases CR.">'
    '</div><div class="bottom"><p>Upon receiving lethal damage, revives with 50% Health and increases Combat '
    "Readiness by 100%.</p></div>"
)


def test_a_kit_is_read_from_icons_and_text() -> None:
    kit = kit_from(parse_kit(PAGE))
    assert kit.on_use[1] == [Effect("def", True, -0.7, 2, 0.6)]
    assert kit.revive == 0.5 and kit.revive_effects == [Effect("cr", False, 1.0, 2, 1.0)]
    unknown = kit_from({1: SkillInfo(1, 0, {"Healing": None, "Increase Speed": None}, "Heals.")})
    assert unknown.unmodelled == ["Healing", "Increase Speed"]  # no value given: never guessed


S1 = {1: SkillPower(rate=1.0, pow=1.0, enhance=0.0, can_crit=False)}


def unit(label: str, side: str, kit: Kit | None = None, hp: float = 10000, resist: float = 0.0) -> Combatant:
    stats = {
        Stat.ATK: 3000.0,
        Stat.HP: hp,
        Stat.DEF: 300.0,
        Stat.CRIT_CHANCE: 0.0,
        Stat.CRIT_DAMAGE: 1.5,
        Stat.EFFECTIVENESS: 0.0,
        Stat.EFFECT_RESISTANCE: resist,
    }
    return Combatant(label, side, stats, 200, S1, {}, kit or Kit())


def test_a_revive_and_immortality_change_the_outcome() -> None:
    plain = simulate([unit("a", "you"), unit("b", "them")], runs=400).win
    revive = Kit(revive=1.0)
    assert simulate([unit("a", "you", revive), unit("b", "them")], runs=400).win > plain
    immortal = Kit(at_start=[Effect("immortal", False, 0.0, 9, 1.0)])  # outlasts the 4 hits this fight needs
    assert simulate([unit("a", "you", immortal), unit("b", "them")], runs=400).win > plain


def test_a_stun_lands_only_through_effect_resistance() -> None:
    stun = Kit(on_use={1: [Effect("stun", True, 0.0, 1, 1.0)]})
    open_target = simulate([unit("a", "you", stun), unit("b", "them")], runs=400).win
    resistant = simulate([unit("a", "you", stun), unit("b", "them", resist=1.0)], runs=400).win
    assert open_target > resistant  # 1 + EFF - ER = 0: never lands (MECH-EFF-01)
