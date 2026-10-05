"""Put what a Hero Info capture shows beyond the stats (M7) into the build: gear, artifact, EE, imprint icon, stars.

Rules (SPEC D51):
- a gear piece is stored only when every field the domain needs was read: main stat and every substat (icon + value,
  MECH-GEAR-11), set (MECH-GEAR-15), grade from the frame colour (MECH-GEAR-12: red = epic, purple = heroic, an
  `assumed` mapping, so the piece confidence is at most FRAME_GRADE_CONFIDENCE), item level and "+N" (MECH-GEAR-14).
  Otherwise the slot keeps the current build's piece, or stays empty, with a note naming what was not read;
- substat rolls and the modified/reforged flags are not shown on Hero Info: rolls stay unknown, the flags False;
- an artifact or EE that is not read keeps the current build's, with a note; their absence on screen is never taken
  as "none" when the roster had one (OCR may have missed them);
- the imprint icon (MECH-IMP-02) is direct evidence: it overrides the mode inferred from the catalog (SPEC D42), a
  disagreement is reported; two disagreeing grades give no grade;
- the star row gives the awakening (MECH-HERO-03); a star count that disagrees with the level cap is reported;
- the completed sets of the stored pieces must match the active-set icons of the CP row (MECH-GEAR-05/07): warning.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from pydantic import ValidationError

from e7ac.catalog.resolve import ResolvedEntity
from e7ac.domain.roster import (
    ArtifactRef,
    ExclusiveEquipment,
    Gear,
    GearGrade,
    GearSlot,
    HeroBuild,
    Imprint,
    ImprintMode,
    StatValue,
    Substat,
)

if TYPE_CHECKING:  # the image readers import OpenCV: loaded only when a capture is read
    from e7ac.vision.hero_info import HeroImageReading, PieceRead
    from e7ac.vision.imprint_icon import ImprintIconReading

_SETTLED_BY_ICON: Final = ("self/team is unknown", "self/team and the grade are unknown", "so it is the team imprint")
GRADE_OF_FRAME: Final[Mapping[str, GearGrade]] = {"red": GearGrade.EPIC, "purple": GearGrade.HEROIC}
"""MECH-GEAR-12: community knowledge, `assumed` (NV-26)."""
FRAME_GRADE_CONFIDENCE: Final = 0.8


@dataclass(frozen=True, slots=True)
class ScreenCatalog:
    """Catalog entities the Hero Info import needs besides the heroes."""

    artifacts: Mapping[str, ResolvedEntity]
    sets: Mapping[str, ResolvedEntity]


def apply_images(images: HeroImageReading, existing: HeroBuild | None, data: dict[str, Any], notes: list[str]) -> None:
    """Gear, artifact and EE from the capture into `data` (a HeroBuild dump being assembled)."""
    notes.extend(images.warnings)
    _gear(images, existing, data, notes)
    _artifact(images, existing, data, notes)
    _exclusive(images, existing, data, notes)


def apply_stars(images: HeroImageReading, existing: HeroBuild | None, data: dict[str, Any], notes: list[str]) -> None:
    """Stars and awakening from the star row (after the level line set `stars` from the level cap)."""
    confidence: dict[str, float] = data["confidence"]
    stars = images.stars
    if stars.stars is not None and stars.stars != data.get("stars"):
        notes.append(f"stars: the star row shows {stars.stars}*, the level cap implies {data.get('stars')}*")
    if stars.awakened is None:
        return
    if stars.awakened > data["stars"]:
        notes.append(f"awakening: the star row shows {stars.awakened} awakened stars for {data['stars']}* (ignored)")
        return
    data["awakening"] = stars.awakened
    confidence["awakening"] = stars.confidence
    notes[:] = [n for n in notes if not n.startswith("awakening is not shown on this screen")]
    if existing is not None and existing.awakening != stars.awakened:
        notes.append(f"awakening: the star row shows {stars.awakened}, the roster had {existing.awakening}")


def apply_imprint_icon(icon: ImprintIconReading, text_locked: bool, data: dict[str, Any], notes: list[str]) -> None:
    """Mode and grade from the imprint icon onto the imprint read from the text (MECH-IMP-02)."""
    from e7ac.vision.imprint_icon import locked_mismatch

    confidence: dict[str, float] = data["confidence"]
    mismatch = locked_mismatch(icon, text_locked)
    if mismatch is not None:
        notes.append(f"imprint: {mismatch}")
    imprint = data.get("imprint")
    if not isinstance(imprint, Imprint):
        return
    mode, grade = imprint.mode, imprint.grade
    if icon.mode is not None:
        # the catalog-inference notes said the mode (or a team grade) could not be told: the icon tells it now
        notes[:] = [n for n in notes if not (n.startswith("imprint '") and any(p in n for p in _SETTLED_BY_ICON))]
        if mode is not None and mode is not icon.mode:
            notes.append(f"imprint mode: the icon shows {icon.mode.value}, the catalog suggests {mode.value} (icon)")
            if mode is ImprintMode.SELF:  # that grade came from the self-imprint table: meaningless for a team imprint
                grade = None
                confidence["imprint.grade"] = 0.0
        mode = icon.mode
        confidence["imprint.mode"] = icon.mode_confidence
    if icon.grade is not None:
        if grade is not None and grade is not icon.grade:
            notes.append(
                f"imprint grade: the icon shows {icon.grade.value}, the value matches {grade.value}: grade left unknown"
            )
            grade = None
            confidence["imprint.grade"] = 0.0
        else:
            grade = icon.grade
            confidence["imprint.grade"] = icon.grade_confidence
    data["imprint"] = imprint.model_copy(update={"mode": mode, "grade": grade})


def set_consistency(build: HeroBuild, images: HeroImageReading, sets: Mapping[str, ResolvedEntity]) -> list[str]:
    """Completed sets of the stored pieces vs the active-set icons of the CP row: warnings only (MECH-GEAR-05/07)."""
    if not images.panel.present or not images.active_sets or len(build.gear) < len(GearSlot):
        return []
    if any(match.set_code is None for match in images.active_sets):
        return ["sets: an active-set icon of the CP row needs review, so the piece sets are not cross-checked"]
    completed: Counter[str] = Counter()
    for code, count in Counter(g.set_code for g in build.gear.values()).items():
        pieces = sets[code].value("pieces") if code in sets else None
        if not isinstance(pieces, int) or isinstance(pieces, bool) or pieces <= 0:
            return [f"sets: the catalog has no piece count for {code}, so the sets are not cross-checked"]
        if count // pieces:
            completed[code] = count // pieces
    active = Counter(match.set_code for match in images.active_sets if match.set_code is not None)
    if completed == active:
        return []
    shown = ", ".join(sorted(active.elements())) or "none"
    found = ", ".join(sorted(completed.elements())) or "none"
    return [f"sets: the CP row shows {shown} but the pieces complete {found} (a set badge or an icon may be misread)"]


# ------------------------------------------------------------------------------------------------ gear


def _gear(images: HeroImageReading, existing: HeroBuild | None, data: dict[str, Any], notes: list[str]) -> None:
    confidence: dict[str, float] = data["confidence"]
    current = existing.gear if existing is not None else {}
    if not images.panel.present:
        if current:
            notes.append("gear panel not found on the screen: the current gear is kept")
        return
    gear: dict[GearSlot, Gear] = {}
    for slot in GearSlot:
        piece = images.pieces.get(slot)
        if piece is None:
            if slot in current:
                gear[slot] = current[slot]
                notes.append(
                    f"{slot.value}: no piece read on the screen (empty slot or not found): kept the current one"
                )
            continue
        made, certainty, missing = _piece(piece)
        if made is not None:
            gear[slot] = made
            confidence[f"gear.{slot.value}"] = certainty
            continue
        what = "; ".join(missing)
        if slot in current:
            gear[slot] = current[slot]
            notes.append(f"{slot.value}: not stored ({what}): kept the current piece")
        else:
            notes.append(f"{slot.value}: not stored ({what}): rescan, or enter it with e7 roster edit")
    data["gear"] = gear


def _piece(piece: PieceRead) -> tuple[Gear | None, float, list[str]]:
    missing: list[str] = []
    rows = [("main stat", piece.main), *((f"substat {i}", sub) for i, sub in enumerate(piece.subs, start=1))]
    for name, row in rows:
        if row.stat is None or row.value is None:
            missing.append(f"{name} {row.raw!r}: {row.note or 'not read'}")
    match = piece.set_match
    if match is None or match.set_code is None:
        detail = "no set icons" if match is None else f"best {match.best} {match.score:.2f}, margin {match.margin:.2f}"
        missing.append(f"set ({detail})")
    grade = GRADE_OF_FRAME.get(piece.frame.value or "")
    if grade is None:
        missing.append(f"grade (frame colour: {piece.frame.note or 'not clear'})")
    for name, field in (("item level", piece.item_level), ("enhancement", piece.enhance)):
        if field.value is None:
            missing.append(f"{name} ({field.note or 'not read'})")
    if missing or match is None or match.set_code is None or grade is None:
        return None, 0.0, missing
    main_stat, main_value = piece.main.stat, piece.main.value
    assert main_stat is not None and main_value is not None and piece.item_level.value is not None
    assert piece.enhance.value is not None
    try:
        gear = Gear(
            slot=piece.slot,
            set_code=match.set_code,
            grade=grade,
            item_level=piece.item_level.value,
            enhance=piece.enhance.value,
            main=StatValue(stat=main_stat, value=main_value),
            substats=tuple(Substat(stat=s.stat, value=s.value) for s in piece.subs if s.stat and s.value is not None),
            score=piece.score.value,
        )
    except ValidationError as exc:
        return None, 0.0, [f"invalid piece: {exc.errors()[0]['msg']}"]
    parts = [piece.main.confidence, *(s.confidence for s in piece.subs), match.confidence, FRAME_GRADE_CONFIDENCE]
    parts += [piece.item_level.confidence, piece.enhance.confidence]
    return gear, round(min(parts), 3), []


# ------------------------------------------------------------------------------------------------ artifact, EE


def _artifact(images: HeroImageReading, existing: HeroBuild | None, data: dict[str, Any], notes: list[str]) -> None:
    confidence: dict[str, float] = data["confidence"]
    had = existing.artifact if existing is not None else None
    panel = images.panel.artifact
    if not images.panel.present:
        return
    if panel is not None and panel.code.value is not None and panel.enhance.value is not None:
        data["artifact"] = ArtifactRef(code=panel.code.value, level=panel.enhance.value)
        confidence["artifact"] = round(min(panel.code.confidence, panel.enhance.confidence), 3)
        return
    detail = "not found" if panel is None else (panel.code.note or panel.enhance.note or "not read")
    if had is not None:
        notes.append(f"artifact not read ({detail}): kept {had.code} +{had.level}")
    else:
        notes.append(f"artifact not read ({detail})")


def _exclusive(images: HeroImageReading, existing: HeroBuild | None, data: dict[str, Any], notes: list[str]) -> None:
    confidence: dict[str, float] = data["confidence"]
    had = existing.exclusive_equipment if existing is not None else None
    read = images.exclusive
    if read is None or read.stat is None or read.value is None:
        if read is not None:
            notes.append(f"exclusive equipment {read.raw!r} not stored: {read.note or 'not read'}")
        elif had is not None and images.panel.present:
            notes.append("no exclusive equipment seen on the screen: the roster's is kept")
        return
    keep = had is not None and had.stat is read.stat
    data["exclusive_equipment"] = ExclusiveEquipment(
        stat=read.stat,
        value=read.value,
        option_code=had.option_code if keep and had is not None else None,
        option_text=had.option_text if keep and had is not None else None,
    )
    confidence["exclusive_equipment"] = read.confidence
