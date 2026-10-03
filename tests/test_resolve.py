from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import JsonValue

from e7ac.catalog.facts import EntityType, Fact, canonical_value
from e7ac.catalog.names import NameIndex, normalise_name
from e7ac.catalog.resolve import conflicts, resolve, resolve_field
from e7ac.domain.codes import DataStatus, SourceId


def fact(value: JsonValue, source: SourceId, status: DataStatus = DataStatus.COMMUNITY, field: str = "f") -> Fact:
    return Fact(entity_type=EntityType.HERO, entity_id="c9001", field=field, value=value, source=source, status=status)


def test_official_value_wins_and_keeps_the_conflict() -> None:
    resolved = resolve_field(
        [
            fact("Queen's Whistle", SourceId.FRIBBELS),
            fact("Queen’s Whistle", SourceId.STOVE, DataStatus.VERIFIED),  # noqa: RUF001 - real-world curly quote
            fact("Queen's Whistle", SourceId.E7CALC),
        ]
    )
    assert resolved.status is DataStatus.VERIFIED
    assert resolved.sources == (SourceId.STOVE,)
    assert resolved.conflict
    assert resolved.alternatives[0].sources == (SourceId.FRIBBELS, SourceId.E7CALC)


def test_two_agreeing_community_sources_are_corroborated() -> None:
    resolved = resolve_field([fact(1138, SourceId.FRIBBELS), fact(1138.0, SourceId.E7CALC)])
    assert resolved.status is DataStatus.COMMUNITY
    assert resolved.corroborated
    assert not resolved.conflict
    assert resolved.value == 1138


def test_majority_beats_a_single_disagreeing_source() -> None:
    resolved = resolve_field([fact(1.0, SourceId.FRIBBELS), fact(1.0, SourceId.USER), fact(0.8, SourceId.E7CALC)])
    assert (resolved.value, resolved.status, resolved.conflict) == (1, DataStatus.COMMUNITY, True)


def test_tie_between_community_sources_becomes_assumed() -> None:
    resolved = resolve_field([fact(0.8, SourceId.E7CALC), fact(1.0, SourceId.FRIBBELS)])
    assert resolved.status is DataStatus.ASSUMED
    assert resolved.value == 1  # Fribbels has priority over e7calc, but the status says we cannot tell
    assert resolved.conflict


def test_assumed_fact_never_beats_community() -> None:
    resolved = resolve_field([fact(1445, SourceId.E7CALC, DataStatus.ASSUMED), fact(1112, SourceId.FRIBBELS)])
    assert (resolved.value, resolved.status) == (1112, DataStatus.COMMUNITY)
    assert resolved.alternatives[0].value == 1445


def test_contradictory_official_values_are_only_assumed() -> None:
    resolved = resolve_field(
        [fact(1, SourceId.STOVE, DataStatus.VERIFIED), fact(0, SourceId.STOVE, DataStatus.VERIFIED)]
    )
    assert (resolved.value, resolved.status, resolved.conflict) == (0, DataStatus.ASSUMED, True)


def test_only_assumed_stays_assumed() -> None:
    assert resolve_field([fact(0.06, SourceId.FRIBBELS, DataStatus.ASSUMED)]).status is DataStatus.ASSUMED


def test_resolve_needs_facts() -> None:
    with pytest.raises(ValueError):
        resolve_field([])


def test_canonical_value_normalises_numbers_recursively() -> None:
    assert canonical_value({"b": [1.0, 0.1 + 0.2], "a": 2.5}) == {"a": 2.5, "b": [1, 0.3]}


def test_resolve_groups_entities_and_lists_conflicts() -> None:
    entities = resolve(
        [
            fact("Test Hero", SourceId.STOVE, DataStatus.VERIFIED, field="name"),
            fact(1000, SourceId.FRIBBELS, field="base.att"),
            fact(1300, SourceId.E7CALC, DataStatus.ASSUMED, field="base.att"),
        ]
    )
    assert len(entities) == 1
    assert entities[0].name == "Test Hero"
    found = conflicts(entities)
    assert [(c.field, c.chosen.value) for c in found] == [("base.att", 1000)]


values = st.one_of(st.integers(-5, 5), st.sampled_from([0.5, 1.0, 1.2]), st.sampled_from(["a", "b"]))
sources = st.sampled_from(list(SourceId))
statuses = st.sampled_from([DataStatus.VERIFIED, DataStatus.COMMUNITY, DataStatus.ASSUMED])


@given(st.lists(st.tuples(values, sources, statuses), min_size=1, max_size=6), st.randoms())
def test_resolution_does_not_depend_on_fact_order(
    items: list[tuple[JsonValue, SourceId, DataStatus]], rng: object
) -> None:
    facts = [fact(v, s, st_) for v, s, st_ in items]
    shuffled = list(facts)
    rng.shuffle(shuffled)  # type: ignore[attr-defined]
    assert resolve_field(facts) == resolve_field(shuffled)


def test_name_index_refuses_ambiguous_names() -> None:
    index = NameIndex()
    index.add("Mercedes", "c1005")
    index.add("Mercedes", "c0001")
    index.add("Archdemon’s Shadow", "c9100")  # noqa: RUF001 - curly quote as in the API
    assert index.lookup("mercedes") is None
    assert index.is_ambiguous("MERCEDES")
    assert index.lookup("Archdemon's Shadow") == "c9100"  # straight vs curly quote
    assert index.lookup("archdemon_shadow") is None  # "...demons shadow" != "...demon shadow": needs an alias
    assert index.ambiguous() == {"mercedes": ["c0001", "c1005"]}
    assert normalise_name("Baal & Sezan") == "baalsezan"
