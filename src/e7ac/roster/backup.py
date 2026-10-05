"""Roster JSON backup: lossless export/import (all heroes with their full snapshot history).

Import is idempotent: owned heroes and snapshots are matched by their `uid`, so re-importing a backup adds nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from e7ac.domain.codes import is_hero_code
from e7ac.domain.roster import HeroBuild
from e7ac.roster.store import RosterError, add_snapshot, build_from_row, current_snapshot, history, list_owned
from e7ac.roster.store import make_current as move_current
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
    created_at: AwareDatetime
    snapshots: list[SnapshotExport]
    game_id: str | None = None
    """The game's id of this copy (SPEC D53); absent in backups written before it."""

    @field_validator("hero_code")
    @classmethod
    def _hero_code(cls, value: str) -> str:
        if not is_hero_code(value):
            raise ValueError(f"not a hero code: {value!r} (expected e.g. 'c2011')")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> OwnedHeroExport:
        wrong = [s.uid for s in self.snapshots if s.build.hero_code != self.hero_code]
        if wrong:
            raise ValueError(f"hero {self.uid} ({self.hero_code}): snapshots for another hero: {', '.join(wrong)}")
        if sum(s.is_current for s in self.snapshots) > 1:
            raise ValueError(f"hero {self.uid}: more than one current snapshot")
        return self


class RosterExport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["orbis-codex-roster"] = "orbis-codex-roster"
    version: Literal[1] = 1
    exported_at: AwareDatetime
    heroes: list[OwnedHeroExport]

    @model_validator(mode="after")
    def _unique_uids(self) -> RosterExport:
        for kind, uids in (
            ("hero", [h.uid for h in self.heroes]),
            ("snapshot", [s.uid for h in self.heroes for s in h.snapshots]),
        ):
            duplicates = sorted({u for u in uids if uids.count(u) > 1})
            if duplicates:
                raise ValueError(f"duplicate {kind} uid(s): {', '.join(duplicates)}")
        return self


@dataclass(frozen=True, slots=True)
class ImportReport:
    heroes_added: int
    snapshots_added: int
    snapshots_skipped: int
    current_changed: tuple[str, ...] = ()
    """Owned-hero uids (already in the roster) whose current build changed because the backup had a newer one."""
    added_snapshot_ids: tuple[int, ...] = field(default=())


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
                game_id=owned.game_id,
            )
        )
    return RosterExport(exported_at=exported_at, heroes=heroes)


def import_roster(session: Session, data: RosterExport) -> ImportReport:
    """Merge a backup into the roster (idempotent; nothing is ever deleted or overwritten).

    - A hero new to the roster keeps the backup's current snapshot (or its newest one).
    - A hero already in the roster: its current build only moves to an imported snapshot captured *after* it.
    - A uid that already belongs to another hero, or a hero uid with another hero code, is a conflict: RosterError
      (the caller rolls the whole import back)."""
    heroes_added = snapshots_added = skipped = 0
    changed: list[str] = []
    added_ids: list[int] = []
    owner_of = {
        uid: owner for uid, owner in session.execute(select(HeroSnapshotRow.uid, HeroSnapshotRow.owned_hero_id))
    }
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
                game_id=_free_game_id(session, hero.game_id),
            )
            session.add(owned)
            session.flush()
            heroes_added += 1
        elif owned.hero_code != hero.hero_code:
            raise RosterError(
                f"backup hero {hero.uid} is {hero.hero_code} but the roster has that uid as {owned.hero_code}"
            )
        previous = current_snapshot(session, owned.id)
        added: list[tuple[SnapshotExport, HeroSnapshotRow]] = []
        for snap in sorted(hero.snapshots, key=lambda s: s.build.captured_at):
            owner = owner_of.get(snap.uid)
            if owner == owned.id:
                skipped += 1
                continue
            if owner is not None:
                raise RosterError(f"snapshot uid {snap.uid} already belongs to another hero in the roster")
            row = add_snapshot(session, owned, snap.build, uid=snap.uid, make_current=False)
            owner_of[snap.uid] = owned.id
            added.append((snap, row))
            added_ids.append(row.id)
            snapshots_added += 1
        if not added:
            continue
        newest = max(added, key=lambda pair: (pair[1].captured_at, pair[1].id))[1]
        if created or previous is None:
            flagged = [row for snap, row in added if snap.is_current]
            move_current(session, owned.id, flagged[0] if flagged else newest)
        elif newest.captured_at > previous.captured_at:
            move_current(session, owned.id, newest)
            changed.append(owned.uid)
        session.flush()
    return ImportReport(
        heroes_added=heroes_added,
        snapshots_added=snapshots_added,
        snapshots_skipped=skipped,
        current_changed=tuple(changed),
        added_snapshot_ids=tuple(added_ids),
    )


def _free_game_id(session: Session, game_id: str | None) -> str | None:
    """The backup's game id, unless another roster copy already has it (the unique link stays with that copy)."""
    if game_id is None:
        return None
    taken = session.scalars(select(OwnedHeroRow.id).where(OwnedHeroRow.game_id == game_id)).first()
    return None if taken is not None else game_id
