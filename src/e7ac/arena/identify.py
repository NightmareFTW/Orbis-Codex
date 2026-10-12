"""Which hero stands under each Arena VS badge (M17a, SPEC D64).

Candidates are the catalog heroes with the badge's element and class (your side: only heroes you own, unless none
fits). A slot is
decided automatically only when:
- one candidate is left ("only candidate");
- your side: exactly one hero of your last confirmed team fits ("last team");
- a learned reference matches with at least `MIN_INLIERS` ORB inliers, `MARGIN` times the best other candidate, over
  at least `MIN_SPREAD` of the reference's height ("reference"; thresholds from the D64 study, `assumed` until
  re-measured on more captures).
Anything else is left to the user ("review"); a near miss is never picked. The same hero twice on one side sends
both slots to review.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from e7ac.arena.references import Match
from e7ac.catalog.resolve import ResolvedEntity
from e7ac.vision.arena_badges import Badge

MIN_INLIERS: Final = 40
MARGIN: Final = 3.0
MIN_SPREAD: Final = 0.25


@dataclass(slots=True)
class Slot:
    badge: Badge
    candidates: list[str]
    """Hero codes, best first (reference score, then name)."""
    code: str | None = None
    method: str = "review"
    """only candidate / last team / reference / confirmed / review."""
    scores: dict[str, int] = field(default_factory=dict)
    """ORB inliers per candidate with a stored reference."""


def candidates(badge: Badge, heroes: Mapping[str, ResolvedEntity], owned: set[str] | None) -> list[str]:
    return sorted(
        (
            code
            for code, hero in heroes.items()
            if hero.value("element") == badge.element.value
            and hero.value("role") == badge.hero_class.value
            and (owned is None or code in owned)
        ),
        key=lambda code: (heroes[code].name or code, code),
    )


def identify(
    badges: Sequence[Badge],
    heroes: Mapping[str, ResolvedEntity],
    owned: set[str],
    last_team: Sequence[str],
    known: set[str],
    best: Callable[[int, str], Match],
) -> list[Slot]:
    """`known`: codes with a stored reference; `best(slot index, code)`: match of that slot's crop with the code's
    references."""
    slots = []
    for index, badge in enumerate(badges):
        # your side: heroes you own; the whole catalog when none fits (a roster not imported yet)
        mine = candidates(badge, heroes, owned) if badge.side == "you" else []
        slot = Slot(badge, mine or candidates(badge, heroes, None))
        matches = {code: best(index, code) for code in slot.candidates if code in known}
        slot.scores = {code: m.inliers for code, m in matches.items()}
        slot.candidates.sort(key=lambda code: -slot.scores.get(code, -1))
        remembered = [code for code in slot.candidates if code in last_team]
        if len(slot.candidates) == 1:
            slot.code, slot.method = slot.candidates[0], "only candidate"
        elif badge.side == "you" and len(remembered) == 1:
            slot.code, slot.method = remembered[0], "last team"
        elif matches:
            top = slot.candidates[0]
            runner_up = max((s for code, s in slot.scores.items() if code != top), default=0)
            m = matches[top]
            if m.inliers >= MIN_INLIERS and m.inliers >= MARGIN * runner_up and m.spread >= MIN_SPREAD:
                slot.code, slot.method = top, "reference"
        slots.append(slot)
    for side in ("you", "them"):
        twice = Counter(s.code for s in slots if s.badge.side == side and s.code)
        for slot in slots:
            if slot.badge.side == side and slot.code and twice[slot.code] > 1:
                slot.code, slot.method = None, "review"
    return slots
