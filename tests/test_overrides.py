"""Hand-refined kits (SPEC D73): TOML parsing, validation, merging with the automatic kit, `e7 kit todo`."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from e7ac.predict.effects import Effect, Kit
from e7ac.predict.overrides import KitError, apply_override, load_overrides, parse_override

GOOD = """
hero = "c2049"
source = "in-game skill text, test"
status = "verified"
replace = [3]
handles = ["Revive"]
cooldowns = { 3 = 4 }

[[effect]]
slot = 3
kind = "resurrect"
value = 0.2
receivers = "all"

[[effect]]
slot = 2
when = "turn"
kind = "heal"
scaling = [0.1, 0, 0]
receivers = "lowest:2"
"""


def test_a_hand_kit_replaces_slots_and_clears_what_it_handles() -> None:
    hand = parse_override(GOOD)
    auto = Kit(on_use={1: [Effect("stun", True, 0.0, 1, 0.4)], 3: [Effect("atk", False, 0.5, 3, 1.0)]})
    auto.unmodelled = ["Revive", "Fury"]
    kit, cooldowns = apply_override(auto, {1: 0, 3: 6}, hand)
    assert kit.on_use[1] == auto.on_use[1]  # untouched slot kept
    assert [e.kind for e in kit.on_use[3]] == ["resurrect"]  # replaced
    assert kit.on_turn[0].scaling == (0.1, 0.0, 0.0) and kit.on_turn[0].receivers == "lowest:2"
    assert kit.unmodelled == ["Fury"] and cooldowns == {1: 0, 3: 4}


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('hero = "Maid Chloe"\nsource = "x"\nstatus = "verified"', "hero code"),
        ('hero = "c2049"\nstatus = "verified"', "'source'"),
        ('hero = "c2049"\nsource = "x"\nstatus = "sure"', "'status'"),
        ('hero = "c2049"\nsource = "x"\nstatus = "assumed"\n[[effect]]\nslot = 3\nkind = "nuke"', "unknown kind"),
        ('hero = "c2049"\nsource = "x"\nstatus = "assumed"\n[[effect]]\nkind = "heal"', "'slot'"),
        ('hero = "c2049"\nsource = "x"\nstatus = "assumed"\n[[effect]]\nslot = 1\nkind = "stun"\nchance = 3', "chance"),
        ("hero = ", "c"),
        ('hero = "c2049"\nsource = "x"\nstatus = "assumed"\n[passive]\nflying = 1', "unknown passive"),
    ],
)
def test_a_malformed_hand_kit_is_an_error_naming_the_problem(text: str, message: str) -> None:
    with pytest.raises(KitError, match=message):
        parse_override(text, "c2049.toml")


def test_hand_kits_are_keyed_by_their_file_name(tmp_path: Path) -> None:
    (tmp_path / "c2049.toml").write_text(GOOD, encoding="utf-8")
    assert set(load_overrides(tmp_path)) == {"c2049"}
    (tmp_path / "c1001.toml").write_text(GOOD, encoding="utf-8")
    with pytest.raises(KitError, match="file name"):
        load_overrides(tmp_path)


def test_the_shipped_hand_kits_are_valid() -> None:
    load_overrides()  # every file in predict/kits parses and is named by its hero code


def test_kit_todo_lists_the_heroes_met_most_first(
    synthetic_catalog: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from e7ac.arena.references import ReferenceStore
    from e7ac.cli import kit as kit_cli
    from e7ac.cli.app import app
    from e7ac.paths import default_paths

    root = default_paths().home / "references"
    when = datetime(2026, 10, 16, tzinfo=UTC)
    crop = np.zeros((20, 20, 3), np.uint8)
    for i in range(3):
        crop[0, 0, 0] = i
        ReferenceStore(root / "faces").add("c9001", crop.copy(), when)
    ReferenceStore(root / "arena").add("c2011", crop, when)
    monkeypatch.setattr(kit_cli, "fetch_kit", lambda http, name: {})
    result = CliRunner().invoke(app, ["kit", "todo", "--offline"])
    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if "x  " in line]
    assert "Test Hero" in lines[0] and "3x" in lines[0] and "Blood Blade Karin" in lines[1]
    assert "(no epic7db page)" in lines[0]


def _duel(kit_a: Kit, kit_b: Kit | None = None, hp_a: float = 10000, hp_b: float = 10000) -> float:
    from e7ac.predict.battle import simulate
    from tests.test_effects import unit

    return simulate([unit("a", "you", kit_a, hp=hp_a), unit("b", "them", kit_b, hp=hp_b)], runs=400).win


def test_conditions_sacrifice_reflect_and_immunity_in_battle() -> None:
    from e7ac.predict.battle import _State, simulate
    from tests.test_effects import unit

    plain = _duel(Kit())
    # a Combat Readiness push only when the skill defeats an enemy: here one extra turn (after the weak one dies),
    # between "no push" and "a push at every hit"
    push = Effect("cr", False, 1.0, 2, 1.0, condition="kill")
    ambush = [unit("a", "you", hp=6000), unit("b1", "them", hp=100), unit("b2", "them", hp=5000)]
    wins = [
        simulate(
            [unit("a", "you", kit, hp=6000), unit("b1", "them", hp=100), unit("b2", "them", hp=5000)], runs=300
        ).win
        for kit in (Kit(), Kit(on_use={1: [push]}), Kit(on_use={1: [dataclasses.replace(push, condition="")]}))
    ]
    assert wins[0] < wins[1] < wins[2] and len(ambush) == 3
    # health paid for nothing makes a duel harder; reflect makes the attacker pay
    pay = Kit(on_use={1: [Effect("sacrifice", False, 0.5, 0, 1.0)]})
    assert _duel(pay) < plain
    assert _duel(Kit(), Kit(reflect=1.0)) < plain
    # stun never lands on a stun-immune hero
    stun = Kit(on_use={1: [Effect("stun", True, 0.0, 1, 1.0)]})
    assert _duel(stun, Kit(immune=("stun",))) < _duel(stun)
    # a revive status brings its holder back once, for its turns
    state = _State()
    state.apply(Effect("revive_buff", False, 0.3, 3, 1.0))
    assert state.timers["revive_buff"] == 3 and state.revive_hp == 0.3
    assert simulate([unit("a", "you"), unit("b", "them")], runs=50).runs == 50


def test_a_speed_cap_and_no_counter_shape_turn_order() -> None:
    from e7ac.predict.battle import Combatant, simulate
    from tests.test_effects import S1, unit

    stats = unit("x", "you").stats
    fast = Combatant("fast", "you", stats, 300, S1, {}, Kit())
    slow = Combatant("slow", "them", stats, 100, S1, {}, Kit())
    assert simulate([fast, slow], runs=200).win > 0.9
    capper = Combatant("cap", "them", stats, 100, S1, {}, Kit(speed_cap=0.9))
    assert simulate([fast, capper], runs=200).win < 0.1  # 300 capped to 90: the capper acts first and wins the duel


def test_the_shipped_kits_apply_and_show_their_passives() -> None:
    from e7ac.predict.effects import Kit as AutoKit

    kits = load_overrides()
    assert {"c1162", "c2011", "c1153", "c1104", "c1131", "c2049"} <= set(kits)
    yulha, _ = apply_override(AutoKit(), {}, kits["c1131"])
    assert yulha.reflect == 0.4 and yulha.low_hp == 0.3 and len(yulha.low_hp_effects) == 3
    harsetti, _ = apply_override(AutoKit(), {}, kits["c1153"])
    assert harsetti.speed_cap == 0.9
    karin, _ = apply_override(AutoKit(counter=0.3), {}, kits["c2011"])
    assert karin.counter == 0.3  # a hand kit never drops what was read automatically


def test_a_redirected_provoke_sends_the_basic_skill_at_the_highest_max_health_enemy() -> None:
    from e7ac.predict.battle import simulate
    from tests.test_effects import unit

    forced = Kit(at_start=[Effect("redirect_provoke", False, 0.0, 9, 1.0)])  # provoked for the whole fight

    def win(kit: Kit) -> float:
        a = unit("a", "you", kit, hp=6000)
        return simulate([a, unit("weak", "them", hp=100), unit("tank", "them", hp=5000)], runs=300).win

    assert win(forced) < win(Kit())  # forced onto the tank, the weak enemy stays alive longer


def test_the_screen_verified_kits_say_so() -> None:
    kits = load_overrides()
    karin, cooldowns = apply_override(Kit(), {3: 5}, kits["c2011"])
    assert cooldowns[3] == 4 and any(e.kind == "extra_turn" and e.condition == "kill" for e in karin.on_use[3])
    yulha, _ = apply_override(Kit(), {}, kits["c1131"])
    assert [e.kind for e in yulha.on_use[1]] == ["redirect_provoke"]
    assert "verified" in kits["c1131"].source and "verified" in kits["c2011"].source


def test_guard_fear_uncounterable_pierce_and_resistance_in_battle() -> None:
    from e7ac.predict.battle import Combatant, simulate
    from tests.test_effects import unit

    def win(side: list[Combatant]) -> float:
        return simulate(side, runs=300).win

    fear = Kit(on_use={1: [Effect("fear", True, 0.0, 2, 1.0)]})
    duel = [unit("a", "you", fear), unit("b", "them")]
    immune = [unit("a", "you", fear), unit("b", "them", Kit(immune=("fear",)))]
    assert win(duel) > win([unit("a", "you"), unit("b", "them")]) + 0.2  # the feared one loses turns
    assert win(immune) < win(duel) - 0.2  # ... unless it is immune to fear
    counter = Kit(counter=1.0)
    assert win([unit("a", "you", Kit(uncounterable=(1,))), unit("b", "them", counter)]) > win(
        [unit("a", "you"), unit("b", "them", counter)]
    )  # a skill that cannot trigger a counterattack
    stun = Kit(on_use={1: [Effect("stun", True, 0.0, 1, 1.0)]})
    ignoring = Kit(on_use=stun.on_use, ignore_resist=True)
    weaker_resistant = unit("b", "them", hp=10000, resist=1.0)
    assert win([unit("a", "you", ignoring, hp=10100), weaker_resistant]) > win(
        [unit("a", "you", stun, hp=10100), weaker_resistant]
    )  # ignores the resistance of a target with less max Health
    assert win([unit("a", "you", Kit(pierce_top={1: 0.9})), unit("b", "them", hp=20000)]) > win(
        [unit("a", "you"), unit("b", "them", hp=20000)]
    )
    guard = Kit(
        guard=1.0,
        guard_cooldown=3,
        guard_effects=[Effect("barrier", False, 0.0, 2, 1.0, scaling=(0.5, 0.0, 0.0), receivers="all")],
    )
    boss = unit("b", "them", hp=30000)
    assert (
        win([unit("a", "you"), unit("m", "you", guard), boss]) > win([unit("a", "you"), unit("m", "you"), boss]) + 0.2
    )


def test_continuous_healing_dual_attack_and_fighting_spirit_in_battle() -> None:
    from e7ac.predict.battle import Combatant, simulate
    from tests.test_effects import unit

    def win(side: list[Combatant]) -> float:
        return simulate(side, runs=300).win

    hot = Kit(on_use={1: [Effect("hot", False, 0.15, 2, 1.0, receivers="all")]})
    assert win([unit("a", "you", hot), unit("b", "them")]) > win([unit("a", "you"), unit("b", "them")]) + 0.2
    dual = Kit(on_use={1: [Effect("dual_attack", False, 0.0, 0, 1.0)]})
    boss = unit("b", "them", hp=30000)
    assert win([unit("a", "you", dual), unit("m", "you"), boss]) > win([unit("a", "you"), unit("m", "you"), boss]) + 0.2
    spirit = Kit(
        spirit_full=100,
        spirit_start=80,
        spirit_hit=20,
        spirit_share=0.3,
        spirit_effects=[Effect("extra_turn", False, 0.0, 0, 1.0)],
    )
    assert win([unit("a", "you", spirit), unit("b", "them")]) > win([unit("a", "you"), unit("b", "them")]) + 0.2
    # the foremost ally except the caster: an Earth one ("wind") adds the starting Fighting Spirit (the only source)
    bonus = Kit(spirit_full=100, spirit_start=0, spirit_bonus=100, spirit_hit=0, spirit_share=1.0)
    stats = unit("x", "you").stats
    caster = Combatant("a", "you", stats, 200, unit("a", "you").skills, {}, bonus, "light")
    earth = Combatant("e", "you", stats, 200, {}, {}, Kit(), "wind")
    plain = Combatant("p", "you", stats, 200, {}, {}, Kit(), "fire")
    boss = unit("b", "them", hp=30000)
    assert win([caster, earth, boss]) > win([caster, plain, boss])


def test_mort_and_karin_kits_carry_the_screen_verified_passives() -> None:
    kits = load_overrides()
    mort, cooldowns = apply_override(Kit(), {3: 5}, kits["c1104"])
    assert cooldowns[3] == 4 and mort.guard == 0.2 and mort.guard_cooldown == 3 and mort.pierce_top == {3: 0.7}
    assert mort.immune == ("stun", "fear") and mort.ignore_resist and len(mort.guard_effects) == 2
    assert [e.kind for e in mort.on_use[3]] == ["fear"]
    karin, _ = apply_override(Kit(), {}, kits["c2011"])
    assert karin.uncounterable == (3,)


def test_young_senya_kit_carries_the_screen_verified_numbers() -> None:
    kits = load_overrides()
    senya, cooldowns = apply_override(Kit(), {3: 5}, kits["c1162"])
    assert cooldowns[3] == 4 and senya.spirit_full == 100 and senya.spirit_start == 50 and senya.spirit_bonus == 40
    kinds = {e.kind: e for e in senya.on_use[3]}
    assert kinds["cr"].value == 0.20 and kinds["hot"].value == 0.15 and kinds["hot"].turns == 2
    assert kinds["spirit"].value == 20 and [e.kind for e in senya.spirit_effects] == ["cleanse", "extra_turn"]
    assert {e.kind for e in senya.on_use[1]} == {"cr", "dual_attack"}


def test_harsetti_yulha_karin_and_chloe_kits_carry_the_screen_verified_numbers() -> None:
    kits = load_overrides()
    harsetti, cooldowns = apply_override(Kit(), {3: 5}, kits["c1153"])
    assert cooldowns[3] == 4 and harsetti.speed_cap == 0.9 and harsetti.cr_lock
    assert [(e.kind, e.value, e.chance, e.receivers) for e in harsetti.on_use[1]] == [
        ("def", -0.7, 0.75, ""),
        ("atk", -0.5, 0.6, "enemies"),
    ]
    assert [e.kind for e in harsetti.on_use[3]] == ["shorten", "unable_counter", "unable_buff"]
    assert all(e.debuff and e.turns == 2 for e in harsetti.on_use[3])
    yulha, cooldowns = apply_override(Kit(on_use={3: [Effect("heal", False, 0.0, 0, 1.0)]}), {3: 5}, kits["c1131"])
    assert 3 not in yulha.on_use and cooldowns[3] == 5  # the in-game S3 has no heal
    assert [e.value for e in yulha.low_hp_effects if e.kind == "cr"] == [0.20]
    karin, _ = apply_override(Kit(), {}, kits["c2011"])
    assert karin.lethal_immortal == 1
    chloe, _ = apply_override(Kit(), {}, kits["c2049"])
    assert [(e.kind, e.value, e.receivers, e.condition) for e in chloe.on_use[1]] == [
        ("stun", 0.0, "", ""),
        ("cr", 0.15, "", ""),
        ("cr", 0.15, "others", "stun"),
    ]


def test_unable_to_buff_and_shortened_buffs_in_battle_state() -> None:
    import random

    from e7ac.predict.battle import _State, _use
    from tests.test_effects import unit

    units = [unit("a", "you"), unit("b", "them")]
    hp, cr, state, rng = [5000.0, 10000.0], [0.0, 0.0], [_State(), _State()], random.Random(1)
    state[0].timers["unable_buff"] = 2
    _use(Effect("atk", False, 0.5, 2, 1.0), 0, [], units, hp, cr, state, rng)
    _use(Effect("heal", False, 0.0, 0, 1.0, scaling=(0.1, 0.0, 0.0)), 0, [], units, hp, cr, state, rng)
    assert not state[0].buffs and hp[0] == 6000.0  # no buff, but a heal still works
    held = _State(buffs={"atk": (0.5, 3)}, timers={"immortal": 2, "stun": 2}, hots=[(0.1, 2)], barrier=100.0)
    held.barrier_turns = 2
    held.shorten(2)
    assert held.buffs == {"atk": (0.5, 1)} and held.timers == {"stun": 2} and not held.hots and held.barrier == 0.0
    # shorten always lands; a debuff with "enemies" reaches every living enemy, not only the target hit
    trio = [unit("a", "you"), unit("b1", "them"), unit("b2", "them")]
    hp, cr, state = [1.0, 1.0, 1.0], [0.0] * 3, [_State(), _State(buffs={"def": (0.5, 3)}), _State()]
    _use(Effect("shorten", True, 0.0, 3, 1.0), 0, [1], trio, hp, cr, state, rng)
    assert not state[1].buffs
    _use(Effect("atk", True, -0.5, 1, 2.0, receivers="enemies"), 0, [1], trio, hp, cr, state, rng)
    assert "atk" in state[1].debuffs and "atk" in state[2].debuffs and "atk" not in state[0].debuffs


def test_counterattack_lock_combat_readiness_lock_and_lethal_immortality_in_battle() -> None:
    from e7ac.predict.battle import simulate
    from tests.test_effects import unit

    def win(a: Kit, b: Kit | None = None, hp: float = 10000) -> float:
        return simulate([unit("a", "you", a, hp=hp), unit("b", "them", b or Kit())], runs=300).win

    plain = win(Kit())
    counter = Kit(counter=1.0)
    lock = Kit(on_use={1: [Effect("unable_counter", True, 0.0, 9, 2.0)]})
    assert win(lock, counter, 14000) > win(Kit(), counter, 14000) + 0.1  # a hero that cannot counterattack stops
    turn = Effect("extra_turn", False, 0.0, 0, 1.0)
    extra, locked = Kit(on_use={1: [turn]}), Kit(on_use={1: [turn]}, cr_lock=True)
    assert win(extra) > plain + 0.2 and win(locked) < win(extra) - 0.2  # no Combat Readiness up on her own turn
    assert win(Kit(lethal_immortal=9)) > plain + 0.2


def test_effects_that_need_a_landed_stun_reach_every_ally_but_the_caster() -> None:
    from e7ac.predict.battle import _receivers, _State, simulate
    from tests.test_effects import unit

    units = [unit("a", "you"), unit("m", "you"), unit("b", "them")]
    others = Effect("cr", False, 0.1, 0, 1.0, receivers="others")
    assert _receivers(others, 0, units, [1.0, 1.0, 1.0], [_State(), _State(), _State()]) == [1]
    stun = Effect("stun", True, 0.0, 1, 0.5)
    boost = Effect("extra_turn", False, 0.0, 0, 1.0, receivers="others")
    only_on_stun = dataclasses.replace(boost, condition="stun")

    def win(*effects: Effect, boss: float = 40000) -> float:
        a = unit("a", "you", Kit(on_use={1: list(effects)}))
        return simulate([a, unit("m", "you"), unit("b", "them", hp=boss)], runs=300).win

    assert win(only_on_stun, boss=30000) == win(boss=30000)  # no stun landed: the condition keeps the effect away
    assert win(stun, only_on_stun) > win(stun) + 0.03  # the allies get their turn once the stun landed


def test_a_hit_cap_a_variant_s1_and_a_cooldown_cut_work_in_battle() -> None:
    """Spirit Eye Celine (SPEC D108): Sixth Sense limits the damage of one attack, Nimble Sword is her S1 when she has a
    buff, and every death takes a turn off her cooldowns."""
    import random

    from e7ac.domain.codes import Stat
    from e7ac.predict.battle import Combatant, _lethal, _State, _strike, simulate
    from e7ac.predict.damage import SkillPower
    from tests.test_effects import unit

    # the cap: one huge hit takes at most 51% of the max Health, a hit under the cap is whole
    capped = [unit("a", "you"), unit("b", "them", Kit(hit_cap=0.51), hp=10000)]
    hp, cr, state = [10000.0, 10000.0], [0.0, 0.0], [_State(), _State()]
    lost, lethal = _strike(capped, hp, cr, state, 1, 90000.0, random.Random(1), 0)
    assert lost == pytest.approx(5100.0) and not lethal and state[0].dealt == pytest.approx(5100.0)
    assert _strike(capped, hp, cr, state, 1, 1000.0, random.Random(1), 0)[0] == pytest.approx(1000.0)

    # the variant: S1 is the strong "S2" data while she holds a buff, the plain S1 without one
    weak, strong = SkillPower(0.1, 1.0, 0.0), SkillPower(3.0, 1.0, 0.0, penetration=0.5)
    stats = unit("x", "you").stats

    def celine(buff: bool) -> Combatant:
        at_start = [Effect("atk", False, 0.1, 99, 1.0)] if buff else []
        kit = Kit(variant_slot=2, at_start=at_start)
        return Combatant("c", "you", stats, 300, {1: weak, 2: strong}, {}, kit)

    boss = unit("b", "them", hp=120000)

    def dealt(buff: bool) -> float:
        return simulate([celine(buff), boss], runs=100).heroes[0].damage

    assert dealt(True) > 3 * dealt(False)  # the variant's multiplier is thirty times the plain S1's

    # the cooldown cut: each death adds a turn to the cut of every living hero that has it
    team = [
        Combatant("c", "you", stats, 300, {1: weak}, {}, Kit(death_cd=1)),
        unit("ally", "you"),
        unit("b", "them"),
    ]
    hp, cr, state = [10000.0, 0.0, 10000.0], [0.0] * 3, [_State(), _State(), _State()]
    _lethal(team, hp, cr, state, 1, random.Random(1))
    assert [s.cd_cut for s in state] == [1, 0, 0] and hp[1] <= 0
    assert Stat.HP in stats


def test_spirit_eye_celine_kit_carries_the_popup_numbers() -> None:
    celine, cooldowns = apply_override(Kit(), {3: 6}, load_overrides()["c2103"])
    assert cooldowns[3] == 5  # "6 turns" and "-1 turn cooldown" at +1
    assert celine.hit_cap == 0.51 and celine.variant_slot == 2 and celine.death_cd == 1
    assert celine.hit_cap == pytest.approx(0.70 - (0.02 * 3 + 0.03 * 3 + 0.04))  # the steps of Sixth Sense
    assert [(e.kind, e.value, e.turns) for e in celine.variant_effects] == [("atk", 0.5, 1)]
    assert {e.kind: (e.value, e.turns, e.receivers) for e in celine.on_use[3]} == {
        "immortal": (0.0, 1, "all"),
        "atk": (0.25, 3, ""),
    }


def test_a_reset_cooldown_and_a_two_target_skill_work_in_battle() -> None:
    """Arbiter Vildred (SPEC D108): Dark Blade is ready again when it defeats an enemy, Sweep hits two enemies."""
    from e7ac.domain.codes import Stat
    from e7ac.predict.battle import Combatant, simulate
    from e7ac.predict.damage import SkillPower
    from tests.test_effects import unit

    weak, blade = SkillPower(0.05, 1.0, 0.0), SkillPower(4.0, 1.0, 0.0, targets=3)
    stats = unit("x", "you").stats

    def vildred(reset: bool) -> Combatant:
        effects = [Effect("reset_cd", False, 3.0, 2, 1.0, condition="kill")] if reset else []
        return Combatant("v", "you", stats, 300, {1: weak, 3: blade}, {3: 4}, Kit(on_use={3: effects}))

    foes = [unit(f"b{i}", "them", hp=3000) for i in range(3)]
    quick, slow = simulate([vildred(True), *foes], runs=200), simulate([vildred(False), *foes], runs=200)
    assert quick.heroes[0].damage >= slow.heroes[0].damage  # a defeated enemy gives the next Dark Blade at once

    # "attacks two enemies": with four enemies the skill touches two of them, never all
    pair = SkillPower(1.0, 1.0, 0.0, targets=2)
    striker = Combatant("s", "you", stats, 400, {1: pair}, {}, Kit())
    four = [unit(f"t{i}", "them", hp=10**7) for i in range(4)]
    one = simulate([striker, *four], runs=1).heroes[0].damage
    allfour = simulate(
        [Combatant("s", "you", stats, 400, {1: SkillPower(1.0, 1.0, 0.0, targets=3)}, {}, Kit()), *four], runs=1
    )
    assert 0 < one < allfour.heroes[0].damage
    assert Stat.HP in stats


def test_arbiter_vildred_kit_carries_the_popup_numbers() -> None:
    vildred, cooldowns = apply_override(Kit(), {3: 5}, load_overrides()["c2007"])
    assert cooldowns[3] == 5 and vildred.revive == 1.0
    assert [(e.kind, e.value) for e in vildred.revive_effects] == [("cr", 1.0)]
    assert [(e.kind, e.value, e.turns, e.condition) for e in vildred.on_use[3]] == [
        ("atk", 0.5, 2, ""),
        ("reset_cd", 3.0, 2, "kill"),
    ]


def test_forms_erosion_extra_attacks_and_effectiveness_from_speed_work_in_battle() -> None:
    """Rhianna and Luciella (SPEC D108): two forms that swap, erosion and its detonation, extra attacks with a
    condition, and Effectiveness from Speed."""
    import random

    from e7ac.predict.battle import _State, _use, simulate
    from tests.test_effects import unit

    team = [unit("a", "you"), unit("b", "them")]
    hp, cr, state = [10000.0, 6000.0], [0.0, 0.0], [_State(), _State()]
    rng = random.Random(1)
    _use(Effect("erosion", True, 0.10, 2, 2.0), 0, [1], team, hp, cr, state, rng)  # chance 200%: always lands
    assert state[1].erosions == [(0.10, 2)] and state[1].debuffed
    _use(Effect("detonate", True, 0.0, 0, 1.0), 0, [1], team, hp, cr, state, rng)
    assert hp[1] == pytest.approx(6000 - 0.10 * 4000 * 2) and not state[1].erosions  # share x lost Health x turns
    state[1].erosions = [(0.10, 1)]
    _use(Effect("cleanse", False, 0.0, 0, 1.0, receivers="all"), 1, [], team, hp, cr, state, rng)
    assert not state[1].erosions  # a cleanse removes erosion

    stun = Effect("stun", True, 0.0, 2, 1.0, form=1)  # only in the second form
    swap = Effect("swap", False, 0.0, 0, 1.0)

    def length(kit: Kit) -> float:  # a stunned boss needs more actions to defeat the hero
        boss = unit("b", "them", hp=10**6)
        return simulate([unit("a", "you", kit), boss], runs=100).mean_actions

    plain = length(Kit(on_use={1: [stun]}))  # never in the second form: the stun never happens
    swapping = length(Kit(on_use={1: [stun, swap]}))  # the first S1 swaps, the next ones stun
    assert swapping > plain * 1.2

    resistant = unit("t", "them", hp=10**6, resist=0.8)
    stun_all = Effect("stun", True, 0.0, 2, 1.0)

    def stuns(kit: Kit) -> float:
        return simulate([unit("a", "you", kit), resistant], runs=200).mean_actions

    assert stuns(Kit(on_use={1: [stun_all]}, eff_speed=0.3)) > stuns(Kit(on_use={1: [stun_all]})) * 1.1


def test_rhianna_and_luciella_kit_carries_the_popup_numbers() -> None:
    kit, cooldowns = apply_override(Kit(), {2: 3, 3: 5}, load_overrides()["c2185"])
    assert cooldowns == {2: 3, 3: 4} and kit.eff_speed == 0.3
    first = {(e.kind, e.form): (e.value, e.turns) for e in kit.on_use[1]}
    assert first == {("erosion", 0): (0.10, 2), ("detonate", 0): (0.0, 2)}
    assert [(e.kind, e.value, e.turns, e.form) for e in kit.before_use[1]] == [("atk", 0.5, 1, 1)]
    assert [(e.kind, e.value, e.turns, e.form) for e in kit.before_use[2]] == [("atk", 0.5, 3, 1)]
    assert [e.kind for e in kit.on_use[2]][-2:] == ["extra_turn", "swap"]  # the swap comes last
    third = {e.kind: e for e in kit.on_use[3]}
    assert third["barrier"].scaling == (0.0, 1.6, 0.0) and third["barrier"].turns == 3
    assert third["fear"].receivers == "enemies" and third["dispel"].count == 2


def test_notos_belian_and_setsuka_mechanics_work_in_battle() -> None:
    """SPEC D108: unaffected by Combat Readiness, a skill that starts on its cooldown, an extra attack with another
    skill's data and a chance, and beguile."""
    import random

    from e7ac.domain.codes import Stat
    from e7ac.predict.battle import Combatant, _raise_cr, _State, _use, simulate
    from e7ac.predict.damage import SkillPower
    from tests.test_effects import unit

    team = [unit("n", "you", Kit(cr_immune=True)), unit("b", "them")]
    cr = [0.0, 0.0]
    quiet = [_State(), _State()]
    _raise_cr(team, [1.0, 1.0], cr, quiet, 0, 0.5)
    assert cr[0] == 0.0  # increases do not move her
    _raise_cr(team, [1.0, 1.0], cr, quiet, 1, 0.5)
    assert cr[1] == 0.5
    _use(Effect("cr", True, -0.4, 1, 2.0), 1, [0], team, [1.0, 1.0], [0.9, 0.0], [_State(), _State()], random.Random(1))
    assert team[0].kit.cr_immune  # (a decrease on her is skipped: checked below on the Combat Readiness list)
    cr = [0.9, 0.0]
    _use(Effect("cr", True, -0.4, 1, 2.0), 1, [0], team, [1.0, 1.0], cr, [_State(), _State()], random.Random(1))
    assert cr[0] == 0.9

    # a skill that starts on its cooldown comes later: the S3 of the first battle is not there at once
    strong = {1: SkillPower(0.05, 1.0, 0.0), 3: SkillPower(6.0, 1.0, 0.0)}
    stats = unit("x", "you").stats

    def early(start_wait: tuple[int, ...]) -> float:  # the damage done while it lives: S3 comes later when it waits
        hero = Combatant("n", "you", stats, 300, strong, {3: 5}, Kit(start_wait=start_wait))
        return simulate([hero, unit("t", "them", hp=10**6)], runs=100).heroes[0].damage

    assert early((3,)) < early(()) * 0.9

    # an extra attack with S2's data on all enemies, with a chance, and the effects that follow only when it happened
    weak, wide = SkillPower(0.05, 1.0, 0.0), SkillPower(2.0, 1.0, 0.0, targets=3)

    def dealt(chance: float) -> float:
        extra = Effect("extra_attack", False, 2.0, 0, chance)
        kit = Kit(on_use={1: [extra, Effect("stun", True, 0.0, 1, 2.0, condition="proc")]})
        hero = Combatant("b", "you", stats, 300, {1: weak, 2: wide}, {}, kit)
        foes = [unit(f"t{i}", "them", hp=10**6) for i in range(2)]
        return simulate([hero, *foes], runs=100).heroes[0].damage

    assert dealt(1.0) > 5 * dealt(0.0)

    # beguile: when the bearer's turn ends its allies lose a share of their max Health, once (a share of 100% here)
    def rounds(beguile: bool) -> float:
        effects = [Effect("beguile", True, 1.0, 2, 2.0)] if beguile else []
        hero = Combatant("s", "you", stats, 300, {1: weak}, {}, Kit(on_use={1: effects}))
        foes = [unit("bearer", "them", hp=10**6), unit("ally", "them", hp=10**6)]
        return simulate([hero, *foes], runs=100).heroes[2].damage  # what the beguiled bearer's ally went on to do

    assert rounds(True) < rounds(False) * 0.8  # its ally is gone after the bearer's first turn
    assert Stat.HP in stats


def test_notos_belian_and_setsuka_kits_carry_the_popup_numbers() -> None:
    kits = load_overrides()
    notos, cooldowns = apply_override(Kit(), {2: 2, 3: 8}, kits["c2181"])
    assert cooldowns == {2: 0, 3: 7} and notos.cr_immune and notos.start_wait == (3,)
    assert [(e.kind, e.value, e.turns, e.chance) for e in notos.on_use[1]] == [("def", -0.7, 1, 0.75)]
    assert {e.kind: (e.value, e.turns) for e in notos.on_use[3] if e.kind != "heal"} == {
        "atk": (1.0, 3),
        "def": (1.0, 3),
        "spd": (1.0, 3),
    }
    belian, cooldowns = apply_override(Kit(), {3: 5}, kits["c1117"])
    assert cooldowns[3] == 4
    first = {e.kind: e for e in belian.on_use[1]}
    assert first["dispel"].chance == 0.65 and first["extra_attack"].chance == 0.35 and first["extra_attack"].value == 2
    assert first["spd"].condition == "proc" and first["spd"].value == -0.3
    third = {e.kind: e for e in belian.on_use[3]}
    assert third["cr"].value == -0.30 and third["provoke"].turns == 1 and third["def"].value == 0.6
    setsuka, cooldowns = apply_override(Kit(), {3: 6}, kits["c1176"])
    assert cooldowns[3] == 3 and setsuka.ignore_resist
    assert {e.kind: (e.value, e.count, e.receivers) for e in setsuka.on_use[3]} == {
        "dispel": (0.0, 0, "enemies"),
        "beguile": (0.10, 0, "enemies"),
    }


def test_seal_collapse_and_effects_before_the_attack_work_in_battle() -> None:
    """Genesis Ras (SPEC D109): seal switches a hero's passives off, collapse takes a share of its max Health, and an
    effect before the attack acts on the targets the attack has picked."""
    import random

    from e7ac.predict.battle import _State, _strike, _use
    from tests.test_effects import unit

    rng = random.Random(1)
    team = [unit("a", "you"), unit("b", "them", Kit(hit_cap=0.1))]
    hp, cr, state = [10000.0, 10000.0], [0.0, 0.0], [_State(), _State()]
    _use(Effect("collapse", True, 0.5, 2, 2.0), 0, [1], team, hp, cr, state, rng)
    assert hp[1] == 5000 and state[1].debuffed  # the Health above the new maximum is gone
    heal = Effect("heal", False, 0.0, 0, 1.0, scaling=(1.0, 0.0, 0.0), receivers="self")
    _use(heal, 1, [], team, hp, cr, state, rng)
    assert hp[1] == 5000  # healing stops at the lower maximum
    state[1].tick()
    state[1].tick()  # two own turns: the collapse is over
    _use(heal, 1, [], team, hp, cr, state, rng)
    assert hp[1] == 10000

    state[1], hp[1] = _State(), 10000.0
    _strike(team, hp, cr, state, 1, 5000.0, rng)
    assert hp[1] == 9000  # its passive hit cap: 10% of max Health
    state[1], hp[1] = _State(), 10000.0
    _use(Effect("seal", True, 0.0, 2, 2.0), 0, [1], team, hp, cr, state, rng)
    _strike(team, hp, cr, state, 1, 5000.0, rng)
    assert hp[1] == 5000  # sealed: the cap does not work
    state[1].remove(debuffs=True, count=1)
    assert "seal" not in state[1].timers  # a cleanse takes the seal off

    sealed = Kit(counter=0.5, hit_cap=0.1, on_use={1: [Effect("stun", True, 0.0, 1, 1.0)]}, taunt=True).sealed()
    assert sealed.counter == 0 and sealed.hit_cap == 0 and sealed.on_use and sealed.taunt  # active skills stay

    collapse = Effect("collapse", True, 0.5, 2, 2.0)
    assert _duel(Kit(before_use={1: [collapse]})) > _duel(Kit()) + 0.1  # acts on the target chosen for that attack


def test_genesis_ras_kit_carries_the_popup_numbers() -> None:
    kit, cooldowns = apply_override(Kit(), {2: 3, 3: 4}, load_overrides()["c2001"])
    assert cooldowns == {2: 3, 3: 4}
    assert [(e.kind, e.value, e.turns) for e in kit.before_use[2]] == [("seal", 0.0, 2), ("collapse", 0.5, 2)]
    assert [(e.kind, e.condition) for e in kit.on_use[2]] == [("extra_turn", "crit")]
    assert {(e.kind, e.value, e.turns) for e in kit.at_start} == {("immunity", 0.0, 5), ("revive_buff", 1.0, 5)}
    assert not kit.on_use.get(3)


def test_penetration_resistance_ally_combat_readiness_and_resistance_buffs_work_in_battle() -> None:
    """Empyrean Ilynav (SPEC D109): the side's Penetration Resistance lowers the penetration of an attack on it, an
    ally's hit gives her Combat Readiness, and an Effect Resistance buff makes a debuff harder to land."""
    import random

    from e7ac.domain.codes import Stat
    from e7ac.predict.battle import Combatant, _after_hit, _State, _use, simulate
    from e7ac.predict.damage import SkillPower
    from tests.test_effects import S1, unit

    piercing = {1: SkillPower(1.0, 1.0, 0.0, penetration=1.0)}
    stats = unit("x", "you").stats

    def dealt(resist: float) -> float:  # what a piercing attacker does to a high-Defense target before it falls
        tank = Combatant("t", "them", {**stats, Stat.DEF: 5000.0, Stat.HP: 10**6}, 200, S1, {}, Kit(pen_resist=resist))
        return simulate([Combatant("a", "you", stats, 300, piercing, {}, Kit()), tank], runs=100).heroes[0].damage

    assert dealt(0.5) < dealt(0.0) * 0.8

    rng = random.Random(1)
    team = [unit("a", "you"), unit("b", "them"), unit("c", "them", Kit(ally_hit_cr=0.1))]
    cr = [0.0, 0.0, 0.0]
    _after_hit(1, 0, 100.0, False, team, [1e4, 1e4, 1e4], cr, [_State(), _State(), _State()], {}, rng)
    assert cr == [0.0, 0.0, pytest.approx(0.1)]  # the ally's hit moved her, not the one hit
    sealed = [_State(), _State(), _State(timers={"seal": 2})]
    cr = [0.0, 0.0, 0.0]
    _after_hit(1, 0, 100.0, False, team, [1e4, 1e4, 1e4], cr, sealed, {}, rng)
    assert cr[2] == 0.0  # sealed: a passive that does not work

    stun = Effect("stun", True, 0.0, 1, 1.0)
    target = unit("t", "them", resist=0.8)

    def landed(buff: bool) -> int:
        count = 0
        for seed in range(200):
            state = [_State(), _State()]
            if buff:
                _use(
                    Effect("res", False, 0.3, 2, 1.0, receivers="self"),
                    1,
                    [],
                    [unit("a", "you"), target],
                    [1e4, 1e4],
                    [0.0, 0.0],
                    state,
                    random.Random(seed),
                )
            _use(stun, 0, [1], [unit("a", "you"), target], [1e4, 1e4], [0.0, 0.0], state, random.Random(seed))
            count += state[1].timers.get("stun", 0) > 0
        return count

    assert landed(False) > 10 and landed(True) == 0  # 1 - 0.8 - 0.3 < 0: never lands


def test_empyrean_ilynav_kit_carries_the_popup_numbers() -> None:
    kit, cooldowns = apply_override(Kit(), {3: 5}, load_overrides()["c2113"])
    assert cooldowns == {3: 4} and kit.pen_resist == 0.40 and kit.ally_hit_cr == 0.10
    third = {e.kind: e for e in kit.on_use[3]}
    assert third["cleanse"].count == 2 and third["cleanse"].receivers == "all"
    assert (third["def"].value, third["def"].turns, third["def"].receivers) == (0.3, 2, "all")
    assert (third["res"].value, third["res"].turns, third["res"].receivers) == (0.3, 2, "all")


def test_deify_and_an_extra_attack_that_defeats_an_enemy_work_in_battle() -> None:
    """Zio (SPEC D109): Deify adds 50 points of Critical Hit Chance while attacking and halves the damage of attacks
    on the holder; an extra attack that defeats an enemy lets the `proc_kill` effects follow."""
    import random

    from e7ac.predict.battle import Combatant, _crit_chance, _State, _strike, _use, simulate
    from e7ac.predict.damage import SkillPower
    from tests.test_effects import unit

    team = [unit("a", "you"), unit("b", "them")]
    state = [_State(), _State()]
    assert _crit_chance(team, state, 0) == 0.0
    _use(Effect("deify", False, 0.0, 3, 1.0), 0, [], team, [1e4, 1e4], [0.0, 0.0], state, random.Random(1))
    assert _crit_chance(team, state, 0) == pytest.approx(0.5)
    hp = [10000.0, 10000.0]
    _strike(team, hp, [0.0, 0.0], state, 0, 1000.0, random.Random(1), by=1)  # an attack on the holder
    assert hp[0] == pytest.approx(9500)
    _strike(team, hp, [0.0, 0.0], state, 0, 1000.0, random.Random(1))  # damage that is no attack (not halved)
    assert hp[0] == pytest.approx(8500)
    state[0].remove(debuffs=False, count=1)
    assert "deify" not in state[0].timers  # a dispel takes it off

    # the extra attack with S2's data defeats a weak enemy, and only then does the Combat Readiness follow
    stats = unit("x", "you").stats
    skills = {1: SkillPower(0.01, 1.0, 0.0), 2: SkillPower(5.0, 1.0, 0.0)}

    def dealt(boss_hp: float, with_cr: bool) -> float:
        effects = [Effect("extra_attack", False, 2.0, 0, 1.0)]
        if with_cr:  # a full bar: the hero acts again at once
            effects.append(Effect("cr", False, 1.0, 0, 1.0, condition="proc_kill"))
        hero = Combatant("z", "you", stats, 200, skills, {}, Kit(on_use={1: effects}))
        foes = [unit("t", "them", hp=boss_hp), unit("u", "them", hp=10**6)]
        return simulate([hero, *foes], runs=100).heroes[0].damage

    assert dealt(100.0, True) > dealt(100.0, False) * 1.05
    assert dealt(10**6, True) == pytest.approx(dealt(10**6, False))  # nobody defeated: nothing follows


def test_zio_kit_carries_the_popup_numbers() -> None:
    kit, cooldowns = apply_override(Kit(), {3: 5}, load_overrides()["c1133"])
    assert cooldowns == {3: 4}
    assert [(e.kind, e.value, e.turns) for e in kit.before_use[1]] == [("atk", 0.5, 1)]
    assert [(e.kind, e.value) for e in kit.at_start] == [("cr", 0.40)]
    assert [(e.kind, e.value, e.count, e.turns, e.condition) for e in kit.on_use[3]] == [
        ("dispel", 0.0, 2, 2, ""),
        ("silence", 0.0, 0, 1, ""),
        ("cr", -0.5, 0, 2, ""),
        ("deify", 0.0, 0, 3, ""),
        ("extra_attack", 2.0, 0, 2, ""),
        ("cr", 0.5, 0, 2, "proc_kill"),
    ]


def test_a_skill_effect_nullifier_and_a_one_turn_cooldown_work_in_battle() -> None:
    """New Moon Luna (SPEC D109): the first effect of an enemy skill that lands on her is nullified, once; and a
    cooldown of 1 turn is not read as no cooldown at all (MECH-CD-02)."""
    import random

    from e7ac.predict.battle import Combatant, _State, _use, simulate
    from e7ac.predict.damage import SkillPower
    from tests.test_effects import unit

    team = [unit("a", "you"), unit("b", "them")]
    state = [_State(), _State()]
    _use(Effect("nullifier", False, 0.0, 99, 1.0), 1, [], team, [1e4, 1e4], [0.0, 0.0], state, random.Random(1))
    assert state[1].timers["nullifier"] == 99
    stun = Effect("stun", True, 0.0, 1, 2.0)
    _use(stun, 0, [1], team, [1e4, 1e4], [0.0, 0.0], state, random.Random(1))
    assert "stun" not in state[1].timers and "nullifier" not in state[1].timers  # nullified, and used up
    _use(stun, 0, [1], team, [1e4, 1e4], [0.0, 0.0], state, random.Random(1))
    assert state[1].timers["stun"] > 0  # the second one lands

    stats = unit("x", "you").stats
    skills = {1: SkillPower(0.05, 1.0, 0.0), 3: SkillPower(4.0, 1.0, 0.0)}

    def dealt(cooldown: int) -> float:
        hero = Combatant("l", "you", stats, 300, skills, {3: cooldown}, Kit())
        return simulate([hero, unit("t", "them", hp=10**6)], runs=100).heroes[0].damage

    assert dealt(1) == pytest.approx(dealt(2))  # a 1-turn cooldown is read as two own turns


def test_new_moon_luna_kit_carries_the_popup_numbers() -> None:
    kit, cooldowns = apply_override(Kit(), {2: 2, 3: 4}, load_overrides()["c2066"])
    assert cooldowns == {2: 2, 3: 1}
    assert [(e.kind, e.value) for e in kit.on_use[1]] == [("cr", 0.25)]
    assert [(e.kind, e.value, e.turns) for e in kit.on_use[2]] == [("def", -0.7, 1), ("cr", 0.5, 2)]
    third = {e.kind: (e.turns, e.receivers) for e in kit.on_use[3]}
    assert third == {"dispel": (2, "enemies"), "seal": (2, "enemies"), "unable_buff": (2, "enemies")}
    assert [(e.kind, e.turns) for e in kit.at_start] == [("nullifier", 99)]


def test_random_debuffs_sleep_restrict_stealth_and_block_work_in_battle() -> None:
    """Witch of the Mere Tenebria (SPEC D109)."""
    import random

    from e7ac.domain.codes import Stat
    from e7ac.predict.battle import (
        Combatant,
        _dual_attack,
        _hidden,
        _raise_cr,
        _State,
        _strike,
        _use,
        simulate,
    )
    from tests.test_effects import S1, unit

    # random: exactly one of the options for each enemy, all of them over many uses
    options = (Effect("stun", True, 0.0, 1, 1.0), Effect("sleep", True, 0.0, 1, 1.0), Effect("spd", True, -0.3, 1, 1.0))
    team = [unit("a", "you"), unit("b", "them"), unit("c", "them")]
    seen = set()
    for seed in range(60):
        state = [_State(), _State(), _State()]
        _use(
            Effect("random", True, 0.0, 1, 2.0, receivers="enemies", options=options),
            0,
            [],
            team,
            [1e4] * 3,
            [0.0] * 3,
            state,
            random.Random(seed),
        )
        for j in (1, 2):
            held = {k for k in ("stun", "sleep") if state[j].timers.get(k, 0) > 0} | (
                {"spd"} if "spd" in state[j].debuffs else set()
            )
            assert len(held) == 1  # one debuff each, never none or two
            seen |= held
    assert seen == {"stun", "sleep", "spd"}

    # sleep: no turn, and an attack wakes it (damage that is no attack does not)
    sleeper = _State(timers={"sleep": 2, "stealth": 2})
    hp = [1e4, 1e4, 1e4]
    _strike(team, hp, [0.0] * 3, [_State(), sleeper, _State()], 1, 100.0, random.Random(1))
    assert sleeper.timers.get("sleep") == 2
    _strike(team, hp, [0.0] * 3, [_State(), sleeper, _State()], 1, 100.0, random.Random(1), by=0)
    assert "sleep" not in sleeper.timers and "stealth" not in sleeper.timers

    # restrict: no Combat Readiness increase; stealth: hidden only while an ally lives; block: buffs without effect
    cr = [0.0, 0.0, 0.0]
    _raise_cr(team, [1e4] * 3, cr, [_State(), _State(timers={"restrict": 2}), _State()], 1, 0.5)
    assert cr[1] == 0.0
    hidden = [_State(), _State(timers={"stealth": 2}), _State()]
    assert _hidden(team, [1e4] * 3, hidden, 1) and not _hidden(team, [1e4, 1e4, 0.0], hidden, 1)
    blocked = _State(timers={"block": 2})
    blocked.apply(Effect("atk", False, 0.5, 2, 1.0))
    assert blocked.factor("atk") == 1.0
    state = [_State(), _State(timers={"block": 2}), _State()]
    _use(Effect("atk", False, 0.5, 2, 1.0), 1, [], team, [1e4] * 3, [0.0] * 3, state, random.Random(1))
    assert "atk" not in state[1].buffs  # nor can it receive a buff

    # the ally with the highest Attack is the one that follows with a basic attack
    strong = Combatant("s", "you", {**unit("x", "you").stats, Stat.ATK: 9000.0}, 200, S1, {}, Kit())
    mates = [unit("t", "you"), unit("w", "you"), strong, unit("f", "them")]
    states = [_State() for _ in mates]
    hits = {(m, 3, 1): float(100 * (m + 1)) for m in range(3)}
    _dual_attack(0, [3], mates, [1e4] * 4, [0.0] * 4, states, hits, random.Random(1), strongest=True)
    assert states[2].dealt > 0 and states[1].dealt == 0

    # an unseen target in stealth lives longer: the attacker goes for the other foe first
    def damage_of(stealthy: bool) -> float:
        kit = Kit(at_start=[Effect("stealth", False, 0.0, 99, 1.0)]) if stealthy else Kit()
        foes = [unit("t", "them", kit, hp=3000), unit("u", "them", hp=10**6)]
        return simulate([unit("a", "you"), *foes], runs=100).heroes[1].damage

    assert damage_of(True) > damage_of(False) * 1.2


def test_tenebria_kit_carries_the_popup_numbers() -> None:
    kit, cooldowns = apply_override(Kit(), {2: 3, 3: 5}, load_overrides()["c6050"])
    assert cooldowns == {2: 3, 3: 5}
    first = kit.on_use[1]
    assert [(o.kind, o.value) for o in first[0].options] == [("poison", 0.0), ("spd", -0.3), ("def", -0.7)]
    assert (first[1].kind, first[1].receivers, first[1].condition) == ("dual_attack", "highest_atk", "cd3")
    assert [(e.kind, e.turns) for e in kit.on_use[2]] == [("restrict", 2), ("stealth", 2)]
    third = kit.on_use[3]
    assert [(e.kind, e.count, e.turns, e.receivers) for e in third[:2]] == [
        ("dispel", 2, 2, "enemies"),
        ("block", 0, 2, "enemies"),
    ]
    assert [o.kind for o in third[2].options] == ["stun", "sleep", "redirect_provoke"] and third[3].value == 0.5


def test_a_random_effect_must_list_its_options() -> None:
    text = GOOD.replace('kind = "resurrect"', 'kind = "random"')
    with pytest.raises(KitError, match="options"):
        parse_override(text, "c2049.toml")


def test_soul_shield_team_combat_readiness_and_reactions_work_in_battle() -> None:
    """Frieren, Celine and Kizuna AI (SPEC D110): Soul nullifies an attack, a hit on the hero gives its allies Combat
    Readiness, a non-attack skill of an enemy sets off Blink, and a cleanse can pick one debuffed ally."""
    import random

    from e7ac.domain.codes import Stat
    from e7ac.predict.battle import Combatant, _after_hit, _react, _State, _strike, _use, simulate
    from tests.test_effects import S1, unit

    rng = random.Random(1)
    team = [unit("f", "you", Kit(soul_shield=4, hit_team_cr=0.13)), unit("a", "you"), unit("e", "them")]
    hp, cr = [1e4, 1e4, 1e4], [0.0, 0.0, 0.0]
    state = [_State(soul=3), _State(), _State()]
    _strike(team, hp, cr, state, 0, 1000.0, rng, by=2)
    assert hp[0] == 9000 and state[0].soul == 3  # not enough Soul
    state[0].soul = 4
    _strike(team, hp, cr, state, 0, 1000.0, rng, by=2)
    assert hp[0] == 9000 and state[0].soul == 0  # nullified, 4 Soul spent
    state[0].soul = 4
    _strike(team, hp, cr, state, 0, 1000.0, rng)  # damage that is no attack is not nullified
    assert hp[0] == 8000
    state[0].timers["seal"] = 2
    _strike(team, hp, cr, state, 0, 1000.0, rng, by=2)
    assert hp[0] == 7000  # sealed: Defensive Magic does not work

    state = [_State(), _State(), _State()]
    _after_hit(0, 2, 100.0, False, team, [1e4] * 3, cr, state, {}, rng)
    assert cr[0] == pytest.approx(0.13) and cr[1] == pytest.approx(0.13) and cr[2] == 0.0

    def foe_damage(shielded: bool) -> float:  # the Soul she gathers with S1 shields her from every second attack
        kit = Kit(soul_shield=4, soul_gain={1: 2}) if shielded else Kit()
        return simulate([unit("f", "you", kit, hp=10**6), unit("e", "them", hp=10**6)], runs=50).heroes[1].damage

    assert foe_damage(True) < foe_damage(False) * 0.8

    # Blink: after an enemy's non-attack skill she cleanses, attacks a random enemy and gains Combat Readiness
    reaction = [
        Effect("cleanse", False, 0.0, 0, 1.0, receivers="self"),
        Effect("extra_attack", False, 2.0, 0, 1.0),
        Effect("cr", False, 0.3, 0, 1.0),
    ]
    celine = Combatant(
        "c", "you", unit("x", "you").stats, 200, {1: S1[1], 2: S1[1]}, {}, Kit(enemy_support_effects=reaction)
    )
    pair = [celine, unit("e", "them")]
    hp, cr, state = [1e4, 1e4], [0.0, 0.0], [_State(dots=[(100.0, 2)]), _State()]
    _react(0, pair, hp, cr, state, {(0, 1, 2): 500.0}, rng)
    assert not state[0].dots and cr[0] == pytest.approx(0.3) and hp[1] == 9500

    def dealt(reacts: bool) -> float:  # the foe spends turns on a skill with no attack, which sets Blink off
        foe = unit("e", "them", Kit(on_use={3: [Effect("atk", False, 0.1, 1, 1.0)]}), hp=10**7)
        foe = dataclasses.replace(foe, cooldowns={3: 2})
        hero = dataclasses.replace(celine, kit=Kit(enemy_support_effects=reaction) if reacts else Kit())
        return (
            simulate([dataclasses.replace(hero, stats={**hero.stats, Stat.HP: 10**7}), foe], runs=50).heroes[0].damage
        )

    assert dealt(True) > dealt(False) * 1.05

    # a cleanse that picks one debuffed ally takes one debuff from that ally only
    allies = [unit("k", "you"), unit("a", "you"), unit("b", "you")]
    state = [_State(), _State(dots=[(1.0, 2), (2.0, 2)]), _State(dots=[(1.0, 2)])]
    _use(
        Effect("cleanse", False, 0.0, 0, 1.0, count=1, receivers="debuffed"),
        0,
        [],
        allies,
        [1e4] * 3,
        [0.0] * 3,
        state,
        rng,
    )
    assert len(state[1].dots) == 1 and len(state[2].dots) == 1


def test_celine_kizuna_ai_and_frieren_kits_carry_the_popup_numbers() -> None:
    kits = load_overrides()
    celine, cooldowns = apply_override(Kit(), {3: 3}, kits["c1103"])
    assert cooldowns == {3: 2}
    assert [(e.kind, e.turns) for e in celine.on_use[1]] == [("stealth", 1)]
    assert [(e.kind, e.turns) for e in celine.at_start] == [("stealth", 2)]
    assert [(e.kind, e.value) for e in celine.enemy_support_effects] == [
        ("cleanse", 0.0),
        ("extra_attack", 2.0),
        ("cr", 0.3),
    ]
    assert (celine.on_use[3][0].kind, celine.on_use[3][0].scaling, celine.on_use[3][0].turns) == (
        "barrier",
        (0.0, 0.5, 0.0),
        2,
    )
    kizuna, cooldowns = apply_override(Kit(), {3: 4}, kits["c1107"])
    assert (
        cooldowns == {3: 4}
        and kizuna.on_turn[0].scaling == (0.1, 0.0, 0.0)
        and kizuna.on_turn[0].receivers == "lowest:1"
    )
    third = {e.kind: e for e in kizuna.on_use[3]}
    assert (third["atk"].value, third["atk"].turns, third["atk"].receivers) == (0.5, 3, "all")
    assert (third["barrier"].scaling, third["barrier"].turns) == ((0.14, 0.0, 0.0), 3)
    assert (third["spd"].value, third["spd"].turns, third["spd"].receivers) == (0.3, 2, "self")
    assert (kizuna.on_use[1][0].kind, kizuna.on_use[1][0].receivers) == ("cleanse", "debuffed")
    frieren, cooldowns = apply_override(Kit(), {3: 4}, kits["c1172"])
    assert cooldowns == {3: 3} and frieren.hit_team_cr == 0.13 and frieren.soul_shield == 4
    assert frieren.soul_gain == {1: 2, 3: 3}
    assert [(e.kind, e.turns) for e in frieren.on_use[1]] == [("immunity", 1)]
    assert [(e.kind, e.value, e.turns) for e in frieren.before_use[3]] == [("atk", 0.5, 3)]
    assert [e.kind for e in frieren.on_use[3]] == ["dispel", "cr"]


def test_nullifier_transfer_rescue_bleed_and_overload_work_in_battle() -> None:
    """Fallen Cecilia, School Nurse Yulha and Tactical Archetype Coli (SPEC D111)."""
    import random

    from e7ac.domain.codes import Stat
    from e7ac.predict.battle import _max_hp, _State, _strike, _use
    from tests.test_effects import unit

    rng = random.Random(1)
    team = [unit("a", "you"), unit("b", "you"), unit("e", "them")]
    hp, cr = [1e4, 1e4, 1e4], [0.0, 0.0, 0.0]

    # a skill nullifier takes the next damage from a skill, once
    state = [_State(), _State(timers={"skill_nullifier": 99}), _State()]
    _strike(team, hp, cr, state, 1, 1000.0, rng, by=2)
    assert hp[1] == 1e4 and "skill_nullifier" not in state[1].timers
    _strike(team, hp, cr, state, 1, 1000.0, rng, by=2)
    assert hp[1] == 9000

    # transfer: the caster's debuffs go to the target
    mine = _State(debuffs={"def": (-0.7, 2)}, dots=[(100.0, 2)], timers={"silence": 1})
    state = [mine, _State(), _State()]
    _use(Effect("transfer", True, 0.0, 0, 1.0), 0, [2], team, [1e4] * 3, cr, state, rng)
    assert not mine.debuffed and state[2].debuffs == {"def": (-0.7, 2)} and state[2].dots == [(100.0, 2)]
    assert state[2].timers["silence"] == 1

    # rescue: a dead ally comes back at full Health with superhumanization (Health and Speed +100%)
    hp = [1e4, 0.0, 1e4]
    state = [_State(), _State(debuffs={"atk": (-0.5, 2)}), _State()]
    _use(Effect("rescue", False, 0.5, 3, 1.0), 0, [], team, hp, cr, state, rng)
    assert hp[1] == pytest.approx(2e4) and _max_hp(team, state, 1) == pytest.approx(2e4)
    assert state[1].factor("spd") == pytest.approx(2.0) and cr[1] == pytest.approx(0.5)
    assert not state[1].debuffs
    hp = [1e4, 3000.0, 6000.0]  # nobody dead: the weakest ally is saved
    state = [_State(), _State(), _State()]
    _use(Effect("rescue", False, 0.5, 3, 1.0), 0, [], team, hp, cr, state, rng)
    assert hp[1] == pytest.approx(2e4) and hp[2] == 6000.0 and state[0].healed == pytest.approx(17000)

    # two bleeding effects with their own chances, detonated at once: damage per turn x turns left
    state = [_State(), _State(), _State()]
    hp = [1e4] * 3
    _use(Effect("bleed", True, 0.0, 2, 2.0, count=2), 0, [2], team, hp, cr, state, rng)
    assert len(state[2].dots) == 2
    _use(Effect("detonate_dots", True, 0.0, 0, 1.0), 0, [2], team, hp, cr, state, rng)
    assert not state[2].dots and hp[2] == pytest.approx(1e4 - 2 * 2 * 0.3 * team[0].stats[Stat.ATK])

    # overload: Attack and Speed +50%, then Energy Depletion (-50%) for 1 turn
    over = _State()
    over.apply(Effect("overload", False, 0.0, 2, 1.0))
    assert over.factor("atk") == pytest.approx(1.5) and over.factor("spd") == pytest.approx(1.5)
    over.tick()
    over.tick()
    assert over.factor("atk") == pytest.approx(0.5) and over.factor("spd") == pytest.approx(0.5)
    over.tick()
    assert over.factor("atk") == 1.0


def test_ally_triggers_always_critical_hits_and_focus_work_in_battle() -> None:
    """Yulha's S2, Coli's Guard and Flan's Dual Attack (SPEC D111)."""
    from e7ac.predict.battle import Combatant, simulate
    from e7ac.predict.damage import SkillPower
    from tests.test_effects import S1, unit

    stats = unit("x", "you").stats

    def healed(kit: Kit) -> float:
        yulha = unit("y", "you", kit, hp=10**6)
        return simulate([yulha, unit("a", "you", hp=10**5), unit("e", "them", hp=10**6)], runs=50).heroes[0].healed

    heal = Effect("heal", False, 0.0, 0, 1.0, scaling=(0.1, 0.0, 0.0), receivers="target")
    assert healed(Kit(ally_turn_effects=[heal])) > 0 and healed(Kit()) == 0

    skills = {1: SkillPower(0.05, 1.0, 0.0), 2: SkillPower(6.0, 1.0, 0.0)}

    def coli_damage(kit: Kit) -> float:  # an ally's attack is followed by her Flank Strike
        coli = Combatant("c", "you", stats, 150, skills, {}, kit)
        ally = unit("a", "you", hp=10**7)
        return simulate([coli, ally, unit("e", "them", hp=10**7)], runs=50).heroes[0].damage

    follow = [Effect("extra_attack", False, 2.0, 0, 1.0)]
    never = coli_damage(Kit())
    assert coli_damage(Kit(ally_attack_effects=follow, ally_attack_single=True)) > never * 2
    assert coli_damage(Kit(ally_attack_effects=follow, ally_attack_cooldown=6)) < coli_damage(
        Kit(ally_attack_effects=follow)
    )

    def flan_damage(focus: int) -> float:  # Dual Attack costs Focus: with none she never follows
        kit = Kit(
            ally_attack_effects=[Effect("self_dual", False, 0.0, 0, 1.0)], ally_attack_focus=True, focus_start=focus
        )
        flan = Combatant("f", "you", stats, 150, S1, {}, kit)
        return simulate([flan, unit("a", "you", hp=10**7), unit("e", "them", hp=10**7)], runs=50).heroes[0].damage

    assert flan_damage(3) > flan_damage(0) * 1.1

    def crit_damage(
        always: tuple[int, ...],
    ) -> float:  # no critical chance at all: only the guarantee gives critical hits
        flan = Combatant("f", "you", stats, 150, {1: SkillPower(1.0, 1.0, 0.0)}, {}, Kit(always_crit=always))
        return simulate([flan, unit("e", "them", hp=10**7)], runs=50).heroes[0].damage

    assert crit_damage((1,)) > crit_damage(()) * 1.3


def test_cecilia_yulha_coli_and_flan_kits_carry_the_popup_numbers() -> None:
    kits = load_overrides()
    cecilia, cooldowns = apply_override(Kit(), {3: 5}, kits["c2002"])
    assert cooldowns == {3: 4} and (cecilia.on_use[1][0].kind, cecilia.on_use[1][0].chance) == ("provoke", 1.0)
    assert [(e.kind, e.scaling, e.receivers, e.turns) for e in cecilia.at_start] == [
        ("barrier", (0.1, 0.0, 0.0), "all", 2)
    ]
    assert [(e.kind, e.receivers) for e in cecilia.on_turn] == [("barrier", "lowest:1")]
    assert [e.kind for e in cecilia.on_use[3]] == ["dispel", "transfer", "skill_nullifier"]
    yulha, cooldowns = apply_override(Kit(), {3: 7}, kits["c2131"])
    assert cooldowns == {3: 4}
    assert [(e.kind, e.count, e.chance, e.turns) for e in yulha.on_use[1]] == [
        ("bleed", 2, 0.95, 2),
        ("detonate_dots", 0, 1.0, 2),
    ]
    assert [(e.kind, e.receivers) for e in yulha.ally_turn_effects] == [("cleanse", "target"), ("heal", "target")]
    assert (yulha.on_use[3][0].kind, yulha.on_use[3][0].value, yulha.on_use[3][0].turns) == ("rescue", 0.5, 3)
    coli, cooldowns = apply_override(Kit(), {2: 4, 3: 5}, kits["c5033"])
    assert cooldowns == {2: 0, 3: 4} and coli.ally_attack_single and coli.ally_attack_cooldown == 4
    assert [(e.kind, e.value) for e in coli.ally_attack_effects] == [
        ("swift", 0.5),
        ("extra_attack", 2.0),
        ("dispel", 0.0),
        ("def", -0.7),
        ("cr", 0.2),
    ]
    assert [(e.kind, e.condition, e.chance) for e in coli.on_use[1]][:2] == [
        ("bleed", "no_overload", 0.8),
        ("bleed", "overload", 1.0),
    ]
    assert [(e.kind, e.turns) for e in coli.before_use[3]] == [("overload", 2)]
    flan, cooldowns = apply_override(Kit(), {3: 4}, kits["c5110"])
    assert cooldowns == {3: 3} and flan.always_crit == (1, 3) and flan.focus_start == 3 and flan.focus_gain == {1: 1}
    assert flan.ally_attack_slots == (1,) and flan.ally_attack_focus
    assert [(e.kind, e.value) for e in flan.ally_attack_effects] == [("self_dual", 0.0), ("cr", 0.35)]
    assert (flan.on_use[1][0].kind, flan.on_use[1][0].chance) == ("stun", 0.4)
    assert [(e.kind, e.value, e.receivers) for e in flan.on_use[3]] == [("def", -0.7, ""), ("cr", 0.2, "all")]


def test_cooldown_cuts_fighting_spirit_and_ready_reduction_work_in_battle() -> None:
    """Kayron, Krau, Bellona and Diene (SPEC D113)."""
    import random

    from e7ac.predict.battle import Combatant, _after_hit, _fs_add, _State, _strike, _use, simulate
    from e7ac.predict.damage import SkillPower
    from tests.test_effects import unit

    rng = random.Random(1)
    team = [
        unit("k", "you", Kit(hit_cd_cut=1, area_hit_cd_cut=1, area_hit_effects=[])),
        unit("a", "you"),
        unit("e", "them"),
    ]
    hp, cr = [1e4, 1e4, 1e4], [0.0, 0.0, 0.0]
    state = [_State(), _State(), _State()]
    _after_hit(0, 2, 100.0, False, team, hp, cr, state, {}, rng)
    assert state[0].cd_cut == 1  # attacked: cooldowns -1
    _after_hit(0, 2, 100.0, False, team, hp, cr, state, {}, rng, area=True)
    assert state[0].cd_cut == 3  # and 1 more after an attack on all allies
    state = [_State(), _State(), _State()]
    _use(Effect("cd_cut", False, 1.0, 0, 1.0), 0, [], team, hp, cr, state, rng)
    assert state[0].cd_cut == 1

    # Fighting Spirit: at full it is consumed and its effects happen at once
    fs_team = [
        unit("b", "you", Kit(fs_full=100, fs_effects=[Effect("cr", False, 0.3, 0, 1.0, receivers="self")])),
        unit("e", "them"),
    ]
    state, cr = [_State(), _State()], [0.0, 0.0]
    _fs_add(0, 60, fs_team, [1e4, 1e4], cr, state, {}, rng)
    assert state[0].fs == 60 and cr[0] == 0.0
    _fs_add(0, 50, fs_team, [1e4, 1e4], cr, state, {}, rng)
    assert state[0].fs == 10 and cr[0] == pytest.approx(0.3)
    _fs_add(1, 100, fs_team, [1e4, 1e4], cr, state, {}, rng)  # no Fighting Spirit kit: nothing
    assert state[1].fs == 0

    # additional damage that grows with the Spirit held, consuming it
    state, hp = [_State(fs=50), _State()], [1e4, 1e4]
    solo = [unit("k", "you", Kit(fs_full=100)), unit("e", "them")]
    _use(Effect("fs_damage", False, 5000.0, 2, 1.0, count=10000), 0, [], solo, hp, [0.0, 0.0], state, rng)
    assert hp[1] == pytest.approx(1e4 - 10000) and state[0].fs == 0  # 5,000 + half of 10,000

    # damage reduction while the S3 is off cooldown (and a sealed hero loses the passive, not this skill's own)
    guard = [unit("k", "you", Kit(ready_reduction=0.3)), unit("e", "them")]
    hp, state = [1e4, 1e4], [_State(s3_ready=True), _State()]
    _strike(guard, hp, [0.0, 0.0], state, 0, 1000.0, rng, by=1)
    assert hp[0] == pytest.approx(9300)
    state[0].s3_ready = False
    _strike(guard, hp, [0.0, 0.0], state, 0, 1000.0, rng, by=1)
    assert hp[0] == pytest.approx(8300)

    skills = {1: SkillPower(1.0, 1.0, 0.0), 3: SkillPower(2.0, 1.0, 0.0)}

    def foe_damage(reduction: float) -> float:  # an S3 that is never used keeps the reduction on all battle long
        kit = Kit(ready_reduction=reduction)
        hero = Combatant("k", "you", unit("x", "you").stats, 100, skills, {3: 9}, kit)
        return simulate([hero, unit("e", "them", hp=10**6)], runs=50).heroes[1].damage

    assert foe_damage(0.3) < foe_damage(0.0)


def test_kayron_krau_bellona_and_diene_kits_carry_the_popup_numbers() -> None:
    kits = load_overrides()
    kayron, cooldowns = apply_override(Kit(), {3: 5}, kits["c2023"])
    assert (
        cooldowns == {3: 4} and kayron.fs_full == 100 and kayron.fs_enemy_turn == 10 and kayron.ready_reduction == 0.3
    )
    assert [(e.kind, e.value) for e in kayron.fs_effects] == [("cleanse", 0.0), ("cr", 1.0)]
    assert [(e.kind, e.turns) for e in kayron.at_start] == [("counter", 2)] and kayron.on_turn[0].kind == "counter"
    assert (kayron.on_use[1][0].kind, kayron.on_use[1][0].scaling) == ("barrier", (0.12, 0.0, 0.0))
    assert (kayron.on_use[3][0].kind, kayron.on_use[3][0].value, kayron.on_use[3][0].count) == (
        "fs_damage",
        5000,
        10000,
    )
    krau, cooldowns = apply_override(Kit(), {2: 0, 3: 9}, kits["c2070"])
    assert cooldowns == {2: 0, 3: 8} and krau.hit_cd_cut == 1 and krau.area_hit_cd_cut == 1
    assert [(e.kind, e.scaling, e.receivers, e.turns) for e in krau.area_hit_effects] == [
        ("barrier", (0.14, 0.0, 0.0), "all", 2)
    ]
    assert [(e.kind, e.value) for e in krau.on_use[3]] == [("immunity", 0.0), ("cd_cut", 1.0)]
    assert (krau.on_use[1][0].kind, krau.on_use[1][0].value) == ("spd", 0.3)
    bellona, cooldowns = apply_override(Kit(), {3: 3}, kits["c2071"])
    assert cooldowns == {2: 0, 3: 3} and bellona.always_crit == (1, 2, 3)
    assert bellona.fs_gain == {1: 20, 3: 20} and bellona.fs_ally_hit == 20
    assert [(e.kind, e.value) for e in bellona.fs_effects] == [("extra_attack", 2.0), ("cr", 0.3)]
    assert [(e.kind, e.value, e.turns) for e in bellona.before_use[3]] == [("atk", 0.3, 3), ("def", 0.3, 3)]
    diene, cooldowns = apply_override(Kit(), {2: 4, 3: 5}, kits["c2076"])
    assert cooldowns == {2: 0, 3: 4} and diene.always_crit == (1, 3)
    assert [(e.kind, e.value, e.turns) for e in diene.at_start] == [("immortal", 0.0, 1), ("res", 2.0, 1)]
    assert (diene.on_use[1][0].kind, diene.on_use[1][0].receivers, diene.on_use[1][0].condition) == (
        "dual_attack",
        "highest_atk",
        "immortal",
    )
    assert [(e.kind, e.turns) for e in diene.before_use[3]] == [("seal", 2)] and diene.on_use[3][0].value == -0.3


def test_target_bzzt_cascade_immobilize_concealment_and_turn_order_passives_work_in_battle() -> None:
    """Lilibet, Laika, Choux, Roana and Aube (SPEC D114)."""
    import random

    from e7ac.domain.codes import Stat
    from e7ac.predict.battle import Combatant, _crit_chance, _hidden, _State, _strike, _use, simulate
    from e7ac.predict.damage import SkillPower
    from tests.test_effects import unit

    rng = random.Random(1)
    team = [unit("a", "you", Kit(crit_bonus=0.3)), unit("b", "you"), unit("e", "them")]
    hp, cr = [1e4, 1e4, 1e4], [0.0] * 3
    state = [_State(), _State(), _State(timers={"target": 2})]
    _strike(team, hp, cr, state, 2, 1000.0, rng, by=0)
    assert hp[2] == pytest.approx(1e4 - 1150)  # Target: 15% more damage suffered
    assert _crit_chance(team, state, 0) == pytest.approx(0.3) and _crit_chance(team, state, 1) == 0.0

    # concealment hides the holder while it has an ally, and an attack does not end it (stealth is)
    state = [_State(), _State(), _State(timers={"concealment": 99, "stealth": 2})]
    assert _hidden(team, hp, state, 2) is False  # alone on its side: nothing to hide it
    mates = [unit("a", "you"), unit("e", "them"), unit("f", "them")]
    state = [_State(), _State(timers={"concealment": 99}), _State()]
    assert _hidden(mates, [1e4] * 3, state, 1)
    _strike(mates, [1e4] * 3, [0.0] * 3, state, 1, 100.0, rng, by=0)
    assert state[1].timers.get("concealment") == 99 and _hidden(mates, [1e4] * 3, state, 1)

    # cd_up: the targets' cooldowns do not count down for a turn; debuffs the mark gives are counted
    state = [_State(), _State(dots=[(1.0, 2)], debuffs={"def": (-0.7, 1)}), _State()]
    _use(Effect("cd_up", True, 1.0, 0, 1.0), 0, [1], team, [1e4] * 3, [0.0] * 3, state, rng)
    assert state[1].cd_cut == -1 and state[1].debuff_count() == 2

    stats = unit("x", "you").stats
    piercing = {1: SkillPower(1.0, 1.0, 0.0), 3: SkillPower(2.0, 1.0, 0.0, penetration=0.8, targets=3)}

    def dealt(
        kit: Kit, foe_kit: Kit | None = None, skills: dict[int, SkillPower] | None = None, speed: int = 300
    ) -> float:
        hero = Combatant("h", "you", stats, speed, skills or piercing, {3: 2}, kit)
        tank = Combatant(
            "t", "them", {**stats, Stat.DEF: 4000.0, Stat.HP: 10**7}, 200, piercing, {3: 2}, foe_kit or Kit()
        )
        return simulate([hero, tank], runs=50).heroes[0].damage

    assert dealt(Kit(pen_marked={3: 0.8})) < dealt(Kit()) * 0.9  # no Target on the foe: no penetration
    assert dealt(Kit(at_start=[Effect("bzzt", False, 3000.0, 99, 1.0)])) > dealt(
        Kit()
    )  # additional damage after attacks
    one_shot = Kit(at_start=[Effect("cascade", False, 50000.0, 9, 1.0)])
    assert dealt(one_shot) - dealt(Kit()) == pytest.approx(50000, rel=0.02)  # dispelled after attacking: once
    assert dealt(Kit(speed_mult=0.5)) < dealt(Kit()) * 0.7

    def foe_damage(
        immobilized: bool,
    ) -> float:  # an immobilized foe gets no Combat Readiness from Speed for four actions
        foe = Kit(at_start=[Effect("immobilize", True, 0.0, 4, 1.0, receivers="self")]) if immobilized else Kit()
        hero = Combatant("h", "you", stats, 150, piercing, {3: 2}, Kit())
        tank = Combatant("t", "them", stats, 400, piercing, {3: 2}, foe)
        return simulate([hero, tank], runs=50).heroes[1].damage

    assert foe_damage(True) < foe_damage(False)

    def roana(front_cr: float) -> float:  # she follows the foremost ally's turn
        kit = Kit(front_ally_cr=front_cr)
        hero = Combatant("r", "you", stats, 50, piercing, {3: 2}, kit)
        ally = Combatant("f", "you", {**stats, Stat.HP: 10**7}, 300, piercing, {3: 2}, Kit())
        return simulate([ally, hero, unit("e", "them", hp=10**7)], runs=50).heroes[1].damage

    assert roana(1.0) > roana(0.0) * 1.5


def test_lilibet_laika_choux_roana_and_aube_kits_carry_the_popup_numbers() -> None:
    kits = load_overrides()
    lilibet, cooldowns = apply_override(Kit(), {2: 0, 3: 5}, kits["c2095"])
    assert cooldowns == {2: 0, 3: 4} and lilibet.fs_full == 100 and lilibet.fs_gain == {1: 25, 3: 50}
    assert lilibet.fs_debuff_turn == 10 and (lilibet.on_use[1][0].kind, lilibet.on_use[1][0].chance) == ("def", 0.75)
    assert [(e.kind, e.value) for e in lilibet.fs_effects] == [
        ("cleanse", 0.0),
        ("def", 0.6),
        ("immunity", 0.0),
        ("cr", 0.4),
    ]
    assert [(e.kind, e.value, e.receivers) for e in lilibet.on_use[3]] == [("cd_up", 1.0, ""), ("cr", 0.15, "all")]
    laika, cooldowns = apply_override(Kit(), {2: 4, 3: 5}, kits["c2099"])
    assert cooldowns == {2: 3, 3: 4} and laika.pen_marked == {3: 0.8}
    assert [(e.kind, e.condition) for e in laika.on_use[2]] == [
        ("dispel", ""),
        ("target", ""),
        ("atk", ""),
        ("immunity", ""),
        ("extra_turn", "marked"),
    ]
    assert (laika.on_use[1][0].kind, laika.on_use[1][0].value) == ("cr", 0.2)
    choux, cooldowns = apply_override(Kit(), {2: 0, 3: 5}, kits["c2101"])
    assert cooldowns == {2: 0, 3: 4} and choux.crit_bonus == 0.3 and choux.focus_start == 1
    assert (choux.on_turn[0].kind, choux.on_turn[0].value, choux.on_turn[0].turns) == ("bzzt", 2500, 1)
    assert [(e.kind, e.value, e.receivers) for e in choux.on_use[3]] == [("spd", 0.3, "all")]
    roana, cooldowns = apply_override(Kit(), {2: 0, 3: 5}, kits["c2102"])
    assert cooldowns == {2: 0, 3: 4} and roana.speed_mult == 0.5 and roana.front_ally_cr == 1.0
    assert (roana.on_use[1][0].kind, roana.on_use[1][0].chance) == ("def", 0.75)
    assert [(e.kind, e.value) for e in roana.on_use[3]] == [("dispel", 0.0), ("cd_up", 1.0), ("cr", -0.3)]
    aube, cooldowns = apply_override(Kit(), {2: 3, 3: 7}, kits["c5190"])
    assert cooldowns == {2: 3, 3: 6} and (aube.on_use[1][0].kind, aube.on_use[1][0].value) == ("cr", -0.2)
    assert [(e.kind, e.value, e.receivers) for e in aube.on_use[2]] == [
        ("cascade", 4000.0, "all"),
        ("skill_nullifier", 0.0, "self"),
        ("extra_turn", 0.0, ""),
    ]
    assert [(e.kind, e.turns, e.receivers) for e in aube.on_use[3]] == [
        ("dispel", 2, "enemies"),
        ("immobilize", 4, "enemies"),
        ("restrict", 2, "enemies"),
    ]
    assert [(e.kind, e.turns) for e in aube.at_start] == [("concealment", 999)]


def test_ally_pickers_and_starting_fighting_spirit_work_in_battle() -> None:
    """Command Model Laika and Benimaru (SPEC D116)."""
    import random

    from e7ac.domain.codes import Stat
    from e7ac.predict.battle import Combatant, _State, _use, simulate
    from tests.test_effects import S1, unit

    rng = random.Random(1)
    strong = Combatant("s", "you", {**unit("x", "you").stats, Stat.ATK: 9000.0}, 200, S1, {}, Kit())
    team = [unit("c", "you"), unit("w", "you"), strong, unit("e", "them")]
    hp, state = [1e4] * 4, [_State() for _ in team]
    cr = [0.0, 0.2, 0.9, 0.0]
    _use(Effect("cr", False, 0.15, 0, 1.0, receivers="highest_atk"), 0, [], team, hp, cr, state, rng)
    assert cr == [0.0, 0.2, pytest.approx(1.0), 0.0]  # the ally with the highest Attack (but the caster)
    cr = [0.0, 0.2, 0.9, 0.0]
    _use(Effect("cr", False, 0.15, 0, 1.0, receivers="highest_cr"), 0, [], team, hp, cr, state, rng)
    assert cr[2] == pytest.approx(1.0) and cr[1] == 0.2  # the one with the highest Combat Readiness
    seen = set()
    for seed in range(40):
        cr = [0.0, 0.0, 0.0, 0.0]
        _use(
            Effect("cr", False, 0.1, 0, 1.0, receivers="random_other"), 0, [], team, hp, cr, state, random.Random(seed)
        )
        assert cr[0] == 0.0 and cr[3] == 0.0 and sum(cr) == pytest.approx(0.1)  # one ally, never the caster
        seen.add(cr.index(0.1))
    assert seen == {1, 2}
    alone = [unit("c", "you"), unit("e", "them")]
    cr = [0.0, 0.0]
    _use(
        Effect("cr", False, 0.1, 0, 1.0, receivers="random_other"),
        0,
        [],
        alone,
        [1e4] * 2,
        cr,
        [_State(), _State()],
        rng,
    )
    assert cr == [0.0, 0.0]  # nobody to give it to

    def damage(start: int) -> float:  # Hell Flare comes sooner with Fighting Spirit at the start
        from e7ac.predict.damage import SkillPower

        skills = {1: SkillPower(0.01, 1.0, 0.0), 2: SkillPower(5.0, 1.0, 0.0)}
        kit = Kit(fs_full=100, fs_start=start, fs_gain={1: 25}, fs_effects=[Effect("extra_attack", False, 2.0, 0, 1.0)])
        hero = Combatant("b", "you", unit("x", "you").stats, 300, skills, {}, kit)
        return simulate([hero, unit("e", "them", hp=10**7)], runs=50).heroes[0].damage

    assert damage(50) > damage(0)


def test_renoa_cerise_command_model_laika_and_benimaru_kits_carry_the_popup_numbers() -> None:
    kits = load_overrides()
    renoa, cooldowns = apply_override(Kit(), {2: 0, 3: 3}, kits["c1193"])
    assert cooldowns == {2: 0, 3: 3} and [(e.kind, e.value, e.receivers) for e in renoa.on_use[3]] == [
        ("cr", 0.5, "all")
    ]
    cerise, cooldowns = apply_override(Kit(), {2: 3, 3: 4}, kits["c1081"])
    assert cooldowns == {2: 3, 3: 4}
    assert [(e.kind, e.value, e.condition) for e in cerise.on_use[1]] == [("cr", 0.2, ""), ("dual_attack", 0.0, "cd3")]
    assert [(e.kind, e.turns) for e in cerise.on_use[2]][:2] == [("stun", 1), ("unable_buff", 2)]
    assert [(e.kind, e.value, e.turns) for e in cerise.on_use[3]] == [
        ("shorten", 0.0, 1),
        ("spd", -0.3, 2),
        ("restrict", 0.0, 2),
        ("invincible", 0.0, 1),
    ]
    laika, cooldowns = apply_override(Kit(), {2: 4, 3: 5}, kits["c1099"])
    assert cooldowns == {2: 3, 3: 4}
    assert [(e.kind, e.value, e.receivers) for e in laika.on_use[1]] == [
        ("cr", 0.15, "self"),
        ("cr", 0.15, "random_other"),
    ]
    assert [(e.kind, e.value, e.chance) for e in laika.on_use[2]][:2] == [("target", 0.0, 1.0), ("atk", 0.75, 1.0)]
    assert (laika.on_use[2][2].kind, laika.on_use[2][2].receivers) == ("dual_attack", "highest_atk")
    assert [(e.kind, e.turns) for e in laika.on_use[3]] == [("spd", 2), ("sleep", 1)]
    benimaru, cooldowns = apply_override(Kit(), {2: 0, 3: 4}, kits["c1146"])
    assert cooldowns == {2: 0, 3: 3} and benimaru.fs_full == 100 and benimaru.fs_start == 50
    assert benimaru.fs_gain == {1: 25, 3: 25} and benimaru.fs_effects[0].kind == "extra_attack"
    assert [(e.kind, e.receivers) for e in benimaru.on_use[3]] == [("def", ""), ("cr", "highest_cr")]
