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
    taunt = true                  # in front (first living hero of the side): enemies' single-target skills pick it
    hit_cr = 0.25                 # Combat Readiness gained each time it is attacked and survives
    fragrance = 0.08              # share of the Health an ally loses to a hit that the attacker takes back
    cr_mirror = 0.5               # share of an enemy's Combat Readiness increase this hero gains too
    ally_low = 0.5                # an ally left at this Health share or less after a hit triggers `when = "ally_low"`
    ally_low_cooldown = 3         # ... at most once every N own turns
    eff_speed = 0.3               # Effectiveness gained at the start of the battle, per 100 of Speed
    cr_immune = true              # unaffected by Combat Readiness increase and decrease effects
    start_wait = [3]              # skill slots that begin the battle with their full cooldown count
    hit_cap = 0.51                # damage suffered in one attack never exceeds this share of max Health
    variant_slot = 2              # S1 uses the skill data of this slot while the hero has a buff (`when = "variant"`
                                  # effects run just before that attack)
    death_cd = 1                  # every skill cooldown goes down this many turns each time a hero dies
    pen_resist = 0.4              # Penetration Resistance of every ally while this hero lives
    ally_hit_cr = 0.1             # Combat Readiness gained after every attack an ally suffers

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
targets of the skill (an extra attack such as Harsetti's En Passant); `receivers = "self"` on a debuff or dispel
makes it hit the caster, always landing (Rinak's Clumsy Finish: the 1-turn self stun is `turns = 2`, the end-of-turn
count being included). SPEC D89: eff (Effectiveness up by `value` for `turns`, added to the Effectiveness of the
debuffs the caster inflicts), venom (`value` = share of the target's max Health per turn, `turns`, `count` stacks each
with its own chance; they tick at the start of the holder's turn and a cleanse removes them), detonate (every venom of
the targets, or of every enemy with `receivers = "enemies"`, hits at once: share x turns left x stacks; always
lands), fixed_damage (`value` damage to the targets or to every enemy, ignoring Defense, always lands), offering
(`value` = share of the damage allies take that the holder takes instead, `turns` = immortality turns once per battle
on lethal damage; given to the foremost ally; `when = "offering_death"` effects follow when the holder dies),
offering_cost (`value` = share of the holder's current Health paid, never to death), abundance (`value` additional
damage after each attack of the holder, `turns`; `receivers = "not_offering"` skips the offering holder), pilfer (the
target's Attack and Defense down 20% and the caster gains a Spoils stack, 20% Attack and Defense each, up to 4; a
dispel of the caster's Spoils lifts the pilfers). SPEC D108: reset_cd (the cooldown of the skill in `value` is over
at once: Arbiter Vildred's Dark Blade when it defeats an enemy), erosion (`value` = share of the holder's lost Health
per turn, `turns`; a detonation counts it for the turns left), swap (the hero changes form: effects with `form = 0` or
`1` only act in that form; Rhianna and Luciella), extra_attack (the caster attacks the same targets again with the
skill of the slot in `value`, S1 when 0, with the `chance` of it happening; effects with condition "proc" follow only
if it did: Belian's Incursion), beguile (`value` = share of max Health that every other ally of the holder suffers when
the holder's turn ends, once); SPEC D109: seal (the holder's passives do not work for `turns`, `Kit.sealed`),
collapse (`value` = share of max Health the holder loses for `turns`: the Health above the new maximum is gone and
healing stops at it); random (SPEC D109: one of the `options`, a list of effect tables, is picked at random for each
target: Tenebria's random debuff), sleep (cannot act until attacked, `assumed`), restrict (no Combat Readiness increase
but the Speed stat's), stealth (enemies' single-target skills cannot pick the holder while it has an ally; ends when
attacked), block (buffs have no effect and none can be received), nullifier (the first effect of an enemy skill that
lands on the holder is nullified), deify (Critical Hit Chance +50 points when attacking, 50% less damage from attacks),
res (Effect Resistance up);
`when = "before"` effects happen before the attack of their slot, on the targets it has chosen; conditions "crit",
"debuffed", "half" (a target with half its Health or less) and "proc" join "kill" and "stun"; "proc_kill" (SPEC D109)
follows an `extra_attack` that defeated an enemy and "cd3" holds while the hero's S3 is on cooldown.
`receivers = "highest_atk"` on a dual_attack picks the ally with the highest Attack. `of_target = true` on a heal takes
its scaling from the target's max Health (Lady's Divine Punishment). A malformed file is an error naming the file
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
        "eff",
        "venom",
        "detonate",
        "fixed_damage",
        "offering",
        "offering_cost",
        "abundance",
        "pilfer",
        "reset_cd",
        "swap",
        "extra_attack",
        "erosion",
        "beguile",
        "seal",
        "collapse",
        "res",
        "deify",
        "nullifier",
        "random",
        "sleep",
        "restrict",
        "stealth",
        "block",
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
        "venom",
        "pilfer",
        "erosion",
        "beguile",
        "seal",
        "collapse",
        "random",
        "sleep",
        "restrict",
        "block",
    }
)
WHEN: Final = frozenset(
    {"use", "start", "turn", "revive", "low_hp", "guard", "spirit", "ally_low", "offering_death", "variant", "before"}
)
CONDITIONS: Final = frozenset({"", "kill", "stun", "crit", "debuffed", "half", "proc", "proc_kill", "cd3"})
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
        "taunt",
        "hit_cr",
        "fragrance",
        "cr_mirror",
        "ally_low",
        "ally_low_cooldown",
        "hit_cap",
        "variant_slot",
        "death_cd",
        "eff_speed",
        "cr_immune",
        "start_wait",
        "pen_resist",
        "ally_hit_cr",
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
    unread: tuple[int, ...] = ()
    """Skill slots this hand kit still leaves out entirely (their effects are known to matter and are not simulated):
    the hero stays "not read" for them, so every chance that involves it is still flagged (SPEC D87)."""
    passive: Mapping[str, Any] = field(default_factory=dict)
    cooldowns: Mapping[int, int] = field(default_factory=dict)
    effects: tuple[Placed, ...] = ()
    path: str = ""


def _slot(value: object, name: str, key: str) -> int:
    slot = _int(value, name, key)
    if slot not in (1, 2, 3):
        raise KitError(f"{name}: '{key}' lists skill slots 1, 2 or 3, not {slot}")
    return slot


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
        unread=tuple(_slot(s, name, "unread") for s in data.get("unread", [])),
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
        unread=list(override.unread),  # read by hand: what is still missing is in `unmodelled`, or in `unread`
        low_hp_effects=[],
        guard_effects=[],
        spirit_effects=[],
        ally_low_effects=[],
        offering_death_effects=[],
        variant_effects=[],
        before_use={},
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
        elif placed.when == "ally_low":
            out.ally_low_effects.append(placed.effect)
        elif placed.when == "offering_death":
            out.offering_death_effects.append(placed.effect)
        elif placed.when == "variant":
            out.variant_effects.append(placed.effect)
        elif placed.when == "before":
            out.before_use.setdefault(placed.slot, []).append(placed.effect)
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
    out.taunt = bool(passive.get("taunt", out.taunt))
    out.hit_cr = float(passive.get("hit_cr", out.hit_cr))
    out.fragrance = float(passive.get("fragrance", out.fragrance))
    out.cr_mirror = float(passive.get("cr_mirror", out.cr_mirror))
    out.ally_low = float(passive.get("ally_low", out.ally_low))
    out.ally_low_cooldown = int(passive.get("ally_low_cooldown", out.ally_low_cooldown))
    out.hit_cap = float(passive.get("hit_cap", out.hit_cap))
    out.variant_slot = int(passive.get("variant_slot", out.variant_slot))
    out.death_cd = int(passive.get("death_cd", out.death_cd))
    out.eff_speed = float(passive.get("eff_speed", out.eff_speed))
    out.cr_immune = bool(passive.get("cr_immune", out.cr_immune))
    out.start_wait = tuple(int(slot) for slot in passive.get("start_wait", out.start_wait))
    out.pen_resist = float(passive.get("pen_resist", out.pen_resist))
    out.ally_hit_cr = float(passive.get("ally_hit_cr", out.ally_hit_cr))
    return out, {**cooldowns, **override.cooldowns}


def _placed(raw: Any, name: str, index: int) -> Placed:
    where = f"{name}: effect {index}"
    if not isinstance(raw, dict):
        raise KitError(f"{where}: not a table")
    when = raw.get("when", "use")
    if when not in WHEN:
        raise KitError(f"{where}: 'when' must be one of {sorted(WHEN)}")
    slot = _int(raw.get("slot", 2 if when != "use" else 0), where, "slot")
    if not 1 <= slot <= 3:
        raise KitError(f"{where}: 'slot' must be 1, 2 or 3")
    return Placed(slot, str(when), _effect(raw, where), str(raw.get("note", "")))


def _effect(raw: Any, where: str) -> Effect:
    if not isinstance(raw, dict):
        raise KitError(f"{where}: not a table")
    kind = raw.get("kind")
    if kind not in KINDS:
        raise KitError(f"{where}: unknown kind {kind!r} (one of {sorted(KINDS)})")
    scaling = raw.get("scaling", [0.0, 0.0, 0.0])
    if not (isinstance(scaling, list) and len(scaling) == 3):
        raise KitError(f"{where}: 'scaling' must be [hp, atk, def]")
    options = raw.get("options", [])
    if kind == "random" and not (isinstance(options, list) and options):
        raise KitError(f"{where}: a random effect lists its 'options'")
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
            of_target=bool(raw.get("of_target", False)),
            form=int(raw.get("form", -1)),
            options=tuple(_effect(o, f"{where}, option {i}") for i, o in enumerate(options, 1)),
        )
    except (TypeError, ValueError) as exc:
        raise KitError(f"{where}: {exc}") from exc
    if not 0.0 <= effect.chance <= 2.0:
        raise KitError(f"{where}: 'chance' must be between 0 and 2")
    if effect.condition not in CONDITIONS:
        raise KitError(f"{where}: 'condition' must be one of {sorted(CONDITIONS)} or left out")
    if effect.form not in (-1, 0, 1):
        raise KitError(f"{where}: 'form' must be 0 or 1 (or left out: any form)")
    return effect


def _int(value: Any, where: str, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise KitError(f"{where}: '{key}' must be whole numbers")
    return value
