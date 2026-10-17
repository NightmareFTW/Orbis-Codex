"""The kit worklist (SPEC D73, D81): the heroes met in Arena, most seen first, with what the simulation still does not
cover for each. Shared by `e7 kit todo` and the Dev page of the window."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from e7ac.catalog.facts import EntityType
from e7ac.catalog.resolve import ResolvedEntity
from e7ac.catalog.store import current_snapshot, load_entity
from e7ac.predict.damage import support_amounts
from e7ac.predict.effects import Kit, Scaling, kit_from
from e7ac.predict.overrides import KitOverride, apply_override
from e7ac.sources.epic7db import SkillInfo, fetch_kit
from e7ac.sources.http import CachedHttp

type Fetch = Callable[[CachedHttp, str], dict[int, SkillInfo]]


@dataclass(frozen=True, slots=True)
class WorkRow:
    code: str
    name: str
    seen: int
    refined: bool
    """A hand-written kit exists (`predict/kits/<code>.toml`)."""
    unmodelled: tuple[str, ...]


def kit_amounts(session: Session, code: str) -> dict[int, dict[str, Scaling]]:
    """Heal / barrier amounts of the hero's skills from the current catalog (Fribbels), by slot."""
    snapshot = current_snapshot(session)
    found: dict[int, dict[str, Scaling]] = {}
    if snapshot is None:
        return found
    for slot in (1, 2, 3):
        entity = load_entity(session, snapshot.id, EntityType.SKILL, f"{code}:s{slot}")
        if entity is not None:
            found[slot] = support_amounts(entity)
    return found


def effective_kit(
    http: CachedHttp,
    name: str,
    amounts: dict[int, dict[str, Scaling]],
    hand: KitOverride | None,
    fetch: Fetch = fetch_kit,
) -> tuple[Kit, dict[int, int]]:
    """The kit as the simulation sees it: read automatically (epic7db icons and text), corrected by the hand file."""
    skills = fetch(http, name)
    kit = kit_from(skills, amounts)
    # the cooldown of a fully enhanced skill (the steps that shorten it applied; skills assumed complete, NV-32)
    cooldowns = {s: k.max_cooldown for s, k in skills.items() if k.max_cooldown is not None}
    if not skills:
        kit.unmodelled.append("(no epic7db page)")
        kit.unread = [1, 2, 3]  # nothing is known of its skills
    return apply_override(kit, cooldowns, hand) if hand is not None else (kit, cooldowns)


def worklist(
    counts: Sequence[tuple[str, int]],
    heroes: Mapping[str, ResolvedEntity],
    amounts: Mapping[str, dict[int, dict[str, Scaling]]],
    overrides: Mapping[str, KitOverride],
    http: CachedHttp,
    fetch: Fetch = fetch_kit,
) -> list[WorkRow]:
    """One row per (hero code, times seen), in the order given."""
    rows = []
    for code, seen in counts:
        name = heroes[code].name if code in heroes and heroes[code].name else code
        kit, _ = effective_kit(http, name, amounts.get(code, {}), overrides.get(code), fetch)
        unread = tuple(
            f"S{slot}: effects in its text, none read"
            for slot in kit.unread
            if "(no epic7db page)" not in kit.unmodelled
        )
        rows.append(WorkRow(code, name, seen, code in overrides, (*unread, *kit.unmodelled)))
    return rows
