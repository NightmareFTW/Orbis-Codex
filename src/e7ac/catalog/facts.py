"""Catalog facts: one value for one field of one entity, as asserted by one source (docs/DATA_SOURCES.md pipeline)."""

from __future__ import annotations

import json
import math
from datetime import datetime
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, JsonValue

from e7ac.domain.codes import DataStatus, SourceId


class EntityType(StrEnum):
    HERO = "hero"
    SKILL = "skill"
    ARTIFACT = "artifact"
    SET = "set"


class Fact(BaseModel):
    """`status` is the status the *source* can claim: VERIFIED for official data, COMMUNITY for community data,
    ASSUMED for our own inferences. The resolver decides the final status of the field."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_type: EntityType
    entity_id: str
    field: str
    value: JsonValue
    source: SourceId
    status: DataStatus
    note: str = ""


class SourceRun(BaseModel):
    """What one source contributed to a sync: provenance for every fact it produced."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: SourceId
    version: str
    """Content hash of the fetched documents (and upstream version when known)."""
    retrieved_at: datetime
    from_cache: bool
    stale: bool
    facts: int
    warnings: tuple[str, ...] = ()


_FLOAT_DIGITS: Final = 9


def canonical_value(value: JsonValue) -> JsonValue:
    """Normalise values so that equal data from different sources compares equal (1138 == 1138.0)."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isfinite(value) and value.is_integer():
            return int(value)
        return round(value, _FLOAT_DIGITS)
    if isinstance(value, list):
        return [canonical_value(v) for v in value]
    return {str(k): canonical_value(v) for k, v in sorted(value.items())}


def value_key(value: JsonValue) -> str:
    return json.dumps(canonical_value(value), sort_keys=True, separators=(",", ":"))


def skill_id(hero_code: str, slot: int) -> str:
    return f"{hero_code}:s{slot}"
