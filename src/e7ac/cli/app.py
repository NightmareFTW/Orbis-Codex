"""`e7` command-line entry point. Thin layer: all logic lives in UI-free core modules."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from e7ac import __version__
from e7ac.doctor import CheckStatus, run_checks
from e7ac.paths import default_paths
from e7ac.settings import (
    EDITABLE_KEYS,
    Settings,
    SettingsError,
    allowed_values,
    load_settings,
    save_settings,
    with_value,
)

app = typer.Typer(
    name="e7",
    help="E7 Arena Companion (Orbis Codex): roster tracker and Arena win-probability tools for Epic Seven.",
    add_completion=False,
    pretty_exceptions_enable=False,
)
config_app = typer.Typer(help="Show or change settings (client, region, display).", add_completion=False)
app.add_typer(config_app, name="config")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"e7 (Orbis Codex) {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option("--version", help="Show the version and exit.", callback=_version_callback, is_eager=True),
    ] = False,
) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


@app.command()
def paths() -> None:
    """Show where data, cache, captures and logs are stored."""
    p = default_paths()
    for label, location in (
        ("home", p.home),
        ("settings", p.settings_file),
        ("database", p.database),
        ("cache", p.cache_dir),
        ("captures", p.captures_dir),
        ("logs", p.logs_dir),
    ):
        typer.echo(f"{label:<9} {location}")


@app.command()
def doctor() -> None:
    """Check the environment (Python, OS, data folder, settings)."""
    results = run_checks(default_paths())
    for result in results:
        typer.echo(f"[{result.status.value:<4}] {result.name:<9} {result.detail}")
    if any(result.status is CheckStatus.FAIL for result in results):
        raise typer.Exit(code=1)


@config_app.command("show")
def config_show() -> None:
    """Print the current settings as JSON (defaults when no settings file exists)."""
    settings = _load_or_exit()
    typer.echo(json.dumps(settings.model_dump(mode="json"), indent=2, sort_keys=True))


@config_app.command("set")
def config_set(
    key: Annotated[str, typer.Argument(help=f"One of: {', '.join(EDITABLE_KEYS)}")],
    value: Annotated[str, typer.Argument(help="New value (use 'auto' for resolution auto-detection).")],
) -> None:
    """Change one setting, e.g. `e7 config set world world_eu` or `e7 config set resolution 1920x1080`."""
    app_paths = default_paths()
    current = _load_or_exit()
    try:
        updated = with_value(current, key, value)
    except SettingsError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    save_settings(updated, app_paths.settings_file)
    shown = updated.model_dump(mode="json")[key]
    typer.echo(f"{key} = {shown if shown is not None else 'auto'}")


@config_app.command("options")
def config_options() -> None:
    """List editable settings and their allowed values."""
    for key in EDITABLE_KEYS:
        choices = allowed_values(key)
        typer.echo(f"{key:<13} {', '.join(choices) if choices else 'auto | WIDTHxHEIGHT'}")


def _load_or_exit() -> Settings:
    """Load settings or exit with code 2 and a readable message (never silently reset a broken file)."""
    try:
        return load_settings(default_paths().settings_file)
    except SettingsError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


def main() -> None:
    app()
