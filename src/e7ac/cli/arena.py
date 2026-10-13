"""`e7 arena watch` / `e7 arena scan`: identify both teams on the Arena VS screen (M17a, SPEC D64-D65)."""

from __future__ import annotations

import contextlib
import itertools
import queue
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
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
from e7ac.cli.roster import _catalog_heroes, _engine, _fail, _resolve_hero_code
from e7ac.domain.codes import HeroClass
from e7ac.paths import AppPaths, default_paths
from e7ac.roster.store import list_owned
from e7ac.settings import SettingsError, load_settings
from e7ac.sources.assets import fetch_class_sprite, fetch_hero_icons, load_class_sprite, load_hero_icons
from e7ac.storage.db import session_scope
from e7ac.ui.watch_signal import AMBER, GREEN, LIGHT, RED, NoSignal, WatchSignal, open_signal
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
from e7ac.vision.arena_guide import Face, FaceMatcher
from e7ac.vision.image import ImageError, load_image
from e7ac.vision.window import Rect

arena_app = typer.Typer(help="Arena screens: identify the heroes shown.", no_args_is_help=True)

_SIDES = {"you": "you ", "them": "them"}


@arena_app.command("watch")
def watch(
    hwnd: Annotated[int | None, typer.Option(help="Watch this window (handle from e7 capture --list-windows).")] = None,
    offline: Annotated[bool, typer.Option("--offline", help="Use cached data only.")] = False,
    signal: Annotated[bool, typer.Option(help="Show what to do, and the result, on a small badge on the game.")] = True,
) -> None:
    """The simple way: leave this running and play. On the Arena VS screen your team is read; tap the magnifier
    (Attack Guide) and the opponent's team is recognised from its face icons; the chance to win is then shown here
    and on the badge. To try another opponent (or another team): change it in the game, then click "Check again" on
    the badge. Read-only: it only copies screen pixels, never a key or click to the game. Ctrl+C stops."""
    paths = default_paths()
    try:
        client = load_settings(paths.settings_file).client
    except SettingsError as exc:
        _fail(str(exc))
    templates = _templates(paths, offline)
    heroes, owned = _context()
    face_store = ReferenceStore(paths.home / "references" / "faces")
    faces = _faces(paths, heroes, offline, face_store)
    store = ReferenceStore(paths.home / "references" / "arena")
    answers: queue.Queue[str] = queue.Queue()  # names typed in the window, read in the background
    retry = threading.Event()  # the badge's "Check again" button
    badge, why = signal_factory(retry.set) if signal else (NoSignal(), "")
    if why:
        typer.echo(f"note: {why}", err=True)
    match = _Match(team=store.last_team())
    badge.say("Arena: open a match", "the VS screen, before the battle", LIGHT)
    typer.echo("Watching the game for the Arena VS screen and the Attack Guide (magnifier). Ctrl+C stops.")
    try:
        for item in game_frames(client, hwnd):
            if retry.is_set():
                retry.clear()
                _again(match, badge)
            if item is not None:
                frame, area = item
                badge.place(area)
                context = _Context(templates, faces, face_store, heroes, owned, store, badge, offline, paths, answers)
                _step(normalise(frame), match, context)
    except (KeyboardInterrupt, typer.Abort):  # Ctrl+C, also while a question waits
        typer.echo("Stopped.")
    badge.close()


@dataclass(slots=True)
class _Match:
    """What the watch knows about the current Arena match."""

    team: list[str] = field(default_factory=list)
    """Your heroes (codes), from the VS screen or your last team."""
    vs: tuple[BgrImage, list[Badge]] | None = None
    """The last VS screen read (its opponent crops are learned once the Attack Guide names them)."""
    previous: list[Badge] = field(default_factory=list)
    vs_armed: bool = True
    """One reading per VS screen: re-armed once the screen has gone; the same for the Attack Guide."""
    seen_faces: tuple[str | None, ...] = ()
    guide_armed: bool = True
    vs_theirs: list[str] = field(default_factory=list)
    """The opponent as the last VS screen named it (all four, or nothing)."""
    pending: list[Face] = field(default_factory=list)
    """Attack Guide faces waiting for a name typed in the window."""
    pending_codes: list[str | None] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _Context:
    templates: dict[HeroClass, Template]
    faces: FaceMatcher
    face_store: ReferenceStore
    heroes: dict[str, ResolvedEntity]
    owned: set[str]
    store: ReferenceStore
    badge: WatchSignal
    offline: bool
    paths: AppPaths
    answers: queue.Queue[str]


def _again(match: _Match, badge: WatchSignal) -> None:
    """The "Check again" button (another opponent, or another team of yours): read the VS screen and the Attack Guide
    anew. The opponent is never carried over: only the Attack Guide or its learned VS models name it."""
    match.vs_armed = match.guide_armed = True
    match.previous, match.seen_faces = [], ()
    badge.say("Checking again...", "VS screen, then the magnifier", AMBER)
    typer.echo("Check again: reading the VS screen anew.")


def _step(image: BgrImage, match: _Match, c: _Context) -> None:
    if match.pending and not c.answers.empty():
        _answer(c.answers.get(), match, c)
    try:
        badges = read_badges(image, c.templates)
    except ArenaScreenError:
        badges = []
    complete = sum(b.side == "you" for b in badges) == 4 and sum(b.side == "them" for b in badges) == 4
    if not badges:
        match.vs_armed = True
    elif match.vs_armed and complete and _same_layout(badges, match.previous):  # still for two frames
        match.vs_armed = False
        kept = _keep_capture(c.paths.captures_dir / "arena", image)
        typer.echo(f"{datetime.now().astimezone():%H:%M:%S} Arena VS screen (capture kept: {kept})")
        slots = _resolve(image, badges, c.heroes, c.owned, c.store, asking=True, always_ask=False, sides=("you",))
        match.vs = (image, badges)
        team = [s.code for s in slots if s.badge.side == "you" and s.code]
        theirs = [s.code for s in slots if s.badge.side == "them" and s.code]
        match.vs_theirs = theirs if len(theirs) == 4 else []
        if len(team) == 4:
            match.team = team
        if len(team) == 4 and len(theirs) == 4:  # the opponent's VS models were learned before
            _predict(match.team, theirs, c)
        elif len(team) == 4:
            c.badge.say("Tap the magnifier", "to read the opponent's team", AMBER)
            typer.echo("  -> tap the magnifier (Attack Guide) in the game to read the opponent's team")
        else:
            c.badge.say("Your team is not known", "answer in the Arena window", RED)
    match.previous = badges if complete else []
    try:
        read = c.faces.read(image)
    except ArenaScreenError:
        read = []
    codes = tuple(f.code for f in read)
    known = sum(code is not None for code in codes)
    if known < 2 and not (_four_in_a_row(read) and (known or match.vs is not None)):  # not the Attack Guide
        match.guide_armed, match.seen_faces = True, ()
        return
    if not match.guide_armed or codes != match.seen_faces:  # read twice the same: fully shown
        match.seen_faces = codes
        return
    match.guide_armed = False
    typer.echo(
        f"{datetime.now().astimezone():%H:%M:%S} Attack Guide: " + ", ".join(_face_line(f, c.heroes) for f in read)
    )
    names: list[str | None] = list(codes)
    unknown = [i for i, code in enumerate(names) if code is None]
    left = [code for code in match.vs_theirs if code not in names]
    if len(unknown) == 1 and len(left) == 1:  # by elimination: the VS screen named all four
        names[unknown[0]] = left[0]
        _learn_face(read[unknown[0]], left[0], c)
        typer.echo(f"  face {unknown[0] + 1} is {_name(c.heroes, left[0])} (from the VS screen): learned")
    if None in names and match.vs is not None:
        _by_badges(names, read, match.vs[1], c)
    if None in names:
        _keep_capture(c.paths.captures_dir / "arena", image)
        match.pending, match.pending_codes = read, names
        c.badge.say("Opponent hero not recognised", "type its name in the Arena window", RED)
        _ask_next(match, c)
        return
    _guide_done([n for n in names if n], match, c)


def _four_in_a_row(read: list[Face]) -> bool:
    """Four round icons of one size, evenly spaced: the Attack Guide's defence team, even with no face known yet."""
    if len(read) != 4:
        return False
    gaps = [b.x - a.x for a, b in itertools.pairwise(read)]
    mean = sum(gaps) / len(gaps)
    return all(abs(g - mean) <= 0.15 * mean for g in gaps) and max(f.r for f in read) <= 1.2 * min(f.r for f in read)


def _by_badges(names: list[str | None], read: list[Face], badges: list[Badge], c: _Context) -> None:
    """Faces not recognised, compared again only with the heroes of the VS badges left over (their element and class,
    SPEC D70), when the VS screen shows this opponent (the recognised faces fit its badges)."""
    left = [(b.element.value, b.hero_class.value) for b in badges if b.side == "them"]
    for code in names:
        shape = _shape([code], c.heroes) if code else []
        if shape and shape[0] in left:
            left.remove(shape[0])
        elif code:
            return  # a recognised face the VS screen does not show: another opponent's VS screen
    for index, face in enumerate(read):
        if names[index] is not None:
            continue
        group = [code for code in c.heroes if _shape([code], c.heroes)[0] in left]
        code, score, margin = c.faces.among(face, group)
        if code is None:
            continue
        names[index] = code
        left.remove(_shape([code], c.heroes)[0])
        _learn_face(face, code, c)
        typer.echo(
            f"  face {index + 1} is {_name(c.heroes, code)} ({score:.2f}, {margin:.2f} ahead among the {len(group)} "
            f"heroes the VS badges allow): learned"
        )


def _ask_next(match: _Match, c: _Context) -> None:
    number = match.pending_codes.index(None) + 1
    typer.echo(
        f"Opponent hero {number} not recognised: type its exact name (or code) and press Enter, or just Enter to skip. "
        "The watch goes on meanwhile."
    )
    listen(c.answers)


def _answer(line: str, match: _Match, c: _Context) -> None:
    """A name typed for the first face not recognised: learned, then the next one is asked, or the check is done."""
    index = match.pending_codes.index(None)
    if not line:
        match.pending, match.pending_codes = [], []
        c.badge.say("Opponent not recognised", "skipped: no prediction", RED)
        return
    found = _hero_by_name(line, c.heroes)
    if found is None:
        typer.echo("  not one hero of the catalog (newer heroes: run e7 catalog sync)")
        _ask_next(match, c)
        return
    match.pending_codes[index] = found
    _learn_face(match.pending[index], found, c)
    typer.echo(f"  face {index + 1} is {_name(c.heroes, found)}: learned")
    if None in match.pending_codes:
        _ask_next(match, c)
        return
    names = [n for n in match.pending_codes if n]
    match.pending, match.pending_codes = [], []
    _guide_done(names, match, c)


def _guide_done(theirs: list[str], match: _Match, c: _Context) -> None:
    if match.vs is not None:
        _learn(match.vs, theirs, c.heroes, c.store)
    if len(match.team) != 4:
        c.badge.say("Your team is not known", "open the VS screen again", RED)
        typer.echo("Your team is not known yet: go back to the VS screen.", err=True)
    else:
        _predict(match.team, theirs, c)


def _learn_face(face: Face, code: str, c: _Context) -> None:
    """A face named by the user or by elimination: kept (local only) and recognised directly from now on."""
    if face.crop is not None:
        c.faces.learn(code, face.crop)
        c.face_store.add(code, face.crop, datetime.now(UTC))


def _hero_by_name(answer: str, heroes: dict[str, ResolvedEntity]) -> str | None:
    lowered = answer.strip().lower()
    if lowered in heroes:
        return lowered
    found = [code for code, hero in heroes.items() if (hero.name or "").lower() == lowered]
    return found[0] if len(found) == 1 else None


def _listen(answers: queue.Queue[str]) -> None:
    """One line typed in the window, read in the background so the watch goes on."""

    def read() -> None:
        with contextlib.suppress(OSError, ValueError):
            answers.put(sys.stdin.readline().strip())

    threading.Thread(target=read, name="e7-arena-answer", daemon=True).start()


listen: Callable[[queue.Queue[str]], None] = _listen


def _face_line(face: Face, heroes: dict[str, ResolvedEntity]) -> str:
    return _name(heroes, face.code) if face.code else f"? (closest {_name(heroes, face.candidate)} {face.score:.2f})"


def _learn(
    vs: tuple[BgrImage, list[Badge]], theirs: list[str], heroes: dict[str, ResolvedEntity], store: ReferenceStore
) -> None:
    """The VS screen's opponent models, named by the Attack Guide: kept as references when the badge's element and
    class point to exactly one of the named heroes (so the VS screen alone recognises them next time)."""
    image, badges = vs
    shown = sorted((b.element.value, b.hero_class.value) for b in badges if b.side == "them")
    if shown != _shape(theirs, heroes):  # the VS screen read was of another opponent: nothing learned
        return
    when = datetime.now(UTC)
    for badge in (b for b in badges if b.side == "them"):
        fits = [
            c
            for c in theirs
            if heroes[c].value("element") == badge.element.value and heroes[c].value("role") == badge.hero_class.value
        ]
        if len(fits) == 1:
            store.add(fits[0], hero_crop(image, badge, badges), when)


def _predict(team: list[str], theirs: list[str], c: _Context) -> None:
    names = ", ".join(_name(c.heroes, code) for code in theirs)
    try:
        odds = predict_cli.win_odds(", ".join(team), ", ".join(theirs), None, c.offline)
    except typer.Exit:
        c.badge.say("No prediction", "see the Arena window", RED)
        return
    colour = GREEN if odds.win >= 0.6 else RED if odds.win <= 0.4 else AMBER
    c.badge.say(f"Win chance {odds.win:.0%}", f"vs {names}"[:48], colour)
    typer.echo("Watching for the next Arena match... (Ctrl+C stops)")


def _faces(
    paths: AppPaths, heroes: dict[str, ResolvedEntity], offline: bool, face_store: ReferenceStore
) -> FaceMatcher:
    """The face icons of every catalog hero (downloaded once, one per second, then read from the cache), and the faces
    learned before."""
    missing = set(heroes) - set(load_hero_icons(paths.cache_dir, heroes))
    if missing and not offline:
        typer.echo(f"Downloading {len(missing)} hero face icons, only once (about {len(missing) // 55 + 1} min)...")
        with catalog_cli.http_factory(paths, offline) as http:
            failed = fetch_hero_icons(
                http, missing, progress=lambda d, t: typer.echo(f"  {d}/{t}") if d % 50 == 0 else None
            )
        if failed:
            typer.echo(f"note: {len(failed)} face icons not available: those heroes are asked", err=True)
    return FaceMatcher.from_png(load_hero_icons(paths.cache_dir, heroes), face_store.crops())


@arena_app.command("forget")
def forget(
    hero: Annotated[str, typer.Argument(help='The hero named wrongly: exact name or code, e.g. "Maid Chloe".')],
) -> None:
    """Forget what was learned for a hero (its Attack Guide faces and VS screen models), e.g. after typing a wrong
    name: the next time that face or model appears it is asked again. Restart a running watch afterwards."""
    paths = default_paths()
    with session_scope(_engine()) as session:
        code = _resolve_hero_code(session, hero)
        heroes = _catalog_heroes(session) or {}
    removed = 0
    for kind in ("faces", "arena"):
        for path in sorted((paths.home / "references" / kind / code).glob("*.png")):
            path.unlink(missing_ok=True)
            removed += 1
    store = ReferenceStore(paths.home / "references" / "arena")
    team = store.last_team()
    if code in team:
        store.save_last_team([c for c in team if c != code])
    typer.echo(f"{_name(heroes, code)} ({code}): {removed} learned picture(s) forgotten.")


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
    slots = _resolve(frame, badges, heroes, owned, store, asking=asking, always_ask=True)
    unresolved = [i for i, s in enumerate(slots, 1) if s.code is None]
    if unresolved:
        typer.echo(f"Not identified: slot(s) {', '.join(map(str, unresolved))}", err=True)
        raise typer.Exit(code=1)
    you = ", ".join(s.code for s in slots if s.badge.side == "you" and s.code)
    them = ", ".join(s.code for s in slots if s.badge.side == "them" and s.code)
    typer.echo(f'Run: e7 predict win --team "{you}" --vs "{them}"')
    if win:
        predict_cli.win(team=you, vs=them, world=None, offline=offline)


def _game_frames(client: Any, hwnd: int | None) -> Iterator[tuple[BgrImage, Rect] | None]:
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
            yield np.asarray(_frame_image(frame), np.uint8), frame.window.client
        arena_sleep(INTERVAL)


INTERVAL: Final = 0.5
game_frames: Callable[[Any, int | None], Iterator[tuple[BgrImage, Rect] | None]] = _game_frames


def _open_badge(on_click: Callable[[], None]) -> tuple[WatchSignal, str]:
    return open_signal(False, ("Check\nagain", on_click))


def _shape(codes: list[str], heroes: dict[str, ResolvedEntity]) -> list[tuple[str, str]]:
    """Elements and classes of these heroes, sorted (what a VS screen shows of a team)."""
    return sorted((str(heroes[c].value("element")), str(heroes[c].value("role"))) for c in codes if c in heroes)


signal_factory: Callable[[Callable[[], None]], tuple[WatchSignal, str]] = _open_badge
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
    sides: tuple[str, ...] = ("you", "them"),
) -> list[Slot]:
    """Identify, ask (only about `sides`) and learn what was confirmed."""
    for side in ("you", "them"):
        count = sum(1 for b in badges if b.side == side)
        if count != 4:
            typer.echo(f"warning: {count} heroes found on the {side} side (4 expected)", err=True)
    crops = [hero_crop(frame, badge, badges) for badge in badges]
    slots = identify(
        badges, heroes, owned, store.last_team(), store.codes(), lambda i, code: store.best(code, crops[i])
    )
    _show(slots, heroes)
    open_question = any(s.code is None and s.badge.side in sides for s in slots)
    if asking and (always_ask or open_question):
        _ask(slots, heroes, sides)
        when = datetime.now(UTC)
        for slot, crop in zip(slots, crops, strict=True):
            if slot.code is not None and slot.badge.side in sides:
                store.add(slot.code, crop, when)
        team = [s.code for s in slots if s.badge.side == "you" and s.code]
        if len(team) == 4:
            store.save_last_team(team)
    return slots


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


def _ask(slots: list[Slot], heroes: dict[str, ResolvedEntity], sides: tuple[str, ...] = ("you", "them")) -> None:
    for number, slot in enumerate(slots, 1):
        if slot.code is None and slot.badge.side in sides:
            _choose(number, slot, slots, heroes)
    while True:
        answer = typer.prompt("All right? Enter = yes, or the slot number(s) to fix", default="", show_default=False)
        numbers = [int(part) for part in answer.replace(",", " ").split() if part.isdigit()]
        fix = [n for n in numbers if 1 <= n <= len(slots) and slots[n - 1].badge.side in sides]
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
