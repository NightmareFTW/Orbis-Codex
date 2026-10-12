"""Stove usage histograms of a hero's final stats, and their percentiles (M10, docs/DATA_SOURCES.md §1).

Stove gives 10 counts per stat and no bin edges. Its page labels only the ends with constants (below); the totals are
equal across the 8 stats of a hero, so values outside the range are clamped into the end bins, not dropped. Model:
10 equal-width bins between the label bounds, bin 0 open below and bin 9 open above. Status `assumed` (NV-01): the
edges are a table here, to correct without touching the maths. A percentile is interpolated linearly inside its bin;
one that falls in an open end bin is flagged (its true value may lie beyond the label bound).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from e7ac.domain.codes import Stat

BINS: Final = 10
LABEL_BOUNDS: Final[Mapping[Stat, tuple[float, float]]] = {
    Stat.ATK: (1200, 5200),
    Stat.DEF: (800, 2400),
    Stat.HP: (9000, 25000),
    Stat.SPEED: (110, 270),
    Stat.CRIT_CHANCE: (0.15, 1.00),
    Stat.CRIT_DAMAGE: (1.50, 3.50),
    Stat.EFFECTIVENESS: (0.0, 1.80),
    Stat.EFFECT_RESISTANCE: (0.0, 2.25),
}
"""Ends of the Stove histogram labels (front-end constants, 2026-10-03), rates as fractions. Status: assumed."""


@dataclass(frozen=True, slots=True)
class Percentile:
    value: float
    open_end: bool
    """In an open end bin: the true value may be beyond the label bound (lower confidence)."""


@dataclass(frozen=True, slots=True)
class StatHistogram:
    stat: Stat
    counts: tuple[int, ...]

    def __post_init__(self) -> None:
        if len(self.counts) != BINS or any(c < 0 for c in self.counts):
            raise ValueError(f"{self.stat.value}: expected {BINS} non-negative counts, got {self.counts}")

    @property
    def total(self) -> int:
        return sum(self.counts)

    def percentile(self, p: float) -> Percentile | None:
        """The p-quantile (0 < p < 1), or None for an empty histogram."""
        if not 0 < p < 1:
            raise ValueError(f"percentile {p} is not between 0 and 1")
        total = self.total
        if total == 0:
            return None
        low, high = LABEL_BOUNDS[self.stat]
        width = (high - low) / BINS
        target = p * total
        below = 0
        for index, count in enumerate(self.counts):
            if count and below + count >= target:
                value = low + (index + (target - below) / count) * width
                return Percentile(round(value, 6), index in (0, BINS - 1))
            below += count
        raise AssertionError("unreachable: the counts add up to the total")


def parse_counts(stat: Stat, text: str) -> StatHistogram:
    """'c0,c1,...,c9' as Stove sends it."""
    try:
        counts = tuple(int(part) for part in text.split(","))
    except ValueError as exc:
        raise ValueError(f"{stat.value}: counts are not integers: {text!r}") from exc
    return StatHistogram(stat, counts)


def sample_size(histograms: Sequence[StatHistogram]) -> int | None:
    """The common total of a hero's histograms (Stove's sample size), or None when they disagree."""
    totals = {h.total for h in histograms}
    return totals.pop() if len(totals) == 1 else None
