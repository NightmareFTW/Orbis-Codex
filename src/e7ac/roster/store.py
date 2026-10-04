"""Roster persistence: owned heroes and immutable build snapshots (history)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from e7ac.domain.codes import Stat
from e7ac.domain.roster import (
    ArtifactRef,
    BuildSource,
    ExclusiveEquipment,
    FinalStats,
    Gear,
    GearGrade,
    GearSlot,
    HeroBuild,
    Imprint,
    ImprintGrade,
    SkillEnhancements,
    StatValue,
    Substat,
)
from e7ac.storage.models import GearRow, GearSubstatRow, HeroSnapshotRow, OwnedHeroRow, SnapshotGearRow


class RosterError(Exception):
    """Unknown owned hero or inconsistent roster data."""


def new_uid() -> str:
    return uuid.uuid4().hex


def add_owned_hero(
    session: Session,
    build: HeroBuild,
    *,
    arena_relevant: bool = False,
    note: str = "",
    uid: str | None = None,
    created_at: datetime | None = None,
    snapshot_uid: str | None = None,
) -> OwnedHeroRow:
    owned = OwnedHeroRow(
        uid=uid or new_uid(),
        hero_code=build.hero_code,
        arena_relevant=arena_relevant,
        note=note,
        created_at=created_at or datetime.now(UTC),
    )
    session.add(owned)
    session.flush()
    add_snapshot(session, owned, build, uid=snapshot_uid)
    return owned


def add_snapshot(
    session: Session, owned: OwnedHeroRow, build: HeroBuild, *, uid: str | None = None, make_current: bool = True
) -> HeroSnapshotRow:
    """Store a new immutable snapshot. Older snapshots are kept (history)."""
    if build.hero_code != owned.hero_code:
        raise RosterError(f"snapshot is for {build.hero_code} but owned hero #{owned.id} is {owned.hero_code}")
    if make_current:
        session.execute(
            update(HeroSnapshotRow).where(HeroSnapshotRow.owned_hero_id == owned.id).values(is_current=False)
        )
    stats = build.final_stats
    row = HeroSnapshotRow(
        uid=uid or new_uid(),
        owned_hero_id=owned.id,
        captured_at=build.captured_at,
        source=build.source.value,
        is_current=make_current,
        stars=build.stars,
        awakening=build.awakening,
        level=build.level,
        skill_s1=build.skills.s1,
        skill_s2=build.skills.s2,
        skill_s3=build.skills.s3,
        imprint_grade=build.imprint.grade.value if build.imprint and build.imprint.grade else None,
        imprint_stat=build.imprint.stat.value if build.imprint else None,
        imprint_value=build.imprint.value if build.imprint else None,
        ee_stat=_opt_value(build.exclusive_equipment.stat) if build.exclusive_equipment else None,
        ee_value=build.exclusive_equipment.value if build.exclusive_equipment else None,
        ee_option_code=build.exclusive_equipment.option_code if build.exclusive_equipment else None,
        ee_option_text=build.exclusive_equipment.option_text if build.exclusive_equipment else None,
        artifact_code=build.artifact.code if build.artifact else None,
        artifact_level=build.artifact.level if build.artifact else None,
        atk=stats.atk if stats else None,
        defense=stats.defense if stats else None,
        hp=stats.hp if stats else None,
        speed=stats.speed if stats else None,
        crit_chance=stats.crit_chance if stats else None,
        crit_damage=stats.crit_damage if stats else None,
        effectiveness=stats.effectiveness if stats else None,
        effect_resistance=stats.effect_resistance if stats else None,
        dual_attack=stats.dual_attack if stats else None,
        cp=build.cp,
        confidence_json=json.dumps(build.confidence, sort_keys=True),
        note=build.note,
    )
    session.add(row)
    session.flush()
    for slot, gear in sorted(build.gear.items()):
        session.add(SnapshotGearRow(snapshot_id=row.id, slot=slot.value, gear_id=_gear_row(session, gear).id))
    session.flush()
    return row


def get_owned(session: Session, owned_id: int) -> OwnedHeroRow:
    owned = session.get(OwnedHeroRow, owned_id)
    if owned is None:
        raise RosterError(f"no owned hero with id {owned_id}")
    return owned


def current_snapshot(session: Session, owned_id: int) -> HeroSnapshotRow | None:
    return session.scalars(
        select(HeroSnapshotRow).where(HeroSnapshotRow.owned_hero_id == owned_id, HeroSnapshotRow.is_current.is_(True))
    ).first()


def history(session: Session, owned_id: int) -> list[HeroSnapshotRow]:
    """Oldest first."""
    return list(
        session.scalars(
            select(HeroSnapshotRow)
            .where(HeroSnapshotRow.owned_hero_id == owned_id)
            .order_by(HeroSnapshotRow.captured_at, HeroSnapshotRow.id)
        )
    )


def list_owned(session: Session) -> list[tuple[OwnedHeroRow, HeroSnapshotRow | None]]:
    owned_rows = list(session.scalars(select(OwnedHeroRow).order_by(OwnedHeroRow.id)))
    current = {
        row.owned_hero_id: row
        for row in session.scalars(select(HeroSnapshotRow).where(HeroSnapshotRow.is_current.is_(True)))
    }
    return [(owned, current.get(owned.id)) for owned in owned_rows]


def make_current(session: Session, owned_id: int, snapshot: HeroSnapshotRow) -> None:
    """Move the 'current' marker (clear all first: the partial unique index allows one current row per hero)."""
    session.execute(update(HeroSnapshotRow).where(HeroSnapshotRow.owned_hero_id == owned_id).values(is_current=False))
    snapshot.is_current = True
    session.flush()


def set_arena_relevant(session: Session, owned_id: int, value: bool) -> None:
    get_owned(session, owned_id).arena_relevant = value
    session.flush()


def build_from_row(session: Session, row: HeroSnapshotRow) -> HeroBuild:
    owned = get_owned(session, row.owned_hero_id)
    gear: dict[GearSlot, Gear] = {}
    links = session.scalars(select(SnapshotGearRow).where(SnapshotGearRow.snapshot_id == row.id)).all()
    for link in links:
        gear[GearSlot(link.slot)] = _gear_from_row(session, link.gear_id)
    imprint = None
    if row.imprint_stat is not None and row.imprint_value is not None:
        grade = ImprintGrade(row.imprint_grade) if row.imprint_grade is not None else None
        imprint = Imprint(grade=grade, stat=Stat(row.imprint_stat), value=row.imprint_value)
    ee = None
    if any(v is not None for v in (row.ee_stat, row.ee_value, row.ee_option_code, row.ee_option_text)):
        ee = ExclusiveEquipment(
            stat=Stat(row.ee_stat) if row.ee_stat else None,
            value=row.ee_value,
            option_code=row.ee_option_code,
            option_text=row.ee_option_text,
        )
    artifact = None
    if row.artifact_code is not None and row.artifact_level is not None:
        artifact = ArtifactRef(code=row.artifact_code, level=row.artifact_level)
    final_stats = None
    if row.atk is not None:
        final_stats = FinalStats(
            atk=row.atk,
            defense=_req(row.defense),
            hp=_req(row.hp),
            speed=_req(row.speed),
            crit_chance=_req(row.crit_chance),
            crit_damage=_req(row.crit_damage),
            effectiveness=_req(row.effectiveness),
            effect_resistance=_req(row.effect_resistance),
            dual_attack=_req(row.dual_attack),
        )
    return HeroBuild(
        hero_code=owned.hero_code,
        stars=row.stars,
        awakening=row.awakening,
        level=row.level,
        skills=SkillEnhancements(s1=row.skill_s1, s2=row.skill_s2, s3=row.skill_s3),
        imprint=imprint,
        exclusive_equipment=ee,
        artifact=artifact,
        gear=gear,
        final_stats=final_stats,
        cp=row.cp,
        captured_at=row.captured_at,
        source=BuildSource(row.source),
        confidence=json.loads(row.confidence_json),
        note=row.note,
    )


def _gear_row(session: Session, gear: Gear) -> GearRow:
    fingerprint = gear.fingerprint()
    query = select(GearRow).where(GearRow.fingerprint == fingerprint)
    if gear.external_id is None:
        query = query.where(GearRow.external_id.is_(None))
    else:
        query = query.where(GearRow.external_id == gear.external_id)
    query = query.where(GearRow.score.is_(None) if gear.score is None else GearRow.score == gear.score)
    existing = session.scalars(query.order_by(GearRow.id)).first()
    if existing is not None:
        return existing
    row = GearRow(
        slot=gear.slot.value,
        set_code=gear.set_code,
        grade=gear.grade.value,
        item_level=gear.item_level,
        enhance=gear.enhance,
        main_stat=gear.main.stat.value,
        main_value=gear.main.value,
        score=gear.score,
        external_id=gear.external_id,
        fingerprint=fingerprint,
    )
    session.add(row)
    session.flush()
    for index, sub in enumerate(gear.substats):
        session.add(
            GearSubstatRow(
                gear_id=row.id,
                idx=index,
                stat=sub.stat.value,
                value=sub.value,
                rolls=sub.rolls,
                modified=sub.modified,
                reforged=sub.reforged,
            )
        )
    session.flush()
    return row


def _gear_from_row(session: Session, gear_id: int) -> Gear:
    row = session.get(GearRow, gear_id)
    if row is None:
        raise RosterError(f"missing gear row {gear_id}")
    subs = session.scalars(select(GearSubstatRow).where(GearSubstatRow.gear_id == gear_id).order_by(GearSubstatRow.idx))
    return Gear(
        slot=GearSlot(row.slot),
        set_code=row.set_code,
        grade=GearGrade(row.grade),
        item_level=row.item_level,
        enhance=row.enhance,
        main=StatValue(stat=Stat(row.main_stat), value=row.main_value),
        substats=tuple(
            Substat(stat=Stat(s.stat), value=s.value, rolls=s.rolls, modified=s.modified, reforged=s.reforged)
            for s in subs
        ),
        score=row.score,
        external_id=row.external_id,
    )


def _opt_value(stat: Stat | None) -> str | None:
    return None if stat is None else stat.value


def _req[T](value: T | None) -> T:
    if value is None:
        raise RosterError("snapshot row has partial final stats")
    return value
