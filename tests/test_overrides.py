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
