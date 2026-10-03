"""Roster JSON backup: lossless export/import (all heroes with their full snapshot history).

Import is idempotent: owned heroes and snapshots are matched by their `uid`, so re-importing a backup adds nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from e7ac.domain.roster import HeroBuild
from e7ac.roster.store import add_snapshot, build_from_row, history, list_owned
from e7ac.storage.models import HeroSnapshotRow, OwnedHeroRow

FORMAT: Final = "orbis-codex-roster"


class SnapshotExport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uid: str
    is_current: bool
    build: HeroBuild


class OwnedHeroExport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uid: str
    hero_code: str
    arena_relevant: bool
    note: str
    created_at: datetime
    snapshots: list[SnapshotExport]


class RosterExport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["orbis-codex-roster"] = "orbis-codex-roster"
    version: Literal[1] = 1
    exported_at: datetime
    heroes: list[OwnedHeroExport]


@dataclass(frozen=True, slots=True)
class ImportReport:
    heroes_added: int
    snapshots_added: int
    snapshots_skipped: int


def export_roster(session: Session, exported_at: datetime) -> RosterExport:
    heroes = []
    for owned, _ in list_owned(session):
        snapshots = [
            SnapshotExport(uid=row.uid, is_current=row.is_current, build=build_from_row(session, row))
            for row in history(session, owned.id)
        ]
        heroes.append(
            OwnedHeroExport(
                uid=owned.uid,
                hero_code=owned.hero_code,
                arena_relevant=owned.arena_relevant,
                note=owned.note,
                created_at=owned.created_at,
                snapshots=snapshots,
            )
        )
    return RosterExport(exported_at=exported_at, heroes=heroes)


def import_roster(session: Session, data: RosterExport) -> ImportReport:
    heroes_added = snapshots_added = skipped = 0
    known_snapshots = set(session.scalars(select(HeroSnapshotRow.uid)))
    for hero in data.heroes:
        owned = session.scalars(select(OwnedHeroRow).where(OwnedHeroRow.uid == hero.uid)).first()
        created = owned is None
        if owned is None:
            owned = OwnedHeroRow(
                uid=hero.uid,
                hero_code=hero.hero_code,
                arena_relevant=hero.arena_relevant,
                note=hero.note,
                created_at=hero.created_at,
            )
            session.add(owned)
            session.flush()
            heroes_added += 1
        added_here: list[HeroSnapshotRow] = []
        for snap in sorted(hero.snapshots, key=lambda s: s.build.captured_at):
            if snap.uid in known_snapshots:
                skipped += 1
                continue
            row = add_snapshot(session, owned, snap.build, uid=snap.uid, make_current=False)
            row.is_current = snap.is_current and created
            known_snapshots.add(snap.uid)
            added_here.append(row)
            snapshots_added += 1
        # existing hero: the newest snapshot (old or imported) becomes current;
        # new hero: keep the backup's current flag, or the newest if the backup had none
        if added_here and (not created or not any(r.is_current for r in added_here)):
            _make_latest_current(session, owned.id)
        session.flush()
    return ImportReport(heroes_added=heroes_added, snapshots_added=snapshots_added, snapshots_skipped=skipped)


def _make_latest_current(session: Session, owned_id: int) -> None:
    rows = history(session, owned_id)
    for row in rows:
        row.is_current = row is rows[-1]
