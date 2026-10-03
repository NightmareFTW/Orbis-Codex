"""Coverage report: which catalog entities miss required fields, and how much of the data is only `assumed`."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from e7ac.catalog.facts import EntityType
from e7ac.catalog.resolve import ResolvedEntity
from e7ac.domain.codes import DataStatus, Stat

REQUIRED_FIELDS: Final[dict[EntityType, tuple[str, ...]]] = {
    EntityType.HERO: (
        "name",
        "element",
        "role",
        "rarity",
        "horoscope",
        f"base.{Stat.ATK.value}",
        f"base.{Stat.HP.value}",
        f"base.{Stat.DEF.value}",
        f"base.{Stat.SPEED.value}",
        f"base.{Stat.CRIT_CHANCE.value}",
        f"base.{Stat.CRIT_DAMAGE.value}",
    ),
    EntityType.ARTIFACT: ("name", "rarity", "atk_min", "hp_min", "atk_max", "hp_max"),
    EntityType.SET: ("name", "pieces", "effect_text", "kind"),
}


@dataclass(frozen=True, slots=True)
class Gap:
    entity_type: EntityType
    entity_id: str
    name: str
    missing: tuple[str, ...]
    assumed: tuple[str, ...]


def coverage(entities: Iterable[ResolvedEntity]) -> list[Gap]:
    gaps = []
    for entity in entities:
        required = REQUIRED_FIELDS.get(entity.entity_type)
        if required is None:
            continue
        missing = tuple(f for f in required if f not in entity.fields)
        assumed = tuple(f for f in required if f in entity.fields and entity.fields[f].status is DataStatus.ASSUMED)
        if missing or assumed:
            gaps.append(
                Gap(
                    entity_type=entity.entity_type,
                    entity_id=entity.entity_id,
                    name=entity.name,
                    missing=missing,
                    assumed=assumed,
                )
            )
    return gaps


def status_counts(entities: Iterable[ResolvedEntity]) -> dict[DataStatus, int]:
    counts = dict.fromkeys(DataStatus, 0)
    for entity in entities:
        for resolved in entity.fields.values():
            counts[resolved.status] += 1
    return counts
