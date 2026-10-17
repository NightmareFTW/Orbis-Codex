"""Every opponent team the Arena watch read (SPEC D81): what the Dev page turns into "the heroes met most".

One line per defence team in `<home>/arena/sightings.jsonl` (local only, never in the repository): when it was read
(all the teams of one refresh of the Opponents list share the instant), where (the Opponents list or the Attack Guide),
its position on the list and its four heroes left to right (null = a face that was not recognised: counted, never
guessed). Only hero codes are kept: no player name, nothing that identifies the opponent.

The file is append-only; a line that cannot be read (a crash half-way through a write, a hand edit) is counted and
skipped, never silently dropped: `SightingStore.read` returns how many.
"""

from __future__ import annotations

import itertools
import json
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from e7ac.domain.codes import is_hero_code
from e7ac.fileio import write_text_atomic

FILE_NAME: Final = "sightings.jsonl"
"""Under `<home>/arena/`."""
SOURCES: Final = ("list", "guide")
TEAM_SIZE: Final = 4


def sightings_path(home: Path) -> Path:
    return home / "arena" / FILE_NAME


@dataclass(frozen=True, slots=True)
class Sighting:
    when: datetime
    """UTC instant of the refresh (the Opponents list) or of the Attack Guide reading."""
    source: str
    """"list" (the Opponents list) or "guide" (the Attack Guide of one opponent)."""
    row: int
    """Position on the Opponents list, top to bottom from 1 (always 1 for the Attack Guide)."""
    codes: tuple[str | None, ...]
    """The four heroes left to right; None for a face that was not recognised."""
    shapes: tuple[tuple[str, str] | None, ...] = ()
    """(element, class) of each of the four, as their badges show (also for a face that was not recognised); empty when
    the reading had no badges (older lines, the Attack Guide)."""

    def to_json(self) -> str:
        return json.dumps(
            {
                "when": self.when.astimezone(UTC).isoformat(),
                "source": self.source,
                "row": self.row,
                "codes": self.codes,
                **({"shapes": self.shapes} if self.shapes else {}),
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, line: str) -> Sighting:
        """ValueError for a line that is not a sighting."""
        data = json.loads(line)
        codes = data["codes"]
        when = datetime.fromisoformat(data["when"])
        if when.tzinfo is None:
            raise ValueError("no time zone")
        if data["source"] not in SOURCES or not isinstance(data["row"], int) or data["row"] < 1:
            raise ValueError("bad source or row")
        if len(codes) != TEAM_SIZE or not all(c is None or (isinstance(c, str) and is_hero_code(c)) for c in codes):
            raise ValueError("a team is four hero codes or null")
        shapes = data.get("shapes") or []
        if shapes and (
            len(shapes) != TEAM_SIZE
            or not all(s is None or (len(s) == 2 and all(isinstance(v, str) for v in s)) for s in shapes)
        ):
            raise ValueError("shapes are four (element, class) pairs or null")
        return cls(
            when, data["source"], data["row"], tuple(codes), tuple(None if s is None else (s[0], s[1]) for s in shapes)
        )


class SightingStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def add(self, sightings: Iterable[Sighting]) -> None:
        lines = [s.to_json() + "\n" for s in sightings]
        if not lines:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.writelines(lines)

    def name_face(self, when: str, row: int, face: int, code: str) -> bool:
        """The user named a face the watch could not (`codes[face]` was null): rewrite that line, atomically. False when
        the line is not there, or that face already has a name (a name is never overwritten)."""
        if not self.path.is_file() or not is_hero_code(code) or not 0 <= face < TEAM_SIZE:
            return False
        lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        changed = False
        for i, line in enumerate(lines):
            try:
                sighting = Sighting.from_json(line)
            except (ValueError, KeyError, TypeError):
                continue
            if (
                sighting.when.astimezone(UTC).isoformat() == when
                and sighting.row == row
                and sighting.codes[face] is None
            ):
                codes = list(sighting.codes)
                codes[face] = code
                lines[i] = replace(sighting, codes=tuple(codes)).to_json()
                changed = True
        if changed:
            write_text_atomic(self.path, "\n".join(lines) + "\n")
        return changed

    def read(self) -> tuple[list[Sighting], int]:
        """Every readable sighting, and how many lines could not be read."""
        if not self.path.is_file():
            return [], 0
        found: list[Sighting] = []
        skipped = 0
        for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                found.append(Sighting.from_json(line))
            except (ValueError, KeyError, TypeError):
                skipped += 1
        return found, skipped


@dataclass(frozen=True, slots=True)
class HeroSeen:
    code: str
    teams: int
    """Defence teams the hero was in."""
    share: float
    """`teams` over all the teams read (0-1)."""


@dataclass(frozen=True, slots=True)
class Popularity:
    heroes: tuple[HeroSeen, ...]
    """Most seen first (ties: by code, so the order never changes between reads)."""
    teams: int
    refreshes: int
    """Distinct instants: refreshes of the Opponents list plus Attack Guide readings."""
    faces: int
    unknown_faces: int
    """Faces that were not recognised: the heroes above are an undercount by about this share."""
    pairs: tuple[tuple[tuple[str, str], int], ...]
    """The pairs of heroes seen together most often, with their number of teams."""
    elements: tuple[tuple[str, int], ...] = ()
    """Heroes shown per element and per class (faces with a readable badge, recognised or not), most first."""
    classes: tuple[tuple[str, int], ...] = ()


def popularity(sightings: Sequence[Sighting], since: datetime | None = None, pairs: int = 10) -> Popularity:
    """What the sightings say about the Arena right now. A team read twice (the same opponent seen on the list and in
    the Attack Guide) counts twice: the Arena shows it twice, and the watch cannot tell it is the same player."""
    kept = [s for s in sightings if since is None or s.when >= since]
    seen: Counter[str] = Counter()
    together: Counter[tuple[str, str]] = Counter()
    elements: Counter[str] = Counter()
    classes: Counter[str] = Counter()
    unknown = 0
    for sighting in kept:
        for shape in sighting.shapes:
            if shape is not None:
                elements[shape[0]] += 1
                classes[shape[1]] += 1
        team = sorted({c for c in sighting.codes if c is not None})
        seen.update(team)
        together.update(itertools.combinations(team, 2))
        unknown += sum(c is None for c in sighting.codes)
    total = len(kept)
    heroes = tuple(
        HeroSeen(code, count, count / total) for code, count in sorted(seen.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    top_pairs = tuple(sorted(together.items(), key=lambda kv: (-kv[1], kv[0]))[:pairs])
    return Popularity(
        heroes,
        total,
        len({s.when for s in kept}),
        total * TEAM_SIZE,
        unknown,
        top_pairs,
        _ranked(elements),
        _ranked(classes),
    )


def _ranked(counts: Counter[str]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
