"""`e7 kit`: the skill kits the battle simulation uses, and the worklist for refining them by hand (SPEC D73)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy.orm import Session

from e7ac.arena.met import met_heroes
from e7ac.cli import catalog as catalog_cli
from e7ac.cli.roster import _catalog_heroes, _engine, _fail, _resolve_hero_code
from e7ac.paths import default_paths
from e7ac.predict.audit import audit_hero, screens_needed, summarize, to_json, vocabulary
from e7ac.predict.effects import Effect, Kit, Scaling
from e7ac.predict.overrides import KitError, KitOverride, load_overrides
from e7ac.predict.worklist import effective_kit, kit_amounts, worklist
from e7ac.skills.store import SkillStore, StoredSkill, skills_dir
from e7ac.sources.epic7db import fetch_kit, fetch_library
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
        kit, cooldowns = _effective(
            http, name, amounts, overrides.get(code), SkillStore(skills_dir(paths.home)).load(code)
        )
    typer.echo(f"{name} ({code})")
    hand = overrides.get(code)
    typer.echo(f"  hand file: {hand.path} ({hand.status}; {hand.source})" if hand else "  hand file: none (automatic)")
    for slot in (1, 2, 3):
        cooldown = cooldowns.get(slot)
        label = "passive" if slot == 2 and not cooldown else f"cooldown {cooldown}" if cooldown else "no cooldown"
        effects = kit.on_use.get(slot, [])
        typer.echo(f"  S{slot} ({label}): " + (", ".join(_describe(e) for e in effects) or "-"))
    for slot, effects in sorted(kit.before_use.items()):
        typer.echo(f"  before the S{slot} attack: " + ", ".join(_describe(e) for e in effects))
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
    if kit.unread:
        slots = ", ".join(f"S{slot}" for slot in kit.unread)
        typer.echo(
            f"  NOT READ: {slots}: the text describes effects but epic7db shows none; they count as plain attacks"
        )


@kit_app.command("todo")
def todo(
    limit: Annotated[int, typer.Option(min=1, help="How many heroes to list.")] = 30,
    offline: Annotated[bool, _OFFLINE] = False,
) -> None:
    """The heroes met in Arena (most seen first), with what the simulation does not cover yet for each: the list to
    refine by hand. It counts the defence teams the watch read (the Dev page of `e7 app` collects them on every
    refresh of the Opponents list); before any is read, the pictures learned (VS models and faces) are counted."""
    paths = default_paths()
    overrides = _overrides()
    seen = _met(paths.home)
    if not seen:
        typer.echo("No Arena hero seen yet: run e7 arena watch (Arena.cmd) or the Dev page of e7 app first.")
        return
    with session_scope(_engine()) as session:
        heroes = _catalog_heroes(session) or {}
        amounts = {code: _amounts(session, code) for code, _ in seen[:limit]}
    with catalog_cli.http_factory(paths, offline) as http:
        store = SkillStore(skills_dir(paths.home))
        captured = {code: store.load(code) for code, _ in seen[:limit]}
        rows = worklist(seen[:limit], heroes, amounts, overrides, http, lambda h, name: fetch_kit(h, name), captured)
    for row in rows:
        mark = "refined" if row.refined else "auto"
        missing = ", ".join(row.unmodelled) or "-"
        typer.echo(f"  {row.seen:>3}x  {row.name:<28} {row.code:<7} [{mark}]  not simulated: {missing}")


@kit_app.command("audit")
def audit(
    limit: Annotated[int, typer.Option(min=0, help="Only the first N heroes of the catalog (0: all).")] = 0,
    top: Annotated[int, typer.Option(min=1, help="How many unknown mechanics to list.")] = 40,
    out: Annotated[Path | None, typer.Option("--out", help="Where to write the full audit (JSON).")] = None,
    offline: Annotated[bool, _OFFLINE] = False,
) -> None:
    """Every hero of the catalog: what the program knows of its skills and what only the game can still tell (SPEC D90).
    Reads epic7db's page of each hero (cached; the first run fetches one page a second), so it takes a while."""
    paths = default_paths()
    overrides = _overrides()
    seen = dict(_met(paths.home))
    with session_scope(_engine()) as session:
        heroes = _catalog_heroes(session) or {}
        names = sorted((h.name, code) for code, h in heroes.items() if h.name)
        if limit:
            names = names[:limit]
        amounts = {code: _amounts(session, code) for _, code in names}
    audits = []
    store = SkillStore(skills_dir(paths.home))
    with catalog_cli.http_factory(paths, offline) as http:
        for index, (name, code) in enumerate(names, 1):
            skills = fetch_kit(http, name)
            library = fetch_library(http, name) if skills else None
            audits.append(audit_hero(code, name, skills, amounts[code], overrides.get(code), library, store.load(code)))
            if index % 25 == 0 or index == len(names):
                typer.echo(f"  {index}/{len(names)} heroes read", err=True)
    total = summarize(audits)
    typer.echo(f"{total.heroes} heroes; epic7db has a page for {total.heroes - len(total.no_page)}")
    typer.echo(
        f"  every skill read: {total.done}; with a skill still not read: {total.unread} "
        f"({total.skills_unread} of {total.skills} skills); hand kits: {total.hand}"
    )
    typer.echo(f"  Fribbels library data (average stats, sets, builds): {total.library} heroes")
    typer.echo(f"  skills with a shorter cooldown when fully enhanced: {total.cooldown_cuts}")
    typer.echo("  enhancement steps: " + ", ".join(f"{k} {n}" for k, n in total.steps.most_common()))
    if total.no_page:
        typer.echo(f"  no epic7db page: {', '.join(total.no_page[:15])}" + (" ..." if len(total.no_page) > 15 else ""))
    words = vocabulary(audits, seen)
    typer.echo(f"Named mechanics no reader knows ({len(words)}; the most met / widespread first):")
    for row in words[:top]:
        typer.echo(
            f"  {row.keyword:<34} heroes {row.heroes:>3}  met {row.weight:>3}  e.g. {', '.join(row.examples[:3])}"
        )
    needs = screens_needed(audits, seen=seen)
    typer.echo(f"The fewest skill popups that describe all of them: {len(needs)}")
    for need in needs[:top]:
        typer.echo(f"  {need.name} S{need.slot}: {', '.join(need.why)}")
    target = out or paths.home / "audit" / "skills_audit.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(to_json(audits), indent=1, ensure_ascii=False), encoding="utf-8")
    typer.echo(f"Written {target}")


def _effective(
    http: CachedHttp,
    name: str,
    amounts: dict[int, dict[str, Scaling]],
    hand: KitOverride | None,
    captured: list[StoredSkill] | None = None,
) -> tuple[Kit, dict[int, int]]:
    return effective_kit(http, name, amounts, hand, lambda h, n: fetch_kit(h, n), captured or ())


def _amounts(session: Session, code: str) -> dict[int, dict[str, Scaling]]:
    return kit_amounts(session, code)


def _met(home: Path) -> list[tuple[str, int]]:
    return met_heroes(home)


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
    if kit.lethal_immortal:
        found.append(f"immortal for {kit.lethal_immortal} turn(s), once, on lethal damage")
    if kit.cr_lock:
        found.append("no Combat Readiness increase sticks during her turn")
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
    if kit.taunt:
        found.append("taunt while in front: enemies' single-target skills can only pick it")
    if kit.hit_cr:
        found.append(f"+{kit.hit_cr:.0%} Combat Readiness each time it is attacked")
    if kit.fragrance:
        found.append(f"reflects {kit.fragrance:.0%} of the Health an ally loses back to the attacker")
    if kit.cr_mirror:
        found.append(f"gains {kit.cr_mirror:.0%} of every Combat Readiness increase of an enemy")
    if kit.ally_low:
        found.append(
            f"an ally left at {kit.ally_low:.0%} Health or less after a hit (every {kit.ally_low_cooldown} turns): "
            + ", ".join(_describe(e) for e in kit.ally_low_effects)
        )
    if kit.offering_death_effects:
        found.append(
            "when the ally granted offering dies: " + ", ".join(_describe(e) for e in kit.offering_death_effects)
        )
    if kit.cr_immune:
        found.append("unaffected by Combat Readiness increases and decreases")
    if kit.start_wait:
        found.append("begins the battle with the full cooldown of " + ", ".join(f"S{slot}" for slot in kit.start_wait))
    if kit.eff_speed:
        found.append(f"Effectiveness +{kit.eff_speed:.0%} of Speed for the whole battle")
    if kit.hit_cap:
        found.append(f"damage suffered in one attack never exceeds {kit.hit_cap:.0%} of max Health")
    if kit.variant_slot:
        found.append(
            f"S1 uses the data of S{kit.variant_slot} while it has a buff, after: "
            + (", ".join(_describe(e) for e in kit.variant_effects) or "nothing")
        )
    if kit.death_cd:
        found.append(f"every cooldown -{kit.death_cd} turn each time a hero dies")
    return found


def _describe(effect: Effect) -> str:
    if effect.kind == "shorten":
        return f"buff durations -{effect.turns} turns {effect.receivers}".strip()
    if effect.kind == "extra_attack":
        chance = f"{effect.chance:.0%} chance of " if effect.chance != 1 else ""
        return f"{chance}an extra attack with S{int(effect.value) or 1}"
    if effect.kind == "reset_cd":
        return f"reset the cooldown of S{effect.value:.0f}" + (f" if {effect.condition}" if effect.condition else "")
    form = f" [form {effect.form}]" if effect.form >= 0 else ""
    return _describe_plain(effect) + form


def _describe_plain(effect: Effect) -> str:
    parts = [effect.kind]
    if effect.kind == "spirit":
        parts.append(f"+{effect.value:g} Fighting Spirit")
    elif effect.kind in ("fixed_damage", "abundance"):
        parts.append(f"{effect.value:,.0f} damage")
    elif effect.value:
        parts.append(f"{effect.value:+.0%}")
    if any(effect.scaling):
        hp, atk, df = effect.scaling
        parts.append("+".join(f"{v:.0%} {s}" for v, s in ((hp, "HP"), (atk, "ATK"), (df, "DEF")) if v))
    if effect.of_target:
        parts.append("of the target's max Health")
    if effect.receivers:
        parts.append(effect.receivers)
    if effect.kind in ("cleanse", "dispel"):
        parts.append(f"x{effect.count or 'all'}")
    if effect.kind == "venom":
        parts.append(f"x{max(1, effect.count)} stacks")
    if effect.debuff and effect.chance != 1:
        parts.append(f"{effect.chance:.0%} chance")
    if effect.condition:
        parts.append(f"if {effect.condition}")
    if effect.kind not in ("heal", "resurrect", "cleanse", "dispel", "cr", "extra_turn", "detonate", "fixed_damage"):
        parts.append(f"{effect.turns}t")
    return " ".join(parts)
