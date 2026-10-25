"""How far to believe a simulated chance (SPEC D94, NV-59).

The first real comparison of the Arena watch with a fight was a "100%" that was lost (NV-51): a simulation that follows
the rules it knows says "sure" whenever it wins every time, however much it does not know (the opponent's real build,
artifacts, combat set effects, how the game AI chooses). Two corrections, both `assumed` until the results log
(`e7 arena results`) can replace them with a calibration measured on real fights:

* the opponent's build is not one point: a fight is simulated against several builds of each hero (Stove's median,
  strong and top, `BUILDS`) and the chance is their weighted mean, the lowest and highest being its range;
* the chance is then read as if the simulation were `EVIDENCE` real fights (Laplace's rule): a simulation that wins
  every time is believed at about 95%, never 100%, and an even fight stays even.

It is a monotonic correction, so the order of the three opponents of the Opponents list never changes."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Final

from e7ac.opponents.assumed import Profile
from e7ac.predict.battle import BattleOdds

BUILDS: Final = ((Profile.TYPICAL, 0.5), (Profile.STRONG, 0.3), (Profile.TOP, 0.2))
"""The opponent builds simulated and how likely each is taken to be (`assumed`: Arena defence teams are probably better
built than Stove's median, which also holds PvE builds, SPEC D58; to be set by results)."""
EVIDENCE: Final = 20.0
"""How many real fights the simulation is worth (`assumed`): 20 puts a sure win at 21/22 = 95.5%."""


def believe(win: float, evidence: float = EVIDENCE) -> float:
    """A simulated share of wins read as the evidence of `evidence` fights: (n * p + 1) / (n + 2)."""
    return (evidence * win + 1.0) / (evidence + 2.0)


def mix(parts: Sequence[tuple[float, BattleOdds]]) -> BattleOdds:
    """The odds against one opponent build each, weighted: the chance is the weighted mean, `low` and `high` the worst
    and best build. Heroes, warnings and unread skills are those of the first (they do not depend on the build)."""
    if not parts:
        raise ValueError("nothing to mix")
    total = sum(weight for weight, _ in parts)

    def mean(pick: str) -> float:
        return sum(weight * float(getattr(odds, pick)) for weight, odds in parts) / total

    first = parts[0][1]
    wins = [odds.win for _, odds in parts]
    return replace(
        first,
        win=mean("win"),
        loss=mean("loss"),
        draw=mean("draw"),
        mean_actions=mean("mean_actions"),
        runs=sum(odds.runs for _, odds in parts),
        low=min(wins),
        high=max(wins),
    )


def believed(odds: BattleOdds) -> BattleOdds:
    """The odds as they are shown: the chance (and its range) corrected by `believe`; the simulation's own share is kept
    in `raw` (the results log keeps that one, to calibrate it) and `loss` / `draw` stay the simulation's."""
    return replace(
        odds,
        win=believe(odds.win),
        low=None if odds.low is None else believe(odds.low),
        high=None if odds.high is None else believe(odds.high),
        raw=odds.win,
    )
