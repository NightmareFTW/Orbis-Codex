"""The mechanics read from the skill screens of Lady of the Scales, Boss Arunka, Perfumer Byblis and Rinak (SPEC D89):
offering, abundance, the Combat Readiness mirror, taunt, venom and its detonation, Effectiveness buffs, fixed damage,
Lingering Fragrance, La Vie en Rose, Pilfer and Spoils; and the four hand kits that use them (synthetic units)."""

from __future__ import annotations

import dataclasses
import random
from typing import Any

import pytest

from e7ac.domain.codes import Stat
from e7ac.predict import battle
from e7ac.predict.battle import (
    Combatant,
    _after_hit,
    _battle,
    _raise_cr,
    _State,
    _strike,
    _use,
    simulate,
)
from e7ac.predict.damage import SkillPower, expected_hit
from e7ac.predict.effects import Effect, Kit
from e7ac.predict.overrides import apply_override, load_overrides
from tests.test_effects import unit


class Always(random.Random):
    """Every chance lands."""

    def random(self) -> float:
        return 0.0


class Never(random.Random):
    """No chance lands (0.9 is above the 85% cap of MECH-EFF-01)."""

    def random(self) -> float:
        return 0.9


def world(*units: Combatant) -> tuple[list[Combatant], list[float], list[float], list[_State]]:
    hp = [u.stats[Stat.HP] for u in units]
    return list(units), hp, [0.0] * len(units), [_State() for _ in units]


def heal_all(share: float) -> Effect:
    return Effect("heal", False, 0.0, 0, 1.0, scaling=(share, 0.0, 0.0), receivers="all")


# --- Offering -------------------------------------------------------------------------------------------------------


def test_the_offering_holder_takes_70_percent_of_what_its_allies_take() -> None:
    units, hp, cr, state = world(unit("holder", "you"), unit("ally", "you"), unit("foe", "them"))
    state[0].offering, state[0].offering_share = True, 0.7
    lost, lethal = _strike(units, hp, cr, state, 1, 1000.0, Always())
    assert (hp[0], hp[1], lost, lethal) == (9300.0, 9700.0, 300.0, False)
    _strike(units, hp, cr, state, 0, 1000.0, Always())  # the holder itself takes all of its own damage
    assert hp[0] == 8300.0
    _strike(units, hp, cr, state, 2, 1000.0, Always())  # an enemy is no ally of the holder
    assert hp[2] == 9000.0


def test_the_holder_is_immortal_once_and_its_death_heals_the_granters_allies() -> None:
    lady = unit("lady", "you", Kit(offering_death_effects=[heal_all(0.5)]), hp=8000)
    units, hp, cr, state = world(unit("holder", "you"), unit("ally", "you"), lady, unit("foe", "them"))
    state[0].offering, state[0].offering_share, state[0].offering_turns, state[0].offering_by = True, 0.7, 1, 2
    hp[0], hp[1] = 100.0, 2000.0
    _strike(units, hp, cr, state, 1, 500.0, Always())  # the holder's 350 is lethal: immortal for a turn, once
    assert hp[0] == 1.0 and state[0].timers["immortal"] == 1 and state[0].offering_used
    state[0].timers.clear()
    _strike(units, hp, cr, state, 1, 100.0, Always())  # lethal again: it dies, and Lady heals the living allies
    assert hp[0] <= 0 and not state[0].offering
    assert hp[1] == pytest.approx(2000.0 - 500.0 * 0.3 - 100.0 * 0.3 + 0.5 * 8000)
    assert hp[2] == 8000.0


def test_offering_is_granted_to_the_foremost_other_ally_and_its_cost_never_kills() -> None:
    units, hp, cr, state = world(unit("lady", "you"), unit("front", "you"), unit("back", "you"), unit("foe", "them"))
    _use(Effect("offering", False, 0.7, 1, 1.0), 0, [], units, hp, cr, state, Always())
    assert [s.offering for s in state] == [False, True, False, False] and state[1].offering_by == 0
    _use(Effect("offering_cost", False, 0.2, 0, 1.0), 0, [], units, hp, cr, state, Always())
    assert hp[1] == 8000.0
    hp[1] = 1.0
    _use(Effect("offering_cost", False, 0.2, 0, 1.0), 0, [], units, hp, cr, state, Always())
    assert hp[1] == 1.0  # "consumes" Health, never to death


def test_abundance_adds_damage_after_each_attack_and_not_to_the_offering_holder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    units, hp, cr, state = world(unit("holder", "you"), unit("ally", "you"), unit("lady", "you"), unit("foe", "them"))
    state[0].offering = True
    effect = Effect("abundance", False, 2000.0, 2, 1.0, receivers="not_offering")
    _use(effect, 2, [], units, hp, cr, state, Always())
    assert [s.timers.get("abundance", 0) for s in state] == [0, 2, 2, 0]
    assert state[1].abundance == 2000.0
    state[1].remove(debuffs=False, count=0)  # a dispel takes it away
    assert "abundance" not in state[1].timers
    # in a battle: the holder's attack is followed by the extra damage (a second hit of 2,000 on the same target)
    seen: list[tuple[int, float, int]] = []
    real = battle._strike

    def spy(*args: Any, **kwargs: Any) -> tuple[float, bool]:
        seen.append((args[4], args[5], args[7] if len(args) > 7 else kwargs.get("by", -1)))
        return real(*args, **kwargs)

    kit = Kit(at_start=[Effect("abundance", False, 2000.0, 9, 1.0)])
    fighters = [unit("a", "you", kit), unit("b", "them", hp=10**7)]
    hits = {(0, 1, 1): 1000.0, (1, 0, 1): 1.0}
    monkeypatch.setattr(battle, "_strike", spy)
    _battle(fighters, hits, random.Random(1))
    dealt = [damage for target, damage, by in seen if by == 0 and target == 1]
    assert dealt[0] != 2000.0 and dealt[1] == 2000.0  # the attack, then the abundance damage on the same target


# --- Combat Readiness ------------------------------------------------------------------------------------------------


def test_an_enemys_combat_readiness_increase_is_mirrored_by_lady() -> None:
    units, hp, cr, state = world(unit("lady", "you", Kit(cr_mirror=0.5)), unit("foe", "them"), unit("foe2", "them"))
    _raise_cr(units, hp, cr, state, 1, 0.4)
    assert cr == [0.2, 0.4, 0.0]
    cr[2] = 0.9
    _raise_cr(units, hp, cr, state, 2, 0.4)  # only the 10% that was really added counts, nothing above 100%
    assert cr[2] == 1.0 and cr[0] == pytest.approx(0.25)
    _raise_cr(units, hp, cr, state, 0, 0.3)  # her own side's increase is not mirrored
    assert cr[1] == 0.4


def test_being_attacked_gives_combat_readiness_to_a_hero_with_hit_cr() -> None:
    tank = unit("tank", "you", Kit(hit_cr=0.25))
    units, hp, cr, state = world(tank, unit("foe", "them"))
    _after_hit(0, 1, 100.0, True, units, hp, cr, state, {}, Always())
    assert cr[0] == 0.25


# --- Taunt -----------------------------------------------------------------------------------------------------------


def test_a_single_target_skill_can_only_pick_the_hero_in_front_with_ferocious_stand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attacker = unit("a", "you")
    front = unit("front", "them", Kit(taunt=True), hp=10**7)
    weak = unit("weak", "them", hp=1000)
    targets: list[int] = []
    real = battle._strike

    def spy(*args: Any, **kwargs: Any) -> tuple[float, bool]:
        if args[7] == 0:  # the strikes of the attacker
            targets.append(args[4])
        return real(*args, **kwargs)

    hits = {(0, 1, 1): 100.0, (0, 2, 1): 100000.0, (1, 0, 1): 1.0, (2, 0, 1): 1.0}
    monkeypatch.setattr(battle, "_strike", spy)
    _battle([attacker, front, weak], hits, random.Random(3))
    assert targets[0] == 1  # the weak one would be a one-hit kill, but the taunting hero is in front
    behind = dataclasses.replace(front, kit=Kit())  # without it the weak hero is picked first
    targets.clear()
    _battle([attacker, behind, weak], hits, random.Random(3))
    assert targets[0] == 2


# --- Venom, Effectiveness, fixed damage ------------------------------------------------------------------------------


def test_venom_stacks_and_detonates_for_the_turns_left_times_the_stacks() -> None:
    units, hp, cr, state = world(unit("byblis", "you"), unit("foe", "them"))
    venom = Effect("venom", True, 0.1, 2, 1.0, count=2, receivers="enemies")
    _use(venom, 0, [], units, hp, cr, state, Always())
    assert state[1].venoms == [(0.1, 2), (0.1, 2)]
    _use(Effect("detonate", True, 0.0, 0, 1.0, receivers="enemies"), 0, [], units, hp, cr, state, Never())
    assert hp[1] == pytest.approx(10000 - 0.1 * 10000 * 2 * 2) and state[1].venoms == []  # always lands
    _use(venom, 0, [], units, hp, cr, state, Never())  # each stack rolls its own chance
    assert state[1].venoms == []


def test_venom_ticks_at_the_start_of_the_holders_turn_and_a_cleanse_removes_it() -> None:
    victim = unit("v", "you", hp=1000)
    foe = unit("f", "them", hp=10**7)
    # a venom of 60% of max Health per turn on a hero that has to act before anything else kills it
    fighters = [dataclasses.replace(victim, speed=300), foe]
    hits = {(0, 1, 1): 1.0, (1, 0, 1): 1.0}
    rng = random.Random(1)
    state = [_State(), _State()]
    state[0].venoms = [(0.6, 3)]
    assert battle._play(fighters, hits, rng, state)[0] == "them"  # it dies of venom ticks alone
    cleansed = _State()
    cleansed.venoms = [(0.6, 3)]
    cleansed.remove(debuffs=True, count=0)
    assert cleansed.venoms == []


def test_an_effectiveness_buff_makes_debuffs_land() -> None:
    units, hp, cr, state = world(unit("a", "you"), unit("foe", "them", resist=0.6))
    stun = Effect("stun", True, 0.0, 1, 1.0)
    rng = Never()
    rng.random = lambda: 0.6  # type: ignore[method-assign]  # 40% to land: 1 + 0 - 0.6; with +50%: capped at 85%
    _use(stun, 0, [1], units, hp, cr, state, rng)
    assert "stun" not in state[1].timers
    _use(Effect("eff", False, 0.5, 3, 1.0), 0, [], units, hp, cr, state, rng)
    _use(stun, 0, [1], units, hp, cr, state, rng)
    assert state[1].timers["stun"] == 1


def test_fixed_damage_ignores_defence_and_hits_every_enemy() -> None:
    tough = dataclasses.replace(unit("t", "them"), stats={**unit("t", "them").stats, Stat.DEF: 10**6})
    units, hp, cr, state = world(unit("rinak", "you"), tough, unit("foe2", "them"))
    _use(Effect("fixed_damage", True, 7000.0, 0, 1.0, receivers="enemies"), 0, [], units, hp, cr, state, Never())
    assert hp[1] == 3000.0 and hp[2] == 3000.0 and hp[0] == 10000.0


def test_a_debuff_on_oneself_always_lands_through_immunity_and_resistance() -> None:
    sealed = unit("rinak", "you", resist=5.0)
    units, hp, cr, state = world(sealed, unit("foe", "them"))
    state[0].timers["immunity"] = 5
    state[0].buffs["atk"] = (0.5, 2)
    _use(Effect("stun", True, 0.0, 2, 1.0, receivers="self"), 0, [], units, hp, cr, state, Never())
    assert state[0].timers["stun"] == 2
    _use(Effect("dispel", True, 0.0, 0, 1.0, count=0, receivers="self"), 0, [], units, hp, cr, state, Never())
    assert "atk" not in state[0].buffs and "immunity" not in state[0].timers


# --- Lingering Fragrance and La Vie en Rose --------------------------------------------------------------------------


def test_lingering_fragrance_reflects_part_of_the_damage_any_ally_suffers() -> None:
    byblis = unit("byblis", "you", Kit(fragrance=0.08))
    units, hp, cr, state = world(unit("ally", "you"), byblis, unit("foe", "them"))
    _after_hit(0, 2, 1000.0, True, units, hp, cr, state, {}, Always())
    assert hp[2] == pytest.approx(10000 - 80.0)
    hp[1] = 0.0  # she is dead: the fragrance goes with her
    _after_hit(0, 2, 1000.0, True, units, hp, cr, state, {}, Always())
    assert hp[2] == pytest.approx(10000 - 80.0)


def test_la_vie_en_rose_fires_when_an_attacked_ally_is_at_half_health_and_then_waits() -> None:
    effects = [
        Effect("cleanse", False, 0.0, 0, 1.0, receivers="all", count=2),
        Effect("heal", False, 0.0, 0, 1.0, scaling=(0.23, 0.0, 0.0), receivers="all"),
        Effect("barrier", False, 0.0, 2, 1.0, scaling=(0.1, 0.0, 0.0), receivers="all"),
    ]
    byblis = unit("byblis", "you", Kit(ally_low=0.5, ally_low_cooldown=3, ally_low_effects=effects))
    units, hp, cr, state = world(unit("ally", "you"), byblis, unit("foe", "them"))
    state[0].debuffs["atk"] = (-0.5, 2)
    hp[0] = 6000.0
    _after_hit(0, 2, 1000.0, True, units, hp, cr, state, {}, Always())  # 60%: not low
    assert hp[0] == 6000.0 and state[1].ally_wait == 0
    hp[0] = 4000.0
    _after_hit(0, 2, 1000.0, True, units, hp, cr, state, {}, Always())
    assert hp[0] == pytest.approx(4000.0 + 0.23 * 10000) and state[0].barrier == 1000.0 and not state[0].debuffs
    assert state[1].ally_wait == 3
    hp[0] = 4000.0
    _after_hit(0, 2, 1000.0, True, units, hp, cr, state, {}, Always())  # still waiting
    assert hp[0] == 4000.0


# --- Pilfer and Spoils -----------------------------------------------------------------------------------------------


def test_pilfer_weakens_the_target_and_feeds_the_casters_spoils_until_they_are_dispelled() -> None:
    units, hp, cr, state = world(unit("rinak", "you"), unit("a", "them"), unit("b", "them"))
    pilfer = Effect("pilfer", True, 0.0, 99, 1.0)
    _use(pilfer, 0, [1], units, hp, cr, state, Always())
    _use(pilfer, 0, [1], units, hp, cr, state, Always())  # a second one on the same hero adds nothing (assumed)
    _use(pilfer, 0, [2], units, hp, cr, state, Always())
    assert state[0].spoils == 2 and all(s.pilfer for s in state[1:])
    assert state[0].factor("atk") == pytest.approx(1.4) and state[1].factor("def") == pytest.approx(0.8)
    assert state[0].factor("spd") == 1.0
    _use(Effect("dispel", True, 0.0, 0, 1.0, count=0), 1, [0], units, hp, cr, state, Always())  # an enemy dispels her
    assert state[0].spoils == 0 and not any(s.pilfer for s in state)


def test_spoils_stop_at_four_stacks() -> None:
    units, hp, cr, state = world(unit("rinak", "you"), *[unit(f"e{i}", "them") for i in range(5)])
    for i in range(1, 6):
        _use(Effect("pilfer", True, 0.0, 99, 1.0), 0, [i], units, hp, cr, state, Always())
    assert state[0].spoils == 4


def test_speed_scales_the_damage_of_a_speed_skill() -> None:
    skill = SkillPower(rate=0.25, pow=1.0, enhance=0.0, spd_scaling=0.0015, penetration=1.0, can_crit=False)
    plain = dataclasses.replace(skill, spd_scaling=0.0)
    me = {**unit("a", "you").stats, Stat.SPEED: 200.0}
    foe = unit("b", "them").stats
    assert expected_hit(me, skill, foe) == pytest.approx(expected_hit(me, plain, foe) * 1.3)


# --- the hand kits ---------------------------------------------------------------------------------------------------


def kit_of(code: str) -> tuple[Kit, dict[int, int]]:
    return apply_override(Kit(), {}, load_overrides()[code])


def test_lady_of_the_scales_kit_follows_the_skill_screens() -> None:
    kit, cooldowns = kit_of("c6005")
    assert cooldowns == {3: 4} and kit.cr_mirror == 0.5 and not kit.unread
    assert [e.kind for e in kit.on_use[1]] == ["fear", "heal"] and kit.on_use[1][1].of_target
    assert [(e.kind, e.value, e.turns) for e in kit.at_start] == [("offering", 0.7, 1)]
    assert [e.kind for e in kit.offering_death_effects] == ["heal"]
    assert [e.kind for e in kit.on_use[3]] == ["cleanse", "cr", "offering_cost", "abundance"]
    assert kit.on_use[3][3].receivers == "not_offering" and kit.on_use[3][3].value == 2000.0


def test_boss_arunka_kit_follows_the_skill_screens() -> None:
    kit, cooldowns = kit_of("c2124")
    assert cooldowns == {3: 3} and kit.taunt and kit.hit_cr == 0.25 and not kit.unread
    assert [(e.kind, e.value, e.chance) for e in kit.on_use[1]] == [("def", -0.7, 0.75)]
    assert [e.kind for e in kit.on_use[3]] == ["dispel", "def", "def", "barrier"]
    assert kit.on_use[3][3].scaling == (0.0, 0.0, 1.2)


def test_perfumer_byblis_kit_follows_the_skill_screens() -> None:
    kit, cooldowns = kit_of("c5154")
    assert cooldowns == {3: 3} and kit.fragrance == 0.08 and kit.ally_low == 0.5 and kit.ally_low_cooldown == 3
    assert [e.kind for e in kit.ally_low_effects] == ["cleanse", "heal", "barrier"]
    assert [e.kind for e in kit.on_use[3]] == ["eff", "redirect_provoke", "venom", "detonate"]
    assert kit.on_use[3][2].count == 2 and kit.on_use[3][2].value == 0.1


def test_rinak_kit_follows_the_skill_screens() -> None:
    kit, cooldowns = kit_of("c1168")
    assert cooldowns == {2: 3, 3: 4} and not kit.unread
    assert [e.kind for e in kit.on_use[2]] == ["dispel", "cr", "pilfer", "atk", "extra_turn"]
    assert [(e.kind, e.receivers) for e in kit.on_use[3]] == [
        ("dispel", "self"),
        ("stun", "self"),
        ("fixed_damage", "enemies"),
    ]


def test_the_four_kits_play_a_whole_battle_and_report_what_each_hero_did() -> None:
    lady, arunka, byblis, rinak = (kit_of(c)[0] for c in ("c6005", "c2124", "c5154", "c1168"))
    skills = {1: SkillPower(rate=1.0, pow=1.0, enhance=0.0)}
    team = [
        dataclasses.replace(unit("arunka", "them", arunka, hp=20000), skills=skills, cooldowns={3: 3}),
        dataclasses.replace(unit("rinak", "them", rinak, hp=15000), skills=skills, cooldowns={2: 3, 3: 4}),
        dataclasses.replace(unit("byblis", "them", byblis, hp=18000), skills=skills, cooldowns={3: 3}),
        dataclasses.replace(unit("lady", "them", lady, hp=16000), skills=skills, cooldowns={3: 4}),
    ]
    odds = simulate([unit("a", "you"), unit("b", "you"), *team], runs=40)
    assert odds.win + odds.loss + odds.draw == pytest.approx(1.0)
    totals = {h.label: h for h in odds.heroes}
    assert totals["lady"].healed > 0 and totals["byblis"].damage > 0 and totals["arunka"].shielded > 0
