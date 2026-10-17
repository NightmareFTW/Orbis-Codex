"""Win probability by simulating many battles (M11 part, SPEC D61): turn order and damage, nothing else yet.

Each battle: start CR 0-5% at random (MECH-CR-02), CR fills with speed and the first to 100% acts (MECH-CR-01); an
opponent's speed is drawn from Stove each battle. A unit uses S3 whenever its cooldown allows (epic7db; available
again N of the unit's own turns later, MECH-CD-01 `assumed`), else an active S2 whose cooldown allows, else S1
(MECH-AI-01 `assumed`; a skill that only resurrects waits for a dead ally); without a known cooldown, S3 on its first
action only (`assumed`); it attacks the living enemy it needs the fewest hits to defeat (focus fire,
`assumed`: the game's targeting is not known); a skill with more than one target hits every living enemy. Each hit
is critical with the attacker's crit chance (MECH-DMG-05). Skill effects from epic7db (SPEC D63, `predict/effects`):
attack/defense/speed changes, stun/sleep, combat readiness, extra turns, immortality, immunity, revives; debuffs land
by MECH-EFF-01. Survival (SPEC D68): heals (never above max Health), barriers (absorb damage before Health, the
stronger kept, MECH-BAR-01 `assumed`), resurrection, cleanse, dispel, invincibility; passive heals and barriers act at
the start of each of the caster's turns. Part 2 (SPEC D74): burn / bleed / poison tick at the start of the holder's
turn (MECH-DOT-01), provoke (S1 at the provoker) and silence (S1 only), counterattacks (S1 back, no chain). Hand kits
(SPEC D76): effects only when an enemy was defeated, health sacrifice, reflect, speed cap, no counterattacks, immune
debuffs, revive given to allies, effects when hit down to a Health share. Not modelled: artifacts, combat set effects:
low confidence.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from e7ac.domain.codes import Stat
from e7ac.predict.damage import CD_CAP, SkillPower, base_hit
from e7ac.predict.effects import DOT_ATK, POISON_MAX_HP, Effect, Kit
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
    waiting: list[dict[int, int]] = [{} for _ in units]  # skill slot -> own turns until ready again
    state = [_State() for _ in units]
    for i, unit in enumerate(units):
        for effect in unit.kit.at_start:
            _use(effect, i, [], units, hp, cr, state, rng)
    for actions in range(1, MAX_ACTIONS + 1):
        alive = [i for i in range(n) if hp[i] > 0]
        speed = [speeds[i] * (1.0 if units[i].kit.speed_cap else state[i].factor("spd")) for i in range(n)]
        for c in alive:  # a speed cap (Harsetti): every other hero at most this share of the caster's speed
            if units[c].kit.speed_cap:
                speed = [v if i == c else min(v, units[c].kit.speed_cap * speed[c]) for i, v in enumerate(speed)]
        actor = min(alive, key=lambda i: (1 - cr[i]) / speed[i])
        elapsed = max(0.0, (1 - cr[actor]) / speed[actor])
        for i in alive:
            cr[i] += elapsed * speed[i]
        cr[actor] = 0.0
        unit, me = units[actor], state[actor]
        for effect in unit.kit.on_turn:  # passive heals / barriers at the start of the turn
            _use(effect, actor, [], units, hp, cr, state, rng)
        if me.dots:  # damage over time at the start of the turn (MECH-DOT-01)
            for damage, _ in me.dots:
                me.hurt(hp, actor, damage)
            me.dots = [(damage, turns - 1) for damage, turns in me.dots if turns > 1]
            if hp[actor] <= 0:
                _lethal(units, hp, cr, state, actor)
            if hp[actor] <= 0:
                standing = {units[i].side for i in range(n) if hp[i] > 0}
                if len(standing) == 1:
                    return standing.pop(), actions
                continue
        if me.timers.get("stun", 0) > 0:
            me.tick()
            continue
        for ready in waiting[actor]:
            waiting[actor][ready] = max(0, waiting[actor][ready] - 1)
        me.low_wait = max(0, me.low_wait - 1)
        dead_ally = any(hp[j] <= 0 and units[j].side == unit.side for j in range(n))
        held = any(me.timers.get(kind, 0) > 0 for kind in ("silence", "provoke", "redirect_provoke"))
        slot = 1 if held else _choose(unit, waiting[actor], acted[actor], dead_ally)
        if slot != 1 and unit.cooldowns.get(slot):
            waiting[actor][slot] = unit.cooldowns[slot]  # counts down at the start of each own turn (MECH-CD-01)
        acted[actor] = True
        enemies = [j for j in alive if units[j].side != unit.side]
        skill = unit.skills.get(slot)
        targets: list[int] = []
        killed = False
        if skill is not None and enemies:
            targets = enemies if skill.targets > 1 else [min(enemies, key=lambda j: hp[j] / hits[(actor, j, slot)])]
            if me.timers.get("provoke", 0) > 0 and me.provoker in enemies:
                targets = [me.provoker]  # provoked: attacks its provoker
            if me.timers.get("redirect_provoke", 0) > 0:  # MECH-PROV-01 (verified in game): basic skill on the enemy
                targets = [max(enemies, key=lambda j: units[j].stats[Stat.HP])]  # with the highest max Health
            crit = skill.can_crit and rng.random() < min(unit.stats[Stat.CRIT_CHANCE], 1.0)
            factor = min(unit.stats[Stat.CRIT_DAMAGE], CD_CAP) if crit else 1.0
            attack = {**unit.stats, Stat.ATK: unit.stats[Stat.ATK] * me.factor("atk")}
            for j in targets:
                guard = {**units[j].stats, Stat.DEF: units[j].stats[Stat.DEF] * state[j].factor("def")}
                before = hp[j]
                state[j].hurt(hp, j, base_hit(attack, skill, guard) * factor)
                if hp[j] <= 0:
                    _lethal(units, hp, cr, state, j)
                    killed = killed or hp[j] <= 0
                else:
                    _after_hit(j, actor, before - hp[j], units, hp, cr, state, hits, rng)
        for effect in unit.kit.on_use.get(slot, []):
            if effect.condition == "kill" and not killed:
                continue
            _use(effect, actor, targets, units, hp, cr, state, rng)
        me.tick()
        standing = {units[i].side for i in range(n) if hp[i] > 0}
        if len(standing) == 1:
            return standing.pop(), actions
    return None, MAX_ACTIONS


def _choose(unit: Combatant, waiting: Mapping[int, int], acted: bool, dead_ally: bool) -> int:
    """S3, else an active S2, when ready (MECH-AI-01 `assumed`); else S1."""
    for slot in (3, 2):
        effects = unit.kit.on_use.get(slot, [])
        if slot not in unit.skills and not effects:
            continue
        cooldown = unit.cooldowns.get(slot)
        if slot == 2 and not cooldown:
            continue  # a passive S2, or not known to be active
        if not (waiting.get(slot, 0) == 0 if cooldown is not None else not acted):
            continue
        if slot not in unit.skills and all(e.kind == "resurrect" for e in effects) and not dead_ally:
            continue  # nobody to bring back yet
        return slot
    return 1


@dataclass(slots=True)
class _State:
    """Active effects of one unit: stat changes (kind -> (value, turns)), timers (stun, immortal, immunity,
    invincible) and a barrier."""

    buffs: dict[str, tuple[float, int]] = field(default_factory=dict)
    debuffs: dict[str, tuple[float, int]] = field(default_factory=dict)
    timers: dict[str, int] = field(default_factory=dict)
    revived: bool = False
    barrier: float = 0.0
    barrier_turns: int = 0
    dots: list[tuple[float, int]] = field(default_factory=list)
    """Damage over time: (damage per turn, turns left)."""
    provoker: int = -1
    revive_hp: float = 0.0
    """Health share restored by a revive status (kind "revive_buff")."""
    low_wait: int = 0
    """Own turns until the low-Health trigger works again."""

    def factor(self, kind: str) -> float:
        return max(0.0, 1 + self.buffs.get(kind, (0.0, 0))[0] + self.debuffs.get(kind, (0.0, 0))[0])

    def apply(self, effect: Effect) -> None:
        if effect.kind == "revive_buff":
            self.timers["revive_buff"] = max(self.timers.get("revive_buff", 0), effect.turns)
            self.revive_hp = effect.value
        elif effect.kind in ("atk", "def", "spd"):
            table = self.debuffs if effect.debuff else self.buffs
            old = table.get(effect.kind, (0.0, 0))
            table[effect.kind] = (effect.value if abs(effect.value) >= abs(old[0]) else old[0], effect.turns)
        elif effect.kind in _TIMERS:
            self.timers[effect.kind] = max(self.timers.get(effect.kind, 0), effect.turns)

    def hurt(self, hp: list[float], j: int, damage: float) -> None:
        """Invincibility blocks the damage; a barrier absorbs it before Health (MECH-BAR-01)."""
        if self.timers.get("invincible", 0) > 0:
            return
        absorbed = min(self.barrier, damage)
        self.barrier -= absorbed
        hp[j] -= damage - absorbed

    def remove(self, debuffs: bool, count: int) -> None:
        """Cleanse (debuffs) or dispel (buffs): all when `count` is 0."""
        kinds = (
            [("timer", k) for k in ("stun", "provoke", "silence", "redirect_provoke")]
            + [("dot", "")] * len(self.dots)
            + [("debuff", k) for k in self.debuffs]
            if debuffs
            else [("timer", k) for k in ("immortal", "invincible", "immunity", "counter", "revive_buff")]
            + [("barrier", "")]
            + [("buff", k) for k in self.buffs]
        )
        removed = 0
        for table, kind in kinds:
            if count and removed >= count:
                return
            removed += self._drop(table, kind)

    def _drop(self, table: str, kind: str) -> bool:
        if table == "timer":
            return self.timers.pop(kind, 0) > 0
        if table == "debuff":
            return self.debuffs.pop(kind, None) is not None
        if table == "buff":
            return self.buffs.pop(kind, None) is not None
        if table == "dot":
            return bool(self.dots) and self.dots.pop(0) is not None
        had = self.barrier > 0
        self.barrier, self.barrier_turns = 0.0, 0
        return had

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
        if self.barrier_turns:
            self.barrier_turns -= 1
            if self.barrier_turns <= 0:
                self.barrier = 0.0


_TIMERS: Final = (
    "stun",
    "immortal",
    "immunity",
    "invincible",
    "provoke",
    "silence",
    "counter",
    "revive_buff",
    "redirect_provoke",
)


def _allies(units: Sequence[Combatant], hp: Sequence[float], i: int) -> list[int]:
    return [j for j in range(len(units)) if units[j].side == units[i].side and hp[j] > 0]


def _lethal(units: Sequence[Combatant], hp: list[float], cr: list[float], state: list[_State], j: int) -> None:
    """Lethal damage: immortality keeps 1 HP; a revive passive works once (MECH via SPEC D63, `assumed`)."""
    kit = units[j].kit
    if state[j].timers.get("immortal", 0) > 0:
        hp[j] = 1.0
    elif state[j].timers.pop("revive_buff", 0) > 0:  # a revive status given to an ally (Maid Chloe)
        hp[j] = units[j].stats[Stat.HP] * state[j].revive_hp
    elif kit.revive is not None and not state[j].revived:
        state[j].revived = True
        hp[j] = units[j].stats[Stat.HP] * kit.revive
        for effect in kit.revive_effects:
            if effect.kind == "cr":
                cr[j] = min(1.0, cr[j] + effect.value)
            else:
                state[j].apply(effect)


def _receivers(effect: Effect, actor: int, units: Sequence[Combatant], hp: Sequence[float]) -> list[int]:
    allies = _allies(units, hp, actor)
    if effect.receivers == "all" or (not effect.receivers and effect.all_allies):
        return allies
    if effect.receivers.startswith("lowest:"):
        count = int(effect.receivers.split(":")[1])
        return sorted(allies, key=lambda j: hp[j] / units[j].stats[Stat.HP])[:count]
    return [actor]


def _use(
    effect: Effect,
    actor: int,
    targets: Sequence[int],
    units: Sequence[Combatant],
    hp: list[float],
    cr: list[float],
    state: list[_State],
    rng: random.Random,
) -> None:
    if hp[actor] <= 0:
        return
    caster = units[actor].stats
    if effect.kind == "sacrifice":  # health paid by the caster, never to death
        hp[actor] = max(1.0, hp[actor] * (1 - effect.value))
        return
    amount = (
        effect.scaling[0] * caster[Stat.HP]
        + effect.scaling[1] * caster[Stat.ATK]
        + effect.scaling[2] * caster[Stat.DEF]
    )
    if effect.kind == "resurrect":
        dead = [j for j in range(len(units)) if units[j].side == units[actor].side and hp[j] <= 0]
        for j in dead if effect.receivers == "all" else dead[:1]:
            revived = state[j].revived
            hp[j], cr[j], state[j] = units[j].stats[Stat.HP] * effect.value, 0.0, _State(revived=revived)
        return
    if not effect.debuff:
        for r in _receivers(effect, actor, units, hp):
            if effect.kind == "extra_turn":
                cr[r] = 1.0
            elif effect.kind == "cr":
                cr[r] = min(1.0, cr[r] + effect.value)
            elif effect.kind == "heal":
                hp[r] = min(units[r].stats[Stat.HP], hp[r] + amount)
            elif effect.kind == "barrier":
                state[r].barrier, state[r].barrier_turns = max(state[r].barrier, amount), effect.turns
            elif effect.kind == "cleanse":
                state[r].remove(debuffs=True, count=effect.count)
            else:
                state[r].apply(effect)
        return
    effectiveness = caster.get(Stat.EFFECTIVENESS, 0.0)
    for j in targets:
        if hp[j] <= 0 or state[j].timers.get("immunity", 0) > 0 or effect.kind in units[j].kit.immune:
            continue
        resistance = units[j].stats.get(Stat.EFFECT_RESISTANCE, 0.0)
        land = effect.chance * min(max(1 + effectiveness - resistance, 0.0), LAND_CAP)  # MECH-EFF-01
        if rng.random() >= land:
            continue
        if effect.kind == "cr":
            cr[j] = max(0.0, cr[j] + effect.value)
        elif effect.kind == "dispel":
            state[j].remove(debuffs=False, count=effect.count)
        elif effect.kind in DOT_ATK or effect.kind == "poison":
            per_turn = (
                POISON_MAX_HP * units[j].stats[Stat.HP]
                if effect.kind == "poison"
                else DOT_ATK[effect.kind] * caster[Stat.ATK]
            )
            state[j].dots.append((per_turn, effect.turns))
        elif effect.kind == "provoke":
            state[j].apply(effect)
            state[j].provoker = actor
        else:
            state[j].apply(effect)


def _after_hit(
    j: int,
    attacker: int,
    lost: float,
    units: Sequence[Combatant],
    hp: list[float],
    cr: list[float],
    state: list[_State],
    hits: Mapping[tuple[int, int, int], float],
    rng: random.Random,
) -> None:
    """What a hero does after a hit it survives (SPEC D76): reflect part of the Health lost, trigger its low-Health
    effects, counterattack."""
    kit = units[j].kit
    if kit.reflect and lost > 0:
        state[attacker].hurt(hp, attacker, min(kit.reflect * lost, units[j].stats[Stat.HP]))
        if hp[attacker] <= 0:
            _lethal(units, hp, cr, state, attacker)
    if kit.low_hp and hp[j] <= kit.low_hp * units[j].stats[Stat.HP] and state[j].low_wait == 0:
        state[j].low_wait = kit.low_hp_cooldown
        for effect in kit.low_hp_effects:
            _use(effect, j, [], units, hp, cr, state, rng)
    if hp[attacker] > 0 and _counters(units, hp, j, state, rng):
        _counterattack(j, attacker, units, hp, cr, state, hits, rng)


def _counters(
    units: Sequence[Combatant], hp: Sequence[float], j: int, state: Sequence[_State], rng: random.Random
) -> bool:
    """A counterattack (S1 back at the attacker): always with the counterattack buff, else the passive chance; never
    while stunned, nor while another hero's "nobody else counterattacks" holds (`assumed`)."""
    unit, me = units[j], state[j]
    if me.timers.get("stun", 0) > 0 or 1 not in unit.skills:
        return False
    if any(units[i].kit.no_counter and hp[i] > 0 and i != j for i in range(len(units))):
        return False
    chance = 1.0 if me.timers.get("counter", 0) > 0 else unit.kit.counter
    return chance > 0 and rng.random() < chance


def _counterattack(
    j: int,
    attacker: int,
    units: Sequence[Combatant],
    hp: list[float],
    cr: list[float],
    state: list[_State],
    hits: Mapping[tuple[int, int, int], float],
    rng: random.Random,
) -> None:
    unit, skill = units[j], units[j].skills[1]
    crit = skill.can_crit and rng.random() < min(unit.stats[Stat.CRIT_CHANCE], 1.0)
    factor = min(unit.stats[Stat.CRIT_DAMAGE], CD_CAP) if crit else 1.0
    state[attacker].hurt(hp, attacker, hits[(j, attacker, 1)] * state[j].factor("atk") * factor)
    if hp[attacker] <= 0:
        _lethal(units, hp, cr, state, attacker)


def confidence_interval(p: float, runs: int) -> float:
    """95% half-width of a simulated share (the simulation's own noise, not the model's error)."""
    return 1.96 * math.sqrt(max(p * (1 - p), 1e-12) / runs)
