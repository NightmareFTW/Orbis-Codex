"""Stable game codes and provenance vocabulary.

Enum *values* are the codes used by the game data itself (Stove API and Fribbels data agree on them,
verified 2026-10-03, docs/DATA_SOURCES.md), so they can be stored and compared without translation.
Display names are separate and never used as keys.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Final


class Element(StrEnum):
    FIRE = "fire"
    ICE = "ice"
    EARTH = "wind"  # the game data calls Earth "wind"
    LIGHT = "light"
    DARK = "dark"

    @property
    def display(self) -> str:
        return _ELEMENT_NAMES[self]


class HeroClass(StrEnum):
    WARRIOR = "warrior"
    KNIGHT = "knight"
    THIEF = "assassin"
    RANGER = "ranger"
    MAGE = "mage"
    SOUL_WEAVER = "manauser"

    @property
    def display(self) -> str:
        return _CLASS_NAMES[self]


class Horoscope(StrEnum):
    ARIES = "ram"
    TAURUS = "bull"
    GEMINI = "twins"
    CANCER = "crab"
    LEO = "lion"
    VIRGO = "maiden"
    LIBRA = "scales"
    SCORPIO = "scorpion"
    SAGITTARIUS = "archer"
    CAPRICORN = "goat"
    AQUARIUS = "waterbearer"
    PISCES = "fish"

    @property
    def display(self) -> str:
        return self.name.capitalize()


class Stat(StrEnum):
    """Stat codes as used by the game data (gear, imprints, EE, set codes).

    Value convention everywhere in e7ac: flat stats in game units (ATK 525), rates as fractions (0.18 = 18%).
    """

    ATK = "att"
    ATK_PERCENT = "att_rate"
    HP = "max_hp"
    HP_PERCENT = "max_hp_rate"
    DEF = "def"
    DEF_PERCENT = "def_rate"
    SPEED = "speed"
    CRIT_CHANCE = "cri"
    CRIT_DAMAGE = "cri_dmg"
    EFFECTIVENESS = "acc"
    EFFECT_RESISTANCE = "res"
    DUAL_ATTACK = "coop"

    @property
    def is_rate(self) -> bool:
        return self not in (Stat.ATK, Stat.HP, Stat.DEF, Stat.SPEED)


class DataStatus(StrEnum):
    """Provenance status of a datum (docs/MECHANICS.md, CLAUDE.md golden rules)."""

    VERIFIED = "verified"
    COMMUNITY = "community"
    ASSUMED = "assumed"
    UNKNOWN = "unknown"


class SourceId(StrEnum):
    STOVE = "stove"
    FRIBBELS = "fribbels"
    E7CALC = "e7calc"
    USER = "user"


_ELEMENT_NAMES: Final = {
    Element.FIRE: "Fire",
    Element.ICE: "Ice",
    Element.EARTH: "Earth",
    Element.LIGHT: "Light",
    Element.DARK: "Dark",
}
_CLASS_NAMES: Final = {
    HeroClass.WARRIOR: "Warrior",
    HeroClass.KNIGHT: "Knight",
    HeroClass.THIEF: "Thief",
    HeroClass.RANGER: "Ranger",
    HeroClass.MAGE: "Mage",
    HeroClass.SOUL_WEAVER: "Soul Weaver",
}

# fullmatch + explicit ASCII classes: no trailing newline ('c2011\n') or non-ASCII (fullwidth) digits slip through
HERO_CODE_RE: Final = re.compile(r"c[0-9]{4}")
ARTIFACT_CODE_RE: Final = re.compile(r"ef[0-9a-z]{2,8}")
SET_CODE_RE: Final = re.compile(r"set_[a-z_]+")


def is_hero_code(value: str) -> bool:
    return bool(HERO_CODE_RE.fullmatch(value))


def is_artifact_code(value: str) -> bool:
    return bool(ARTIFACT_CODE_RE.fullmatch(value))


def is_set_code(value: str) -> bool:
    return bool(SET_CODE_RE.fullmatch(value))
