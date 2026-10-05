"""Hero Info readings (gear, artifact, EE, imprint icon, stars) into the build: synthetic readings, always run."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from e7ac.catalog.facts import EntityType, Fact
from e7ac.catalog.resolve import ResolvedEntity, resolve
from e7ac.domain.codes import DataStatus, SourceId, Stat
from e7ac.domain.roster import (
    ArtifactRef,
    ExclusiveEquipment,
    Gear,
    GearGrade,
    GearSlot,
    HeroBuild,
    ImprintGrade,
    ImprintMode,
    StatValue,
)
from e7ac.roster.screen_gear import FRAME_GRADE_CONFIDENCE, ScreenCatalog
from e7ac.roster.screen_import import ScreenBuild, build_from_screen
from e7ac.vision.gear_panel import ArtifactPanel, GearPanel, Read
from e7ac.vision.hero_info import HeroImageReading, PieceRead, StatRead
from e7ac.vision.hero_screen import parse_hero_screen
from e7ac.vision.imprint_icon import ImprintIconReading
from e7ac.vision.ocr import Box
from e7ac.vision.sets import SetMatch
from e7ac.vision.star_row import StarRowReading
from tests.test_hero_screen import NOW, RENOA, STAT_ROWS, entity, equipment_screen

BOX = Box(0, 0, 10, 10)
INFO_ROWS = [(label, final, None) for label, final, _ in STAT_ROWS]  # Hero Info: no "▲" bonus


def read(stat: Stat | None, value: float | None, confidence: float = 0.95) -> StatRead:
    return StatRead(stat, value, "" if value is None else f"{value:g}", confidence if stat else 0.0)


def piece(
    slot: GearSlot,
    main: StatRead,
    subs: Sequence[StatRead],
    *,
    set_code: str | None = "set_speed",
    frame: str | None = "red",
    level: int | None = 90,
) -> PieceRead:
    match = SetMatch(set_code, set_code or "set_att", 0.9, "set_att", 0.3, BOX)
    return PieceRead(
        slot, main, tuple(subs), Read(level, 1.0), Read(15, 1.0), Read(80, 1.0), Read(frame, 1.0), match, ()
    )


SUBS = [read(Stat.CRIT_CHANCE, 0.1), read(Stat.CRIT_DAMAGE, 0.12), read(Stat.SPEED, 8.0), read(Stat.ATK_PERCENT, 0.1)]
FULL = [
    piece(GearSlot.WEAPON, read(Stat.ATK, 525.0), SUBS),
    piece(GearSlot.HELMET, read(Stat.HP, 2835.0), SUBS),
    piece(GearSlot.ARMOR, read(Stat.DEF, 310.0), SUBS, frame="purple"),
    piece(GearSlot.NECKLACE, read(Stat.CRIT_DAMAGE, 0.7), SUBS[:1] + SUBS[2:], set_code="set_cri"),
    piece(GearSlot.RING, read(Stat.HP_PERCENT, 0.65), SUBS, set_code="set_cri"),
    piece(GearSlot.BOOTS, read(Stat.SPEED, 45.0), SUBS[:3]),
]
ARTIFACT = ArtifactPanel(Read("efa22", 0.9), "Hostess", Read(15, 0.9), Read("Lv.Max/6", 0.9), BOX)


def icon(
    mode: ImprintMode | None = None, grade: ImprintGrade | None = None, locked: bool = False
) -> ImprintIconReading:
    return ImprintIconReading(
        mode, locked, 0.95 if mode else 0.0, "blue", 1, grade, 0.9 if grade else 0.0, None, {}, ()
    )


def images(
    pieces: Sequence[PieceRead] = FULL,
    *,
    present: bool = True,
    artifact: ArtifactPanel | None = ARTIFACT,
    exclusive: StatRead | None = None,
    active: Sequence[SetMatch] = (),
    imprint: ImprintIconReading | None = None,
    stars: StarRowReading | None = None,
) -> HeroImageReading:
    panel = GearPanel(
        present, BOX if present else None, Read(80, 1.0), 40.0, {}, artifact if present else None, None, ()
    )
    return HeroImageReading(
        panel,
        {p.slot: p for p in pieces} if present else {},
        exclusive,
        tuple(active),
        imprint or icon(),
        stars or StarRowReading(6, 6, ("awakened",) * 6, 0.9, ()),
        (),
    )


def set_entity(code: str, pieces: int) -> ResolvedEntity:
    fact = Fact(
        entity_type=EntityType.SET,
        entity_id=code,
        field="pieces",
        value=pieces,
        source=SourceId.FRIBBELS,
        status=DataStatus.COMMUNITY,
    )
    return resolve([fact])[0]


def scan(
    seen: HeroImageReading, existing: HeroBuild | None = None, catalog: ScreenCatalog | None = None
) -> ScreenBuild:
    reading = parse_hero_screen(equipment_screen(rows=INFO_ROWS))
    return build_from_screen(
        reading, {"c1193": RENOA}, captured_at=NOW, existing=existing, images=seen, catalog=catalog
    )


def test_hero_info_gear_artifact_ee_and_awakening_are_stored() -> None:
    result = scan(images(exclusive=read(Stat.CRIT_CHANCE, 0.12)))
    assert result.problems == [] and result.build is not None
    build = result.build
    assert set(build.gear) == set(GearSlot)
    weapon = build.gear[GearSlot.WEAPON]
    assert (weapon.grade, weapon.set_code, weapon.item_level, weapon.enhance, weapon.score) == (
        GearGrade.EPIC,
        "set_speed",
        90,
        15,
        80,
    )
    assert weapon.main == StatValue(stat=Stat.ATK, value=525.0)
    assert [s.stat for s in weapon.substats] == [s.stat for s in SUBS]
    assert all(s.rolls is None and not s.modified for s in weapon.substats)  # not shown on Hero Info
    assert build.gear[GearSlot.ARMOR].grade is GearGrade.HEROIC  # purple frame (MECH-GEAR-12, assumed)
    assert build.confidence["gear.weapon"] <= FRAME_GRADE_CONFIDENCE
    assert build.artifact == ArtifactRef(code="efa22", level=15)
    assert build.exclusive_equipment == ExclusiveEquipment(stat=Stat.CRIT_CHANCE, value=0.12)
    assert (build.stars, build.awakening, build.confidence["awakening"]) == (6, 6, 0.9)
    assert not any("awakening is not shown" in n for n in result.notes)


def test_an_unreadable_piece_is_never_stored_and_the_current_one_is_kept() -> None:
    first = scan(images())
    assert first.build is not None
    unreadable = [
        piece(GearSlot.WEAPON, read(Stat.ATK, 540.0), [*SUBS[:3], read(None, 0.1)]),  # a substat icon not recognised
        piece(GearSlot.HELMET, read(Stat.HP, 2700.0), SUBS, set_code=None),  # set badge needs review
        piece(GearSlot.ARMOR, read(Stat.DEF, 300.0), SUBS, frame=None),  # ambiguous frame colour
        piece(GearSlot.NECKLACE, read(Stat.CRIT_DAMAGE, 0.6), SUBS, level=None),
        *FULL[4:],
    ]
    again = scan(images(unreadable), existing=first.build)
    assert again.build is not None
    for slot in (GearSlot.WEAPON, GearSlot.HELMET, GearSlot.ARMOR, GearSlot.NECKLACE):
        assert again.build.gear[slot] == first.build.gear[slot]  # kept, never half-read
    notes = " ".join(again.notes)
    assert "weapon: not stored (substat 4" in notes and "helmet: not stored (set (best" in notes
    assert "armor: not stored (grade" in notes and "necklace: not stored (item level" in notes
    fresh = scan(images(unreadable))
    assert fresh.build is not None and GearSlot.WEAPON not in fresh.build.gear
    assert any("weapon: not stored" in n and "e7 roster edit" in n for n in fresh.notes)


def test_missing_panel_slot_or_artifact_keeps_what_the_roster_had() -> None:
    first = scan(images(exclusive=read(Stat.CRIT_CHANCE, 0.12)))
    assert first.build is not None
    no_panel = scan(images(present=False), existing=first.build)
    assert no_panel.build is not None and no_panel.build.gear == first.build.gear
    assert any("gear panel not found" in n for n in no_panel.notes)
    partial = scan(images(FULL[1:], artifact=None), existing=first.build)
    assert partial.build is not None and partial.build.gear[GearSlot.WEAPON] == first.build.gear[GearSlot.WEAPON]
    assert partial.build.artifact == first.build.artifact
    assert partial.build.exclusive_equipment == first.build.exclusive_equipment
    notes = " ".join(partial.notes)
    assert "weapon: no piece read" in notes and "artifact not read" in notes and "no exclusive equipment seen" in notes


def test_the_imprint_icon_settles_mode_and_grade() -> None:
    def scan_imprint(hero_fields: Mapping[str, object], imprint: ImprintIconReading) -> ScreenBuild:
        hero = entity("c1193", **hero_fields)
        reading = parse_hero_screen(equipment_screen(rows=INFO_ROWS))
        return build_from_screen(reading, {"c1193": hero}, captured_at=NOW, images=images(imprint=imprint))

    renoa = {k.replace(".", "__"): v.value for k, v in RENOA.fields.items()}
    team = scan_imprint(renoa, icon(ImprintMode.TEAM))  # the catalog's self table matches the value (SSS)
    assert team.build is not None and team.build.imprint is not None
    assert team.build.imprint.mode is ImprintMode.TEAM and team.build.confidence["imprint.mode"] == 0.95
    assert team.build.imprint.grade is None  # the SSS came from the self-imprint table: not a team grade
    assert any("the icon shows team, the catalog suggests self" in n for n in team.notes)
    clash = scan_imprint(renoa, icon(ImprintMode.SELF, ImprintGrade.B))  # the value matches SSS in the catalog
    assert clash.build is not None and clash.build.imprint is not None and clash.build.imprint.grade is None
    assert any("grade left unknown" in n for n in clash.notes)
    agree = scan_imprint(renoa, icon(ImprintMode.SELF, ImprintGrade.SSS))
    assert agree.build is not None and agree.build.imprint is not None
    assert (agree.build.imprint.mode, agree.build.imprint.grade) == (ImprintMode.SELF, ImprintGrade.SSS)
    assert not any(n.startswith("imprint '") and "unknown" in n for n in agree.notes)


def test_star_row_awakening_is_checked() -> None:
    odd = scan(images(stars=StarRowReading(6, 7, (), 0.9, ())))
    assert odd.build is not None and odd.build.awakening == 6  # assumed from the stars, the reading is impossible
    assert any("ignored" in n for n in odd.notes)
    five = scan(images(stars=StarRowReading(5, 2, (), 0.9, ())))
    assert five.build is not None and five.build.awakening == 2
    assert any("the star row shows 5*" in n for n in five.notes)


def test_piece_sets_are_cross_checked_with_the_cp_row() -> None:
    sets = {"set_speed": set_entity("set_speed", 4), "set_cri": set_entity("set_cri", 2)}
    catalog = ScreenCatalog(artifacts={}, sets=sets)
    ok = scan(images(active=[SetMatch(c, c, 0.9, "x", 0.3, BOX) for c in ("set_speed", "set_cri")]), catalog=catalog)
    assert not any(n.startswith("sets:") for n in ok.notes)
    wrong = scan(images(active=[SetMatch("set_speed", "set_speed", 0.9, "x", 0.3, BOX)] * 2), catalog=catalog)
    assert any("sets: the CP row shows set_speed, set_speed but the pieces complete" in n for n in wrong.notes)
    assert wrong.composition is not None  # the final-stat check ran (here it reports, never blocks)
    assert wrong.problems == [] and wrong.build is not None


def test_the_gear_is_validated_like_any_build() -> None:
    bad = [piece(GearSlot.WEAPON, read(Stat.ATK, 525.0), SUBS, level=250), *FULL[1:]]
    result = scan(images(bad))
    assert result.build is not None and GearSlot.WEAPON not in result.build.gear
    assert any("weapon: not stored (invalid piece" in n for n in result.notes)
    assert isinstance(result.build.gear[GearSlot.HELMET], Gear)


# ------------------------------------------------------------------------------------------------ M7 review fixes


def test_an_artifact_level_out_of_range_is_a_note_not_a_crash() -> None:
    first = scan(images())
    assert first.build is not None
    bad = ArtifactPanel(Read("efa22", 0.9), "Hostess", Read(38, 0.4), Read("Lv.Max/11", 0.4), BOX)  # "+30" misread
    again = scan(images(artifact=bad), existing=first.build)
    assert again.build is not None and again.build.artifact == first.build.artifact
    assert any("artifact not read (efa22 +38" in n for n in again.notes)


def test_a_rescan_keeps_what_the_screen_cannot_show_of_the_same_piece() -> None:
    first = scan(images())
    assert first.build is not None
    weapon = first.build.gear[GearSlot.WEAPON]
    rolled = weapon.model_copy(
        update={
            "external_id": "fribbels-123",
            "substats": tuple(s.model_copy(update={"rolls": 2, "reforged": True}) for s in weapon.substats),
        }
    )
    existing = first.build.model_copy(update={"gear": {**first.build.gear, GearSlot.WEAPON: rolled}})
    again = scan(images(), existing=existing)
    assert again.build is not None and again.build.gear[GearSlot.WEAPON] == rolled  # same piece: details kept
    changed = [piece(GearSlot.WEAPON, read(Stat.ATK, 540.0), SUBS), *FULL[1:]]
    other = scan(images(changed), existing=existing)
    assert other.build is not None and other.build.gear[GearSlot.WEAPON].external_id is None  # another piece


def test_reader_warnings_and_contract_errors_block_only_their_piece() -> None:
    gap = piece(GearSlot.WEAPON, read(Stat.ATK, 525.0), SUBS[:3])
    gap = PieceRead(
        gap.slot,
        gap.main,
        gap.subs,
        gap.item_level,
        gap.enhance,
        gap.score,
        gap.frame,
        gap.set_match,
        ("gap between two value rows: a row was probably missed",),
    )
    rate = piece(GearSlot.HELMET, read(Stat.HP, 2835.0), [read(Stat.CRIT_CHANCE, 6.5), *SUBS[1:]])  # "650%"
    result = scan(images([gap, rate, *FULL[2:]]))
    assert result.build is not None and result.problems == []
    assert GearSlot.WEAPON not in result.build.gear and GearSlot.HELMET not in result.build.gear
    assert GearSlot.ARMOR in result.build.gear
    notes = " ".join(result.notes)
    assert "weapon: not stored (gap between two value rows" in notes and "helmet: not stored (invalid value" in notes


def test_pieces_that_complete_sets_without_cp_icons_are_reported() -> None:
    sets = {"set_speed": set_entity("set_speed", 4), "set_cri": set_entity("set_cri", 2)}
    result = scan(images(active=[]), catalog=ScreenCatalog(artifacts={}, sets=sets))
    assert any("the CP row shows none but the pieces complete" in n for n in result.notes)


def test_a_self_icon_keeps_the_warning_that_the_value_is_in_no_grade() -> None:
    renoa = {k.replace(".", "__"): v.value for k, v in RENOA.fields.items()}
    hero = entity("c1193", **{**renoa, "imprint__values": {"B": 0.07, "SSS": 0.21}})  # the screen shows 15%
    reading = parse_hero_screen(equipment_screen(rows=INFO_ROWS))
    result = build_from_screen(reading, {"c1193": hero}, captured_at=NOW, images=images(imprint=icon(ImprintMode.SELF)))
    assert any("in no grade of the catalog's self-imprint table" in n for n in result.notes)
