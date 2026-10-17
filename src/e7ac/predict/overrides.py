"""Hand-refined hero kits (SPEC D73): TOML files in `predict/kits/` that correct or complete the kit read automatically
from epic7db (`predict.effects`, SPEC D63/D68) for the heroes met most in Arena.

One file per hero, named by its code (`c2049.toml`); every number carries where it comes from (golden rule):

    hero = "c2049"                # Maid Chloe (stable code: the file is keyed by it, never by the name)
    source = "in-game skill text, max enhancement, 2026-10-20"
    status = "verified"           # verified / community / assumed
    replace = [3]                 # slots whose automatic effects are dropped first (optional)
    handles = ["Revive"]          # effect names now modelled: removed from the "not simulated" list (optional)
    cooldowns = { 3 = 4 }         # skill slot -> turns (optional, replaces epic7db's)
    unmodelled = ["S1 lifesteal"] # what this hero still has that is not simulated (optional, shown by `e7 kit`)

    [passive]                     # optional: kit-level passives
    reflect = 0.4                 # share of the Health lost to a hit that the attacker takes back
    speed_cap = 0.9               # own speed fixed; every other hero capped at this share of it
    cr_lock = true                # no Combat Readiness increase sticks during this hero's own turn
    lethal_immortal = 1           # immortal for N turns, once per battle, on receiving lethal damage
    no_counter = true             # nobody else counterattacks while this hero lives
    immune = ["stun"]             # debuff kinds that never land
    low_hp = 0.3                  # effects with when = "low_hp" trigger after a hit leaves this Health share or less
    low_hp_cooldown = 5           # ... at most once every N own turns
    guard = 0.2                   # chance to counterattack when an ally is attacked; `when = "guard"` effects follow
    guard_cooldown = 3            # ... at most once every N own turns
    uncounterable = [3]           # skill slots whose attacks trigger no counterattack
    pierce_top = { 3 = 0.7 }      # slot -> penetration against the enemy with the highest max Health only
    ignore_resist = true          # ignores the Effect Resistance of targets with less max Health than this hero

    [[effect]]
    slot = 3                      # 1-3
    when = "use"                  # use (when the skill is used) / start (battle start) / turn (start of each own
                                  # turn) / low_hp (see [passive]) / revive (once on lethal damage: `value` = Health
                                  # fraction)
    kind = "resurrect"            # an `Effect` kind: atk def spd stun cr extra_turn immortal immunity invincible
                                  # heal barrier resurrect cleanse dispel burn bleed poison provoke silence counter
    value = 0.2
    receivers = "all"             # heal / barrier / cleanse: self / all / lowest:N; resurrect: all / one
    note = "VIP Treatment"        # optional, free text

Optional effect fields: debuff (default: the kind's usual side), turns (2), chance (1.0; up to 2.0 for "200% chance"),
all_allies (false), scaling ([hp, atk, def] shares of the caster's stats), count (0 = all), condition ("kill": only
when the skill defeated an enemy; "stun": only when it newly stunned a target). Receivers also: "others" (every ally but
the caster). Extra kinds for hand kits: sacrifice (`value` = share of current Health paid by the
caster), revive_buff (`value` = Health share restored when a hero holding it dies, for `turns`), redirect_provoke (at
its turn the target uses its basic skill on the enemy with the highest max Health), fear (cannot act; 40% chance to be
dispelled at the start of its turn), hot (continuous healing: `value` = share of the holder's max Health at the start of
each of its turns, for `turns`; it stacks), dual_attack (a random ally attacks the same target with a basic attack),
spirit (Fighting Spirit gained by the caster; `[passive]` spirit_* set the Young Senya mechanic), unable_counter
(cannot counterattack for `turns`), unable_buff (receives no buffs for `turns`), shorten (every buff of the targets
lasts `turns` fewer turns; always lands). `receivers = "enemies"` on a debuff hits every living enemy, not only the
targets of the skill (an extra attack such as Harsetti's En Passant). A malformed file is an error naming the file
(never skipped silently).
"""

from __future__ import annotations

import dataclasses
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
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
        "sacrifice",
        "revive_buff",
        "redirect_provoke",
        "fear",
        "hot",
        "dual_attack",
        "spirit",
        "unable_counter",
        "unable_buff",
        "shorten",
    }
)
_DEBUFFS: Final = frozenset(
    {
        "stun",
        "dispel",
        "burn",
        "bleed",
        "poison",
        "provoke",
        "silence",
        "redirect_provoke",
        "fear",
        "unable_counter",
        "unable_buff",
        "shorten",
    }
)
WHEN: Final = frozenset({"use", "start", "turn", "revive", "low_hp", "guard", "spirit"})
PASSIVES: Final = frozenset(
    {
        "reflect",
        "speed_cap",
        "cr_lock",
        "lethal_immortal",
        "no_counter",
        "immune",
        "low_hp",
        "low_hp_cooldown",
        "guard",
        "guard_cooldown",
        "uncounterable",
        "pierce_top",
        "ignore_resist",
        "spirit_full",
        "spirit_start",
        "spirit_bonus",
        "spirit_hit",
        "spirit_share",
    }
)


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
    unmodelled: tuple[str, ...] = ()
    passive: Mapping[str, Any] = field(default_factory=dict)
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
    passive = dict(data.get("passive", {}))
    unknown = sorted(set(passive) - PASSIVES)
    if unknown:
        raise KitError(f"{name}: unknown passive {unknown} (one of {sorted(PASSIVES)})")
    return KitOverride(
        hero=hero,
        source=source,
        status=str(status),
        replace=tuple(_int(s, name, "replace") for s in data.get("replace", [])),
        handles=tuple(str(h) for h in data.get("handles", [])),
        unmodelled=tuple(str(h) for h in data.get("unmodelled", [])),
        passive=passive,
        cooldowns=cooldowns,
        effects=effects,
        path=name,
    )


def apply_override(kit: Kit, cooldowns: Mapping[int, int], override: KitOverride) -> tuple[Kit, dict[int, int]]:
    """The automatic kit corrected by hand: replaced slots dropped, hand effects added, handled names no longer
    listed as not simulated, passives set."""
    dropped = 2 in override.replace  # a replaced S2 clears the passive parts read automatically
    out = dataclasses.replace(
        kit,
        on_use={slot: list(effects) for slot, effects in kit.on_use.items() if slot not in override.replace},
        at_start=[] if dropped else list(kit.at_start),
        on_turn=[] if dropped else list(kit.on_turn),
        revive=None if dropped else kit.revive,
        revive_effects=[] if dropped else list(kit.revive_effects),
        unmodelled=[n for n in kit.unmodelled if n not in override.handles] + list(override.unmodelled),
        low_hp_effects=[],
        guard_effects=[],
        spirit_effects=[],
    )
    revive_effects: list[Effect] = []
    for placed in override.effects:
        if placed.when == "use":
            out.on_use.setdefault(placed.slot, []).append(placed.effect)
        elif placed.when == "start":
            out.at_start.append(placed.effect)
        elif placed.when == "turn":
            out.on_turn.append(placed.effect)
        elif placed.when == "low_hp":
            out.low_hp_effects.append(placed.effect)
        elif placed.when == "guard":
            out.guard_effects.append(placed.effect)
        elif placed.when == "spirit":
            out.spirit_effects.append(placed.effect)
        elif placed.effect.kind in ("heal", "resurrect"):  # revive: `value` = Health fraction regained
            out.revive = placed.effect.value
        else:
            revive_effects.append(placed.effect)
    if revive_effects:
        out.revive_effects = revive_effects
    passive = override.passive
    out.reflect = float(passive.get("reflect", out.reflect))
    out.speed_cap = float(passive.get("speed_cap", out.speed_cap))
    out.cr_lock = bool(passive.get("cr_lock", out.cr_lock))
    out.lethal_immortal = int(passive.get("lethal_immortal", out.lethal_immortal))
    out.no_counter = bool(passive.get("no_counter", out.no_counter))
    out.immune = tuple(str(k) for k in passive.get("immune", out.immune))
    out.low_hp = float(passive.get("low_hp", out.low_hp))
    out.low_hp_cooldown = int(passive.get("low_hp_cooldown", out.low_hp_cooldown))
    out.guard = float(passive.get("guard", out.guard))
    out.guard_cooldown = int(passive.get("guard_cooldown", out.guard_cooldown))
    out.uncounterable = tuple(int(slot) for slot in passive.get("uncounterable", out.uncounterable))
    out.pierce_top = {int(slot): float(v) for slot, v in dict(passive.get("pierce_top", out.pierce_top)).items()}
    out.ignore_resist = bool(passive.get("ignore_resist", out.ignore_resist))
    out.spirit_full = int(passive.get("spirit_full", out.spirit_full))
    out.spirit_start = int(passive.get("spirit_start", out.spirit_start))
    out.spirit_bonus = int(passive.get("spirit_bonus", out.spirit_bonus))
    out.spirit_hit = int(passive.get("spirit_hit", out.spirit_hit))
    out.spirit_share = float(passive.get("spirit_share", out.spirit_share))
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
            condition=str(raw.get("condition", "")),
        )
    except (TypeError, ValueError) as exc:
        raise KitError(f"{where}: {exc}") from exc
    if not 0.0 <= effect.chance <= 2.0:
        raise KitError(f"{where}: 'chance' must be between 0 and 2")
    if effect.condition not in ("", "kill", "stun"):
        raise KitError(f'{where}: \'condition\' must be "kill", "stun" or left out')
    return Placed(slot, str(when), effect, str(raw.get("note", "")))


def _int(value: Any, where: str, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise KitError(f"{where}: '{key}' must be whole numbers")
    return value
