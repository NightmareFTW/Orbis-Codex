"""`e7 arena watch` / `e7 arena scan`: identify both teams on the Arena VS screen (M17a, SPEC D64-D65)."""

from __future__ import annotations

import contextlib
import sys
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Final

import cv2
import numpy as np
import typer

from e7ac.arena.identify import Slot, identify
from e7ac.arena.references import ReferenceStore
from e7ac.catalog.resolve import ResolvedEntity
from e7ac.cli import catalog as catalog_cli
from e7ac.cli import predict as predict_cli
from e7ac.cli.roster import _catalog_heroes, _engine, _fail
from e7ac.domain.codes import HeroClass
from e7ac.paths import AppPaths, default_paths
from e7ac.roster.store import list_owned
from e7ac.settings import SettingsError, load_settings
from e7ac.sources.assets import fetch_class_sprite, load_class_sprite
from e7ac.storage.db import session_scope
from e7ac.vision.arena_badges import (
    ArenaScreenError,
    Badge,
    BgrImage,
    Template,
    class_templates,
    hero_crop,
    normalise,
    read_badges,
)
from e7ac.vision.image import ImageError, load_image

arena_app = typer.Typer(help="Arena screens: identify the heroes shown.", no_args_is_help=True)

_SIDES = {"you": "you ", "them": "them"}


@arena_app.command("watch")
def watch(
    hwnd: Annotated[int | None, typer.Option(help="Watch this window (handle from e7 capture --list-windows).")] = None,
    offline: Annotated[bool, typer.Option("--offline", help="Use cached data only.")] = False,
) -> None:
    """The simple way: leave this running and play. Each time the Arena VS screen appears, both teams are read,
    unknown heroes are asked here (once: they are learned), and the chance to win is shown. Read-only: it only
    copies screen pixels, never a key or click to the game. Ctrl+C stops."""
    paths = default_paths()
    try:
        client = load_settings(paths.settings_file).client
    except SettingsError as exc:
        _fail(str(exc))
    templates = _templates(paths, offline)
    heroes, owned = _context()
    store = ReferenceStore(paths.home / "references" / "arena")
    typer.echo("Watching the game for the Arena VS screen (the one before a battle). Ctrl+C stops.")
    previous: list[Badge] = []
    armed = True  # one reading per VS screen: re-armed once the screen has gone
    try:
        for frame in game_frames(client, hwnd):
            if frame is None:
                continue
            image = normalise(frame)
            try:
                badges = read_badges(image, templates)
            except ArenaScreenError:
                badges = []
            complete = sum(b.side == "you" for b in badges) == 4 and sum(b.side == "them" for b in badges) == 4
            if not badges:
                armed = True
            elif armed and complete and _same_layout(badges, previous):  # still for two frames: fully shown
                armed = False
                kept = _keep_capture(paths.captures_dir / "arena", image)
                typer.echo(f"{datetime.now().astimezone():%H:%M:%S} Arena VS screen (capture kept: {kept})")
                codes = _resolve(image, badges, heroes, owned, store, asking=True, always_ask=False)
                if codes is not None:
                    with contextlib.suppress(typer.Exit):  # a failed prediction is reported, the watch goes on
                        predict_cli.win(team=codes[0], vs=codes[1], world=None, offline=offline)
                typer.echo("Watching for the next Arena VS screen... (Ctrl+C stops)")
            previous = badges if complete else []
    except (KeyboardInterrupt, typer.Abort):  # Ctrl+C, also while a question waits
        typer.echo("Stopped.")


@arena_app.command("scan")
def scan(
    image: Annotated[Path, typer.Argument(help="A capture of the Arena VS screen (e7 capture, or a screenshot).")],
    confirm: Annotated[
        bool | None,
        typer.Option("--confirm/--no-confirm", help="Ask about uncertain heroes (default: when run in a terminal)."),
    ] = None,
    win: Annotated[bool, typer.Option("--win", help="Then run 'e7 predict win' with both teams.")] = False,
    offline: Annotated[bool, typer.Option("--offline", help="Use cached data only.")] = False,
) -> None:
    """Read the badge above each hero (element and class), narrow the candidates (your side: your roster and your
    last team) and recognise heroes you confirmed before. Uncertain heroes are asked, never guessed; your answers
    are kept as references (local only), so the next scan recognises them. 'e7 arena watch' does this by itself."""
    paths = default_paths()
    asking = sys.stdin.isatty() if confirm is None else confirm
    templates = _templates(paths, offline)
    try:
        frame = normalise(np.asarray(load_image(image), np.uint8))
        badges = read_badges(frame, templates)
    except (ImageError, ArenaScreenError) as exc:
        _fail(f"{image.name}: {exc}")
    heroes, owned = _context()
    store = ReferenceStore(paths.home / "references" / "arena")
    codes = _resolve(frame, badges, heroes, owned, store, asking=asking, always_ask=True)
    if codes is None:
        raise typer.Exit(code=1)
    typer.echo(f'Run: e7 predict win --team "{codes[0]}" --vs "{codes[1]}"')
    if win:
        predict_cli.win(team=codes[0], vs=codes[1], world=None, offline=offline)


def _game_frames(client: Any, hwnd: int | None) -> Iterator[BgrImage | None]:
    """Endless captures of the game window, one every `INTERVAL` seconds (None while it cannot be captured)."""
    from e7ac.cli import capture as capture_cli
    from e7ac.cli.roster import _frame_image
    from e7ac.vision.capture import CaptureError
    from e7ac.vision.window import PROFILES, WindowError, select_game_window

    capture_cli.prepare_process()
    backend = capture_cli.backend_factory()
    problem = ""
    while True:
        try:
            window = select_game_window(capture_cli.window_lister(), PROFILES[client], hwnd)
            frame = backend.grab(window)
        except (WindowError, CaptureError) as exc:
            if str(exc) != problem:  # said once, not at every capture
                typer.echo(f"  waiting: {exc}", err=True)
            problem = str(exc)
            yield None
        else:
            problem = ""
            yield np.asarray(_frame_image(frame), np.uint8)
        arena_sleep(INTERVAL)


INTERVAL: Final = 0.5
game_frames: Callable[[Any, int | None], Iterator[BgrImage | None]] = _game_frames
arena_sleep: Callable[[float], None] = time.sleep


def _templates(paths: AppPaths, offline: bool) -> dict[HeroClass, Template]:
    sprite = load_class_sprite(paths.cache_dir)
    if sprite is None and not offline:
        with catalog_cli.http_factory(paths, offline) as http:
            reason = fetch_class_sprite(http)
        sprite = load_class_sprite(paths.cache_dir)
        if sprite is None:
            _fail(f"class icons not available ({reason}): connect once to download them")
    if sprite is None:
        _fail("class icons not cached yet: run once without --offline")
    try:
        return class_templates(sprite)
    except ArenaScreenError as exc:
        _fail(str(exc))


def _context() -> tuple[dict[str, ResolvedEntity], set[str]]:
    with session_scope(_engine()) as session:
        heroes = _catalog_heroes(session)
        if not heroes:
            _fail("no catalog yet: run 'e7 catalog sync' first")
        owned = {row.hero_code for row, _ in list_owned(session)}
    return heroes, owned


def _resolve(
    frame: BgrImage,
    badges: list[Badge],
    heroes: dict[str, ResolvedEntity],
    owned: set[str],
    store: ReferenceStore,
    *,
    asking: bool,
    always_ask: bool,
) -> tuple[str, str] | None:
    """Identify, ask and learn: (your codes, their codes) comma separated, or None when a hero stays unknown."""
    for side in ("you", "them"):
        count = sum(1 for b in badges if b.side == side)
        if count != 4:
            typer.echo(f"warning: {count} heroes found on the {side} side (4 expected)", err=True)
    crops = [hero_crop(frame, badge, badges) for badge in badges]
    slots = identify(
        badges, heroes, owned, store.last_team(), store.codes(), lambda i, code: store.best(code, crops[i])
    )
    _show(slots, heroes)
    if asking and (always_ask or any(s.code is None for s in slots)):
        _ask(slots, heroes)
        when = datetime.now(UTC)
        for slot, crop in zip(slots, crops, strict=True):
            if slot.code is not None:
                store.add(slot.code, crop, when)
        team = [s.code for s in slots if s.badge.side == "you" and s.code]
        if team:
            store.save_last_team(team)
    unresolved = [i for i, s in enumerate(slots, 1) if s.code is None]
    if unresolved:
        typer.echo(f"Not identified: slot(s) {', '.join(map(str, unresolved))}", err=True)
        return None
    you = ", ".join(s.code for s in slots if s.badge.side == "you" and s.code)
    them = ", ".join(s.code for s in slots if s.badge.side == "them" and s.code)
    return you, them


def _same_layout(badges: list[Badge], previous: list[Badge]) -> bool:
    return len(badges) == len(previous) and all(
        abs(a.cx - b.cx) <= 0.5 * a.h and abs(a.cy - b.cy) <= 0.5 * a.h for a, b in zip(badges, previous, strict=True)
    )


def _keep_capture(folder: Path, image: BgrImage) -> Path:
    from e7ac.vision.capture import capture_name

    folder.mkdir(parents=True, exist_ok=True)
    path = folder / capture_name(datetime.now().astimezone(), "vs", lambda n: (folder / n).exists())
    cv2.imwrite(str(path), image)
    return path


def _show(slots: list[Slot], heroes: dict[str, ResolvedEntity]) -> None:
    for number, slot in enumerate(slots, 1):
        badge = slot.badge
        what = f"{badge.element.display} {badge.hero_class.display}"
        if slot.code is not None:
            hero = f"{_name(heroes, slot.code)} ({slot.code}) [{slot.method}]"
        else:
            hero = f"? {len(slot.candidates)} candidates"
        typer.echo(f"  {number}. {_SIDES[badge.side]}  {what:<22} {hero}")


def _ask(slots: list[Slot], heroes: dict[str, ResolvedEntity]) -> None:
    for number, slot in enumerate(slots, 1):
        if slot.code is None:
            _choose(number, slot, slots, heroes)
    while True:
        answer = typer.prompt("All right? Enter = yes, or the slot number(s) to fix", default="", show_default=False)
        numbers = [int(part) for part in answer.replace(",", " ").split() if part.isdigit()]
        fix = [n for n in numbers if 1 <= n <= len(slots)]
        if not fix:
            return
        for n in fix:
            _choose(n, slots[n - 1], slots, heroes)
        _show(slots, heroes)


def _choose(number: int, slot: Slot, slots: list[Slot], heroes: dict[str, ResolvedEntity]) -> None:
    taken = {s.code for s in slots if s is not slot and s.badge.side == slot.badge.side and s.code}
    options = [code for code in slot.candidates if code not in taken]
    badge = slot.badge
    typer.echo(f"Slot {number} ({badge.side}, {badge.element.display} {badge.hero_class.display}):")
    for i, code in enumerate(options, 1):
        score = f"  [{slot.scores[code]} matching points]" if code in slot.scores else ""
        typer.echo(f"  {i:>2}. {_name(heroes, code)} ({code}){score}")
    while True:
        answer = typer.prompt("Number, exact name or code (Enter = skip)", default="", show_default=False).strip()
        if not answer:
            slot.code, slot.method = None, "review"
            return
        chosen = _pick(answer, options, heroes)
        if chosen is not None:
            slot.code, slot.method = chosen, "confirmed"
            return
        typer.echo("  not one of the candidates above")


def _pick(answer: str, options: list[str], heroes: dict[str, ResolvedEntity]) -> str | None:
    if answer.isdigit():
        index = int(answer)
        return options[index - 1] if 1 <= index <= len(options) else None
    lowered = answer.lower()
    exact = [code for code in options if code == lowered or _name(heroes, code).lower() == lowered]
    return exact[0] if len(exact) == 1 else None


def _name(heroes: dict[str, ResolvedEntity], code: str) -> str:
    hero = heroes.get(code)
    return hero.name if hero is not None and hero.name else code
