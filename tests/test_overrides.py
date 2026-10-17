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
