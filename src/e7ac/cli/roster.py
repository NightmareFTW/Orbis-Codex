"""`e7 roster ...` commands: manual entry/edit, list/show/history, validation, arena flag, JSON backup."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Final

import typer
from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from e7ac.catalog.facts import EntityType
from e7ac.catalog.resolve import ResolvedEntity
from e7ac.catalog.store import current_snapshot as current_catalog
from e7ac.catalog.store import find, load_entities, load_entity
from e7ac.domain.codes import Element, HeroClass, Stat, is_hero_code
from e7ac.domain.roster import FINAL_STAT_FIELDS, BuildSource, HeroBuild
from e7ac.paths import default_paths
from e7ac.roster.backup import RosterExport, export_roster, import_roster
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
from e7ac.roster.validation import Issue, Severity, validate_build
from e7ac.storage.db import open_database, session_scope
from e7ac.storage.models import HeroSnapshotRow

roster_app = typer.Typer(help="Your heroes: builds with history, validation and JSON backup.")

_SORT_FIELDS: Final = ("id", "name", "level", "cp", *FINAL_STAT_FIELDS)
_PERCENT_FIELDS: Final = frozenset({"crit_chance", "crit_damage", "effectiveness", "effect_resistance", "dual_attack"})


def _engine() -> Engine:
    paths = default_paths()
    paths.ensure()
    return open_database(paths.database)


def _catalog_heroes(session: Session) -> dict[str, ResolvedEntity]:
    snapshot = current_catalog(session)
    if snapshot is None:
        return {}
    return {e.entity_id: e for e in load_entities(session, snapshot.id, EntityType.HERO)}


def _resolve_hero_code(session: Session, query: str) -> str:
    """Hero code from a code or an exact catalog name; never a near-miss (golden rule)."""
    if is_hero_code(query.strip()):
        return query.strip()
    snapshot = current_catalog(session)
    if snapshot is None:
        typer.echo("Error: no catalog yet - use a hero code (e.g. c2011) or run: e7 catalog sync", err=True)
        raise typer.Exit(code=2)
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


StatOption = Annotated[int | None, typer.Option(min=0)]
PercentOption = Annotated[float | None, typer.Option(min=0, help="Percent, e.g. 100 for 100%.")]


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
    return {k: (v / 100 if k in _PERCENT_FIELDS else v) for k, v in values.items() if v is not None}


def _apply(base: dict[str, Any], *, stats: dict[str, Any], **fields: Any) -> dict[str, Any]:
    """Overlay the CLI options on a build dict. Final stats must be complete the first time they are given."""
    data = dict(base)
    data.update({key: value for key, value in fields.items() if value is not None})
    if stats:
        current = data.get("final_stats")
        if current:
            data["final_stats"] = {**current, **stats}
        else:
            missing = [f for f in FINAL_STAT_FIELDS if f not in stats]
            if missing:
                typer.echo(f"Error: give all final stats the first time (missing: {', '.join(missing)})", err=True)
                raise typer.Exit(code=2)
            data["final_stats"] = stats
    return data


def _validated_build(data: dict[str, Any]) -> HeroBuild:
    try:
        return HeroBuild.model_validate(data)
    except ValidationError as exc:
        typer.echo(f"Error: invalid build:\n{exc}", err=True)
        raise typer.Exit(code=2) from exc


def _check(session: Session, build: HeroBuild, force: bool) -> None:
    issues = _issues(session, build)
    _print_issues(issues)
    if any(i.severity is Severity.ERROR for i in issues) and not force:
        typer.echo("Not saved: fix the errors above (or use --force to store it anyway).", err=True)
        raise typer.Exit(code=2)


def _issues(session: Session, build: HeroBuild) -> list[Issue]:
    snapshot = current_catalog(session)
    if snapshot is None:
        return validate_build(build)
    hero = load_entity(session, snapshot.id, EntityType.HERO, build.hero_code)
    artifact = load_entity(session, snapshot.id, EntityType.ARTIFACT, build.artifact.code) if build.artifact else None
    return validate_build(
        build,
        hero_role=_str(hero.value("role")) if hero else None,
        artifact_role_lock=_str(artifact.value("role_lock")) if artifact else None,
        imprint_stat=_stat(hero.value("imprint.stat")) if hero else None,
        imprint_values=_float_map(hero.value("imprint.values")) if hero else None,
        ee_stat=_stat(hero.value("ee.stat")) if hero else None,
    )


def _print_issues(issues: list[Issue]) -> None:
    for issue in issues:
        typer.echo(f"  {issue.severity.value.upper():<7} {issue.rule:<20} {issue.field}: {issue.message}", err=True)


@roster_app.command("add")
def add(
    hero: Annotated[str, typer.Argument(help="Hero code (c2011) or exact name.")],
    stars: Annotated[int, typer.Option(min=1, max=6)] = 6,
    awakening: Annotated[int, typer.Option(min=0, max=6)] = 6,
    level: Annotated[int, typer.Option(min=1, max=60)] = 60,
    atk: StatOption = None,
    defense: Annotated[int | None, typer.Option("--def", min=0)] = None,
    hp: StatOption = None,
    speed: Annotated[int | None, typer.Option("--spd", min=0)] = None,
    crit_chance: Annotated[float | None, typer.Option("--cc", min=0, help="Percent.")] = None,
    crit_damage: Annotated[float | None, typer.Option("--cd", min=0, help="Percent.")] = None,
    effectiveness: Annotated[float | None, typer.Option("--eff", min=0, help="Percent.")] = None,
    effect_resistance: Annotated[float | None, typer.Option("--er", min=0, help="Percent.")] = None,
    dual_attack: Annotated[float | None, typer.Option("--dac", min=0, help="Percent.")] = None,
    cp: StatOption = None,
    arena: Annotated[bool, typer.Option("--arena", help="Mark as arena-relevant.")] = False,
    note: Annotated[str, typer.Option()] = "",
    from_json: Annotated[Path | None, typer.Option(help="Full build JSON (gear, artifact, imprint, EE...).")] = None,
    force: Annotated[bool, typer.Option(help="Store even if validation finds errors.")] = False,
) -> None:
    """Add an owned hero (manual entry). Rates are given in percent: --cc 100 means 100%."""
    engine = _engine()
    with session_scope(engine) as session:
        code = _resolve_hero_code(session, hero)
        base: dict[str, Any] = {"hero_code": code, "stars": stars, "awakening": awakening, "level": level}
        if from_json is not None:
            base = {**json.loads(from_json.read_text(encoding="utf-8-sig")), "hero_code": code}
        base.setdefault("captured_at", datetime.now(UTC).isoformat())
        base["source"] = BuildSource.MANUAL.value
        stats = _stat_overrides(
            atk, defense, hp, speed, crit_chance, crit_damage, effectiveness, effect_resistance, dual_attack
        )
        build = _validated_build(_apply(base, stats=stats, cp=cp))
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
    defense: Annotated[int | None, typer.Option("--def", min=0)] = None,
    hp: StatOption = None,
    speed: Annotated[int | None, typer.Option("--spd", min=0)] = None,
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
            base = json.loads(from_json.read_text(encoding="utf-8-sig"))
        elif row is not None:
            base = build_from_row(session, row).model_dump(mode="json")
        else:
            typer.echo("Error: this hero has no snapshot to edit; use --from-json", err=True)
            raise typer.Exit(code=2)
        base["hero_code"] = owned.hero_code
        base["captured_at"] = datetime.now(UTC).isoformat()
        base["source"] = BuildSource.MANUAL.value
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
        typer.echo(f"Error: --sort must be one of: {', '.join(_SORT_FIELDS)}", err=True)
        raise typer.Exit(code=2)
    engine = _engine()
    with session_scope(engine) as session:
        catalog = _catalog_heroes(session)
        rows = []
        for owned, snap in list_owned(session):
            entity = catalog.get(owned.hero_code)
            name = entity.name if entity else owned.hero_code
            if element and (entity is None or entity.value("element") != element.value):
                continue
            if role and (entity is None or entity.value("role") != role.value):
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
        catalog = _catalog_heroes(session)
        issues = _issues(session, build)
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
        typer.echo(f"  Imprint {build.imprint.grade.value} {build.imprint.stat.value} {build.imprint.value:g}")
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
    """Re-check the current build against the rules (and the catalog, when synced)."""
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
def export_cmd(target: Annotated[Path, typer.Argument(help="JSON file to write.")]) -> None:
    """Back up the whole roster (every snapshot) to a JSON file."""
    engine = _engine()
    with session_scope(engine) as session:
        data = export_roster(session, datetime.now(UTC))
    target.write_text(data.model_dump_json(indent=2), encoding="utf-8")
    typer.echo(f"Exported {len(data.heroes)} hero(es), {sum(len(h.snapshots) for h in data.heroes)} snapshot(s)")


@roster_app.command("import")
def import_cmd(source: Annotated[Path, typer.Argument(help="JSON file written by e7 roster export.")]) -> None:
    """Restore a roster backup. Idempotent: entries already present (same uid) are skipped."""
    try:
        data = RosterExport.model_validate_json(source.read_text(encoding="utf-8-sig"))
    except (OSError, ValidationError) as exc:
        typer.echo(f"Error: cannot read backup {source}: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    engine = _engine()
    with session_scope(engine) as session:
        report = import_roster(session, data)
    typer.echo(
        f"Imported {report.heroes_added} hero(es), {report.snapshots_added} snapshot(s); "
        f"{report.snapshots_skipped} already present"
    )


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


def _float_map(value: object) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    out = {str(k): float(v) for k, v in value.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    return out or None


def _same_build(new: HeroBuild, old: HeroBuild) -> bool:
    ignore = {"captured_at", "source"}
    return new.model_dump(exclude=ignore) == old.model_dump(exclude=ignore)
