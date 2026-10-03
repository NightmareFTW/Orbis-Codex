"""Resolve facts from several sources into one value + status per field.

Rules (docs/DATA_SOURCES.md "Catalog pipeline", SPEC D4):
1. An official fact (status VERIFIED) wins; the field is `verified`. Disagreeing community values are kept
   as a conflict.
2. Otherwise the value backed by the most distinct sources wins and the field is `community`
   (`len(sources) >= 2` means corroborated).
3. A tie (same best status, same number of sources) is broken by source priority and then by value, but the
   field becomes `assumed` and the conflict is reported - we cannot tell which value is right. This includes
   an official source contradicting itself.
4. ASSUMED facts never beat VERIFIED or COMMUNITY ones.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Final

from pydantic import BaseModel, ConfigDict, JsonValue

from e7ac.catalog.facts import EntityType, Fact, canonical_value, value_key
from e7ac.domain.codes import DataStatus, SourceId

SOURCE_PRIORITY: Final[tuple[SourceId, ...]] = (SourceId.USER, SourceId.STOVE, SourceId.FRIBBELS, SourceId.E7CALC)
_STATUS_RANK: Final = {DataStatus.VERIFIED: 0, DataStatus.COMMUNITY: 1, DataStatus.ASSUMED: 2, DataStatus.UNKNOWN: 3}


class Alternative(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    value: JsonValue
    sources: tuple[SourceId, ...]


class ResolvedField(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    value: JsonValue
    status: DataStatus
    sources: tuple[SourceId, ...]
    alternatives: tuple[Alternative, ...] = ()

    @property
    def conflict(self) -> bool:
        return bool(self.alternatives)

    @property
    def corroborated(self) -> bool:
        return len(self.sources) >= 2


class ResolvedEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_type: EntityType
    entity_id: str
    fields: dict[str, ResolvedField]

    def value(self, field: str) -> JsonValue:
        resolved = self.fields.get(field)
        return None if resolved is None else resolved.value

    def status(self, field: str) -> DataStatus:
        resolved = self.fields.get(field)
        return DataStatus.UNKNOWN if resolved is None else resolved.status

    @property
    def name(self) -> str:
        value = self.value("name")
        return value if isinstance(value, str) else self.entity_id


class Conflict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_type: EntityType
    entity_id: str
    field: str
    chosen: Alternative
    status: DataStatus
    alternatives: tuple[Alternative, ...]


def resolve_field(facts: Sequence[Fact]) -> ResolvedField:
    if not facts:
        raise ValueError("resolve_field needs at least one fact")
    groups: dict[str, list[Fact]] = defaultdict(list)
    for fact in facts:
        groups[value_key(fact.value)].append(fact)

    def best_status(group: list[Fact]) -> DataStatus:
        return min((f.status for f in group), key=_STATUS_RANK.__getitem__)

    def distinct_sources(group: list[Fact]) -> tuple[SourceId, ...]:
        return tuple(sorted({f.source for f in group}, key=SOURCE_PRIORITY.index))

    def priority(group: list[Fact]) -> int:
        return min(SOURCE_PRIORITY.index(f.source) for f in group)

    def rank(group: list[Fact]) -> tuple[int, int]:
        return (_STATUS_RANK[best_status(group)], -len(distinct_sources(group)))

    # value_key as the last criterion makes the choice independent of fact order
    ranked = sorted(groups.values(), key=lambda g: (*rank(g), priority(g), value_key(g[0].value)))
    chosen = ranked[0]
    chosen_status = best_status(chosen)
    alternatives = tuple(
        Alternative(value=canonical_value(g[0].value), sources=distinct_sources(g)) for g in ranked[1:]
    )

    # A tie (same best status and same number of backing sources) means we cannot tell which value is right,
    # even between official values that contradict each other: the field is only `assumed`.
    tie = len(ranked) > 1 and rank(ranked[1]) == rank(chosen)
    status = DataStatus.ASSUMED if tie and chosen_status is not DataStatus.UNKNOWN else chosen_status
    return ResolvedField(
        value=canonical_value(chosen[0].value),
        status=status,
        sources=distinct_sources(chosen),
        alternatives=alternatives,
    )


def resolve(facts: Iterable[Fact]) -> list[ResolvedEntity]:
    """Group facts by entity and field and resolve each field. Output is sorted for stable hashing."""
    by_field: dict[tuple[EntityType, str], dict[str, list[Fact]]] = defaultdict(lambda: defaultdict(list))
    for fact in facts:
        by_field[(fact.entity_type, fact.entity_id)][fact.field].append(fact)
    entities = []
    for (entity_type, entity_id), fields in sorted(by_field.items()):
        resolved = {name: resolve_field(group) for name, group in sorted(fields.items())}
        entities.append(ResolvedEntity(entity_type=entity_type, entity_id=entity_id, fields=resolved))
    return entities


def conflicts(entities: Iterable[ResolvedEntity]) -> list[Conflict]:
    out = []
    for entity in entities:
        for name, resolved in entity.fields.items():
            if resolved.conflict:
                out.append(
                    Conflict(
                        entity_type=entity.entity_type,
                        entity_id=entity.entity_id,
                        field=name,
                        chosen=Alternative(value=resolved.value, sources=resolved.sources),
                        status=resolved.status,
                        alternatives=resolved.alternatives,
                    )
                )
    return out
