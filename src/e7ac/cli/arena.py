"""`e7 arena scan`: identify both teams on an Arena VS screen capture (M17a, SPEC D64)."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import numpy as np
import typer

from e7ac.arena.identify import Slot, identify
from e7ac.arena.references import ReferenceStore
from e7ac.catalog.resolve import ResolvedEntity
from e7ac.cli import catalog as catalog_cli
from e7ac.cli import predict as predict_cli
from e7ac.cli.roster import _catalog_heroes, _engine, _fail
from e7ac.paths import default_paths
from e7ac.roster.store import list_owned
from e7ac.sources.assets import fetch_class_sprite, load_class_sprite
from e7ac.storage.db import session_scope
from e7ac.vision.arena_badges import ArenaScreenError, BgrImage, class_templates, hero_crop, read_badges
from e7ac.vision.image import ImageError, load_image

arena_app = typer.Typer(help="Arena screens: identify the heroes shown.", no_args_is_help=True)

_SIDES = {"you": "you ", "them": "them"}


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
    are kept as references (local only), so the next scan recognises them."""
    paths = default_paths()
    asking = sys.stdin.isatty() if confirm is None else confirm
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
        frame: BgrImage = np.asarray(load_image(image), np.uint8)
        badges = read_badges(frame, class_templates(sprite))
    except (ImageError, ArenaScreenError) as exc:
        _fail(f"{image.name}: {exc}")
    for side in ("you", "them"):
        count = sum(1 for b in badges if b.side == side)
        if count != 4:
            typer.echo(f"warning: {count} heroes found on the {side} side (4 expected)", err=True)
    crops = [hero_crop(frame, badge, badges) for badge in badges]
    store = ReferenceStore(paths.home / "references" / "arena")
    with session_scope(_engine()) as session:
        heroes = _catalog_heroes(session)
        if not heroes:
            _fail("no catalog yet: run 'e7 catalog sync' first")
        owned = {row.hero_code for row, _ in list_owned(session)}
    slots = identify(
        badges, heroes, owned, store.last_team(), store.codes(), lambda i, code: store.best(code, crops[i])
    )
    _show(slots, heroes)
    if asking:
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
        raise typer.Exit(code=1)
    you = ", ".join(s.code for s in slots if s.badge.side == "you" and s.code)
    them = ", ".join(s.code for s in slots if s.badge.side == "them" and s.code)
    typer.echo(f'Run: e7 predict win --team "{you}" --vs "{them}"')
    if win:
        predict_cli.win(team=you, vs=them, world=None, offline=offline)


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
