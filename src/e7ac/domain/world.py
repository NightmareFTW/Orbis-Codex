"""Game regions ("worlds") as used by the Stove Strategy Guide API (`world_code`).

Source: Stove front-end bundles and API responses (docs/DATA_SOURCES.md §1), verified 2026-10-03.
"""

from enum import StrEnum


class World(StrEnum):
    """Server region. Values are the exact `world_code` strings of the Stove API."""

    GLOBAL = "world_global"
    EUROPE = "world_eu"
    ASIA = "world_asia"
    KOREA = "world_kor"
    JAPAN = "world_jpn"
