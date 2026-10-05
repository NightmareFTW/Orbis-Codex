"""Import Fribbels' local files (M4, path A of SPEC D45; rules in D52, D53): the user runs Fribbels and its own
importer, and this module reads the files it leaves in its saves folder. Orbis Codex never captures game traffic.

Two formats, auto-detected:
- importer data (`gear.txt`, preferred, D53): written by Fribbels' importer when it reads the game; the game's own
  units (hero `code`, game `id`, stars `g`, awakening `z`) and items (wearer `p`/`ingameEquippedId`, game set `f`),
  so nothing is inferred (`_read_importer_data`);
- the optimizer save ("Save all optimizer data", D52), described below; checked on the user's real export.

Optimizer save format, from Fribbels' code:
- `app/js/lib/saves.js`: `{"heroes": [...], "items": [...]}`, the backend objects serialised by Gson;
- items (`backend/.../model/Item.java`): `gear` ("Weapon"…), `rank` ("Epic"…), `set` ("SpeedSet"…), `enhance`,
  `level` (item level), `main` and `substats` (`model/Stat.java`: `type` "AttackPercent"…, `value` with rates in
  percent, `rolls`, `modified`), `op` (the game's raw data: only on pieces imported from the game and not edited
  since), `id` (Fribbels'), `ingameId` (the game's item id), `ingameEquippedId` (the game hero wearing it at the last
  game import, `scanner.js`), `equippedById` (the Fribbels hero it is equipped on: Fribbels' planner state, which its
  optimizer's "Equip" changes without the game);
- heroes (`model/Hero.java`): `id` (Fribbels' own; the game hero id is not kept), `name`, `stars` (from the game when
  the hero was first imported, then editable in Fribbels' bonus dialog, 6 or 5), `equipment` by slot, and bonuses the
  user types in that dialog: `artifactName`/`artifactLevel`, `imprintNumber` (the hero's own imprint value, from
  `self_devotion`), `eeNumber` — strings, "None" when unset (`app/js/lib/dialog.js`).

How the game's gear is rebuilt (never Fribbels' plan): a Fribbels hero is matched to the game hero that wears most
of its game-imported Fribbels pieces and most of whose pieces it holds (no game hero can be matched twice); its gear
is then every piece the game import put on that game hero, at confidence 0.7 when Fribbels' equipment differs from it.
Pieces equipped in Fribbels but worn elsewhere (or in the inventory) in the game are a plan: not taken, with a note.
Pieces with no game data (added by hand, or an old screenshot import) are taken from Fribbels' equipment at
confidence 0.7. `game_link_conflict` tells when a match contradicts the roster (e.g. whole builds swapped in the
planner, which the save alone cannot tell from the game).

Values Fribbels estimates, taken with a lower confidence and a note: the +N of a game-imported piece below +15 (derived
from the number of enhancements: a multiple of 3, up to 2 below the real one); the stars. Substat rolls of a piece
added or edited by hand are Fribbels' guesses: not taken. A 0 main stat value or item level means "unknown" in
Fribbels: that piece is not used.

What is NOT taken, on purpose: Fribbels' hero stats and CP (computed by Fribbels, not read from the game; the displayed
stats stay a screen reading, MECH-STAT-01); the item `wss` score (Fribbels' metric, not the game's piece score);
awakening, level and skill enhancements (not in the save: kept from the roster, or assumed with confidence 0 and
reported); unequipped items (counted only).
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, ValidationError

from e7ac.catalog.names import NameIndex
from e7ac.catalog.resolve import ResolvedEntity
from e7ac.domain.codes import SourceId, Stat, is_hero_code, is_set_code
from e7ac.domain.roster import (
    ArtifactRef,
    BuildSource,
    ExclusiveEquipment,
    Gear,
    GearGrade,
    GearSlot,
    HeroBuild,
    Imprint,
    ImprintGrade,
    ImprintMode,
    StatValue,
    Substat,
)
from e7ac.roster.pieces import FRIBBELS_ID_PREFIX, GAME_ID_PREFIX, MAX_ENHANCE, combine, same_piece, visible
from e7ac.sources.fribbels import SET_PIECES

STAT_TYPES: Final[Mapping[str, Stat]] = {
    "Attack": Stat.ATK,
    "Health": Stat.HP,
    "Defense": Stat.DEF,
    "AttackPercent": Stat.ATK_PERCENT,
    "HealthPercent": Stat.HP_PERCENT,
    "DefensePercent": Stat.DEF_PERCENT,
    "CriticalHitChancePercent": Stat.CRIT_CHANCE,
    "CriticalHitDamagePercent": Stat.CRIT_DAMAGE,
    "EffectivenessPercent": Stat.EFFECTIVENESS,
    "EffectResistancePercent": Stat.EFFECT_RESISTANCE,
    "Speed": Stat.SPEED,
}
"""Fribbels `enums/StatType.java` names (its "Dac" is never a gear stat and is refused)."""
SLOTS: Final[Mapping[str, GearSlot]] = {slot.value.capitalize(): slot for slot in GearSlot}
"""Fribbels `enums/Gear.java`: "Weapon", "Helmet", "Armor", "Necklace", "Ring", "Boots"."""
RANKS: Final[Mapping[str, GearGrade]] = {grade.value.capitalize(): grade for grade in GearGrade}
"""Fribbels `enums/Rank.java`: "Normal", "Good", "Rare", "Heroic", "Epic"."""
SETS: Final[Mapping[str, str]] = {name: code for code, (name, _) in SET_PIECES.items()}
"""Fribbels set names ("SpeedSet") -> catalog set codes (`enums/Set.java`, MECH-GEAR-05)."""
UNSET: Final = frozenset({"", "none", "null"})
NO_WEARER: Final = frozenset({"", "0", "-1", "undefined", "null", "none"})
"""`ingameEquippedId` values taken as "not worn" (`"" + item.p` in scanner.js; the game's value is `assumed`)."""
USER_ENTERED: Final = 0.7
"""Confidence of what the user typed or edited in Fribbels (or Fribbels defaulted: stars), not read from the game."""
ESTIMATED: Final = 0.8
"""`gear.<slot>` confidence of a game-imported piece below +15: its +N is Fribbels' estimate."""
ASSUMED: Final = 0.0


class _Model(BaseModel):
    model_config = ConfigDict(
        extra="ignore", frozen=True, populate_by_name=True, allow_inf_nan=False, coerce_numbers_to_str=True
    )


class FribbelsStat(_Model):
    type: str
    value: float
    rolls: int | None = None
    modified: bool | None = None


class FribbelsItem(_Model):
    id: str | None = None
    ingameId: str | None = None  # noqa: N815 - the save file's own key
    ingameEquippedId: str | None = None  # noqa: N815
    equippedById: str | None = None  # noqa: N815
    gear: str
    rank: str
    set: str | None = None
    """Fribbels' set name; absent for sets Fribbels does not know yet (importer data)."""
    f: str | None = None
    """The game's set code (importer data only, e.g. "set_speed")."""
    enhance: int
    level: int
    main: FribbelsStat
    substats: list[FribbelsStat] = []
    op: list[Any] | None = None

    @property
    def from_game(self) -> bool:
        """Imported from the game and not edited since (an edit drops `op`)."""
        return bool(self.op) and bool(self.ingameId)

    @property
    def wearer(self) -> str | None:
        """The game hero wearing the piece at the last game import (None: not worn, or no game data)."""
        wearer = (self.ingameEquippedId or "").strip()
        return None if wearer.casefold() in NO_WEARER else wearer


class GameHero(_Model):
    """A hero of Fribbels' importer data (`gear.txt`): the game's own unit as Fribbels' scanner wrote it
    (`scanner.js` convertUnits: `stars = g`, `awaken = z`)."""

    id: str
    code: str
    name: str = ""
    g: int | None = None
    z: int | None = None


class FribbelsHero(_Model):
    id: str
    name: str
    stars: int | None = None
    artifactName: str | None = None  # noqa: N815
    artifactLevel: str | None = None  # noqa: N815
    imprintNumber: str | None = None  # noqa: N815
    eeNumber: str | None = None  # noqa: N815
    equipment: dict[str, Any] | None = None


@dataclass(slots=True)
class HeroImport:
    """One hero of the save: a build to store, or the reason it cannot be."""

    name: str
    fribbels_id: str
    hero_code: str | None = None
    build: HeroBuild | None = None
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    unusable: dict[GearSlot, str] = field(default_factory=dict)
    """Slots whose piece in the save could not be used, with the reason."""
    game_id: str | None = None
    """The game hero this Fribbels hero was matched to (None: no game gear taken)."""
    exact: bool = False
    """True for importer data: the hero code and game id are the game's own, nothing is inferred."""


@dataclass(slots=True)
class FribbelsSave:
    importer_data: bool = False
    """True for Fribbels' importer data (`gear.txt`, the game's own heroes and items), False for an optimizer save."""
    heroes: list[HeroImport] = field(default_factory=list)
    unused_items: int = 0
    """Readable pieces worn by no imported hero (inventory, plans, heroes Fribbels does not have)."""
    warnings: list[str] = field(default_factory=list)
    """Entries of the file that could not be read (each named), never dropped silently."""
    locations: dict[str, tuple[str | None, str]] = field(default_factory=dict)
    """External id (game and Fribbels ids) -> (Fribbels hero id or None, where the save puts the piece): on an imported
    hero, in the game's inventory, or worn by a game hero no Fribbels hero matched (owner None, label "worn ...")."""
    wearers: dict[str, str] = field(default_factory=dict)
    """External id -> the game hero wearing the piece, for pieces the save knows the wearer of."""

    def elsewhere(self, entry: HeroImport) -> dict[str, str]:
        """External ids the save puts somewhere other than on this hero. A piece worn by an unmatched game hero counts
        only when this hero has its own game match (else that unmatched hero may be this one)."""
        return {
            key: label
            for key, (owner, label) in self.locations.items()
            if owner != entry.fribbels_id and (entry.game_id is not None or not label.startswith("worn"))
        }


def game_link_conflict(entry: HeroImport, current: HeroBuild | None, save: FribbelsSave) -> str | None:
    """Why the save's match of this hero to a game hero contradicts the roster, or None. The roster's game pieces of
    the hero are worn, in the save, mostly by one other game hero: a Fribbels plan that moved whole builds (which
    the save alone cannot tell from the game), or gear swapped in the game."""
    if current is None or entry.game_id is None:
        return None
    wearers = Counter(
        save.wearers[piece.external_id]
        for piece in current.gear.values()
        if piece.external_id and piece.external_id in save.wearers
    )
    if not wearers:
        return None
    top, count = wearers.most_common(1)[0]
    if top == entry.game_id or count * 2 <= sum(wearers.values()):
        return None
    return (
        f"{count} of the roster's pieces of this hero are worn in the save by another game hero than the one its "
        "Fribbels equipment matches (a Fribbels plan, or gear swapped in the game): use --trust-save if the save is "
        "right"
    )


class FribbelsFileError(ValueError):
    """The file is not a Fribbels save (no `heroes` and `items` lists)."""


def read_fribbels_save(
    text: str,
    heroes: Mapping[str, ResolvedEntity],
    artifacts: Mapping[str, ResolvedEntity],
    *,
    captured_at: datetime,
) -> FribbelsSave:
    """Builds for every hero of a Fribbels save. `heroes`/`artifacts`: catalog entities by code."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FribbelsFileError(f"not a JSON file: {exc.msg} (line {exc.lineno})") from exc
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("heroes"), list)
        or not isinstance(data.get("items"), list)
    ):
        raise FribbelsFileError('not a Fribbels save: "heroes" and "items" lists expected ("Save all optimizer data")')
    if is_importer_data(data):
        return _read_importer_data(data, heroes, captured_at)
    save = FribbelsSave()
    saved = _items(data["items"], save.warnings)
    fribbels_heroes: list[FribbelsHero] = []
    for index, raw in enumerate(data["heroes"]):
        try:
            fribbels_heroes.append(FribbelsHero.model_validate(raw))
        except ValidationError as exc:
            save.warnings.append(f"hero #{index + 1}: not read ({_first_error(exc)})")
    known = {hero.id for hero in fribbels_heroes}
    orphans = Counter(s.item.equippedById for s in saved if s.item.equippedById and s.item.equippedById not in known)
    for unknown, count in sorted(orphans.items()):
        save.warnings.append(f"{count} item(s) equipped by an unknown hero id {unknown}: ignored")
    worn = _game_gear(fribbels_heroes, saved)
    names, artifact_names = catalog_names(heroes), catalog_names(artifacts)
    for hero in fribbels_heroes:
        entry = HeroImport(name=hero.name, fribbels_id=hero.id, game_id=worn[hero.id].game_id)
        save.heroes.append(entry)
        try:
            _hero(entry, hero, names, heroes, artifact_names, worn[hero.id], captured_at)
        except (ValidationError, ValueError) as exc:  # one odd hero never stops the import of the others
            entry.build = None
            reason = _first_error(exc) if isinstance(exc, ValidationError) else str(exc)
            entry.problems.append(f"not read: {reason}")
    used = {id(s) for w in worn.values() for s in w.pieces.values()}
    save.unused_items = sum(1 for s in saved if s.gear is not None and id(s) not in used)
    save.locations = _locations(fribbels_heroes, saved, worn)
    save.wearers = {key: s.item.wearer for s in saved if s.item.wearer for key in _keys(s)}
    save.warnings.extend(f"{s.label()}: not read ({s.problem})" for s in saved if s.gear is None and not s.reported)
    matched = {w.game_id for w in worn.values() if w.game_id}
    unmatched = [s for s in saved if s.item.wearer and s.item.wearer not in matched]
    if unmatched:
        save.warnings.append(
            f"{len(unmatched)} item(s) worn in the game by {len({s.item.wearer for s in unmatched})} hero(es) the save "
            "does not match to a Fribbels hero (not imported by Fribbels, or all their gear moved in it): not taken"
        )
    return save


def catalog_names(entities: Mapping[str, ResolvedEntity]) -> NameIndex:
    """Exact names of catalog entities: the chosen name plus the name Fribbels gives the entity when the sources
    disagree (the save uses Fribbels' names). A name shared by several codes stays ambiguous and is never matched."""
    names = NameIndex()
    for code, entity in entities.items():
        names.add(entity.name, code)
        resolved = entity.fields.get("name")
        for alternative in resolved.alternatives if resolved is not None else ():
            if SourceId.FRIBBELS in alternative.sources and isinstance(alternative.value, str):
                names.add(alternative.value, code)
    return names


def gear_of(item: FribbelsItem) -> Gear:
    """The domain gear piece of a Fribbels item (ValueError naming the field when it cannot be mapped). Substat rolls
    are taken only from pieces imported from the game (`op`): on pieces added or edited by hand they are guesses."""
    slot = _mapped(SLOTS, item.gear, "gear slot")
    grade = _mapped(RANKS, item.rank, "rank")
    if item.f is not None and is_set_code(item.f):
        set_code = item.f  # the game's own set code (importer data): also for sets Fribbels does not know
    elif item.set is not None:
        set_code = _mapped(SETS, item.set, "set")
    else:
        raise ValueError("no set")
    if item.level == 0:
        raise ValueError("item level unknown (0 in the save)")
    if item.main.value == 0:
        raise ValueError("main stat value unknown (0 in the save)")
    main = _stat(item.main)
    substats = tuple(
        Substat(stat=stat.stat, value=stat.value, rolls=s.rolls if item.from_game else None, modified=bool(s.modified))
        for s in item.substats
        for stat in (_stat(s),)
    )
    external = f"{GAME_ID_PREFIX}{item.ingameId}" if item.ingameId else None
    if external is None and item.id:
        external = f"{FRIBBELS_ID_PREFIX}{item.id}"
    return Gear(
        slot=slot,
        set_code=set_code,
        grade=grade,
        item_level=item.level,
        enhance=item.enhance,
        main=main,
        substats=substats,
        external_id=external,
    )


# ------------------------------------------------------------------------------------------------ items


@dataclass(slots=True, eq=False)
class _Saved:
    index: int
    item: FribbelsItem
    gear: Gear | None
    problem: str = ""
    reported: bool = False

    @property
    def slot(self) -> GearSlot | None:
        return self.gear.slot if self.gear is not None else SLOTS.get(self.item.gear)

    def label(self) -> str:
        return f"item #{self.index + 1}" + (f" ({self.item.ingameId or self.item.id})" if self.item.id else "")


@dataclass(slots=True)
class _Worn:
    """A hero's gear as the save shows it in the game, with notes on what was not taken."""

    pieces: dict[GearSlot, _Saved] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    unusable: dict[GearSlot, str] = field(default_factory=dict)
    game_id: str | None = None
    clean: bool = True
    """False when Fribbels' equipment of the hero and the game's gear of its match differ (the match is by majority)."""
    untracked: set[GearSlot] = field(default_factory=set)
    """Slots filled from Fribbels' own equipment because the save does not say who wears the piece in the game."""


def _items(raw_items: Sequence[Any], warnings: list[str]) -> list[_Saved]:
    saved: list[_Saved] = []
    for index, raw in enumerate(raw_items):
        try:
            item = FribbelsItem.model_validate(raw)
        except ValidationError as exc:
            warnings.append(f"item #{index + 1}: not read ({_first_error(exc)})")
            continue
        try:
            saved.append(_Saved(index, item, gear_of(item)))
        except (ValidationError, ValueError) as exc:
            reason = _first_error(exc) if isinstance(exc, ValidationError) else str(exc)
            saved.append(_Saved(index, item, None, reason))  # reported on its hero, or as a warning at the end
    return saved


def _game_gear(heroes: Sequence[FribbelsHero], saved: Sequence[_Saved]) -> dict[str, _Worn]:
    """Each Fribbels hero's gear as worn in the game (see the module docstring)."""
    planned: dict[str, list[_Saved]] = {}
    by_wearer: dict[str, list[_Saved]] = {}
    for s in saved:
        if s.item.equippedById:
            planned.setdefault(s.item.equippedById, []).append(s)
        if s.item.wearer:
            by_wearer.setdefault(s.item.wearer, []).append(s)
    worn = {hero.id: _Worn() for hero in heroes}
    for hero in heroes:
        tracked = [s for s in planned.get(hero.id, []) if s.item.ingameEquippedId is not None]
        wearers = Counter(s.item.wearer for s in tracked if s.item.wearer)
        if not wearers:
            continue
        top, count = wearers.most_common(1)[0]
        known, total = sum(wearers.values()), len(by_wearer[top])
        w = worn[hero.id]
        # most of its pieces are worn by that game hero, and most of that game hero's pieces are equipped here: no
        # other Fribbels hero can pass both, so a game hero is never matched twice
        if count * 2 > known and count * 2 > total:
            w.game_id, w.clean = top, count == known == total
            continue
        why = (
            f"its Fribbels equipment mixes pieces worn by {len(wearers)} game heroes"
            if count * 2 <= known
            else f"its Fribbels equipment holds only {count} of the {total} pieces worn by the game hero most of them "
            "come from"
        )
        w.notes.append(
            f"the save cannot tell which game hero this is ({why}): game gear not taken; import your account again "
            "in Fribbels before saving"
        )
    for hero in heroes:
        w = worn[hero.id]
        mine = planned.get(hero.id, [])
        tracked = [s for s in mine if s.item.ingameEquippedId is not None]
        plan: list[_Saved] = []
        if w.game_id is not None:
            candidates = by_wearer[w.game_id]
            moved = [s for s in candidates if s.item.equippedById != hero.id]
            if moved:
                w.notes.append(f"{_slots(moved)}: worn in the game, moved in Fribbels: the game's piece taken")
            plan = [s for s in tracked if s.item.wearer != w.game_id]
            _fill(w, hero, candidates)
            if not w.clean:
                w.notes.append(
                    f"matched to its game hero by most of its pieces, but Fribbels' equipment differs from the game "
                    f"(an optimizer plan?): its game gear has confidence {USER_ENTERED}"
                )
        else:
            plan = tracked
        if plan:
            w.notes.append(
                f"{_slots(plan)}: equipped in Fribbels only (worn by another hero or in the inventory in the "
                "game, e.g. an optimizer result): not taken"
            )
        for s in tracked if w.game_id is None else plan:
            s.reported = True
        untracked = [
            s for s in mine if s.item.ingameEquippedId is None and s.slot not in w.pieces and s.slot not in w.unusable
        ]
        before = set(w.pieces)
        _fill(w, hero, untracked)
        w.untracked = set(w.pieces) - before
    return worn


def _slots(pieces: Iterable[_Saved]) -> str:
    return ", ".join(sorted(str(s.item.gear).lower() for s in pieces))


def _fill(worn: _Worn, hero: FribbelsHero | None, candidates: Iterable[_Saved]) -> None:
    by_slot: dict[GearSlot | None, list[_Saved]] = {}
    for s in candidates:
        by_slot.setdefault(s.slot, []).append(s)
    for slot, pieces in by_slot.items():
        for s in pieces:
            s.reported = True
        if slot is None:
            worn.notes.extend(f"{s.label()}: not read ({s.problem})" for s in pieces)
            continue
        chosen = pieces[0] if len(pieces) == 1 else _tie_break(hero, slot, pieces)
        if chosen is None:
            listed = ", ".join(s.label() for s in pieces)
            worn.notes.append(f"{slot.value}: {len(pieces)} pieces in the save ({listed}): none taken")
            continue
        if len(pieces) > 1:
            worn.notes.append(f"{slot.value}: {len(pieces)} pieces in the save: {chosen.label()} taken")
        if chosen.gear is None:
            worn.unusable[slot] = chosen.problem
            worn.notes.append(f"{slot.value}: the save's piece is not usable ({chosen.problem})")
        else:
            worn.pieces[slot] = chosen


def _tie_break(hero: FribbelsHero | None, slot: GearSlot, pieces: Sequence[_Saved]) -> _Saved | None:
    """Several pieces claim one slot: the one Fribbels equips on the hero, if that settles it."""
    if hero is None:
        return None
    equipped = [s for s in pieces if s.item.equippedById == hero.id]
    if len(equipped) == 1:
        return equipped[0]
    shown = (hero.equipment or {}).get(slot.value.capitalize())
    shown_id = shown.get("id") if isinstance(shown, dict) else None
    matches = [s for s in pieces if shown_id and s.item.id == shown_id]
    return matches[0] if len(matches) == 1 else None


def _locations(
    heroes: Sequence[FribbelsHero], saved: Sequence[_Saved], worn: Mapping[str, _Worn]
) -> dict[str, tuple[str | None, str]]:
    """Where the save puts each piece it knows the place of: on an imported hero, in the game's inventory, or worn in
    the game by a hero no Fribbels hero matched."""
    owners = {id(s): hero for hero in heroes for s in worn[hero.id].pieces.values()}
    locations: dict[str, tuple[str | None, str]] = {}
    for s in saved:
        hero = owners.get(id(s))
        if hero is not None:
            place: tuple[str | None, str] = (hero.id, f"on {hero.name}")
        elif s.item.wearer is not None:
            place = (None, "worn in the game by another hero")
        elif s.item.ingameId and s.item.ingameEquippedId is not None:
            place = (None, "in the inventory")
        else:
            continue
        for key in _keys(s):
            locations[key] = place
    return locations


def _keys(s: _Saved) -> list[str]:
    """Every external id a roster piece may carry for this item (its game id, and Fribbels' id from older imports)."""
    keys = [f"{GAME_ID_PREFIX}{s.item.ingameId}"] if s.item.ingameId else []
    return keys + ([f"{FRIBBELS_ID_PREFIX}{s.item.id}"] if s.item.id else [])


# ------------------------------------------------------------------------------------------------ importer data


ABSENT_AWAKENING: Final = 0.8
"""Confidence of awakening 0 read from an absent `z` (the game leaves zero fields out; `assumed`)."""


def is_importer_data(data: Mapping[str, Any]) -> bool:
    """Fribbels' importer data (`gear.txt`): its heroes are the game's units (a hero `code` and the stars `g`)."""
    heroes = data.get("heroes")
    return isinstance(heroes, list) and any(isinstance(h, dict) and "code" in h and "g" in h for h in heroes[:50])


def _read_importer_data(
    data: Mapping[str, Any], heroes: Mapping[str, ResolvedEntity], captured_at: datetime
) -> FribbelsSave:
    """Builds from Fribbels' importer data: every game hero by its own code and game id, wearing the pieces the game
    puts on it (`ingameEquippedId`), with its stars and awakening. Nothing is inferred and no name is matched."""
    save = FribbelsSave(importer_data=True)
    saved = _items(data["items"], save.warnings)
    game_heroes: list[GameHero] = []
    for index, raw in enumerate(data["heroes"]):
        try:
            game_heroes.append(GameHero.model_validate(raw))
        except ValidationError as exc:
            save.warnings.append(f"hero #{index + 1}: not read ({_first_error(exc)})")
    by_wearer: dict[str, list[_Saved]] = {}
    for s in saved:
        if s.item.wearer:
            by_wearer.setdefault(s.item.wearer, []).append(s)
    worn: dict[str, _Worn] = {}
    for hero in game_heroes:
        w = worn[hero.id] = _Worn(game_id=hero.id)
        _fill(w, None, by_wearer.get(hero.id, []))
        entry = HeroImport(name=hero.name or hero.code, fribbels_id=hero.id, game_id=hero.id, exact=True)
        save.heroes.append(entry)
        try:
            _game_hero(entry, hero, heroes, w, captured_at)
        except (ValidationError, ValueError) as exc:  # one odd hero never stops the import of the others
            entry.build = None
            reason = _first_error(exc) if isinstance(exc, ValidationError) else str(exc)
            entry.problems.append(f"not read: {reason}")
    known = {hero.id for hero in game_heroes}
    used = {id(s) for w in worn.values() for s in w.pieces.values()}
    save.unused_items = sum(1 for s in saved if s.gear is not None and id(s) not in used)
    names = {hero.id: hero.name or hero.code for hero in game_heroes}
    for s in saved:
        wearer = s.item.wearer
        if wearer in known:
            place: tuple[str | None, str] = (wearer, f"on {names[wearer]}")
        elif wearer is not None:
            place = (None, "worn in the game by a hero missing from the file")
        else:
            place = (None, "in the inventory")
        for key in _keys(s):
            save.locations[key] = place
    save.wearers = {key: s.item.wearer for s in saved if s.item.wearer for key in _keys(s)}
    save.warnings.extend(f"{s.label()}: not read ({s.problem})" for s in saved if s.gear is None and not s.reported)
    missing = [s for s in saved if s.item.wearer and s.item.wearer not in known]
    if missing:
        save.warnings.append(f"{len(missing)} item(s) worn by hero ids missing from the file's heroes: not taken")
    return save


def _game_hero(
    entry: HeroImport,
    hero: GameHero,
    heroes: Mapping[str, ResolvedEntity],
    worn: _Worn,
    captured_at: datetime,
) -> None:
    if not is_hero_code(hero.code):
        entry.problems.append(f"{hero.code} is not a hero code (a monster or material): not imported")
        return
    if hero.code not in heroes:
        entry.problems.append(f"{hero.code} is not in the catalog (run e7 catalog sync)")
        return
    entry.hero_code = hero.code
    entry.name = heroes[hero.code].name
    if hero.g not in range(1, 7):
        entry.problems.append(f"stars {hero.g!r} not usable: not imported")
        return
    assert hero.g is not None
    stars = hero.g
    confidence: dict[str, float] = {"level": ASSUMED}
    if hero.z is None:
        awakening = 0
        confidence["awakening"] = ABSENT_AWAKENING
    elif 0 <= hero.z <= stars:
        awakening = hero.z
    else:
        entry.notes.append(f"awakening {hero.z!r} not usable with {stars} stars: assumed {stars}")
        awakening, confidence["awakening"] = stars, ASSUMED
    entry.notes.extend(worn.notes)
    entry.unusable = dict(worn.unusable)
    gear = _gear_confidence(worn, confidence, entry)
    entry.build = HeroBuild.model_validate(
        {
            "hero_code": hero.code,
            "stars": stars,
            "awakening": awakening,
            "level": stars * 10,  # MECH-HERO-01: the data has the hero's experience, not its level
            "gear": gear,
            "captured_at": captured_at,
            "source": BuildSource.FRIBBELS,
            "confidence": confidence,
        }
    )


def _gear_confidence(worn: _Worn, confidence: dict[str, float], entry: HeroImport) -> dict[GearSlot, Gear]:
    """The worn pieces, with lower confidences (and notes) for Fribbels' estimates."""
    gear: dict[GearSlot, Gear] = {}
    by_hand: list[str] = []
    estimated: list[str] = []
    for slot, s in worn.pieces.items():
        assert s.gear is not None
        gear[slot] = s.gear
        if not s.item.from_game:
            by_hand.append(slot.value)
        elif s.gear.enhance < MAX_ENHANCE:
            estimated.append(slot.value)
            confidence[f"gear.{slot.value}"] = ESTIMATED
        if not s.item.from_game or slot in worn.untracked or not worn.clean:
            confidence[f"gear.{slot.value}"] = USER_ENTERED
    if worn.untracked:
        entry.notes.append(
            f"{', '.join(sorted(slot.value for slot in worn.untracked))}: equipped in Fribbels, and the save does not "
            f"say who wears it in the game (added by hand or an old import): confidence {USER_ENTERED}"
        )
    if by_hand:
        entry.notes.append(
            f"{', '.join(by_hand)}: added or edited by hand in Fribbels: confidence {USER_ENTERED}, rolls not taken"
        )
    if estimated:
        entry.notes.append(
            f"{', '.join(estimated)}: +N below +15 is Fribbels' estimate (a multiple of 3, up to 2 below the real one)"
        )
    return gear


# ------------------------------------------------------------------------------------------------ heroes


def _hero(
    entry: HeroImport,
    hero: FribbelsHero,
    names: NameIndex,
    heroes: Mapping[str, ResolvedEntity],
    artifact_names: NameIndex,
    worn: _Worn,
    captured_at: datetime,
) -> None:
    if names.is_ambiguous(hero.name):
        entry.problems.append(f"several catalog heroes are called {hero.name!r}: not imported (enter it by code)")
        return
    code = names.lookup(hero.name)  # a database name, not OCR: exact (normalised) only, never fuzzy
    if code is None:
        entry.problems.append(f"no catalog hero is called {hero.name!r} (run e7 catalog sync)")
        return
    entry.hero_code = code
    confidence: dict[str, float] = {"awakening": ASSUMED, "level": ASSUMED}
    stars = hero.stars if hero.stars in range(1, 7) else None
    if stars is None:
        entry.notes.append(f"stars {hero.stars!r} not usable: assumed 6")
        stars = 6
        confidence["stars"] = ASSUMED
    else:
        confidence["stars"] = USER_ENTERED  # from the game at Fribbels' first import, then editable there
    entry.notes.extend(worn.notes)
    entry.unusable = dict(worn.unusable)
    gear = _gear_confidence(worn, confidence, entry)
    entity = heroes[code]
    data: dict[str, Any] = {
        "hero_code": code,
        "stars": stars,
        "awakening": stars,
        "level": stars * 10,  # MECH-HERO-01: the save has neither level nor awakening
        "gear": gear,
        "captured_at": captured_at,
        "source": BuildSource.FRIBBELS,
        "artifact": _artifact(hero, artifact_names, entry, confidence),
        "imprint": _imprint(hero, entity, entry, confidence),
        "exclusive_equipment": _exclusive(hero, entity, entry, confidence),
    }
    data["confidence"] = confidence
    entry.build = HeroBuild.model_validate(data)


def _artifact(
    hero: FribbelsHero, names: NameIndex, entry: HeroImport, confidence: dict[str, float]
) -> ArtifactRef | None:
    if _unset(hero.artifactName):
        return None
    assert hero.artifactName is not None
    shown = f"artifact {hero.artifactName!r} +{hero.artifactLevel}"
    if names.is_ambiguous(hero.artifactName):
        entry.notes.append(f"{shown}: several catalog artifacts have this name: not stored")
        return None
    code = names.lookup(hero.artifactName)
    try:
        level = _number(hero.artifactLevel)
    except ValueError:
        level = None
    if code is None or level is None or not level.is_integer() or not 0 <= level <= 30:
        entry.notes.append(f"{shown} not usable: not stored")
        return None
    confidence["artifact"] = USER_ENTERED
    return ArtifactRef(code=code, level=int(level))


def _imprint(
    hero: FribbelsHero, entity: ResolvedEntity, entry: HeroImport, confidence: dict[str, float]
) -> Imprint | None:
    """Fribbels' imprint field is the hero's own (self) imprint value, typed by the user (dialog.js)."""
    try:
        value = _number(hero.imprintNumber)
    except ValueError as exc:
        entry.notes.append(f"imprint {exc}: not stored")
        return None
    if value is None:
        return None
    stat_code, grades = entity.value("imprint.stat"), entity.value("imprint.values")
    if not isinstance(stat_code, str) or stat_code not in Stat._value2member_map_:
        entry.notes.append(f"imprint {hero.imprintNumber!r}: the catalog has no imprint stat for this hero")
        return None
    stat = Stat(stat_code)
    amount = round(value / 100, 6) if stat.is_rate else value
    matches = [
        g
        for g, v in (grades.items() if isinstance(grades, dict) else ())
        if isinstance(v, (int, float)) and abs(v - amount) < 1e-6 and g in ImprintGrade._value2member_map_
    ]
    grade = ImprintGrade(matches[0]) if len(matches) == 1 else None
    confidence["imprint"] = confidence["imprint.mode"] = USER_ENTERED
    confidence["imprint.grade"] = USER_ENTERED if grade is not None else ASSUMED
    return Imprint(grade=grade, stat=stat, value=amount, mode=ImprintMode.SELF)


def _exclusive(
    hero: FribbelsHero, entity: ResolvedEntity, entry: HeroImport, confidence: dict[str, float]
) -> ExclusiveEquipment | None:
    try:
        value = _number(hero.eeNumber)
    except ValueError as exc:
        entry.notes.append(f"exclusive equipment {exc}: not stored")
        return None
    if value is None:
        return None
    stat_code = entity.value("ee.stat")
    if not isinstance(stat_code, str) or stat_code not in Stat._value2member_map_:
        entry.notes.append(f"exclusive equipment {hero.eeNumber!r}: the catalog has no EE stat for this hero")
        return None
    stat = Stat(stat_code)
    confidence["exclusive_equipment"] = USER_ENTERED
    return ExclusiveEquipment(stat=stat, value=round(value / 100, 6) if stat.is_rate else value)


def _stat(raw: FribbelsStat) -> StatValue:
    stat = _mapped(STAT_TYPES, raw.type, "stat type")
    return StatValue(stat=stat, value=round(raw.value / 100, 6) if stat.is_rate else raw.value)


def _mapped[T](table: Mapping[str, T], key: str, what: str) -> T:
    try:
        return table[key]
    except KeyError:
        raise ValueError(f"unknown {what} {key!r}") from None


def _unset(value: object) -> bool:
    return value is None or (isinstance(value, str) and value.strip().casefold() in UNSET)


def _number(value: str | None) -> float | None:
    """None when unset; ValueError when set but not a finite number (never silently "unset")."""
    if _unset(value):
        return None
    try:
        number = float(str(value).strip().rstrip("%").strip())
    except ValueError:
        raise ValueError(f"{value!r} is not a number") from None
    if not math.isfinite(number):
        raise ValueError(f"{value!r} is not a finite number")
    return number


def _first_error(exc: ValidationError) -> str:
    error = exc.errors()[0]
    where = ".".join(str(p) for p in error["loc"])
    return f"{where}: {error['msg']}" if where else str(error["msg"])


# ------------------------------------------------------------------------------------------------ merging


def merge_with_current(
    build: HeroBuild,
    current: HeroBuild | None,
    *,
    unusable: Mapping[GearSlot, str] | None = None,
    elsewhere: Mapping[str, str] | None = None,
) -> tuple[HeroBuild, list[str]]:
    """The imported build on top of the hero's current one (SPEC D52). Returns the validated build and notes.

    - What the save does not carry is kept: level, awakening, skill enhancements; a slot missing from the save keeps
      the roster's piece, unless the save puts that piece elsewhere (`elsewhere`: external id -> place);
    - a piece the roster and the save both have is combined (game id and rolls from the save, the real +N and the
      score from a screen reading: `roster.pieces`);
    - stars, artifact, imprint and EE typed or defaulted in Fribbels replace the roster's only when the roster's are
      no more certain (e.g. an earlier import); otherwise the roster's are kept. Every difference gets a note;
    - the displayed stats and CP are kept only while nothing they depend on changed."""
    if current is None:
        return build, []
    notes: list[str] = []
    confidence = dict(build.confidence)
    update: dict[str, Any] = {"skills": current.skills, "note": current.note}
    _take(confidence, current.confidence, "skills")
    _merge_stars(build, current, update, confidence, notes)
    update["gear"] = _merge_gear(build, current, confidence, notes, unusable or {}, elsewhere or {})
    for name in ("artifact", "imprint", "exclusive_equipment"):
        _merge_bonus(name, build, current, update, confidence, notes)
    merged = HeroBuild.model_validate({**build.model_dump(), **update, "confidence": confidence})
    if _same_stat_inputs(merged, current):
        for name in ("final_stats", "cp"):
            _take(confidence, current.confidence, name)
        merged = HeroBuild.model_validate(
            {**merged.model_dump(), "final_stats": current.final_stats, "cp": current.cp, "confidence": confidence}
        )
    elif current.final_stats is not None or current.cp is not None:
        notes.append("the build changed: the displayed stats and CP of the last screen reading are dropped (rescan it)")
    return merged, notes


def _save_wins(name: str, build: HeroBuild, current: HeroBuild) -> bool:
    """A typed/defaulted value from the save replaces the roster's only when the roster's is no more certain."""
    theirs, mine = build.confidence.get(name, 1.0), current.confidence.get(name, 1.0)
    return theirs > ASSUMED and theirs >= mine


def _merge_stars(
    build: HeroBuild, current: HeroBuild, update: dict[str, Any], confidence: dict[str, float], notes: list[str]
) -> None:
    stars = build.stars
    if build.stars == current.stars or not _save_wins("stars", build, current):
        stars = current.stars
        if build.stars != current.stars and build.confidence.get("stars") != ASSUMED:
            notes.append(
                f"stars: the save says {build.stars}, the roster {current.stars}: kept the roster's "
                "(Fribbels' stars can be stale or set in its bonus dialog)"
            )
        if current.confidence.get("stars", 1.0) >= build.confidence.get("stars", 1.0):
            _take(confidence, current.confidence, "stars")
    else:
        notes.append(f"stars: the save's {build.stars} replaces the roster's {current.stars}")
    update["stars"] = stars
    for name in ("level", "awakening"):
        if confidence.get(name) == ASSUMED:
            update[name] = getattr(current, name)
            _take(confidence, current.confidence, name)
    level = update.get("level", build.level)
    if level > stars * 10:
        notes.append(f"level {level} is above the {stars}-star cap: lowered to {stars * 10}")
        update["level"], confidence["level"] = stars * 10, ASSUMED
    if update.get("awakening", build.awakening) > stars:
        notes.append(f"awakening {update.get('awakening', build.awakening)} is above {stars} stars: lowered to {stars}")
        update["awakening"], confidence["awakening"] = stars, ASSUMED


def _merge_gear(
    build: HeroBuild,
    current: HeroBuild,
    confidence: dict[str, float],
    notes: list[str],
    unusable: Mapping[GearSlot, str],
    elsewhere: Mapping[str, str],
) -> dict[GearSlot, Gear]:
    gear: dict[GearSlot, Gear] = {}
    for slot in GearSlot:
        new, old = build.gear.get(slot), current.gear.get(slot)
        key = f"gear.{slot.value}"
        if new is None:
            if old is None:
                continue
            where = elsewhere.get(old.external_id) if old.external_id else None
            if where is not None:
                notes.append(f"{slot.value}: the roster's piece is {where} in the save: removed from this hero")
            else:
                gear[slot] = old
                _take(confidence, current.confidence, key)
                reason = (
                    f"the save's piece is not usable ({unusable[slot]})"
                    if slot in unusable
                    else "not in the file (Fribbels leaves out items below its import +N and sets it does not know)"
                )
                notes.append(f"{slot.value}: {reason}: kept the roster's piece")
            continue
        if old is not None and same_piece(old, new):
            gear[slot] = combine(old, new)
            certainty = max(build.confidence.get(key, 1.0), current.confidence.get(key, 1.0))
            confidence.pop(key, None)
            if certainty < 1.0:
                confidence[key] = certainty
        else:
            gear[slot] = new
    return gear


def _merge_bonus(
    name: str,
    build: HeroBuild,
    current: HeroBuild,
    update: dict[str, Any],
    confidence: dict[str, float],
    notes: list[str],
) -> None:
    mine, theirs = getattr(current, name), getattr(build, name)
    known_none = mine is None and name in current.confidence  # e.g. an imprint the screen read as "Locked"
    if mine is None and not known_none:
        return
    label = name.replace("_", " ")
    if theirs is None or (mine is not None and _bonus_key(mine) == _bonus_key(theirs)):
        update[name] = mine
        _take(confidence, current.confidence, name)
        if isinstance(mine, Imprint) and isinstance(theirs, Imprint):
            update[name] = _same_imprint(mine, theirs, build, confidence, notes)
        return
    if _save_wins(name, build, current):
        notes.append(
            f"{label}: the save's {_describe(theirs)} replaces the roster's {_describe(mine)} "
            f"(confidence {current.confidence.get(name, 1.0):.2f})"
        )
        return
    update[name] = mine
    _take(confidence, current.confidence, name)
    notes.append(
        f"{label}: the save says {_describe(theirs)}, the roster {_describe(mine)}: kept the roster's "
        "(Fribbels' bonus stats are typed by hand; use e7 roster edit if the save is right)"
    )


def _same_imprint(
    mine: Imprint, theirs: Imprint, build: HeroBuild, confidence: dict[str, float], notes: list[str]
) -> Imprint:
    """The same imprint value: the save may tell its grade, taken from the self-imprint table (Fribbels' value is the
    hero's own imprint), so it is used only for a self imprint; a team mode read on screen is kept."""
    if mine.mode is not None and theirs.mode is not None and mine.mode is not theirs.mode:
        notes.append(f"imprint: the save treats it as {theirs.mode.value}, the roster read {mine.mode.value}: kept")
    mode = mine.mode or theirs.mode
    grade = mine.grade or (theirs.grade if mode is ImprintMode.SELF else None)
    for part, known, value in (("mode", mine.mode, mode), ("grade", mine.grade, grade)):
        if known is None and value is not None:
            confidence[f"imprint.{part}"] = build.confidence.get(f"imprint.{part}", 1.0)
    return mine.model_copy(update={"mode": mode, "grade": grade})


def _take(target: dict[str, float], source: Mapping[str, float], name: str) -> None:
    """Replace the confidences of `name` and its sub-fields (`name.x`) by those of `source`."""
    for key in [k for k in target if k == name or k.startswith(f"{name}.")]:
        del target[key]
    target.update({k: v for k, v in source.items() if k == name or k.startswith(f"{name}.")})


def _bonus_key(value: ArtifactRef | Imprint | ExclusiveEquipment) -> tuple[object, ...]:
    """What makes two artifacts, imprints or EEs the same bonus (an imprint's grade and mode only describe it)."""
    if isinstance(value, ArtifactRef):
        return value.code, value.level
    return value.stat, None if value.value is None else round(value.value, 6)


def _describe(value: ArtifactRef | Imprint | ExclusiveEquipment | None) -> str:
    if value is None:
        return "none"
    if isinstance(value, ArtifactRef):
        return f"{value.code} +{value.level}"
    if value.stat is None or value.value is None:
        return "an EE with an unknown stat"
    amount = f"{value.value * 100:g}%" if value.stat.is_rate else f"{value.value:g}"
    return f"{value.stat.value} {amount}"


def _same_stat_inputs(merged: HeroBuild, current: HeroBuild) -> bool:
    """Whether the displayed stats of `current` still describe `merged`. The imprint mode is left out: the merge never
    changes a known mode, and learning an unknown one changes nothing in the game."""

    def inputs(build: HeroBuild) -> tuple[object, ...]:
        gear = sorted((slot.value, visible(piece), piece.enhance) for slot, piece in build.gear.items())
        imprint = build.imprint and _bonus_key(build.imprint)
        ee = build.exclusive_equipment and _bonus_key(build.exclusive_equipment)
        return build.stars, build.awakening, build.level, gear, build.artifact, imprint, ee

    return inputs(merged) == inputs(current)
