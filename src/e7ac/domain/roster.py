"""Owned-hero builds (roster). Values follow the e7ac convention: flat stats in game units, rates as fractions.

A `HeroBuild` is one immutable snapshot of a hero as seen at `captured_at` (history = list of snapshots).
The displayed final stats are the combat source of truth (MECH-STAT-01); components (gear, artifact, imprint, EE)
are kept for validation and what-ifs.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from e7ac.domain.codes import Stat, is_artifact_code, is_hero_code, is_set_code

# Storage contract (SQLite INTEGER is 64-bit; we stay well inside it). Not a game number: "too big to be real data".
MAX_INT: Final = 2**31 - 1


class GearSlot(StrEnum):
    """In-game layout: left column weapon/helmet/armor, right column necklace/ring/boots."""

    WEAPON = "weapon"
    HELMET = "helmet"
    ARMOR = "armor"
    NECKLACE = "necklace"
    RING = "ring"
    BOOTS = "boots"


class GearGrade(StrEnum):
    """Frame colour / rarity of a gear piece."""

    NORMAL = "normal"
    GOOD = "good"
    RARE = "rare"
    HEROIC = "heroic"
    EPIC = "epic"


class ImprintGrade(StrEnum):
    D = "D"
    C = "C"
    B = "B"
    A = "A"
    S = "S"
    SS = "SS"
    SSS = "SSS"


class BuildSource(StrEnum):
    OCR = "ocr"
    FRIBBELS = "fribbels"
    MANUAL = "manual"
    IMPORT = "import"
    """Restored from an e7ac JSON backup."""


class _Frozen(BaseModel):
    # NaN/inf are never game data; SQLite would store NaN as NULL and JSON export would write null (lossy).
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class StatValue(_Frozen):
    stat: Stat
    value: float


class Substat(_Frozen):
    stat: Stat
    value: float
    rolls: int | None = Field(default=None, ge=0, le=6)
    modified: bool = False
    reforged: bool = False


class Gear(_Frozen):
    slot: GearSlot
    set_code: str
    grade: GearGrade
    item_level: int = Field(ge=1, le=100)
    enhance: int = Field(ge=0, le=15)
    main: StatValue
    substats: tuple[Substat, ...] = ()
    score: int | None = Field(default=None, ge=0, le=MAX_INT)
    external_id: str | None = None
    """Id in the source it came from (e.g. Fribbels item id), used to recognise the same physical piece."""

    @field_validator("set_code")
    @classmethod
    def _set_code(cls, value: str) -> str:
        if not is_set_code(value):
            raise ValueError(f"not a set code: {value!r} (expected e.g. 'set_cri_dmg')")
        return value

    def fingerprint(self) -> str:
        """Content identity of a gear piece (independent of where it is equipped)."""
        subs = ",".join(
            f"{s.stat.value}={s.value!r}/{s.rolls}/{int(s.modified)}{int(s.reforged)}" for s in self.substats
        )
        return (
            f"{self.slot.value}|{self.set_code}|{self.grade.value}|{self.item_level}|{self.enhance}|"
            f"{self.main.stat.value}={self.main.value!r}|{subs}"
        )


class FinalStats(_Frozen):
    """Displayed Hero Info stats (final values)."""

    atk: int = Field(ge=0, le=MAX_INT)
    defense: int = Field(ge=0, le=MAX_INT)
    hp: int = Field(ge=0, le=MAX_INT)
    speed: int = Field(ge=0, le=MAX_INT)
    crit_chance: float = Field(ge=0)
    crit_damage: float = Field(ge=0)
    effectiveness: float = Field(ge=0)
    effect_resistance: float = Field(ge=0)
    dual_attack: float = Field(ge=0)


FINAL_STAT_FIELDS: Final = tuple(FinalStats.model_fields)


class Imprint(_Frozen):
    grade: ImprintGrade | None
    """None when not known: the screen shows the grade as an icon only (read later by icon matching, M7)."""
    stat: Stat
    value: float


class ExclusiveEquipment(_Frozen):
    """At least one field must be known; use `exclusive_equipment=None` for "no EE / not read"."""

    stat: Stat | None = None
    value: float | None = None
    option_code: str | None = None
    """Stove EE option code (e.g. 'ek_c201101_01') when known."""
    option_text: str | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> ExclusiveEquipment:
        if self.stat is None and self.value is None and self.option_code is None and self.option_text is None:
            raise ValueError("an exclusive equipment needs at least one known field (use null for none)")
        return self


class ArtifactRef(_Frozen):
    code: str
    level: int = Field(ge=0, le=30)

    @field_validator("code")
    @classmethod
    def _code(cls, value: str) -> str:
        if not is_artifact_code(value):
            raise ValueError(f"not an artifact code: {value!r} (expected e.g. 'efa22')")
        return value


class SkillEnhancements(_Frozen):
    s1: int = Field(default=0, ge=0, le=10)
    s2: int = Field(default=0, ge=0, le=10)
    s3: int = Field(default=0, ge=0, le=10)


class HeroBuild(_Frozen):
    """One snapshot of an owned hero. Every field may carry a confidence (OCR); missing = 1.0 (manual/file)."""

    hero_code: str
    stars: int = Field(ge=1, le=6)
    awakening: int = Field(ge=0, le=6)
    level: int = Field(ge=1, le=60)
    skills: SkillEnhancements = SkillEnhancements()
    imprint: Imprint | None = None
    exclusive_equipment: ExclusiveEquipment | None = None
    artifact: ArtifactRef | None = None
    gear: dict[GearSlot, Gear] = {}
    final_stats: FinalStats | None = None
    cp: int | None = Field(default=None, ge=0, le=MAX_INT)
    captured_at: datetime
    source: BuildSource
    confidence: dict[str, float] = {}
    note: str = ""

    @field_validator("hero_code")
    @classmethod
    def _hero_code(cls, value: str) -> str:
        if not is_hero_code(value):
            raise ValueError(f"not a hero code: {value!r} (expected e.g. 'c2011')")
        return value

    @field_validator("captured_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("captured_at must be timezone-aware")
        return value

    @field_validator("confidence")
    @classmethod
    def _confidence_range(cls, value: dict[str, float]) -> dict[str, float]:
        bad = {k: v for k, v in value.items() if not 0.0 <= v <= 1.0}
        if bad:
            raise ValueError(f"confidence values must be within [0, 1]: {bad}")
        return value

    @model_validator(mode="after")
    def _gear_slots_match(self) -> HeroBuild:
        for slot, gear in self.gear.items():
            if gear.slot is not slot:
                raise ValueError(f"gear in slot {slot.value} says it is a {gear.slot.value}")
        return self
