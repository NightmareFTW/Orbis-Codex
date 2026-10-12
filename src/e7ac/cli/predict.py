"""`e7 predict`: Arena predictions (Phase 3). First slice: who acts first."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Any

import typer

from e7ac.catalog.facts import EntityType
from e7ac.catalog.resolve import ResolvedEntity
from e7ac.catalog.store import current_snapshot, load_entity
from e7ac.cli import catalog as catalog_cli
from e7ac.cli.roster import _catalog_heroes, _engine, _fail, _resolve_hero_code, _screen_catalog
from e7ac.domain.codes import Stat
from e7ac.domain.roster import HeroBuild
from e7ac.domain.world import World
from e7ac.opponents.assumed import Profile, assumed_build
from e7ac.paths import default_paths
from e7ac.predict.damage import SkillPower, hits_to_defeat, skill_power
from e7ac.predict.turn_order import START_CR_MAX, Unit, first_turns
from e7ac.roster.composition import compose_final_stats
from e7ac.roster.screen_gear import ScreenCatalog
from e7ac.roster.store import build_from_row, list_owned
from e7ac.settings import SettingsError, load_settings
from e7ac.sources.http import FetchError
from e7ac.sources.stove_usage import UsageError, fetch_hero_usage
from e7ac.storage.db import session_scope

predict_app = typer.Typer(help="Arena predictions: turn order, hits to defeat.", no_args_is_help=True)


_TEAM = typer.Option(help='Your heroes, comma separated: "Kawerik, Estelle, c1159".')
_VS = typer.Option(help='Opponents, comma separated; add "=SPEED" when you know it: "c2011, Arbiter Vildred=231".')
_WORLD = typer.Option(help="Stove region for opponents (default: your setting).")
_OFFLINE = typer.Option("--offline", help="Use cached Stove data only.")


@dataclass(slots=True)
class _Fighter:
    label: str
    side: str
    code: str
    stats: dict[Stat, float] = field(default_factory=dict)
    """Known stats (yours) or the typical opponent build (Stove P50)."""
    speed: Any = None
    """A number, or a sampler of the opponent's likely speeds."""
    spread: str = ""
    skills: dict[int, SkillPower] = field(default_factory=dict)


@predict_app.command("order")
def order(
    team: Annotated[str, _TEAM],
    vs: Annotated[str, _VS],
    world: Annotated[World | None, _WORLD] = None,
    offline: Annotated[bool, _OFFLINE] = False,
) -> None:
    """Who takes the first turn: your heroes' speeds come from your roster; an opponent's speed is drawn from Stove's
    usage statistics (or given with "=SPEED"), so the uncertainty about its build is in the odds."""
    fighters = _fighters(team, vs, world, offline)
    result = first_turns([Unit(f.label, f.side, f.speed) for f in fighters])
    spreads = {f.label: f.spread for f in fighters}
    typer.echo(
        f"First turn ({result.runs:,} simulated starts; start CR 0-{START_CR_MAX:.0%} at random, MECH-CR-02; "
        "speed buffs and CR pushes not counted yet)"
    )
    for odds in result.units:
        unit = odds.unit
        shown = spreads.get(unit.label) or "Stove" if callable(unit.speed) else f"{unit.speed:g}"
        typer.echo(
            f"  {unit.side:<4} {unit.label:<24} speed {shown:<22} first {odds.first:6.1%}  "
            f"avg position {odds.mean_position:.1f}"
        )
    typer.echo(f"Your side takes the first turn: {result.your_side_first:.1%}")


@predict_app.command("hits")
def hits(
    team: Annotated[str, _TEAM],
    vs: Annotated[str, _VS],
    world: Annotated[World | None, _WORLD] = None,
    offline: Annotated[bool, _OFFLINE] = False,
) -> None:
    """How many hits of S1 / S3 each hero needs to defeat each enemy (expected damage, MECH-DMG-01/02/05). Opponents
    use their typical (median) Stove build. Not counted yet: buffs, combat set effects, artifacts, elements..."""
    fighters = _fighters(team, vs, world, offline)
    yours = [f for f in fighters if f.side == "you"]
    theirs = [f for f in fighters if f.side == "them"]
    typer.echo("Hits to defeat, with S1 / S3 (skills fully enhanced; ~ = skill only partly modelled):")
    _table("Your hits", yours, theirs)
    _table("Their hits", theirs, yours)


def _table(title: str, attackers: list[_Fighter], defenders: list[_Fighter]) -> None:
    typer.echo(f"  {title:<24}" + "".join(f"{'vs ' + d.label:<24}" for d in defenders))
    for attacker in attackers:
        cells = []
        for defender in defenders:
            parts = []
            for slot in (1, 3):
                skill = attacker.skills.get(slot)
                if skill is None or not attacker.stats or not defender.stats:
                    parts.append("-")
                else:
                    parts.append(
                        f"{'~' if skill.approximate else ''}{hits_to_defeat(attacker.stats, skill, defender.stats)}"
                    )
            cells.append(" / ".join(parts))
        typer.echo(f"  {attacker.label:<24}" + "".join(f"{c:<24}" for c in cells))


def _fighters(team: str, vs: str, world: World | None, offline: bool) -> list[_Fighter]:
    paths = default_paths()
    try:
        region = world or load_settings(paths.settings_file).world
    except SettingsError as exc:
        _fail(str(exc))
    fighters: list[_Fighter] = []
    with session_scope(_engine()) as session:
        owned = list_owned(session)
        heroes = _catalog_heroes(session) or {}
        catalog, _ = _screen_catalog(session)
        snapshot = current_snapshot(session)
        for name in _split(team):
            code = _resolve_hero_code(session, name)
            builds = [(build_from_row(session, row), o.id) for o, row in owned if o.hero_code == code and row]
            if not builds:
                _fail(f"{name}: not in your roster (scan it, or import it first)")
            known: list[tuple[float, int, dict[Stat, float], str]] = []
            reasons = []
            for build, oid in builds:
                stats, how = _stats(build, heroes.get(code), catalog)
                if stats is None:
                    reasons.append(f"#{oid}: {how}")
                else:
                    known.append((stats[Stat.SPEED], oid, stats, how))
            if not known:
                _fail(f"{name}: no stats known ({'; '.join(reasons)}): scan its Hero Info or Equipment tab")
            speed, oid, stats, how = max(known, key=lambda k: (k[0], k[1]))
            if len(builds) > 1:
                typer.echo(f"note: {name}: you own {len(builds)} copies, using the fastest (#{oid})", err=True)
            if how:
                typer.echo(f"note: {name}: stats {how}", err=True)
            fighters.append(_Fighter(name, "you", code, stats, speed))
        for entry in _split(vs):
            label, _, given = (part.strip() for part in entry.partition("="))
            fighters.append(_Fighter(label, "them", _resolve_hero_code(session, label), speed=given or None))
        for fighter in fighters:
            if snapshot is not None:
                for slot in (1, 3):
                    entity = load_entity(session, snapshot.id, EntityType.SKILL, f"{fighter.code}:s{slot}")
                    power = skill_power(entity) if entity is not None else None
                    if power is not None:
                        fighter.skills[slot] = power
    with catalog_cli.http_factory(paths, offline) as http:
        for fighter in (f for f in fighters if f.side == "them"):
            try:
                usage = fetch_hero_usage(http, region, fighter.code)
            except (FetchError, UsageError) as exc:
                _fail(f"Stove usage data for {fighter.code}: {exc}")
            if usage is None:
                if fighter.speed is None:
                    _fail(f"{fighter.label}: Stove has no usage data: give its speed as {fighter.label}=SPEED")
                fighter.speed = _given_speed(fighter)
                continue
            typical = assumed_build(usage, Profile.TYPICAL, http.now())
            fighter.stats = {stat: s.value for stat, s in typical.stats.items()}
            if fighter.speed is not None:
                fighter.speed = _given_speed(fighter)
                continue
            histogram = usage.histograms[Stat.SPEED]
            low, high = histogram.percentile(0.1), histogram.percentile(0.9)
            if low is not None and high is not None:
                fighter.spread = f"~{low.value:.0f}-{high.value:.0f} (P10-P90)"
            fighter.speed = histogram.sampler()
    return fighters


def _given_speed(fighter: _Fighter) -> float:
    try:
        return float(fighter.speed)
    except ValueError:
        _fail(f"{fighter.label}: the speed after '=' is not a number")


def _stats(
    build: HeroBuild, hero: ResolvedEntity | None, catalog: ScreenCatalog
) -> tuple[dict[Stat, float] | None, str]:
    """(displayed stats, "" when read from the screen, else how they were obtained), or (None, why not)."""
    if build.final_stats is not None:
        f = build.final_stats
        return {
            Stat.ATK: f.atk,
            Stat.DEF: f.defense,
            Stat.HP: f.hp,
            Stat.SPEED: f.speed,
            Stat.CRIT_CHANCE: f.crit_chance,
            Stat.CRIT_DAMAGE: f.crit_damage,
        }, ""
    if hero is None:
        return None, "no catalog entry to compute them from gear (run: e7 catalog sync)"
    artifact = catalog.artifacts.get(build.artifact.code) if build.artifact is not None else None
    composed = compose_final_stats(build, hero, artifact=artifact, sets=catalog.sets)
    if isinstance(composed, str):
        return None, composed
    stats = {stat: round(value, 1) for stat, value in composed.values.items()}
    approx = ", approximate" if composed.uncertain else ""
    return stats, f"computed from its gear (no stats read from the screen{approx}); speed {stats[Stat.SPEED]:g}"


def _split(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]
