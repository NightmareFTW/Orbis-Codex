"""Win probability by simulating many battles (M11 part, SPEC D61): turn order and damage, nothing else yet.

Each battle: start CR 0-5% at random (MECH-CR-02), CR fills with speed and the first to 100% acts (MECH-CR-01); an
opponent's speed is drawn from Stove each battle. A unit uses S3 whenever its cooldown allows (epic7db; available
again N of the unit's own turns later, MECH-CD-01 `assumed`), else S1; without a known cooldown, S3 on its first
action only (`assumed`); it attacks the living enemy it needs the fewest hits to defeat (focus fire,
`assumed`: the game's targeting is not known); a skill with more than one target hits every living enemy. Each hit
is critical with the attacker's crit chance (MECH-DMG-05). No buffs, debuffs, passives, counters, heals, barriers,
artifacts or combat set effects yet, so this is a first estimate with low confidence.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from e7ac.domain.codes import Stat
from e7ac.predict.damage import CD_CAP, SkillPower, base_hit
from e7ac.predict.turn_order import START_CR_MAX

RUNS: Final = 10_000
SEED: Final = 7
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
    speeds = [u.speed(rng) if callable(u.speed) else u.speed for u in units]
    cr = [rng.uniform(0, START_CR_MAX) for _ in units]
    hp = [u.stats[Stat.HP] for u in units]
    acted = [False] * len(units)
    waiting = [0] * len(units)  # own turns until S3 is ready again
    for actions in range(1, MAX_ACTIONS + 1):
        alive = [i for i in range(len(units)) if hp[i] > 0]
        actor = min(alive, key=lambda i: (1 - cr[i]) / speeds[i])
        elapsed = (1 - cr[actor]) / speeds[actor]
        for i in alive:
            cr[i] += elapsed * speeds[i]
        cr[actor] = 0.0
        unit = units[actor]
        waiting[actor] = max(0, waiting[actor] - 1)
        cooldown = unit.cooldowns.get(3)
        ready = (waiting[actor] == 0) if cooldown is not None else not acted[actor]
        slot = 3 if ready and 3 in unit.skills else 1
        if slot == 3 and cooldown:
            waiting[actor] = cooldown  # counts down at the start of each own turn: ready N turns later (MECH-CD-01)
        acted[actor] = True
        enemies = [j for j in alive if units[j].side != unit.side]
        skill = unit.skills.get(slot)
        if skill is not None and enemies:
            targets = enemies if skill.targets > 1 else [min(enemies, key=lambda j: hp[j] / hits[(actor, j, slot)])]
            crit = skill.can_crit and rng.random() < min(unit.stats[Stat.CRIT_CHANCE], 1.0)
            factor = min(unit.stats[Stat.CRIT_DAMAGE], CD_CAP) if crit else 1.0
            for j in targets:
                hp[j] -= hits[(actor, j, slot)] * factor
        standing = {units[i].side for i in range(len(units)) if hp[i] > 0}
        if len(standing) == 1:
            return standing.pop(), actions
    return None, MAX_ACTIONS


def confidence_interval(p: float, runs: int) -> float:
    """95% half-width of a simulated share (the simulation's own noise, not the model's error)."""
    return 1.96 * math.sqrt(max(p * (1 - p), 1e-12) / runs)
