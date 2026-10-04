"""`e7 roster ...` commands: manual entry/edit, list/show/history, validation, arena flag, JSON backup."""

from __future__ import annotations

import codecs
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Final, NoReturn

import typer
from pydantic import ValidationError
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from e7ac.catalog.facts import EntityType
from e7ac.catalog.resolve import ResolvedEntity
from e7ac.catalog.store import current_snapshot as current_catalog
from e7ac.catalog.store import find, load_entities, load_entity
from e7ac.domain.codes import Element, HeroClass, Stat, is_hero_code
from e7ac.domain.roster import FINAL_STAT_FIELDS, MAX_INT, BuildSource, HeroBuild
from e7ac.fileio import write_text_atomic
from e7ac.paths import default_paths
from e7ac.roster.backup import RosterExport, export_roster, import_roster
from e7ac.roster.screen_import import FINAL_FIELDS, ScreenBuild, build_from_screen
from e7ac.roster.store import (
    RosterError,
    add_owned_hero,
    add_snapshot,
    build_from_row,
    current_snapshot,
    get_owned,
    history,
    list_owned,
    set_arena_relevant,
)
from e7ac.roster.validation import CatalogContext, Issue, Severity, validate_build
from e7ac.settings import SettingsError, load_settings
from e7ac.storage.db import open_database, session_scope
from e7ac.storage.models import CatalogEntityRow, HeroSnapshotRow
from e7ac.vision.hero_screen import ScreenError, ScreenKind, parse_hero_screen
from e7ac.vision.image import ImageError, captured_at, expand_image_paths, load_image
from e7ac.vision.ocr import RapidOcrReader, TextReader

roster_app = typer.Typer(help="Your heroes: builds with history, validation and JSON backup.")

_SORT_FIELDS: Final = ("id", "name", "level", "cp", *FINAL_STAT_FIELDS)
_PERCENT_FLAGS: Final = {
    "crit_chance": "--cc",
    "crit_damage": "--cd",
    "effectiveness": "--eff",
    "effect_resistance": "--er",
    "dual_attack": "--dac",
}
_DEFAULT_PROGRESS: Final = {"stars": 6, "awakening": 6, "level": 60}
_NO_CATALOG_NOTE: Final = (
    "note: no catalog yet - catalog checks (known codes, class lock, imprint, EE, base stats) skipped; "
    "run: e7 catalog sync"
)


def _fail(message: str, code: int = 2) -> NoReturn:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=code)


def _engine() -> Engine:
    paths = default_paths()
    paths.ensure()
    return open_database(paths.database)


def _catalog_heroes(session: Session) -> dict[str, ResolvedEntity] | None:
    """None when there is no catalog yet (callers must say so instead of silently filtering everything out)."""
    snapshot = current_catalog(session)
    if snapshot is None:
        return None
    return {e.entity_id: e for e in load_entities(session, snapshot.id, EntityType.HERO)}


def _resolve_hero_code(session: Session, query: str) -> str:
    """Hero code from a code or an exact catalog name; never a near-miss (golden rule)."""
    if is_hero_code(query.strip()):
        return query.strip()
    snapshot = current_catalog(session)
    if snapshot is None:
        _fail("no catalog yet - use a hero code (e.g. c2011) or run: e7 catalog sync")
    lookup = find(session, snapshot.id, query)
    heroes = [e for e in lookup.exact if e.entity_type is EntityType.HERO]
    if len(heroes) == 1:
        return heroes[0].entity_id
    candidates = heroes or [e for e in lookup.partial if e.entity_type is EntityType.HERO]
    message = "matches several heroes" if len(heroes) > 1 else "is not an exact hero name"
    typer.echo(f"Error: {query!r} {message}. Use a code:", err=True)
    for entity in candidates[:15]:
        typer.echo(f"  {entity.entity_id:<8} {entity.name}", err=True)
    raise typer.Exit(code=2)


# ------------------------------------------------------------------------------------------------ JSON input


def _read_json(path: Path, what: str) -> dict[str, Any]:
    """A JSON object from a file written by hand, by e7, or redirected by PowerShell (UTF-16 with a BOM)."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        _fail(f"cannot read {what} {path}: {exc.strerror or exc}")
    try:
        data = json.loads(raw.decode(_encoding(raw)), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError) as exc:
        _fail(f"cannot read {what} {path}: {exc}")
    if not isinstance(data, dict):
        _fail(f"cannot read {what} {path}: expected a JSON object, found {type(data).__name__}")
    return data


def _encoding(raw: bytes) -> str:
    if raw.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return "utf-32"
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    return "utf-8-sig"


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not a valid number here")


def _build_json(path: Path, hero_code: str) -> dict[str, Any]:
    """A build from --from-json; also accepts the output of `e7 roster show --json` ({"build": {...}})."""
    data = _read_json(path, "build")
    if "hero_code" not in data and isinstance(data.get("build"), dict):
        data = dict(data["build"])
    found = data.get("hero_code")
    if found is not None and found != hero_code:
        _fail(f"{path} is a build of {found!r}, not {hero_code!r} (nothing saved)")
    data["hero_code"] = hero_code
    return data


# ------------------------------------------------------------------------------------------------ options


StatOption = Annotated[int | None, typer.Option(min=0, max=MAX_INT)]


def _stat_overrides(
    atk: int | None,
    defense: int | None,
    hp: int | None,
    speed: int | None,
    crit_chance: float | None,
    crit_damage: float | None,
    effectiveness: float | None,
    effect_resistance: float | None,
    dual_attack: float | None,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "atk": atk,
        "defense": defense,
        "hp": hp,
        "speed": speed,
        "crit_chance": crit_chance,
        "crit_damage": crit_damage,
        "effectiveness": effectiveness,
        "effect_resistance": effect_resistance,
        "dual_attack": dual_attack,
    }
    out: dict[str, Any] = {}
    for key, value in values.items():
        if value is None:
            continue
        if key in _PERCENT_FLAGS:
            if 0 < value <= 1:
                typer.echo(
                    f"note: {_PERCENT_FLAGS[key]} {value:g} means {value:g}% - options take percent "
                    f"(e.g. {_PERCENT_FLAGS[key]} 100 for 100%)",
                    err=True,
                )
            value = value / 100
        out[key] = value
    return out


def _apply(base: dict[str, Any], *, stats: dict[str, Any], **fields: Any) -> dict[str, Any]:
    """Overlay the CLI options on a build dict. Final stats must be complete the first time they are given.

    A value typed by the user replaces any (OCR) confidence the old value had: the key is dropped (= 1.0)."""
    data = dict(base)
    given = {key: value for key, value in fields.items() if value is not None}
    data.update(given)
    if stats:
        current = data.get("final_stats")
        if current:
            data["final_stats"] = {**current, **stats}
        else:
            missing = [f for f in FINAL_STAT_FIELDS if f not in stats]
            if missing:
                _fail(f"give all final stats the first time (missing: {', '.join(missing)})")
            data["final_stats"] = stats
    replaced = {*given, *stats, *(f"final_stats.{key}" for key in stats)}
    confidence = data.get("confidence")
    if isinstance(confidence, dict):
        data["confidence"] = {k: v for k, v in confidence.items() if k not in replaced}
    return data


def _validated_build(data: dict[str, Any]) -> HeroBuild:
    try:
        return HeroBuild.model_validate(data)
    except ValidationError as exc:
        _fail(f"invalid build:\n{exc}")


# ------------------------------------------------------------------------------------------------ validation


def _check(session: Session, build: HeroBuild, force: bool) -> None:
    issues = _issues(session, build)
    _print_issues(issues)
    if any(i.severity is Severity.ERROR for i in issues) and not force:
        _fail("not saved: fix the errors above (or use --force to store it anyway).")


def _issues(session: Session, build: HeroBuild, *, quiet: bool = False) -> list[Issue]:
    context = _catalog_context(session, build)
    if context is None and not quiet:
        typer.echo(_NO_CATALOG_NOTE, err=True)
    return validate_build(build, context)


def _catalog_context(session: Session, build: HeroBuild) -> CatalogContext | None:
    snapshot = current_catalog(session)
    if snapshot is None:
        return None
    rows = session.execute(
        select(CatalogEntityRow.entity_type, CatalogEntityRow.entity_id).where(
            CatalogEntityRow.snapshot_id == snapshot.id
        )
    )
    known: dict[str, set[str]] = {t.value: set() for t in EntityType}
    for entity_type, entity_id in rows:
        known.setdefault(entity_type, set()).add(entity_id)
    hero = load_entity(session, snapshot.id, EntityType.HERO, build.hero_code)
    artifact = load_entity(session, snapshot.id, EntityType.ARTIFACT, build.artifact.code) if build.artifact else None
    return CatalogContext(
        known_heroes=known[EntityType.HERO.value],
        known_artifacts=known[EntityType.ARTIFACT.value],
        known_sets=known[EntityType.SET.value],
        hero_role=_str(hero.value("role")) if hero else None,
        artifact_role_lock=_str(artifact.value("role_lock")) if artifact else None,
        artifact_role_lock_status=artifact.status("role_lock").value if artifact else None,
        imprint_stat=_stat(hero.value("imprint.stat")) if hero else None,
        imprint_values=_float_map(hero.value("imprint.values")) if hero else None,
        ee_stat=_stat(hero.value("ee.stat")) if hero else None,
        base_crit_damage=_number(hero.value("base.cri_dmg")) if hero else None,
    )


def _print_issues(issues: list[Issue]) -> None:
    for issue in issues:
        typer.echo(f"  {issue.severity.value.upper():<7} {issue.rule:<20} {issue.field}: {issue.message}", err=True)


# ------------------------------------------------------------------------------------------------ commands


@roster_app.command("add")
def add(
    hero: Annotated[str, typer.Argument(help="Hero code (c2011) or exact name.")],
    stars: Annotated[int | None, typer.Option(min=1, max=6, help="Default 6 (said when assumed).")] = None,
    awakening: Annotated[int | None, typer.Option(min=0, max=6, help="Default 6 (said when assumed).")] = None,
    level: Annotated[int | None, typer.Option(min=1, max=60, help="Default 60 (said when assumed).")] = None,
    atk: StatOption = None,
    defense: Annotated[int | None, typer.Option("--def", min=0, max=MAX_INT)] = None,
    hp: StatOption = None,
    speed: Annotated[int | None, typer.Option("--spd", min=0, max=MAX_INT)] = None,
    crit_chance: Annotated[float | None, typer.Option("--cc", min=0, help="Percent.")] = None,
    crit_damage: Annotated[float | None, typer.Option("--cd", min=0, help="Percent.")] = None,
    effectiveness: Annotated[float | None, typer.Option("--eff", min=0, help="Percent.")] = None,
    effect_resistance: Annotated[float | None, typer.Option("--er", min=0, help="Percent.")] = None,
    dual_attack: Annotated[float | None, typer.Option("--dac", min=0, help="Percent.")] = None,
    cp: StatOption = None,
    arena: Annotated[bool, typer.Option("--arena", help="Mark as arena-relevant.")] = False,
    note: Annotated[str, typer.Option()] = "",
    from_json: Annotated[
        Path | None, typer.Option(help="Build JSON (gear, artifact, imprint, EE...); options override it.")
    ] = None,
    force: Annotated[bool, typer.Option(help="Store even if validation finds errors.")] = False,
) -> None:
    """Add an owned hero (manual entry). Rates are given in percent: --cc 100 means 100%."""
    engine = _engine()
    with session_scope(engine) as session:
        code = _resolve_hero_code(session, hero)
        base: dict[str, Any] = {"hero_code": code}
        if from_json is not None:
            base = _build_json(from_json, code)
        base.setdefault("source", BuildSource.MANUAL.value)
        base.setdefault("captured_at", datetime.now(UTC).isoformat())
        given = {"stars": stars, "awakening": awakening, "level": level}
        assumed = {k: v for k, v in _DEFAULT_PROGRESS.items() if given[k] is None and k not in base}
        if assumed:
            listed = ", ".join(f"{k} {v}" for k, v in assumed.items())
            typer.echo(f"note: not given, assumed {listed} (use --stars/--awakening/--level)", err=True)
            base.update(assumed)
        stats = _stat_overrides(
            atk, defense, hp, speed, crit_chance, crit_damage, effectiveness, effect_resistance, dual_attack
        )
        build = _validated_build(_apply(base, stats=stats, cp=cp, **given))
        _check(session, build, force)
        owned = add_owned_hero(session, build, arena_relevant=arena, note=note)
        typer.echo(f"Added #{owned.id} {code}")


@roster_app.command("edit")
def edit(
    owned_id: Annotated[int, typer.Argument(help="Roster id (see e7 roster list).")],
    stars: Annotated[int | None, typer.Option(min=1, max=6)] = None,
    awakening: Annotated[int | None, typer.Option(min=0, max=6)] = None,
    level: Annotated[int | None, typer.Option(min=1, max=60)] = None,
    atk: StatOption = None,
    defense: Annotated[int | None, typer.Option("--def", min=0, max=MAX_INT)] = None,
    hp: StatOption = None,
    speed: Annotated[int | None, typer.Option("--spd", min=0, max=MAX_INT)] = None,
    crit_chance: Annotated[float | None, typer.Option("--cc", min=0, help="Percent.")] = None,
    crit_damage: Annotated[float | None, typer.Option("--cd", min=0, help="Percent.")] = None,
    effectiveness: Annotated[float | None, typer.Option("--eff", min=0, help="Percent.")] = None,
    effect_resistance: Annotated[float | None, typer.Option("--er", min=0, help="Percent.")] = None,
    dual_attack: Annotated[float | None, typer.Option("--dac", min=0, help="Percent.")] = None,
    cp: StatOption = None,
    from_json: Annotated[Path | None, typer.Option(help="Replace the build with this JSON.")] = None,
    force: Annotated[bool, typer.Option(help="Store even if validation finds errors.")] = False,
) -> None:
    """Change a hero: stores a NEW snapshot (the previous build stays in the history)."""
    engine = _engine()
    with session_scope(engine) as session:
        owned = _owned_or_exit(session, owned_id)
        row = current_snapshot(session, owned.id)
        if from_json is not None:
            base = _build_json(from_json, owned.hero_code)
            base.setdefault("source", BuildSource.MANUAL.value)
        elif row is not None:
            base = build_from_row(session, row).model_dump(mode="json")
            base["source"] = BuildSource.MANUAL.value
        else:
            _fail("this hero has no snapshot to edit; use --from-json")
        base["captured_at"] = datetime.now(UTC).isoformat()
        stats = _stat_overrides(
            atk, defense, hp, speed, crit_chance, crit_damage, effectiveness, effect_resistance, dual_attack
        )
        build = _validated_build(_apply(base, stats=stats, stars=stars, awakening=awakening, level=level, cp=cp))
        if row is not None and _same_build(build, build_from_row(session, row)):
            typer.echo("Nothing changed.")
            return
        _check(session, build, force)
        snapshot = add_snapshot(session, owned, build)
        typer.echo(f"#{owned.id}: new snapshot {snapshot.id} ({len(history(session, owned.id))} in history)")


@roster_app.command("list")
def list_cmd(
    element: Annotated[Element | None, typer.Option(help="fire, ice, wind (earth), light, dark")] = None,
    role: Annotated[HeroClass | None, typer.Option("--class", help="warrior, knight, assassin, ...")] = None,
    arena: Annotated[bool, typer.Option("--arena", help="Only arena-relevant heroes.")] = False,
    search: Annotated[str | None, typer.Option(help="Substring of the hero name or code.")] = None,
    sort: Annotated[str, typer.Option(help=f"One of: {', '.join(_SORT_FIELDS)}")] = "id",
    desc: Annotated[bool, typer.Option("--desc", help="Sort descending.")] = False,
) -> None:
    """List owned heroes with their current final stats."""
    if sort not in _SORT_FIELDS:
        _fail(f"--sort must be one of: {', '.join(_SORT_FIELDS)}")
    engine = _engine()
    with session_scope(engine) as session:
        catalog = _catalog_heroes(session)
        if catalog is None and (element or role):
            _fail("--element/--class need the catalog: run e7 catalog sync")
        rows = []
        unknown = 0
        for owned, snap in list_owned(session):
            entity = (catalog or {}).get(owned.hero_code)
            name = entity.name if entity else owned.hero_code
            if (element or role) and entity is None:
                unknown += 1
                continue
            if element and entity is not None and entity.value("element") != element.value:
                continue
            if role and entity is not None and entity.value("role") != role.value:
                continue
            if arena and not owned.arena_relevant:
                continue
            if search and search.casefold() not in f"{name} {owned.hero_code}".casefold():
                continue
            rows.append((owned, snap, name))
    rows.sort(key=lambda r: _sort_key(r[0].id, r[2], r[1], sort), reverse=desc)
    typer.echo(
        f"{'id':>4} {'code':<6} {'name':<26} {'*':>1} {'lv':>2} {'ATK':>5} {'DEF':>5} {'HP':>6} {'SPD':>4} "
        f"{'CC':>5} {'CD':>5} {'EFF':>5} {'ER':>5} {'CP':>7} A"
    )
    for owned, snap, name in rows:
        typer.echo(_row_line(owned.id, owned.hero_code, name, snap, owned.arena_relevant))
    typer.echo(f"{len(rows)} hero(es)")
    if catalog is None and search:
        typer.echo("note: no catalog yet - --search only matched hero codes", err=True)
    if unknown:
        typer.echo(f"note: {unknown} hero(es) not in the catalog were left out by --element/--class", err=True)


@roster_app.command("show")
def show(owned_id: Annotated[int, typer.Argument()], as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Show the current build of one owned hero."""
    engine = _engine()
    with session_scope(engine) as session:
        owned = _owned_or_exit(session, owned_id)
        row = current_snapshot(session, owned.id)
        if row is None:
            typer.echo(f"#{owned.id} {owned.hero_code} has no snapshot", err=True)
            raise typer.Exit(code=1)
        build = build_from_row(session, row)
        catalog = _catalog_heroes(session) or {}
        issues = _issues(session, build, quiet=as_json)
    if as_json:
        typer.echo(json.dumps({"id": owned.id, "uid": owned.uid, "build": build.model_dump(mode="json")}, indent=2))
        return
    entity = catalog.get(owned.hero_code)
    title = entity.name if entity else owned.hero_code
    typer.echo(f"#{owned.id} {title} ({owned.hero_code}){' [arena]' if owned.arena_relevant else ''}")
    typer.echo(
        f"  {build.stars}* awakening {build.awakening}  Lv. {build.level}  skills {build.skills.s1}/"
        f"{build.skills.s2}/{build.skills.s3}  CP {build.cp if build.cp is not None else '-'}"
    )
    if build.final_stats is not None:
        s = build.final_stats
        typer.echo(
            f"  ATK {s.atk}  DEF {s.defense}  HP {s.hp}  SPD {s.speed}  CC {s.crit_chance:.1%}  "
            f"CD {s.crit_damage:.1%}  EFF {s.effectiveness:.1%}  ER {s.effect_resistance:.1%}  "
            f"DAC {s.dual_attack:.1%}"
        )
    if build.imprint:
        grade = build.imprint.grade.value if build.imprint.grade else "?"
        mode = f" ({build.imprint.mode.value})" if build.imprint.mode else ""
        typer.echo(f"  Imprint {grade} {build.imprint.stat.value} {build.imprint.value:g}{mode}")
    if build.exclusive_equipment:
        ee = build.exclusive_equipment
        typer.echo(
            f"  EE {ee.stat.value if ee.stat else '?'} {ee.value if ee.value is not None else '?'} "
            f"{ee.option_code or ''}"
        )
    if build.artifact:
        typer.echo(f"  Artifact {build.artifact.code} +{build.artifact.level}")
    for slot, gear in sorted(build.gear.items()):
        subs = ", ".join(f"{sub.stat.value} {sub.value:g}" for sub in gear.substats)
        typer.echo(
            f"  {slot.value:<9} {gear.set_code:<13} {gear.grade.value:<6} Lv{gear.item_level} +{gear.enhance} "
            f"{gear.main.stat.value} {gear.main.value:g} | {subs}"
        )
    _print_issues(issues)


@roster_app.command("history")
def history_cmd(owned_id: Annotated[int, typer.Argument()]) -> None:
    """List every stored snapshot of a hero (oldest first; * = current)."""
    engine = _engine()
    with session_scope(engine) as session:
        owned = _owned_or_exit(session, owned_id)
        for row in history(session, owned.id):
            mark = "*" if row.is_current else " "
            when = row.captured_at.strftime("%Y-%m-%d %H:%M UTC")
            typer.echo(
                f"{mark} snapshot {row.id:<5} {when}  {row.source:<8} Lv{row.level} "
                f"ATK {row.atk} SPD {row.speed} CP {row.cp}"
            )


@roster_app.command("validate")
def validate_cmd(owned_id: Annotated[int, typer.Argument()]) -> None:
    """Re-check the current build against the rules (and the catalog, when synced). Exit 1 if there are errors."""
    engine = _engine()
    with session_scope(engine) as session:
        owned = _owned_or_exit(session, owned_id)
        row = current_snapshot(session, owned.id)
        if row is None:
            typer.echo("No snapshot.", err=True)
            raise typer.Exit(code=1)
        issues = _issues(session, build_from_row(session, row))
    _print_issues(issues)
    typer.echo(f"{len(issues)} issue(s)")
    if any(i.severity is Severity.ERROR for i in issues):
        raise typer.Exit(code=1)


@roster_app.command("arena")
def arena_cmd(
    owned_id: Annotated[int, typer.Argument()],
    on: Annotated[bool, typer.Option("--on/--off", help="Mark or unmark as arena-relevant.")] = True,
) -> None:
    """Flag a hero as arena-relevant (used to prioritise scans and predictions)."""
    engine = _engine()
    with session_scope(engine) as session:
        _owned_or_exit(session, owned_id)
        set_arena_relevant(session, owned_id, on)
    typer.echo(f"#{owned_id} arena-relevant: {'yes' if on else 'no'}")


@roster_app.command("export")
def export_cmd(
    target: Annotated[Path, typer.Argument(help="JSON file to write.")],
    force: Annotated[bool, typer.Option(help="Overwrite the file if it exists.")] = False,
) -> None:
    """Back up the whole roster (every snapshot) to a JSON file (written atomically)."""
    paths = default_paths()
    database = paths.database.resolve()
    protected = {database, *(database.with_name(database.name + suffix) for suffix in ("-wal", "-shm", "-journal"))}
    if target.resolve() in protected:
        _fail(f"{target} is the roster database itself; choose another file")
    if target.is_dir():
        _fail(f"{target} is a folder; give a file name, e.g. {target / 'roster-backup.json'}")
    if target.exists() and not force:
        _fail(f"{target} already exists; use --force to overwrite it")
    engine = _engine()
    with session_scope(engine) as session:
        data = export_roster(session, datetime.now(UTC))
    text = data.model_dump_json(indent=2)
    RosterExport.model_validate_json(text)  # what we write must be readable back (no lossy values)
    try:
        write_text_atomic(target, text + "\n")
    except OSError as exc:
        _fail(f"cannot write {target}: {exc.strerror or exc}")
    typer.echo(f"Exported {len(data.heroes)} hero(es), {sum(len(h.snapshots) for h in data.heroes)} snapshot(s)")


@roster_app.command("import")
def import_cmd(source: Annotated[Path, typer.Argument(help="JSON file written by e7 roster export.")]) -> None:
    """Restore or merge a roster backup. Idempotent: entries already present (same uid) are skipped.

    Imported builds are validated; problems are reported but do not block the import (backups may hold forced
    builds). Any conflict (same uid for a different hero) aborts the whole import."""
    raw = _read_json(source, "backup")
    try:
        data = RosterExport.model_validate(raw)
    except ValidationError as exc:
        _fail(f"cannot read backup {source}: {exc}")
    engine = _engine()
    with session_scope(engine) as session:
        try:
            report = import_roster(session, data)
        except RosterError as exc:
            _fail(f"backup {source} conflicts with the roster (nothing imported): {exc}")
        flagged = []
        for snapshot_id in report.added_snapshot_ids:
            row = session.get(HeroSnapshotRow, snapshot_id)
            if row is None:
                continue
            issues = _issues(session, build_from_row(session, row), quiet=True)
            if issues:
                errors = sum(i.severity is Severity.ERROR for i in issues)
                flagged.append(
                    f"#{row.owned_hero_id} snapshot {row.id}: {len(issues)} issue(s), {errors} error(s) "
                    f"(e7 roster validate {row.owned_hero_id})"
                )
    typer.echo(
        f"Imported {report.heroes_added} hero(es), {report.snapshots_added} snapshot(s); "
        f"{report.snapshots_skipped} already present"
    )
    if report.current_changed:
        typer.echo(f"Current build updated from newer backup snapshots for {len(report.current_changed)} hero(es)")
    for line in flagged:
        typer.echo(f"  warning: {line}", err=True)


# OCR engine factory (tests replace it with canned text lines).
reader_factory: Callable[[], TextReader] = RapidOcrReader


@roster_app.command("scan")
def scan(
    images: Annotated[
        list[str],
        typer.Argument(
            help="Captures of the hero screen (PNG/WebP/JPEG): files, folders or patterns like captures\\*.png."
        ),
    ],
    owned_id: Annotated[int | None, typer.Option("--id", help="Roster id, when you own several copies.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Read and check only; save nothing.")] = False,
    force: Annotated[bool, typer.Option(help="Store even if validation finds errors.")] = False,
) -> None:
    """Read hero screens (Hero > Equipment tab, or Hero Info) and store the builds (source: ocr).

    Final stats, level, CP and imprint come from the screen. Gear, artifact and EE are kept from the hero's
    current build. On the Equipment tab every stat is checked against the catalog base stats."""
    try:
        paths = expand_image_paths(images)
    except ImageError as exc:
        _fail(str(exc))
    if owned_id is not None and len(paths) != 1:
        _fail("--id works with one image at a time")
    try:
        language = load_settings(default_paths().settings_file).game_language
    except SettingsError as exc:
        _fail(str(exc))
    engine = _engine()
    reader = reader_factory()
    failed = 0
    for path in paths:
        typer.echo(f"{path.name}:")
        try:
            reading = parse_hero_screen(reader.read(load_image(path)), language)
        except (ImageError, ScreenError) as exc:
            typer.echo(f"  Error: {exc}", err=True)
            failed += 1
            continue
        with session_scope(engine) as session:
            heroes = _catalog_heroes(session)
            if heroes is None:
                _fail("the hero is identified by name, which needs the catalog: run e7 catalog sync")
            owned, current = _scan_target(session, reading.name, heroes, owned_id)
            result = build_from_screen(reading, heroes, captured_at=captured_at(path), existing=current)
            kind = "Equipment tab" if reading.kind is ScreenKind.EQUIPMENT else "Hero Info"
            _print_scan(result, kind)
            if result.build is None:
                failed += 1
                continue
            issues = _issues(session, result.build, quiet=True)
            _print_issues(issues)
            if any(i.severity is Severity.ERROR for i in issues) and not force:
                typer.echo("  Error: not saved: fix the errors above (or use --force)", err=True)
                failed += 1
                continue
            if dry_run:
                typer.echo("  (dry run: nothing saved)")
                continue
            if owned is None:
                created = add_owned_hero(session, result.build)
                typer.echo(f"  Saved as new roster hero #{created.id}")
            elif current is not None and _same_build(result.build, current):
                typer.echo(f"  #{owned.id}: unchanged")
            else:
                snapshot = add_snapshot(session, owned, result.build)
                typer.echo(f"  #{owned.id}: new snapshot {snapshot.id}")
    if failed:
        raise typer.Exit(code=1)


def _scan_target(
    session: Session, name: str | None, heroes: dict[str, ResolvedEntity], owned_id: int | None
) -> tuple[Any, HeroBuild | None]:
    """(owned hero or None for a new one, its current build). Several copies of one hero need --id."""
    if owned_id is not None:
        owned = _owned_or_exit(session, owned_id)
    else:
        codes = {code for code, entity in heroes.items() if name and entity.name.casefold() == name.casefold()}
        copies = [o for o, _ in list_owned(session) if o.hero_code in codes]
        if len(copies) > 1:
            listed = ", ".join(f"#{o.id}" for o in copies)
            _fail(f"you own several copies of {name} ({listed}): scan them one at a time with --id")
        owned = copies[0] if copies else None
    if owned is None:
        return None, None
    row = current_snapshot(session, owned.id)
    return owned, build_from_row(session, row) if row is not None else None


def _print_scan(result: ScreenBuild, kind: str) -> None:
    title = f"{result.hero_name or '?'} ({result.hero_code or 'unknown'})"
    build = result.build
    if build is None:
        typer.echo(f"  {title} - {kind}")
    else:
        cp = f"{build.cp:,}" if build.cp is not None else "-"
        typer.echo(f"  {title} - {kind} - Lv. {build.level}, CP {cp}")
        checks = {c.stat: c for c in result.base_checks}
        stats = build.final_stats
        parts = []
        for stat, name in FINAL_FIELDS.items():
            value = getattr(stats, name) if stats is not None else None
            shown = "-" if value is None else (f"{value:g}" if stat in _FLAT_SCAN else f"{value * 100:.1f}%")
            check = checks.get(stat)
            mark = "" if check is None else (" ok" if check.ok else " CHECK")
            parts.append(f"{stat.value} {shown}{mark}")
        typer.echo("  " + "  ".join(parts))
        if build.imprint is not None:
            grade = build.imprint.grade.value if build.imprint.grade else "?"
            mode = build.imprint.mode.value if build.imprint.mode else "self/team unknown"
            typer.echo(f"  imprint {build.imprint.stat.value} {build.imprint.value:g} (grade {grade}, {mode})")
        if result.base_checks:
            good = sum(c.ok for c in result.base_checks)
            typer.echo(f"  base-stat check vs catalog: {good}/{len(result.base_checks)} agree")
    for note in result.notes:
        typer.echo(f"  note: {note}", err=True)
    for problem in result.problems:
        typer.echo(f"  Error: {problem}", err=True)


_FLAT_SCAN: Final = frozenset({Stat.ATK, Stat.DEF, Stat.HP, Stat.SPEED})


def _owned_or_exit(session: Session, owned_id: int) -> Any:
    try:
        return get_owned(session, owned_id)
    except RosterError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


def _sort_key(owned_id: int, name: str, snap: HeroSnapshotRow | None, field: str) -> tuple[int, Any]:
    if field == "id":
        return (0, owned_id)
    if field == "name":
        return (0, name.casefold())
    value = getattr(snap, field, None) if snap is not None else None
    return (0, value) if value is not None else (-1, 0)


def _row_line(owned_id: int, code: str, name: str, snap: HeroSnapshotRow | None, arena: bool) -> str:
    def num(value: int | None, width: int) -> str:
        return f"{value:>{width}}" if value is not None else f"{'-':>{width}}"

    def pct(value: float | None) -> str:
        return f"{value * 100:>5.0f}" if value is not None else f"{'-':>5}"

    if snap is None:
        return f"{owned_id:>4} {code:<6} {name[:26]:<26} (no snapshot)"
    return (
        f"{owned_id:>4} {code:<6} {name[:26]:<26} {snap.stars:>1} {snap.level:>2} {num(snap.atk, 5)} "
        f"{num(snap.defense, 5)} {num(snap.hp, 6)} {num(snap.speed, 4)} {pct(snap.crit_chance)} "
        f"{pct(snap.crit_damage)} {pct(snap.effectiveness)} {pct(snap.effect_resistance)} {num(snap.cp, 7)} "
        f"{'*' if arena else ''}"
    )


def _str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _stat(value: object) -> Stat | None:
    if isinstance(value, str) and value in Stat._value2member_map_:
        return Stat(value)
    return None


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _float_map(value: object) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    out = {str(k): float(v) for k, v in value.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    return out or None


def _same_build(new: HeroBuild, old: HeroBuild) -> bool:
    ignore = {"captured_at", "source"}
    return new.model_dump(exclude=ignore) == old.model_dump(exclude=ignore)
