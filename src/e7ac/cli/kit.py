"""`e7 kit`: the skill kits the battle simulation uses, and the worklist for refining them by hand (SPEC D73)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy.orm import Session

from e7ac.arena.references import ReferenceStore
from e7ac.catalog.facts import EntityType
from e7ac.catalog.store import current_snapshot, load_entity
from e7ac.cli import catalog as catalog_cli
from e7ac.cli.roster import _catalog_heroes, _engine, _fail, _resolve_hero_code
from e7ac.paths import default_paths
from e7ac.predict.damage import support_amounts
from e7ac.predict.effects import Effect, Kit, Scaling, kit_from
from e7ac.predict.overrides import KitError, KitOverride, apply_override, load_overrides
from e7ac.sources.epic7db import fetch_kit
from e7ac.sources.http import CachedHttp
from e7ac.storage.db import session_scope

kit_app = typer.Typer(help="Skill kits used by the battle simulation; the worklist for hand refinement.")
_OFFLINE = typer.Option("--offline", help="Use cached pages only.")


@kit_app.command("show")
def show(
    hero: Annotated[str, typer.Argument(help='Exact name or code, e.g. "Maid Chloe".')],
    offline: Annotated[bool, _OFFLINE] = False,
) -> None:
    """The kit as the simulation sees it: effects per skill (read automatically, corrected by any hand file),
    cooldowns, and what is not simulated."""
    paths = default_paths()
    overrides = _overrides()
    with session_scope(_engine()) as session:
        code = _resolve_hero_code(session, hero)
        heroes = _catalog_heroes(session) or {}
        amounts = _amounts(session, code)
    name = heroes[code].name if code in heroes and heroes[code].name else code
    with catalog_cli.http_factory(paths, offline) as http:
        kit, cooldowns = _effective(http, name, amounts, overrides.get(code))
    typer.echo(f"{name} ({code})")
    hand = overrides.get(code)
    typer.echo(f"  hand file: {hand.path} ({hand.status}; {hand.source})" if hand else "  hand file: none (automatic)")
    for slot in (1, 2, 3):
        cooldown = cooldowns.get(slot)
        label = "passive" if slot == 2 and not cooldown else f"cooldown {cooldown}" if cooldown else "no cooldown"
        effects = kit.on_use.get(slot, [])
        typer.echo(f"  S{slot} ({label}): " + (", ".join(_describe(e) for e in effects) or "-"))
    for title, effects in (("battle start", kit.at_start), ("each own turn", kit.on_turn)):
        if effects:
            typer.echo(f"  {title}: " + ", ".join(_describe(e) for e in effects))
    if kit.revive is not None:
        extra = ", ".join(_describe(e) for e in kit.revive_effects)
        typer.echo(f"  revive once: {kit.revive:.0%} Health" + (f" + {extra}" if extra else ""))
    passives = _passives(kit)
    if passives:
        typer.echo("  passives: " + ", ".join(passives))
    typer.echo("  not simulated: " + (", ".join(kit.unmodelled) or "-"))


@kit_app.command("todo")
def todo(
    limit: Annotated[int, typer.Option(min=1, help="How many heroes to list.")] = 30,
    offline: Annotated[bool, _OFFLINE] = False,
) -> None:
    """The heroes met in Arena (most seen first: VS models and faces learned, your last team), with what the
    simulation does not cover yet for each: the list to refine by hand."""
    paths = default_paths()
    overrides = _overrides()
    seen = _met(paths.home / "references")
    if not seen:
        typer.echo("No Arena hero seen yet: run e7 arena watch (Arena.cmd) first.")
        return
    with session_scope(_engine()) as session:
        heroes = _catalog_heroes(session) or {}
        amounts = {code: _amounts(session, code) for code, _ in seen[:limit]}
    with catalog_cli.http_factory(paths, offline) as http:
        for code, count in seen[:limit]:
            name = heroes[code].name if code in heroes and heroes[code].name else code
            kit, _ = _effective(http, name, amounts[code], overrides.get(code))
            mark = "refined" if code in overrides else "auto"
            missing = ", ".join(kit.unmodelled) or "-"
            typer.echo(f"  {count:>3}x  {name:<28} {code:<7} [{mark}]  not simulated: {missing}")


def _effective(
    http: CachedHttp, name: str, amounts: dict[int, dict[str, Scaling]], hand: KitOverride | None
) -> tuple[Kit, dict[int, int]]:
    skills = fetch_kit(http, name)
    kit = kit_from(skills, amounts)
    cooldowns = {s: k.cooldown for s, k in skills.items() if k.cooldown is not None}
    if not skills:
        kit.unmodelled.append("(no epic7db page)")
    return apply_override(kit, cooldowns, hand) if hand is not None else (kit, cooldowns)


def _amounts(session: Session, code: str) -> dict[int, dict[str, Scaling]]:
    snapshot = current_snapshot(session)
    found: dict[int, dict[str, Scaling]] = {}
    if snapshot is None:
        return found
    for slot in (1, 2, 3):
        entity = load_entity(session, snapshot.id, EntityType.SKILL, f"{code}:s{slot}")
        if entity is not None:
            found[slot] = support_amounts(entity)
    return found


def _met(root: Path) -> list[tuple[str, int]]:
    """Hero code -> pictures learned of it (VS models and Attack Guide faces) plus one when in your last team."""
    counts: dict[str, int] = {}
    for kind in ("arena", "faces"):
        store = ReferenceStore(root / kind)
        for code in store.codes():
            counts[code] = counts.get(code, 0) + len(list((root / kind / code).glob("*.png")))
    for code in ReferenceStore(root / "arena").last_team():
        counts[code] = counts.get(code, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def _overrides() -> dict[str, KitOverride]:
    try:
        return load_overrides()
    except KitError as exc:
        _fail(f"hand-written kit: {exc}")


def _passives(kit: Kit) -> list[str]:
    found = []
    if kit.counter:
        found.append(f"counterattack {kit.counter:.0%}")
    if kit.reflect:
        found.append(f"reflect {kit.reflect:.0%} of Health lost")
    if kit.speed_cap:
        found.append(f"speed cap {kit.speed_cap:.0%} for everyone else")
    if kit.no_counter:
        found.append("nobody else counterattacks")
    if kit.immune:
        found.append("immune to " + "/".join(kit.immune))
    if kit.guard:
        found.append(
            f"{kit.guard:.0%} to counterattack when an ally is attacked (every {kit.guard_cooldown} turns: "
            + ", ".join(_describe(e) for e in kit.guard_effects)
            + ")"
        )
    if kit.uncounterable:
        found.append("S" + "/S".join(str(slot) for slot in kit.uncounterable) + " triggers no counterattack")
    if kit.pierce_top:
        found.append(
            ", ".join(
                f"S{slot} pierces {v:.0%} Defense of the highest-Health enemy" for slot, v in kit.pierce_top.items()
            )
        )
    if kit.ignore_resist:
        found.append("ignores Effect Resistance of weaker targets")
    if kit.spirit_full:
        found.append(
            f"Fighting Spirit {kit.spirit_start}"
            + (f" (+{kit.spirit_bonus} with an Earth foremost ally)" if kit.spirit_bonus else "")
            + f", +{kit.spirit_hit} per hit taken; at {kit.spirit_full}: "
            + ", ".join(_describe(e) for e in kit.spirit_effects)
            + f" and {kit.spirit_share:.0%} of the max Health of her and the foremost ally to every enemy"
        )
    if kit.low_hp:
        found.append(
            f"at {kit.low_hp:.0%} Health or less (every {kit.low_hp_cooldown} turns): "
            + ", ".join(_describe(e) for e in kit.low_hp_effects)
        )
    return found


def _describe(effect: Effect) -> str:
    parts = [effect.kind]
    if effect.kind == "spirit":
        parts.append(f"+{effect.value:g} Fighting Spirit")
    elif effect.value:
        parts.append(f"{effect.value:+.0%}")
    if any(effect.scaling):
        hp, atk, df = effect.scaling
        parts.append("+".join(f"{v:.0%} {s}" for v, s in ((hp, "HP"), (atk, "ATK"), (df, "DEF")) if v))
    if effect.receivers:
        parts.append(effect.receivers)
    if effect.kind in ("cleanse", "dispel"):
        parts.append(f"x{effect.count or 'all'}")
    if effect.debuff and effect.chance != 1:
        parts.append(f"{effect.chance:.0%} chance")
    if effect.condition:
        parts.append(f"if {effect.condition}")
    if effect.kind not in ("heal", "resurrect", "cleanse", "dispel", "cr", "extra_turn"):
        parts.append(f"{effect.turns}t")
    return " ".join(parts)
