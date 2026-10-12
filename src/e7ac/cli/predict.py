"""`e7 predict`: Arena predictions (Phase 3). First slice: who acts first."""

from __future__ import annotations

from typing import Annotated

import typer

from e7ac.cli import catalog as catalog_cli
from e7ac.cli.roster import _engine, _fail, _resolve_hero_code
from e7ac.domain.codes import Stat
from e7ac.domain.world import World
from e7ac.paths import default_paths
from e7ac.predict.turn_order import START_CR_MAX, Unit, first_turns
from e7ac.roster.store import build_from_row, list_owned
from e7ac.settings import SettingsError, load_settings
from e7ac.sources.http import FetchError
from e7ac.sources.stove_usage import UsageError, fetch_hero_usage
from e7ac.storage.db import session_scope

predict_app = typer.Typer(help="Arena predictions (turn order for now).", no_args_is_help=True)


@predict_app.command("order")
def order(
    team: Annotated[str, typer.Option(help='Your heroes, comma separated: "Kawerik, Estelle, c1159".')],
    vs: Annotated[
        str,
        typer.Option(help='Opponents, comma separated; add "=SPEED" when you know it: "c2011, Riolet=231".'),
    ],
    world: Annotated[World | None, typer.Option(help="Stove region for opponents (default: your setting).")] = None,
    offline: Annotated[bool, typer.Option("--offline", help="Use cached Stove data only.")] = False,
) -> None:
    """Who takes the first turn: your heroes' speeds come from your roster; an opponent's speed is drawn from Stove's
    usage statistics (or given with "=SPEED"), so the uncertainty about its build is in the odds."""
    paths = default_paths()
    try:
        region = world or load_settings(paths.settings_file).world
    except SettingsError as exc:
        _fail(str(exc))
    units: list[Unit] = []
    spreads: dict[str, str] = {}
    with session_scope(_engine()) as session:
        owned = list_owned(session)
        for name in _split(team):
            code = _resolve_hero_code(session, name)
            speeds = [
                (build_from_row(session, row).final_stats, o.id) for o, row in owned if o.hero_code == code and row
            ]
            known = [(stats.speed, oid) for stats, oid in speeds if stats is not None]
            if not known:
                _fail(f"{name}: not in your roster with its stats (scan it, or import it first)")
            speed, oid = max(known)
            if len(known) > 1:
                typer.echo(f"note: {name}: you own {len(known)} copies, using the fastest (#{oid})", err=True)
            units.append(Unit(name, "you", float(speed)))
        opponents = [(_resolve_hero_code(session, n.partition("=")[0].strip()), n) for n in _split(vs)]
    with catalog_cli.http_factory(paths, offline) as http:
        for code, entry in opponents:
            label, _, given = (part.strip() for part in entry.partition("="))
            if given:
                try:
                    units.append(Unit(label, "them", float(given)))
                except ValueError:
                    _fail(f"{entry}: the speed after '=' is not a number")
                continue
            try:
                usage = fetch_hero_usage(http, region, code)
            except (FetchError, UsageError) as exc:
                _fail(f"Stove usage data for {code}: {exc}")
            if usage is None:
                _fail(f"{label}: Stove has no usage data for {code}: give its speed as {label}=SPEED")
            histogram = usage.histograms[Stat.SPEED]
            low, high = histogram.percentile(0.1), histogram.percentile(0.9)
            if low is not None and high is not None:
                spreads[label] = f"~{low.value:.0f}-{high.value:.0f} (P10-P90)"
            units.append(Unit(label, "them", histogram.sampler()))
    result = first_turns(units)
    typer.echo(
        f"First turn ({result.runs:,} simulated starts; start CR 0-{START_CR_MAX:.0%} at random, MECH-CR-02; "
        "speed buffs and CR pushes not counted yet)"
    )
    for odds in result.units:
        unit = odds.unit
        shown = spreads.get(unit.label, "Stove") if callable(unit.speed) else f"{unit.speed:g}"
        typer.echo(
            f"  {unit.side:<4} {unit.label:<24} speed {shown:<22} first {odds.first:6.1%}  "
            f"avg position {odds.mean_position:.1f}"
        )
    typer.echo(f"Your side takes the first turn: {result.your_side_first:.1%}")


def _split(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]
