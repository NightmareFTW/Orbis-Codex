"""Win probability by simulating many battles (M11 part, SPEC D61): turn order and damage, nothing else yet.

Each battle: start CR 0-5% at random (MECH-CR-02), CR fills with speed and the first to 100% acts (MECH-CR-01); an
opponent's speed is drawn from Stove each battle. A unit uses S3 whenever its cooldown allows (epic7db; available
again N of the unit's own turns later, MECH-CD-01 `assumed`), else S1; without a known cooldown, S3 on its first
action only (`assumed`); it attacks the living enemy it needs the fewest hits to defeat (focus fire,
`assumed`: the game's targeting is not known); a skill with more than one target hits every living enemy. Each hit
is critical with the attacker's crit chance (MECH-DMG-05). Skill effects from epic7db (SPEC D63, `predict/effects`):
attack/defense/speed changes, stun/sleep, combat readiness, extra turns, immortality, immunity, revives; debuffs land
by MECH-EFF-01. Not modelled: heals, barriers, counters, DoTs, artifacts, combat set effects: low confidence.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from e7ac.domain.codes import Stat
from e7ac.predict.damage import CD_CAP, SkillPower, base_hit
from e7ac.predict.effects import Effect, Kit
from e7ac.predict.turn_order import START_CR_MAX

RUNS: Final = 10_000
SEED: Final = 7
LAND_CAP: Final = 0.85
"""MECH-EFF-01: at most 85% for a debuff to land (15% minimum resist)."""
MAX_ACTIONS: Final = 200
"""A battle still going after this many actions is a draw (nobody can defeat anybody with the modelled skills)."""


@dataclass(frozen=True, slots=True)
class Combatant:
    label: str
    side: str
    stats: Mapping[Stat, float]
    speed: float | Callable[[random.Random], float]
    skills: Mapping[int, SkillPower]
    cooldowns: Mapping[int, int] = field(default_factory=dict)
    """Skill slot -> cooldown in turns (epic7db); missing: unknown."""
    kit: Kit = field(default_factory=Kit)
    """Skill effects (epic7db icons and text, SPEC D63)."""


@dataclass(frozen=True, slots=True)
class BattleOdds:
    win: float
    loss: float
    draw: float
    mean_actions: float
    runs: int
    warnings: list[str] = field(default_factory=list)


def simulate(units: Sequence[Combatant], runs: int = RUNS, seed: int = SEED) -> BattleOdds:
    sides = {u.side for u in units}
    if sides != {"you", "them"}:
        raise ValueError("both sides need at least one hero")
    warnings = [f"{u.label}: no damaging skill known" for u in units if not u.skills]
    hits = {  # non-critical damage of each attacker's skills on each enemy, computed once
        (i, j, slot): base_hit(a.stats, skill, d.stats)
        for i, a in enumerate(units)
        for j, d in enumerate(units)
        if a.side != d.side
        for slot, skill in a.skills.items()
    }
    rng = random.Random(seed)
    wins = losses = actions_total = 0
    for _ in range(runs):
        result, actions = _battle(units, hits, rng)
        wins += result == "you"
        losses += result == "them"
        actions_total += actions
    return BattleOdds(wins / runs, losses / runs, (runs - wins - losses) / runs, actions_total / runs, runs, warnings)


def _battle(
    units: Sequence[Combatant], hits: Mapping[tuple[int, int, int], float], rng: random.Random
) -> tuple[str | None, int]:
    n = len(units)
    speeds = [u.speed(rng) if callable(u.speed) else u.speed for u in units]
    cr = [rng.uniform(0, START_CR_MAX) for _ in units]
    hp = [u.stats[Stat.HP] for u in units]
    acted = [False] * n
    waiting = [0] * n  # own turns until S3 is ready again
    state = [_State() for _ in units]
    for i, unit in enumerate(units):
        for effect in unit.kit.at_start:
            for r in _allies(units, hp, i) if effect.all_allies else [i]:
                state[r].apply(effect)
    for actions in range(1, MAX_ACTIONS + 1):
        alive = [i for i in range(n) if hp[i] > 0]
        speed = [speeds[i] * state[i].factor("spd") for i in range(n)]
        actor = min(alive, key=lambda i: (1 - cr[i]) / speed[i])
        elapsed = max(0.0, (1 - cr[actor]) / speed[actor])
        for i in alive:
            cr[i] += elapsed * speed[i]
        cr[actor] = 0.0
        unit, me = units[actor], state[actor]
        if me.timers.get("stun", 0) > 0:
            me.tick()
            continue
        waiting[actor] = max(0, waiting[actor] - 1)
        cooldown = unit.cooldowns.get(3)
        ready = (waiting[actor] == 0) if cooldown is not None else not acted[actor]
        slot = 3 if ready and 3 in unit.skills else 1
        if slot == 3 and cooldown:
            waiting[actor] = cooldown  # counts down at the start of each own turn: ready N turns later (MECH-CD-01)
        acted[actor] = True
        enemies = [j for j in alive if units[j].side != unit.side]
        skill = unit.skills.get(slot)
        targets: list[int] = []
        if skill is not None and enemies:
            targets = enemies if skill.targets > 1 else [min(enemies, key=lambda j: hp[j] / hits[(actor, j, slot)])]
            crit = skill.can_crit and rng.random() < min(unit.stats[Stat.CRIT_CHANCE], 1.0)
            factor = min(unit.stats[Stat.CRIT_DAMAGE], CD_CAP) if crit else 1.0
            attack = {**unit.stats, Stat.ATK: unit.stats[Stat.ATK] * me.factor("atk")}
            for j in targets:
                guard = {**units[j].stats, Stat.DEF: units[j].stats[Stat.DEF] * state[j].factor("def")}
                hp[j] -= base_hit(attack, skill, guard) * factor
                if hp[j] <= 0:
                    _lethal(units, hp, cr, state, j)
        for effect in unit.kit.on_use.get(slot, []):
            _use(effect, actor, targets, units, hp, cr, state, rng)
        me.tick()
        standing = {units[i].side for i in range(n) if hp[i] > 0}
        if len(standing) == 1:
            return standing.pop(), actions
    return None, MAX_ACTIONS


@dataclass(slots=True)
class _State:
    """Active effects of one unit: stat changes (kind -> (value, turns)) and timers (stun, immortal, immunity)."""

    buffs: dict[str, tuple[float, int]] = field(default_factory=dict)
    debuffs: dict[str, tuple[float, int]] = field(default_factory=dict)
    timers: dict[str, int] = field(default_factory=dict)
    revived: bool = False

    def factor(self, kind: str) -> float:
        return max(0.0, 1 + self.buffs.get(kind, (0.0, 0))[0] + self.debuffs.get(kind, (0.0, 0))[0])

    def apply(self, effect: Effect) -> None:
        if effect.kind in ("atk", "def", "spd"):
            table = self.debuffs if effect.debuff else self.buffs
            old = table.get(effect.kind, (0.0, 0))
            table[effect.kind] = (effect.value if abs(effect.value) >= abs(old[0]) else old[0], effect.turns)
        elif effect.kind in ("stun", "immortal", "immunity"):
            self.timers[effect.kind] = max(self.timers.get(effect.kind, 0), effect.turns)

    def tick(self) -> None:
        """End of the unit's own turn: every duration goes down by one."""
        for table in (self.buffs, self.debuffs):
            for kind, (value, turns) in list(table.items()):
                if turns <= 1:
                    del table[kind]
                else:
                    table[kind] = (value, turns - 1)
        for kind in list(self.timers):
            self.timers[kind] -= 1
            if self.timers[kind] <= 0:
                del self.timers[kind]


def _allies(units: Sequence[Combatant], hp: Sequence[float], i: int) -> list[int]:
    return [j for j in range(len(units)) if units[j].side == units[i].side and hp[j] > 0]


def _lethal(units: Sequence[Combatant], hp: list[float], cr: list[float], state: list[_State], j: int) -> None:
    """Lethal damage: immortality keeps 1 HP; a revive passive works once (MECH via SPEC D63, `assumed`)."""
    kit = units[j].kit
    if state[j].timers.get("immortal", 0) > 0:
        hp[j] = 1.0
    elif kit.revive is not None and not state[j].revived:
        state[j].revived = True
        hp[j] = units[j].stats[Stat.HP] * kit.revive
        for effect in kit.revive_effects:
            if effect.kind == "cr":
                cr[j] = min(1.0, cr[j] + effect.value)
            else:
                state[j].apply(effect)


def _use(
    effect: Effect,
    actor: int,
    targets: Sequence[int],
    units: Sequence[Combatant],
    hp: Sequence[float],
    cr: list[float],
    state: list[_State],
    rng: random.Random,
) -> None:
    if not effect.debuff:
        receivers = _allies(units, hp, actor) if effect.all_allies else [actor]
        for r in receivers:
            if effect.kind == "extra_turn":
                cr[r] = 1.0
            elif effect.kind == "cr":
                cr[r] = min(1.0, cr[r] + effect.value)
            else:
                state[r].apply(effect)
        return
    effectiveness = units[actor].stats.get(Stat.EFFECTIVENESS, 0.0)
    for j in targets:
        if hp[j] <= 0 or state[j].timers.get("immunity", 0) > 0:
            continue
        resistance = units[j].stats.get(Stat.EFFECT_RESISTANCE, 0.0)
        land = effect.chance * min(max(1 + effectiveness - resistance, 0.0), LAND_CAP)  # MECH-EFF-01
        if rng.random() >= land:
            continue
        if effect.kind == "cr":
            cr[j] = max(0.0, cr[j] + effect.value)
        else:
            state[j].apply(effect)


def confidence_interval(p: float, runs: int) -> float:
    """95% half-width of a simulated share (the simulation's own noise, not the model's error)."""
    return 1.96 * math.sqrt(max(p * (1 - p), 1e-12) / runs)
