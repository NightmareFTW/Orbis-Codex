"""`e7 opponent`: what an opponent hero probably looks like, from Stove usage statistics (M10)."""

from __future__ import annotations

from typing import Annotated, Final

import typer

from e7ac.catalog.facts import EntityType
from e7ac.catalog.store import current_snapshot, load_entities
from e7ac.cli import catalog as catalog_cli
from e7ac.cli.roster import _engine, _fail, _resolve_hero_code
from e7ac.domain.codes import Stat
from e7ac.domain.world import World
from e7ac.opponents.assumed import QUANTILES, Profile, assumed_build
from e7ac.opponents.histogram import LABEL_BOUNDS
from e7ac.paths import default_paths
from e7ac.settings import SettingsError, load_settings
from e7ac.sources.http import FetchError
from e7ac.sources.stove_usage import Choice, UsageError, fetch_hero_usage
from e7ac.storage.db import session_scope

opponent_app = typer.Typer(help="Opponent heroes: probable builds from Stove's usage statistics.", no_args_is_help=True)

_FLAT: Final = frozenset({Stat.ATK, Stat.DEF, Stat.HP, Stat.SPEED})


@opponent_app.command("show")
def show(
    hero: Annotated[str, typer.Argument(help="Hero code (c2011) or exact catalog name.")],
    profile: Annotated[
        Profile, typer.Option(help="typical = median build, strong = 75th percentile, top = 90th percentile.")
    ] = Profile.TYPICAL,
    world: Annotated[World | None, typer.Option(help="Stove region (default: your setting).")] = None,
    refresh: Annotated[bool, typer.Option("--refresh", help="Ask Stove again even if this week is cached.")] = False,
    offline: Annotated[bool, typer.Option("--offline", help="Use the cache only.")] = False,
) -> None:
    """Show the assumed build of a hero: each stat's percentile among the builds Stove saw this week, and the most
    used sets, EE options and artifacts. Stove counts Lv. 60 6* awakened heroes of all content (not only Arena)."""
    paths = default_paths()
    try:
        region = world or load_settings(paths.settings_file).world
    except SettingsError as exc:
        _fail(str(exc))
    engine = _engine()
    with session_scope(engine) as session:
        code = _resolve_hero_code(session, hero)
        snapshot = current_snapshot(session)
        names: dict[str, str] = {}
        if snapshot is not None:
            for kind in (EntityType.SET, EntityType.ARTIFACT):
                names.update({e.entity_id: e.name for e in load_entities(session, snapshot.id, kind) if e.name})
    with catalog_cli.http_factory(paths, offline) as http:
        try:
            usage = fetch_hero_usage(http, region, code, refresh=refresh)
        except (FetchError, UsageError) as exc:
            _fail(f"Stove usage data for {code}: {exc}")
        now = http.now()
    if usage is None:
        _fail(f"Stove has no usage data for {code} in {region.value} yet")
    build = assumed_build(usage, profile, now)
    size = f"{build.sample_size:,} builds" if build.sample_size is not None else "builds"
    typer.echo(f"{build.hero_name} ({build.hero_code}) - Stove {region.value}, {size}, updated {build.updated[:10]}")
    typer.echo(f"profile {profile.value}: each stat's P{round(100 * QUANTILES[profile])} taken on its own")
    parts = []
    for stat in LABEL_BOUNDS:
        found = build.stats.get(stat)
        if found is not None:
            shown = f"{found.value:.0f}" if stat in _FLAT else f"{found.value * 100:.1f}%"
            parts.append(f"{stat.value} {shown}{'*' if found.open_end else ''}")
    typer.echo("  " + "  ".join(parts))
    if any(s.open_end for s in build.stats.values()):
        typer.echo("  * beyond the range of Stove's chart: the real value may be further out")
    typer.echo(f"  sets:      {_choices(build.sets, names)}")
    typer.echo(f"  EE:        {_choices(build.ee, names)}")
    typer.echo(f"  artifacts: {_choices(build.artifacts, names)}")
    for warning in build.warnings:
        typer.echo(f"  warning: {warning}", err=True)


def _choices(choices: list[Choice], names: dict[str, str]) -> str:
    if not choices:
        return "-"
    out = []
    for choice in choices:
        label = " + ".join(choice.name or names.get(code, code) for code in choice.codes)
        skill = f" (skill {choice.skill})" if choice.skill else ""
        out.append(f"{label}{skill} {choice.share * 100:.1f}%")
    return ", ".join(out)
