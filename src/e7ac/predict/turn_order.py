"""Who acts first at the start of a battle (M11 part, MECH-CR-01/02).

Each unit's Combat Readiness fills at a rate proportional to its speed and the first to reach 100% acts (MECH-CR-01,
`community`); every unit starts with a random 0-5% (MECH-CR-02, `community`, a parameter: NV-04). A unit's first turn
comes at time (1 - start CR) / speed. Simulated many times with a seeded generator (the result is reproducible); an
opponent whose speed is unknown draws it from Stove's histogram each time, so its uncertainty is in the result.
Not modelled yet: speed buffs/debuffs, CR pushes at battle start (skills, artifacts, sets), ties (MECH-CR-03 unknown).
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

START_CR_MAX: Final = 0.05
"""Starting CR is uniform in [0, START_CR_MAX] (MECH-CR-02, community; NV-04 for PvP)."""
RUNS: Final = 20_000
SEED: Final = 7


@dataclass(frozen=True, slots=True)
class Unit:
    label: str
    side: str
    """"you" or "them"."""
    speed: float | Callable[[random.Random], float]
    """A known speed, or a sampler of the likely speeds (opponents)."""


@dataclass(frozen=True, slots=True)
class UnitOdds:
    unit: Unit
    first: float
    """Share of the starts in which this unit takes the first turn of the battle."""
    mean_position: float
    """Average rank of its first turn (1 = first)."""


@dataclass(frozen=True, slots=True)
class TurnOrder:
    units: list[UnitOdds]
    """Fastest first (by mean position)."""
    your_side_first: float
    runs: int


def first_turns(units: Sequence[Unit], runs: int = RUNS, seed: int = SEED) -> TurnOrder:
    if not units:
        raise ValueError("no units")
    rng = random.Random(seed)
    first = [0] * len(units)
    positions = [0] * len(units)
    yours = 0
    for _ in range(runs):
        times = []
        for unit in units:
            speed = unit.speed(rng) if callable(unit.speed) else unit.speed
            if speed <= 0:
                raise ValueError(f"{unit.label}: speed must be positive")
            times.append((1 - rng.uniform(0, START_CR_MAX)) / speed)
        order = sorted(range(len(units)), key=times.__getitem__)
        first[order[0]] += 1
        yours += units[order[0]].side == "you"
        for rank, index in enumerate(order, start=1):
            positions[index] += rank
    odds = [UnitOdds(u, first[i] / runs, positions[i] / runs) for i, u in enumerate(units)]
    return TurnOrder(sorted(odds, key=lambda o: o.mean_position), yours / runs, runs)
