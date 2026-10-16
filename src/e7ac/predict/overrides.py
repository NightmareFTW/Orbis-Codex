"""Hand-refined hero kits (SPEC D73): TOML files in `predict/kits/` that correct or complete the kit read automatically
from epic7db (`predict.effects`, SPEC D63/D68) for the heroes met most in Arena.

One file per hero, named by its code (`c2049.toml`); every number carries where it comes from (golden rule):

    hero = "c2049"                # Maid Chloe (stable code: the file is keyed by it, never by the name)
    source = "in-game skill text, max enhancement, 2026-10-20"
    status = "verified"           # verified / community / assumed
    replace = [3]                 # slots whose automatic effects are dropped first (optional)
    handles = ["Revive"]          # effect names now modelled: removed from the "not simulated" list (optional)
    cooldowns = { 3 = 4 }         # skill slot -> turns (optional, replaces epic7db's)

    [[effect]]
    slot = 3                      # 1-3
    when = "use"                  # use (when the skill is used) / start (battle start) / turn (start of each own
                                  # turn) / revive (once on lethal damage: `value` = Health fraction)
    kind = "resurrect"            # an `Effect` kind: atk def spd stun cr extra_turn immortal immunity invincible
                                  # heal barrier resurrect cleanse dispel burn bleed poison provoke silence counter
    value = 0.2
    receivers = "all"             # heal / barrier / cleanse: self / all / lowest:N; resurrect: all / one
    note = "VIP Treatment"        # optional, free text

Optional effect fields: debuff (default: the kind's usual side), turns (2), chance (1.0), all_allies (false),
scaling ([hp, atk, def] shares of the caster's stats), count (0 = all). A malformed file is an error naming the file
(never skipped silently).
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final

from e7ac.domain.codes import is_hero_code
from e7ac.predict.effects import Effect, Kit

KITS_DIR: Final = Path(__file__).parent / "kits"
STATUSES: Final = frozenset({"verified", "community", "assumed"})
KINDS: Final = frozenset(
    {
        "atk",
        "def",
        "spd",
        "stun",
        "cr",
        "extra_turn",
        "immortal",
        "immunity",
        "invincible",
        "heal",
        "barrier",
        "resurrect",
        "cleanse",
        "dispel",
        "burn",
        "bleed",
        "poison",
        "provoke",
        "silence",
        "counter",
    }
)
_DEBUFFS: Final = frozenset({"stun", "dispel", "burn", "bleed", "poison", "provoke", "silence"})
WHEN: Final = frozenset({"use", "start", "turn", "revive"})


class KitError(ValueError):
    """A hand-written kit file that cannot be used."""


@dataclass(frozen=True, slots=True)
class Placed:
    slot: int
    when: str
    effect: Effect
    note: str = ""


@dataclass(frozen=True, slots=True)
class KitOverride:
    hero: str
    source: str
    status: str
    replace: tuple[int, ...] = ()
    handles: tuple[str, ...] = ()
    cooldowns: Mapping[int, int] = field(default_factory=dict)
    effects: tuple[Placed, ...] = ()
    path: str = ""


def load_overrides(folder: Path = KITS_DIR) -> dict[str, KitOverride]:
    """Every hand-written kit, by hero code."""
    found: dict[str, KitOverride] = {}
    for path in sorted(folder.glob("*.toml")) if folder.is_dir() else []:
        override = parse_override(path.read_text(encoding="utf-8"), path.name)
        if path.stem != override.hero:
            raise KitError(f"{path.name}: the file name must be the hero code {override.hero!r}")
        found[override.hero] = override
    return found


def parse_override(text: str, name: str = "<kit>") -> KitOverride:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise KitError(f"{name}: {exc}") from exc
    hero = data.get("hero")
    if not isinstance(hero, str) or not is_hero_code(hero):
        raise KitError(f"{name}: 'hero' must be a hero code such as c2049")
    source, status = data.get("source"), data.get("status")
    if not isinstance(source, str) or not source.strip():
        raise KitError(f"{name}: 'source' is required (where the numbers come from)")
    if status not in STATUSES:
        raise KitError(f"{name}: 'status' must be one of {sorted(STATUSES)}")
    cooldowns = {int(k): _int(v, name, "cooldowns") for k, v in dict(data.get("cooldowns", {})).items()}
    effects = tuple(_placed(raw, name, i) for i, raw in enumerate(data.get("effect", []), 1))
    return KitOverride(
        hero=hero,
        source=source,
        status=str(status),
        replace=tuple(_int(s, name, "replace") for s in data.get("replace", [])),
        handles=tuple(str(h) for h in data.get("handles", [])),
        cooldowns=cooldowns,
        effects=effects,
        path=name,
    )


def apply_override(kit: Kit, cooldowns: Mapping[int, int], override: KitOverride) -> tuple[Kit, dict[int, int]]:
    """The automatic kit corrected by hand: replaced slots dropped, hand effects added, handled names no longer
    listed as not simulated."""
    out = Kit(
        on_use={slot: list(effects) for slot, effects in kit.on_use.items() if slot not in override.replace},
        at_start=[] if 2 in override.replace else list(kit.at_start),
        on_turn=[] if 2 in override.replace else list(kit.on_turn),
        revive=None if 2 in override.replace else kit.revive,
        revive_effects=[] if 2 in override.replace else list(kit.revive_effects),
        unmodelled=[n for n in kit.unmodelled if n not in override.handles],
    )
    revive_effects: list[Effect] = []
    for placed in override.effects:
        if placed.when == "use":
            out.on_use.setdefault(placed.slot, []).append(placed.effect)
        elif placed.when == "start":
            out.at_start.append(placed.effect)
        elif placed.when == "turn":
            out.on_turn.append(placed.effect)
        elif placed.effect.kind in ("heal", "resurrect"):  # revive: `value` = Health fraction regained
            out.revive = placed.effect.value
        else:
            revive_effects.append(placed.effect)
    if revive_effects:
        out.revive_effects = revive_effects
    return out, {**cooldowns, **override.cooldowns}


def _placed(raw: Any, name: str, index: int) -> Placed:
    where = f"{name}: effect {index}"
    if not isinstance(raw, dict):
        raise KitError(f"{where}: not a table")
    kind, when = raw.get("kind"), raw.get("when", "use")
    if kind not in KINDS:
        raise KitError(f"{where}: unknown kind {kind!r} (one of {sorted(KINDS)})")
    if when not in WHEN:
        raise KitError(f"{where}: 'when' must be one of {sorted(WHEN)}")
    slot = _int(raw.get("slot", 2 if when != "use" else 0), where, "slot")
    if not 1 <= slot <= 3:
        raise KitError(f"{where}: 'slot' must be 1, 2 or 3")
    scaling = raw.get("scaling", [0.0, 0.0, 0.0])
    if not (isinstance(scaling, list) and len(scaling) == 3):
        raise KitError(f"{where}: 'scaling' must be [hp, atk, def]")
    try:
        effect = Effect(
            kind=str(kind),
            debuff=bool(raw.get("debuff", kind in _DEBUFFS or float(raw.get("value", 0.0)) < 0)),
            value=float(raw.get("value", 0.0)),
            turns=int(raw.get("turns", 2)),
            chance=float(raw.get("chance", 1.0)),
            all_allies=bool(raw.get("all_allies", False)),
            scaling=(float(scaling[0]), float(scaling[1]), float(scaling[2])),
            receivers=str(raw.get("receivers", "")),
            count=int(raw.get("count", 0)),
        )
    except (TypeError, ValueError) as exc:
        raise KitError(f"{where}: {exc}") from exc
    if not 0.0 <= effect.chance <= 1.0:
        raise KitError(f"{where}: 'chance' must be between 0 and 1")
    return Placed(slot, str(when), replace(effect), str(raw.get("note", "")))


def _int(value: Any, where: str, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise KitError(f"{where}: '{key}' must be whole numbers")
    return value
