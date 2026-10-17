"""The heroes met in your Arena, most first (SPEC D73, D81, D91): what the kit and skill worklists are ordered by."""

from __future__ import annotations

from pathlib import Path

from e7ac.arena.references import ReferenceStore
from e7ac.arena.sightings import SightingStore, popularity, sightings_path


def met_heroes(home: Path) -> list[tuple[str, int]]:
    """Hero code -> defence teams it was seen in (the sightings of the watch); before any, the pictures learned of it
    (VS models and Attack Guide faces) plus one when in your last team."""
    sightings, _ = SightingStore(sightings_path(home)).read()
    if sightings:
        return [(h.code, h.teams) for h in popularity(sightings).heroes]
    root = home / "references"
    counts: dict[str, int] = {}
    for kind in ("arena", "faces"):
        store = ReferenceStore(root / kind)
        for code in store.codes():
            counts[code] = counts.get(code, 0) + len(list((root / kind / code).glob("*.png")))
    for code in ReferenceStore(root / "arena").last_team():
        counts[code] = counts.get(code, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
