"""`e7 skills`: learn a hero's skills from the game's own skill popup (SPEC D91).

In the game, hold a skill icon: the popup shows the skill's description at the hero's enhancement level, the soulburn
variant, the enhancement steps and one panel for each effect the text names (Fear, Offering, Venom, ...). `e7 skills
watch` (or the Dev page of `e7 app`) reads it by itself while you hold each skill, so nothing has to be typed or
screenshotted; `e7 skills read` reads captures already taken. Read-only towards the game: screen pixels only, never a
key or a click. What was read feeds the kits at once (`e7 kit show`, `e7 predict win`) and `e7 skills export` writes
one text file to send."""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Final

import typer

from e7ac.arena.met import met_heroes
from e7ac.catalog.resolve import ResolvedEntity
from e7ac.cli import catalog as catalog_cli
from e7ac.cli import roster as roster_cli
from e7ac.cli.roster import _catalog_heroes, _engine, _fail
from e7ac.paths import AppPaths, default_paths
from e7ac.roster.screen_import import resolve_hero_code
from e7ac.settings import SettingsError, load_settings
from e7ac.skills.clean import clean as clean_store
from e7ac.skills.clean import find as find_junk
from e7ac.skills.completeness import cut_effects, cut_parts, find_cut
from e7ac.skills.export import export_text
from e7ac.skills.hero import HeroMemory, decide
from e7ac.skills.importer import import_export
from e7ac.skills.merge import assign, place
from e7ac.skills.own_ui import shows_window, without_badge
from e7ac.skills.store import SkillStore, skills_dir
from e7ac.skills.worklist import progress, worklist
from e7ac.sources.epic7db import SkillInfo, fetch_kit, fetch_tier_order, slug
from e7ac.sources.http import FetchError
from e7ac.storage.db import session_scope
from e7ac.ui.badge_probe import hide_badge
from e7ac.ui.watch_signal import AMBER, GREEN, LIGHT, NoSignal, WatchSignal
from e7ac.vision.skill_popup import SkillPopup, looks_like_popup, parse_skill_popup, read_hero_name

skills_app = typer.Typer(help="Skills read from the game's own skill popup: read, watch, list what is missing, export.")
_OFFLINE = typer.Option("--offline", help="Use cached pages only.")
INTERVAL: Final = 0.5
SETTLE: Final = 1.2
"""Seconds a screen must stay still before it is read: a popup is steady while the skill is held."""

sleep: Callable[[float], None] = time.sleep
clock: Callable[[], float] = time.monotonic


@dataclass(frozen=True, slots=True)
class ReadEvent:
    """What one popup came to: the hero, the skill and whether it taught the program anything."""

    hero_code: str | None
    hero: str
    skill: str
    level: int | None
    new: bool
    changed: bool
    new_effects: tuple[str, ...]
    problem: str = ""
    """Why nothing was stored ("" when it was)."""
    how: str = ""
    """What the hero was taken from (`skills.hero.Decision.how`)."""
    slot: int | None = None
    """Which of the hero's skills it is (1, 2, 3), when epic7db's names place it."""
    passive: bool = False
    cut: tuple[str, ...] = ()
    """The parts of the stored skill that look cut ("description", "effect Vigor"): hold the skill again (SPEC D106)."""


def identify(
    popup: SkillPopup,
    heroes: dict[str, ResolvedEntity],
    override: str | None = None,
    remembered: str | None = None,
) -> tuple[str | None, str]:
    """(hero code, "") for a popup, or (None, why not): see `skills.hero.decide` for what is trusted first."""
    decision = decide(popup.hero, heroes, override, remembered)
    return decision.code, decision.problem


KitOf = Callable[[str], Mapping[int, SkillInfo]]
"""A hero's name -> epic7db's skills of it ({} when the page cannot be had)."""


def make_kit_of(paths: AppPaths, offline: bool = False) -> KitOf:
    """epic7db's skills of a hero (its page is cached on disk, and asked once a run): which slot each popup is."""
    kits: dict[str, Mapping[int, SkillInfo]] = {}

    def kit_of(name: str) -> Mapping[int, SkillInfo]:
        if name not in kits:
            try:
                with catalog_cli.http_factory(paths, offline) as http:
                    kits[name] = fetch_kit(http, name)
            except (FetchError, OSError):  # not placed now: the table shows "?" for it
                kits[name] = {}
        return kits[name]

    return kit_of


def store_popup(
    store: SkillStore,
    heroes: dict[str, ResolvedEntity],
    popup: SkillPopup,
    override: str | None = None,
    remembered: str | None = None,
    kit_of: KitOf | None = None,
) -> ReadEvent:
    decision = decide(popup.hero, heroes, override, remembered)
    code = decision.code
    shown = popup.hero or override or "?"
    if code is None:
        return ReadEvent(None, shown, popup.name, popup.level, False, False, (), decision.problem)
    kit = kit_of(heroes[code].name or "") if kit_of is not None and heroes[code].name else {}
    slot, passive = place(popup.name, kit)
    saved = store.save(code, popup, slot=slot, passive=passive)
    assign(store, code, kit)  # the skills read before this one get their slot too
    return ReadEvent(
        code,
        heroes[code].name or code,
        popup.name,
        popup.level,
        saved.new,
        saved.changed,
        saved.new_effects,
        "",
        decision.how,
        slot,
        passive,
        tuple(cut_parts(saved.stored)),
    )


def describe(event: ReadEvent) -> str:
    level = f" +{event.level}" if event.level is not None else ""
    if event.problem:
        return f"not stored: {event.skill}{level}: {event.problem}"
    what = "new" if event.new else "changed" if event.changed else "again"
    effects = f"; effects learned: {', '.join(event.new_effects)}" if event.new_effects else ""
    source = {"hero screen": " [hero from its own screen]", "start of the name": " [hero from the start of its name]"}
    which = f" [S{event.slot}{', passive' if event.passive else ''}]" if event.slot is not None else ""
    cut = f"; LOOKS CUT ({', '.join(event.cut)}): hold the skill again" if event.cut else ""
    return f"{event.hero}: {event.skill}{which}{level} ({what}){effects}{source.get(event.how, '')}{cut}"


# ------------------------------------------------------------------------------------------------ commands


def _heroes() -> dict[str, ResolvedEntity]:
    with session_scope(_engine()) as session:
        heroes = _catalog_heroes(session)
    if heroes is None:
        _fail("heroes are identified by name, which needs the catalog: run e7 catalog sync")
    return heroes


@skills_app.command("read")
def read(
    images: Annotated[list[str], typer.Argument(help="Captures of a skill popup (files, folders or patterns).")],
    hero: Annotated[str | None, typer.Option(help="The hero, when the capture does not show its name.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Read and print only; store nothing.")] = False,
) -> None:
    """Read the skill popup of each capture and keep it (the hero is identified by the name on the screen)."""
    from e7ac.vision.image import ImageError, expand_image_paths, load_image

    try:
        paths = expand_image_paths(images)
    except ImageError as exc:
        _fail(str(exc))
    heroes = _heroes()
    store = SkillStore(skills_dir(default_paths().home))
    kit_of = make_kit_of(default_paths())
    reader = roster_cli.reader_factory()
    stored = 0
    for path in paths:
        try:
            popup = parse_skill_popup(reader.read(load_image(path)))
        except ImageError as exc:
            typer.echo(f"{path.name}: {exc}", err=True)
            continue
        if popup is None:
            typer.echo(f"{path.name}: no skill popup on this capture", err=True)
            continue
        if dry_run:
            code, problem = identify(popup, heroes, hero)
            typer.echo(
                f"{path.name}: {popup.hero or '?'} -> {code or problem}: {popup.name} +{popup.level} | {popup.text}"
            )
            continue
        event = store_popup(store, heroes, popup, hero, None, kit_of)
        stored += not event.problem
        typer.echo(f"{path.name}: {describe(event)}", err=bool(event.problem))
    typer.echo(f"{stored} skill(s) stored of {len(paths)} capture(s)." if not dry_run else "Dry run: nothing stored.")


@skills_app.command("forget")
def forget(
    hero: Annotated[str, typer.Argument(help='Exact name or code, e.g. "Rhianna and Luciella".')],
    skill: Annotated[
        str | None, typer.Option(help="Only this skill (its name); default: everything read of the hero.")
    ] = None,
) -> None:
    """Drop what was read for a hero (or one of its skills), and the effects only it had shown: for a reading that was
    wrong. Hold the skills again afterwards."""
    heroes = _heroes()
    code, problem = (hero, "") if hero in heroes else resolve_hero_code(hero, heroes)
    if code is None:
        _fail(problem)
    removed = SkillStore(skills_dir(default_paths().home)).forget(code, skill)
    what = f"{skill!r} of " if skill else ""
    typer.echo(f"Forgot {removed} skill(s): {what}{heroes[code].name or code}.")


@skills_app.command("import")
def import_command(
    file: Annotated[Path, typer.Argument(help="A skills export (the file `e7 skills export` writes).")],
) -> None:
    """Put the skills of an export into this PC's store (the readings that are not skills are left out), so skills read
    on another PC feed the kits here."""
    try:
        text = file.read_text(encoding="utf-8")
    except OSError as exc:
        _fail(f"cannot read {file}: {exc}")
    done = import_export(SkillStore(skills_dir(default_paths().home)), text, make_kit_of(default_paths()))
    for item in done.left_out:
        typer.echo(f"  left out (not a skill): {item}")
    typer.echo(f"{done.skills} skill(s) of {done.heroes} hero(es) imported from {file.name}.")


@skills_app.command("check")
def check() -> None:
    """The readings that look cut (a description or an effect that stops at "by", "of", a number...): the skills to
    hold again. An effect is also listed by the heroes whose skills show it."""
    paths = default_paths()
    heroes = _heroes()
    store = SkillStore(skills_dir(paths.home))
    cuts = find_cut(store)
    for cut in cuts:
        name = heroes[cut.hero].name if cut.hero in heroes else cut.hero
        typer.echo(f"  {name}: {cut.skill!r}: {cut.part} looks cut")
    for title, codes in sorted(cut_effects(store).items()):
        names = ", ".join(heroes[c].name or c for c in codes if c in heroes)
        typer.echo(f"  effect {title!r} is stored cut (seen on: {names or '?'}): hold a skill that shows it")
    typer.echo(f"{len(cuts)} reading(s) look cut." if cuts else "No reading looks cut.")


@skills_app.command("clean")
def clean_command(
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Only list what would go.")] = False,
) -> None:
    """Drop the readings that are not skills (the text of the Orbis window or badge taken for a popup, an enhancement
    step taken for a title): what an earlier version stored. Hold again what it takes away, if it was a real skill."""
    store = SkillStore(skills_dir(default_paths().home))
    heroes = _heroes()
    found = find_junk(store) if dry_run else clean_store(store)
    for item in found:
        typer.echo(f"  {heroes[item.code].name if item.code in heroes else item.code}: {item.name!r}: {item.why}")
    verb = "would go" if dry_run else "dropped"
    typer.echo(f"{len(found)} reading(s) {verb}." if found else "Nothing to clean.")


@skills_app.command("show")
def show(hero: Annotated[str, typer.Argument(help='Exact name or code, e.g. "Lady of the Scales".')]) -> None:
    """The skills read for a hero, as the game worded them."""
    from e7ac.cli.roster import _resolve_hero_code

    with session_scope(_engine()) as session:
        code = _resolve_hero_code(session, hero)
        heroes = _catalog_heroes(session) or {}
    store = SkillStore(skills_dir(default_paths().home))
    skills = store.load(code)
    name = heroes[code].name if code in heroes and heroes[code].name else code
    if not skills:
        typer.echo(f"{name} ({code}): nothing read yet. Hold its skills in the game with `e7 skills watch` running.")
        return
    typer.echo(export_text(store, {code: name}, [code]).split("\n== Effects")[0].strip())


@skills_app.command("todo")
def todo(
    limit: Annotated[int, typer.Option(min=1, help="How many heroes to list.")] = 40,
    done: Annotated[bool, typer.Option("--done", help="Also the heroes already complete.")] = False,
    offline: Annotated[bool, _OFFLINE] = False,
) -> None:
    """The heroes whose skills are still to read, the ones that matter most first: met in your Arena, then the RTA
    tier list (best tier first), then the rest."""
    paths = default_paths()
    heroes = _heroes()
    names = {code: hero.name for code, hero in heroes.items() if hero.name}
    by_slug = {slug(name): code for code, name in names.items()}
    with catalog_cli.http_factory(paths, offline) as http:
        tiers = [by_slug[s] for s in fetch_tier_order(http) if s in by_slug]
    rows = worklist(names, met_heroes(paths.home), tiers, SkillStore(skills_dir(paths.home)))
    complete, started, skills, total = progress(rows)
    typer.echo(f"{complete} of {total} heroes complete ({started} started, {skills} skills read).")
    shown = [r for r in rows if done or not r.complete][:limit]
    for number, row in enumerate(shown, 1):
        typer.echo(f"  {number:>3}. {row.name:<30} {row.code:<7} skills {row.marks}   {row.why}")


@skills_app.command("export")
def export(
    out: Annotated[Path | None, typer.Option("--out", help="Where to write it (default: the skills folder).")] = None,
) -> None:
    """Write everything read as one text file to send (the game's own words; it never goes anywhere by itself)."""
    paths = default_paths()
    store = SkillStore(skills_dir(paths.home))
    with session_scope(_engine()) as session:
        heroes = _catalog_heroes(session) or {}
    text = export_text(store, {code: h.name or code for code, h in heroes.items()})
    target = out or skills_dir(paths.home) / "skills_export.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    typer.echo(f"Written {target} ({len(text.splitlines())} lines). Send it, or paste it in the chat.")


# ------------------------------------------------------------------------------------------------ the watch


@dataclass(frozen=True, slots=True)
class SkillWatchOptions:
    hwnd: int | None = None
    on_read: Callable[[ReadEvent], None] | None = None
    """Called with each popup stored (or refused)."""
    chosen: Callable[[], str | None] | None = None
    """The hero the user said is open (a name or code), asked at each popup; None or "" : read it from the screen."""
    status: Callable[[str], None] | None = None


def run_skill_watch(options: SkillWatchOptions, badge: WatchSignal, stop: threading.Event) -> None:
    """Watch the game until `stop`: each time a screen settles, read it for a skill popup and keep what it shows. The
    reading runs on a thread (a screen takes a few seconds), in order, so no popup is skipped."""
    from e7ac.cli.arena import game_frames
    from e7ac.vision.watch import ScreenWatcher, thumbnail

    paths = default_paths()
    try:
        client = load_settings(paths.settings_file).client
    except SettingsError as exc:
        _fail(str(exc))
    heroes = _heroes()
    store = SkillStore(skills_dir(paths.home))
    reader = roster_cli.reader_factory()
    pending: queue.Queue[Any | None] = queue.Queue()
    counts = {"read": 0, "ignored": 0, "evidence": 0}
    kit_of = make_kit_of(paths)
    memory = HeroMemory()
    """The hero seen on its own screen (SPEC D97): its name is whole there, and covered once a skill is held."""

    def work() -> None:
        while (image := pending.get()) is not None:
            lines = reader.read(image)
            if shows_window(lines):  # the Orbis window is on top of the game: its text is not the game's
                counts["ignored"] += 1
                typer.echo(f"  ! the Orbis window is in the picture{_evidence(image, counts, paths, 'window')}")
                badge.say("Skills: the Orbis window is over the game", "move it beside the game", AMBER)
                continue
            clean_lines = without_badge(lines)  # nor is the badge's own text
            if len(clean_lines) != len(lines):
                typer.echo(f"  ! the badge's own text is in the picture{_evidence(image, counts, paths, 'badge')}")
            lines = clean_lines
            popup = parse_skill_popup(lines)
            if popup is None and (known := memory.recall(clock())) is not None and heroes[known].name:
                titles = [n for info in kit_of(heroes[known].name).values() for n in info.names]
                popup = parse_skill_popup(lines, titles)  # a passive that has no badge and no "Skill Enhance"
            if popup is None:
                counts["ignored"] += 1
                name = read_hero_name(lines)
                seen = resolve_hero_code(name, heroes)[0] if name else None
                if seen is None and looks_like_popup(
                    lines
                ):  # a popup this reader does not understand: keep it to learn
                    kept = _keep(image, paths.captures_dir / "skills", "unread")
                    lost = ReadEvent(
                        None, "?", "?", None, False, False, (), f"a skill popup was seen but not understood: {kept}"
                    )
                    typer.echo("  ! " + describe(lost))
                    if options.on_read is not None:
                        options.on_read(lost)
                    badge.say("Skills: popup not understood", "its picture was kept - send it", AMBER)
                    continue
                if seen is None:
                    badge.say("Skills: hold a skill icon", "no skill popup on this screen", LIGHT)
                    continue
                memory.see(seen, clock())
                typer.echo(f"  hero screen: {heroes[seen].name}")
                badge.say(f"Hero: {heroes[seen].name}", "now hold each skill icon ~2 s", GREEN)
                continue
            chosen = options.chosen() if options.chosen is not None else None
            event = store_popup(store, heroes, popup, chosen, memory.recall(clock()), kit_of)
            counts["read"] += not event.problem
            typer.echo(("  " if not event.problem else "  ! ") + describe(event))
            if options.on_read is not None:
                options.on_read(event)
            if event.problem:
                badge.say("Skills: hero not identified", event.problem[:60], AMBER)
                typer.echo(f"  picture kept: {_keep(image, paths.captures_dir / 'skills')}")
            elif event.cut:
                badge.say(f"Read: {event.hero} (cut?)", f"{event.cut[0]} looks cut: hold it again", AMBER)
            else:
                top = f"Read: {event.hero}"
                badge.say(top, f"{event.skill}" + (f" +{event.level}" if event.level is not None else ""), GREEN)

    thread = threading.Thread(target=work, name="e7-skill-reader", daemon=True)
    thread.start()
    badge.say("Skills: hold a skill icon", "in the hero's Skill tab; keep it held ~1.5 s", LIGHT)
    typer.echo("Watching the game: open a hero's Skill tab and hold each skill icon for about 2 seconds. Ctrl+C stops.")
    watcher = ScreenWatcher(settle=SETTLE)
    was_settling = False
    badge_seen = False
    for item in game_frames(client, options.hwnd):
        if stop.is_set():
            break
        if item is not None:
            frame, area = item
            badge.place(area)
            frame, in_picture = hide_badge(frame, area, badge.region())
            if in_picture and not badge_seen:
                badge_seen = True
                typer.echo(
                    "  note: the badge shows in the screen capture on this PC (hiding it from the capture does not "
                    "hold here): its place is blanked out of what is read"
                )
            if watcher.feed(thumbnail(frame), clock()):
                pending.put(frame)
                badge.say("Skills: reading...", "keep holding until it says Read", LIGHT)
            elif watcher.settling and not was_settling:
                badge.settling()
            was_settling = watcher.settling
        elif options.status is not None:
            options.status("the game window is not visible (it must be on screen, not minimised or covered)")
    pending.put(None)
    thread.join(timeout=30)
    typer.echo(f"Stopped. {counts['read']} skill popup(s) read, {counts['ignored']} screen(s) without one.")


EVIDENCE: Final = 3
"""Pictures kept per run of a screen that showed the program's own text: to see what was on the screen."""


def _evidence(image: Any, counts: dict[str, int], paths: AppPaths, tag: str) -> str:
    """Keep the picture (the first `EVIDENCE` of a run) and say where; what to append to the console line."""
    if counts["evidence"] >= EVIDENCE:
        return ""
    counts["evidence"] += 1
    return f": picture kept at {_keep(image, paths.captures_dir / 'skills', 'own-' + tag)}"


def _keep(image: Any, folder: Path, tag: str = "skill") -> Path:
    """Keep a picture the watch could not make sense of, to read it again or to look at (local, never committed)."""
    import cv2

    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = folder / f"{stamp}-{tag}.png"
    for number in range(2, 100):  # two pictures in one second keep both
        if not path.exists():
            break
        path = folder / f"{stamp}-{tag}-{number}.png"
    ok, data = cv2.imencode(".png", image)
    if ok:
        data.tofile(str(path))
    return path


@skills_app.command("watch")
def watch(
    hwnd: Annotated[int | None, typer.Option(help="Watch this window (handle from e7 capture --list-windows).")] = None,
    signal: Annotated[bool, typer.Option(help="Show a small badge on the game.")] = True,
    hero: Annotated[
        str | None, typer.Option(help="The hero you are reading (name or code), when its name is not read well.")
    ] = None,
) -> None:
    """Watch the game and read each skill popup you open: in a hero's Skill tab, hold each skill icon for about two
    seconds (the popup stays while you hold it). Read-only: only screen pixels are copied. Ctrl+C stops."""
    from e7ac.cli import arena as arena_cli

    stop = threading.Event()
    badge: WatchSignal = NoSignal()
    if signal:
        badge, why = arena_cli.signal_factory(lambda: None, lambda: None)
        if why:
            typer.echo(f"note: {why}", err=True)
    try:
        run_skill_watch(SkillWatchOptions(hwnd=hwnd, chosen=(lambda: hero) if hero else None), badge, stop)
    except KeyboardInterrupt:
        stop.set()
    finally:
        badge.close()
