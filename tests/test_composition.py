"""Final-stat composition check (MECH-STAT-02): synthetic builds (always run) and the user's captures (local only).

Every number below is TEST DATA, not a game constant of e7ac. It was transcribed by eye from the user's Hero Info
captures of 2026-10-04 (gear, imprint, artifact level, EE, displayed stats; set per piece from the set icons) and
copied from a synced catalog snapshot (Fribbels base stats, Stove/Fribbels artifact values, Stove set bonuses).
"""

from __future__ import annotations

import functools
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import JsonValue

from e7ac.catalog.facts import EntityType, Fact
from e7ac.catalog.resolve import ResolvedEntity, resolve
from e7ac.domain.codes import DataStatus, SourceId, Stat
from e7ac.domain.roster import (
    FINAL_STAT_FIELDS,
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
    ImprintMode,
    StatValue,
    Substat,
)
from e7ac.roster.composition import DISPLAYED_STATS, CompositionReport, StatCheck, check_final_stats
from e7ac.vision.hero_screen import HeroScreenReading
from tests.markers import FIXTURES_DIR

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
W, H, A, N, R, B = (
    GearSlot.WEAPON,
    GearSlot.HELMET,
    GearSlot.ARMOR,
    GearSlot.NECKLACE,
    GearSlot.RING,
    GearSlot.BOOTS,
)
ATK, DEF, HP, SPD = Stat.ATK, Stat.DEF, Stat.HP, Stat.SPEED
CC, CD, EFF, ER = Stat.CRIT_CHANCE, Stat.CRIT_DAMAGE, Stat.EFFECTIVENESS, Stat.EFFECT_RESISTANCE


# ------------------------------------------------------------------------------------------------ test data helpers


def entity(entity_type: EntityType, code: str, fields: Mapping[str, JsonValue]) -> ResolvedEntity:
    facts = [
        Fact(
            entity_type=entity_type,
            entity_id=code,
            field=name,
            value=value,
            source=SourceId.FRIBBELS,
            status=DataStatus.COMMUNITY,
        )
        for name, value in fields.items()
    ]
    return resolve(facts)[0]


def hero(code: str, base: Sequence[float], **extra: JsonValue) -> ResolvedEntity:
    """`base` in screen order (ATK, DEF, HP, SPD, CC, CD, EFF, ER, DAC); extra fields use '__' for '.'."""
    fields: dict[str, JsonValue] = {
        f"base.{stat.value}": value for stat, value in zip(DISPLAYED_STATS, base, strict=True)
    }
    fields.update({name.replace("__", "."): value for name, value in extra.items()})
    return entity(EntityType.HERO, code, fields)


def artifact(code: str, **fields: JsonValue) -> ResolvedEntity:
    return entity(EntityType.ARTIFACT, code, fields)


def a_set(code: str, pieces: int, *bonus: tuple[str, float, bool]) -> ResolvedEntity:
    static: list[JsonValue] = [{"stat": s, "value": v, "of_base": of_base} for s, v, of_base in bonus]
    return entity(EntityType.SET, code, {"pieces": pieces, "static_bonus": static})


SETS = {
    entity.entity_id: entity
    for entity in (
        a_set("set_cri_dmg", 4, ("cri_dmg", 0.6, False)),
        a_set("set_max_hp", 2, ("max_hp", 0.2, True)),
        a_set("set_speed", 4, ("speed", 0.25, True)),
        a_set("set_immune", 2),
        a_set("set_acc", 2, ("acc", 0.2, False)),
        a_set("set_rage", 4),
        a_set("set_cri", 2, ("cri", 0.12, False)),
        a_set("set_def", 2, ("def", 0.2, True)),
        a_set("set_att", 4, ("att", 0.45, True)),
        a_set("set_chase", 2),
    )
}

# icon family + "%" -> stat code, as on the screen: "%" on atk/def/hp is the percent stat; rates always show "%"
_FLAT_FAMILY = {"atk": ATK, "def": DEF, "hp": HP, "spd": SPD}
_RATE_FAMILY = {
    "atk": Stat.ATK_PERCENT,
    "def": Stat.DEF_PERCENT,
    "hp": Stat.HP_PERCENT,
    "cc": CC,
    "cd": CD,
    "eff": EFF,
    "er": ER,
}

Value = tuple[str, str]
"""A gear value as shown: (text, icon family), e.g. ("2,765", "hp") or ("13%", "def")."""


@dataclass(frozen=True)
class Piece:
    set_code: str
    item_level: int
    enhance: int
    main: Value
    subs: tuple[Value, ...]


def piece(set_code: str, main: Value, *subs: Value, level: int = 90, enhance: int = 15) -> Piece:
    return Piece(set_code, level, enhance, main, subs)


def gear_value(text: str, family: str) -> tuple[Stat, float]:
    number = text.replace(",", "")
    if number.endswith("%"):
        return _RATE_FAMILY[family], float(number[:-1]) / 100
    return _FLAT_FAMILY[family], float(number)


def to_gear(slot: GearSlot, p: Piece) -> Gear:
    main_stat, main_value = gear_value(*p.main)
    subs = tuple(Substat(stat=s, value=v) for s, v in (gear_value(*sub) for sub in p.subs))
    return Gear(
        slot=slot,
        set_code=p.set_code,
        grade=GearGrade.EPIC,
        item_level=p.item_level,
        enhance=p.enhance,
        main=StatValue(stat=main_stat, value=main_value),
        substats=subs,
    )


def final_stats(values: Sequence[float]) -> FinalStats:
    flats = [int(v) for v in values[:4]]
    return FinalStats.model_validate(dict(zip(FINAL_STAT_FIELDS, [*flats, *values[4:]], strict=True)))


@dataclass(frozen=True)
class Case:
    code: str
    hero: ResolvedEntity
    artifact: ResolvedEntity | None
    artifact_level: int
    imprint: Imprint | None
    ee: ExclusiveEquipment | None
    pieces: Mapping[GearSlot, Piece]
    displayed: tuple[float, ...]
    expected: tuple[float, ...]
    """Exact composition (spikes/m7_stat_composition.py), screen order."""
    level: int = 60
    stars: int = 6
    awakening: int = 6

    def build(self, **changes: Any) -> HeroBuild:
        ref = None if self.artifact is None else ArtifactRef(code=self.artifact.entity_id, level=self.artifact_level)
        data: dict[str, Any] = {
            "hero_code": self.code,
            "stars": self.stars,
            "awakening": self.awakening,
            "level": self.level,
            "imprint": self.imprint,
            "exclusive_equipment": self.ee,
            "artifact": ref,
            "gear": {slot: to_gear(slot, p) for slot, p in self.pieces.items()},
            "final_stats": final_stats(self.displayed),
            "captured_at": NOW,
            "source": BuildSource.OCR,
        }
        data.update(changes)
        return HeroBuild.model_validate(data)

    def check(self, build: HeroBuild | None = None, **kwargs: Any) -> CompositionReport:
        kwargs.setdefault("artifact", self.artifact)
        kwargs.setdefault("sets", SETS)
        return check_final_stats(build or self.build(), self.hero, **kwargs)

    def with_value(self, slot: GearSlot, index: int, value: Value) -> Case:
        """Index 0 = main stat, 1..4 = substats."""
        p = self.pieces[slot]
        changed = replace(p, main=value) if index == 0 else replace(p, subs=_put(p.subs, index - 1, value))
        return replace(self, pieces={**self.pieces, slot: changed})

    def with_sets(self, **sets: str) -> Case:
        changed = {GearSlot(slot): replace(self.pieces[GearSlot(slot)], set_code=code) for slot, code in sets.items()}
        return replace(self, pieces={**self.pieces, **changed})


def _put(values: tuple[Value, ...], index: int, value: Value) -> tuple[Value, ...]:
    return (*values[:index], value, *values[index + 1 :])


# ------------------------------------------------------------------------------------------------ the four captures

HARU = Case(
    code="c1192",
    hero=hero("c1192", (966, 657, 7323, 102, 0.15, 1.5, 0, 0, 0.03), imprint__stat="cri"),
    artifact=artifact("efw42", atk_min=0, def_min=5, def_max=65, hp_min=76, hp_max=988),
    artifact_level=15,
    imprint=Imprint(grade=ImprintGrade.B, stat=Stat.HP_PERCENT, value=0.04, mode=ImprintMode.TEAM),
    ee=None,
    pieces={
        W: piece("set_cri_dmg", ("525", "atk"), ("231", "hp"), ("9%", "cd"), ("15%", "er"), ("19%", "cc")),
        H: piece("set_cri_dmg", ("2,765", "hp"), ("13%", "hp"), ("13%", "def"), ("12", "spd"), ("14%", "cd"), level=88),
        A: piece("set_cri_dmg", ("310", "def"), ("17%", "hp"), ("8%", "def"), ("15", "spd"), ("9%", "cc"), level=88),
        N: piece("set_cri_dmg", ("70%", "cd"), ("25%", "hp"), ("3", "spd"), ("242", "hp"), ("14%", "cc")),
        R: piece("set_max_hp", ("65%", "hp"), ("12", "spd"), ("14%", "cd"), ("9%", "cc"), ("15%", "er")),
        B: piece("set_max_hp", ("45", "spd"), ("8%", "cd"), ("9%", "cc"), ("32%", "atk"), ("14%", "hp")),
    },
    displayed=(1800, 1139, 22370, 189, 0.75, 3.25, 0.0, 0.30, 0.03),
    expected=(1800.12, 1139.97, 22370.42, 189, 0.75, 3.25, 0.0, 0.30, 0.03),
)
LOTS = Case(  # Lady of the Scales
    code="c6005",
    hero=hero("c6005", (694, 655, 4855, 117, 0.15, 1.5, 0, 0.3, 0.03), imprint__stat="res"),
    artifact=artifact("efh19", atk_min=9, atk_max=117, def_min=0, hp_min=76, hp_max=988),
    artifact_level=15,
    imprint=Imprint(grade=ImprintGrade.SSS, stat=ER, value=0.15, mode=ImprintMode.TEAM),
    ee=None,
    pieces={
        W: piece("set_speed", ("515", "atk"), ("12%", "atk"), ("11%", "hp"), ("17", "spd"), ("6%", "eff"), level=88),
        H: piece("set_speed", ("2,765", "hp"), ("12%", "atk"), ("11%", "hp"), ("17", "spd"), ("6%", "eff"), level=88),
        A: piece("set_immune", ("310", "def"), ("17%", "hp"), ("20%", "def"), ("740", "hp"), ("9%", "eff")),
        N: piece("set_speed", ("65%", "hp"), ("11%", "atk"), ("11%", "def"), ("17", "spd"), ("6%", "eff"), level=88),
        R: piece("set_immune", ("65%", "hp"), ("207", "atk"), ("8%", "eff"), ("4", "spd"), ("14%", "cc")),
        B: piece("set_speed", ("45", "spd"), ("21%", "atk"), ("21%", "hp"), ("6%", "def"), ("14%", "eff"), level=88),
    },
    displayed=(1867, 1207, 18116, 246, 0.29, 1.5, 0.49, 0.30, 0.03),
    expected=(1867.64, 1207.35, 18116.5, 246.25, 0.29, 1.5, 0.49, 0.30, 0.03),
)
AINZ = Case(
    code="c1155",
    hero=hero("c1155", (1039, 673, 5299, 115, 0.15, 1.5, 0.18, 0.18, 0.03), imprint__stat="acc"),
    artifact=artifact("efm30", atk_min=15, atk_max=195, def_min=0, hp_min=54, hp_max=702),
    artifact_level=4,
    imprint=Imprint(grade=ImprintGrade.SSS, stat=EFF, value=0.27, mode=ImprintMode.SELF),
    ee=None,
    pieces={
        W: piece("set_speed", ("500", "atk"), ("10%", "cc"), ("9%", "eff"), ("15%", "atk"), ("8%", "hp"), level=85),
        H: piece("set_speed", ("2,700", "hp"), ("4%", "eff"), ("12%", "cc"), ("17%", "hp"), ("12%", "atk"), level=85),
        A: piece("set_acc", ("300", "def"), ("375", "hp"), ("9%", "cc"), ("14%", "cd"), ("11%", "eff"), level=85),
        N: piece("set_speed", ("60%", "hp"), ("6%", "eff"), ("21%", "atk"), ("86", "atk"), ("6", "spd"), level=85),
        R: piece("set_speed", ("60%", "eff"), ("13%", "def"), ("25%", "er"), ("14%", "atk"), ("4", "spd"), level=85),
        B: piece("set_acc", ("40", "spd"), ("8%", "atk"), ("8%", "hp"), ("711", "hp"), ("10%", "cd"), level=85),
    },
    displayed=(2391, 1060, 14153, 193, 0.46, 1.74, 1.55, 0.43, 0.03),
    expected=(2391.3, 1060.49, 14153.47, 193.75, 0.46, 1.74, 1.55, 0.43, 0.03),
)
STRAZE = Case(
    code="c1034",
    hero=hero("c1034", (1228, 553, 5784, 109, 0.23, 1.65, 0, 0, 0.03), imprint__stat="att_rate", ee__stat="cri"),
    artifact=artifact("ef317", atk_min=16, atk_max=208, def_min=0, hp_min=14, hp_max=182),
    artifact_level=30,
    imprint=Imprint(grade=ImprintGrade.SSS, stat=Stat.ATK_PERCENT, value=0.21, mode=ImprintMode.SELF),
    ee=ExclusiveEquipment(stat=CC, value=0.12),
    pieces={
        W: piece("set_rage", ("525", "atk"), ("7%", "eff"), ("13%", "cd"), ("13%", "cc"), ("24%", "atk")),
        H: piece("set_rage", ("2,835", "hp"), ("24%", "atk"), ("12%", "cc"), ("8%", "eff"), ("10%", "cd")),
        A: piece("set_rage", ("310", "def"), ("28%", "cd"), ("16%", "cc"), ("8%", "eff"), ("9%", "er")),
        N: piece("set_cri", ("70%", "cd"), ("145", "atk"), ("33%", "atk"), ("9%", "hp"), ("6%", "cc"), level=88),
        R: piece("set_cri", ("65%", "atk"), ("6%", "cc"), ("53", "atk"), ("30%", "cd"), ("6", "spd")),
        B: piece("set_rage", ("65%", "atk"), ("10%", "cc"), ("14%", "cd"), ("9", "spd"), ("56", "atk")),
    },
    displayed=(5063, 863, 9321, 124, 1.0, 3.30, 0.23, 0.09, 0.03),
    expected=(5063.96, 863, 9321.56, 124, 1.10, 3.30, 0.23, 0.09, 0.03),
)
CASES = {"haru": HARU, "lots": LOTS, "ainz": AINZ, "straze": STRAZE}
LV85_0 = {"level": 85, "enhance": 0}
POLITIS = Case(  # Lv5, 5 stars, awakening 1: no catalog base applies
    code="c5112",
    hero=hero("c5112", (993, 611, 6002, 120, 0.15, 1.5, 0, 0, 0.03)),
    artifact=None,  # efr34 +0 on the screen; never composed (not applicable)
    artifact_level=0,
    imprint=Imprint(grade=ImprintGrade.B, stat=Stat.ATK_PERCENT, value=0.04, mode=ImprintMode.TEAM),
    ee=None,
    pieces={
        W: piece("set_speed", ("100", "atk"), ("187", "hp"), ("4", "spd"), ("8%", "atk"), level=85, enhance=0),
        H: piece("set_chase", ("2,700", "hp"), ("13%", "def"), ("6%", "atk"), ("7%", "hp"), ("38%", "er"), level=85),
        A: piece(
            "set_speed", ("60", "def"), ("3", "spd"), ("8%", "hp"), ("5%", "er"), ("201", "hp"), level=85, enhance=0
        ),
        N: piece(
            "set_speed", ("12%", "hp"), ("8%", "def"), ("3", "spd"), ("6%", "er"), ("6%", "eff"), level=85, enhance=0
        ),
        R: piece(
            "set_speed", ("12%", "hp"), ("8%", "def"), ("4", "spd"), ("6%", "er"), ("6%", "eff"), level=85, enhance=0
        ),
        B: piece("set_chase", ("45", "spd"), ("99", "atk"), ("229", "hp"), ("41%", "atk"), ("9%", "hp"), level=88),
    },
    displayed=(1, 1, 1, 1, 0, 0, 0, 0, 0),  # replaced by the screen in the golden test
    expected=(),
    level=5,
    stars=5,
    awakening=1,
)


def by_stat(report: CompositionReport) -> dict[Stat, StatCheck]:
    return {check.stat: check for check in report.checks}


def verdicts(report: CompositionReport, verdict: str) -> set[Stat]:
    return {check.stat for check in report.checks if check.verdict == verdict}


# ------------------------------------------------------------------------------------------------ reproduction


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_four_captured_builds_are_reproduced_exactly(name: str) -> None:
    case = CASES[name]
    report = case.check()
    assert report.applicable and report.reason is None and report.ok
    assert [c.stat for c in report.checks] == list(DISPLAYED_STATS)
    assert [c.expected for c in report.checks] == pytest.approx(case.expected, abs=1e-9)
    assert [c.displayed for c in report.checks] == pytest.approx(case.displayed)
    capped = {CC} if name == "straze" else set()
    assert verdicts(report, "capped") == capped and verdicts(report, "ok") == set(DISPLAYED_STATS) - capped
    assert all(c.suspects == () for c in report.checks if c.verdict == "ok")
    assert len(report.warnings) == len(capped)


def test_a_capped_crit_chance_names_what_it_cannot_verify() -> None:
    report = STRAZE.check()
    cc = by_stat(report)[CC]
    assert cc.verdict == "capped" and cc.expected == pytest.approx(1.10) and cc.displayed == 1.0
    assert "exclusive_equipment" in cc.suspects and "set set_cri" in cc.suspects and "gear.armor.sub2" in cc.suspects
    assert "100% cap" in report.warnings[0]
    # blind spot of the cap (spike: 249 of 249 missed gear errors): a CC field misread upwards stays hidden
    blind = STRAZE.with_value(A, 2, ("46%", "cc")).check()
    assert blind.ok and by_stat(blind)[CC].verdict == "capped"


def test_flat_stats_are_the_floor_of_the_exact_total() -> None:
    """Proposed MECH-STAT-08: round-half-up would show Lots HP 18117 and Haru DEF 1140."""
    for case, stat, index, rounded in ((LOTS, HP, 2, 18117), (HARU, DEF, 1, 1140), (AINZ, SPD, 3, 194)):
        shown = list(case.displayed)
        shown[index] = rounded
        report = case.check(case.build(final_stats=final_stats(shown)))
        assert verdicts(report, "mismatch") == {stat}


def test_float_noise_never_flips_the_floor() -> None:
    """100 · (1 + 0.13 + 0.15) is 128 exactly, but 127.99999999999999 in binary floats."""
    assert math.floor(100 * (1 + 0.13 + 0.15)) == 127  # the trap
    toy = toy_case()
    report = toy.check()
    assert report.ok and by_stat(report)[ATK].expected == 228 and by_stat(report)[ATK].verdict == "ok"
    # a value that already carries the noise (0.2799999999999998 from a float sum) still floors to 228
    noisy = (1 + 0.13 + 0.15) - 1
    assert repr(noisy) != "0.28"
    gear = dict(toy.build().gear)
    gear[W] = gear[W].model_copy(update={"substats": (Substat(stat=Stat.ATK_PERCENT, value=noisy),)})
    gear[H] = gear[H].model_copy(update={"substats": ()})
    assert toy.check(toy.build(gear=gear)).ok


def test_completed_sets_stack_and_name_how_many_times() -> None:
    """MECH-GEAR-07: three Health 2-piece sets give 3 x 20% of base HP (toy numbers)."""
    toy = toy_case()
    stacked = toy.with_sets(**{slot.value: "set_max_hp" for slot in GearSlot})
    shown = list(toy.displayed)
    shown[2] = 2600  # 1000 base + 60% of base + 1000 flat
    report = stacked.check(stacked.build(final_stats=final_stats(shown)))
    assert report.ok and by_stat(report)[HP].expected == 2600
    shown[2] = 2400
    wrong = stacked.check(stacked.build(final_stats=final_stats(shown)))
    assert verdicts(wrong, "mismatch") == {HP} and "set set_max_hp x3" in by_stat(wrong)[HP].suspects


def toy_case() -> Case:
    return Case(
        code="c9999",
        hero=hero("c9999", (100, 100, 1000, 100, 0, 0, 0, 0, 0)),
        artifact=artifact("eftoy", atk_min=0, def_min=0, hp_min=0),  # adds nothing: no "artifact unknown" shield
        artifact_level=0,
        imprint=None,
        ee=None,
        pieces={
            W: piece("set_rage", ("100", "atk"), ("13%", "atk")),
            H: piece("set_rage", ("1,000", "hp"), ("15%", "atk")),
            A: piece("set_rage", ("100", "def")),
            N: piece("set_rage", ("10%", "cc")),
            R: piece("set_immune", ("10%", "eff")),
            B: piece("set_immune", ("10", "spd")),
        },
        displayed=(228, 200, 2000, 110, 0.1, 0, 0.1, 0, 0),
        expected=(228, 200, 2000, 110, 0.1, 0, 0.1, 0, 0),
    )


# ------------------------------------------------------------------------------------------------ fault injection


GEAR_FAULTS = [
    # (case, slot, index (0 = main), misread value, stats that must fail, suspect)
    pytest.param(HARU, B, 3, ("33%", "atk"), {ATK}, "gear.boots.sub3", id="digit-substat"),
    pytest.param(HARU, H, 2, ("18%", "def"), {DEF}, "gear.helmet.sub2", id="digit-substat-def"),
    pytest.param(STRAZE, W, 0, ("526", "atk"), {ATK}, "gear.weapon.main", id="digit-main"),
    pytest.param(AINZ, B, 3, ("7111", "hp"), {HP}, "gear.boots.sub3", id="digit-inserted"),
    pytest.param(LOTS, R, 1, ("27", "atk"), {ATK}, "gear.ring.sub1", id="digit-dropped"),
    pytest.param(LOTS, A, 2, ("20", "def"), {DEF}, "gear.armor.sub2", id="percent-lost"),
    pytest.param(STRAZE, R, 2, ("53%", "atk"), {ATK}, "gear.ring.sub2", id="percent-added"),
    pytest.param(AINZ, N, 3, ("86", "hp"), {ATK, HP}, "gear.necklace.sub3", id="icon-atk-as-hp"),
    pytest.param(LOTS, W, 3, ("17", "atk"), {SPD, ATK}, "gear.weapon.sub3", id="icon-spd-as-atk"),
    pytest.param(HARU, W, 2, ("9%", "er"), {CD, ER}, "gear.weapon.sub2", id="icon-cd-as-er"),
    pytest.param(STRAZE, A, 1, ("28%", "cc"), {CD}, "gear.armor.sub1", id="icon-cd-as-cc-while-capped"),
]


@pytest.mark.parametrize(("case", "slot", "index", "value", "failing", "suspect"), GEAR_FAULTS)
def test_a_misread_gear_value_is_a_mismatch_naming_its_field(
    case: Case, slot: GearSlot, index: int, value: Value, failing: set[Stat], suspect: str
) -> None:
    report = case.with_value(slot, index, value).check()
    assert report.applicable and not report.ok
    assert verdicts(report, "mismatch") == failing  # localised: only the stats the field feeds
    assert verdicts(report, "unknown") == set()
    assert all(suspect in check.suspects for check in report.checks if check.verdict == "mismatch")
    assert sum(suspect in w for w in report.warnings) == len(failing)


def test_a_misread_artifact_level_is_a_mismatch_naming_the_artifact() -> None:
    report = AINZ.check(AINZ.build(artifact=ArtifactRef(code="efm30", level=5)))
    assert verdicts(report, "mismatch") == {ATK, HP}
    assert all("artifact efm30+5" in by_stat(report)[s].suspects for s in (ATK, HP))
    haru = HARU.check(HARU.build(artifact=ArtifactRef(code="efw42", level=12)))
    assert verdicts(haru, "mismatch") == {DEF, HP}  # a DEF + HP artifact: ATK is untouched


@pytest.mark.parametrize(
    ("case", "mode", "stat", "suspect"),
    [
        (STRAZE, ImprintMode.TEAM, ATK, "imprint (team, not added)"),
        (HARU, ImprintMode.SELF, HP, "imprint"),
        (LOTS, ImprintMode.SELF, ER, "imprint"),
        (AINZ, ImprintMode.TEAM, EFF, "imprint (team, not added)"),
    ],
)
def test_a_flipped_imprint_mode_is_a_mismatch_naming_the_imprint(
    case: Case, mode: ImprintMode, stat: Stat, suspect: str
) -> None:
    assert case.imprint is not None
    report = case.check(case.build(imprint=case.imprint.model_copy(update={"mode": mode})))
    assert verdicts(report, "mismatch") == {stat} and suspect in by_stat(report)[stat].suspects


def test_a_misread_set_is_a_mismatch_naming_the_set() -> None:
    swapped = HARU.with_sets(ring="set_def", boots="set_def").check()
    assert verdicts(swapped, "mismatch") == {DEF, HP} and "set set_def" in by_stat(swapped)[DEF].suspects
    broken = HARU.with_sets(ring="set_cri").check()  # one piece: Health incomplete, Critical incomplete
    assert verdicts(broken, "mismatch") == {HP}
    assert "set set_max_hp (1 of 2 pieces, no bonus)" in by_stat(broken)[HP].suspects
    speed = LOTS.with_sets(weapon="set_att", helmet="set_att", necklace="set_att", boots="set_att").check()
    assert verdicts(speed, "mismatch") == {ATK, SPD} and "set set_att" in by_stat(speed)[ATK].suspects


def test_a_wrong_ee_value_is_a_mismatch_naming_the_ee() -> None:
    report = STRAZE.check(STRAZE.build(exclusive_equipment=ExclusiveEquipment(stat=CC, value=0.01)))
    assert verdicts(report, "mismatch") == {CC} and "exclusive_equipment" in by_stat(report)[CC].suspects


def test_a_misread_displayed_value_is_a_mismatch() -> None:
    shown = list(HARU.displayed)
    shown[0] = 1806
    report = HARU.check(HARU.build(final_stats=final_stats(shown)))
    assert verdicts(report, "mismatch") == {ATK} and "1806" in report.warnings[0]
    under_cap = list(STRAZE.displayed)
    under_cap[4] = 0.99  # composed 110% must show the 100% cap (MECH-STAT-05)
    assert verdicts(STRAZE.check(STRAZE.build(final_stats=final_stats(under_cap))), "mismatch") == {CC}
    rate = list(LOTS.displayed)
    rate[6] = 0.491  # "49.1%": one 0.1% display step off is beyond the half-step tolerance
    assert verdicts(LOTS.check(LOTS.build(final_stats=final_stats(rate))), "mismatch") == {EFF}


# ------------------------------------------------------------------------------------------------ unknown components


@pytest.mark.parametrize(
    ("ee", "depends"),
    [
        (None, "exclusive_equipment (none on the build)"),
        (ExclusiveEquipment(stat=CC), "exclusive_equipment (value unknown)"),
        (ExclusiveEquipment(value=0.12), "exclusive_equipment (stat not on the build, catalog says cri)"),
        (ExclusiveEquipment(option_text="Star Extinction"), "exclusive_equipment (value unknown)"),
    ],
)
def test_an_unread_ee_makes_its_stat_unknown_never_a_mismatch(ee: ExclusiveEquipment | None, depends: str) -> None:
    report = STRAZE.check(STRAZE.build(exclusive_equipment=ee))
    assert report.ok and verdicts(report, "unknown") == {CC} and verdicts(report, "mismatch") == set()
    assert by_stat(report)[CC].suspects[0] == depends and any(depends in w for w in report.warnings)


def test_an_ee_of_unknown_stat_only_shields_the_stats_that_do_not_fit() -> None:
    noisy = HARU.with_value(B, 3, ("33%", "atk"))
    report = noisy.check(noisy.build(exclusive_equipment=ExclusiveEquipment(value=0.1)))
    assert verdicts(report, "unknown") == {ATK} and verdicts(report, "mismatch") == set()
    assert "exclusive_equipment (stat unknown)" in by_stat(report)[ATK].suspects


def test_an_artifact_missing_from_the_catalog_or_the_build_is_unknown() -> None:
    report = HARU.check(artifact=None)  # efw42 is DEF + HP: ATK fits without it, DEF and HP cannot be checked
    assert verdicts(report, "unknown") == {DEF, HP} and verdicts(report, "ok") == set(DISPLAYED_STATS) - {DEF, HP}
    assert "artifact efw42+15 (not in the catalog)" in by_stat(report)[DEF].suspects
    none = HARU.check(HARU.build(artifact=None), artifact=None)
    assert verdicts(none, "unknown") == {DEF, HP} and "artifact (none on the build)" in by_stat(none)[HP].suspects


def test_an_unknown_imprint_mode_or_a_missing_imprint_is_unknown() -> None:
    assert STRAZE.imprint is not None and HARU.imprint is not None
    straze = STRAZE.check(STRAZE.build(imprint=STRAZE.imprint.model_copy(update={"mode": None})))
    assert verdicts(straze, "unknown") == {ATK} and straze.ok
    haru = HARU.check(HARU.build(imprint=HARU.imprint.model_copy(update={"mode": None})))
    assert verdicts(haru, "ok") == set(DISPLAYED_STATS)  # fits without it: consistent with a team imprint
    missing = STRAZE.check(STRAZE.build(imprint=None))  # catalog imprint stat att_rate -> ATK
    assert verdicts(missing, "unknown") == {ATK} and "imprint (none on the build)" in by_stat(missing)[ATK].suspects


def test_a_set_missing_from_the_catalog_is_unknown() -> None:
    sets = {code: e for code, e in SETS.items() if code != "set_max_hp"}
    report = HARU.check(sets=sets)
    assert verdicts(report, "unknown") == {HP} and report.ok
    assert "set set_max_hp (not in the catalog)" in by_stat(report)[HP].suspects
    malformed = {**SETS, "set_max_hp": entity(EntityType.SET, "set_max_hp", {"pieces": 2, "static_bonus": "?"})}
    assert verdicts(HARU.check(sets=malformed), "unknown") == {HP}
    no_pieces = {**SETS, "set_max_hp": entity(EntityType.SET, "set_max_hp", {"static_bonus": []})}
    assert "set set_max_hp (piece count unknown)" in by_stat(HARU.check(sets=no_pieces))[HP].suspects


def test_weak_catalog_data_is_reported() -> None:
    facts = [
        Fact(
            entity_type=EntityType.HERO,
            entity_id="c1192",
            field=name,
            value=resolved.value,
            source=SourceId.FRIBBELS,
            status=DataStatus.ASSUMED if name == "base.att" else resolved.status,
        )
        for name, resolved in HARU.hero.fields.items()
    ]
    report = check_final_stats(HARU.build(), resolve(facts)[0], artifact=HARU.artifact, sets=SETS)
    assert report.ok and any("catalog base att of c1192 is assumed" in w for w in report.warnings)
    off_rule = artifact("efw42", atk_min=0, def_min=5, def_max=66, hp_min=76)
    warnings = HARU.check(artifact=off_rule).warnings
    assert any("+30 def 66 is not 13 x +0 5" in w for w in warnings)
    assert any("no +30 hp in the catalog, 13 x +0 used" in w for w in warnings)


# ------------------------------------------------------------------------------------------------ not applicable


def test_a_hero_below_lv60_6_star_awakened_is_not_checked() -> None:
    politis = POLITIS.check()
    assert not politis.applicable and not politis.ok and politis.checks == []
    assert politis.reason is not None and "Lv5 5-star with awakening 1" in politis.reason
    for change in ({"awakening": 5}, {"level": 59}):
        assert not HARU.check(HARU.build(**change)).applicable


def test_missing_gear_base_or_final_stats_make_the_check_not_applicable() -> None:
    gear = {slot: g for slot, g in HARU.build().gear.items() if slot is not B}
    report = HARU.check(HARU.build(gear=gear))
    assert not report.applicable and report.reason == "gear missing: boots (all six pieces are needed)"
    no_final = HARU.check(HARU.build(final_stats=None))
    assert not no_final.applicable and no_final.reason == "the build has no displayed final stats"
    partial = hero("c1192", (966, 657, 7323, 102, 0.15, 1.5, 0, 0, 0.03))
    fields = {name: f.value for name, f in partial.fields.items() if name != "base.coop"}
    no_base = check_final_stats(
        HARU.build(), entity(EntityType.HERO, "c1192", fields), artifact=HARU.artifact, sets=SETS
    )
    assert not no_base.applicable and no_base.reason == "the catalog has no base coop for c1192"


def test_entities_of_another_build_are_a_caller_error() -> None:
    with pytest.raises(ValueError, match="is not hero c1192"):
        check_final_stats(HARU.build(), STRAZE.hero, artifact=HARU.artifact, sets=SETS)
    with pytest.raises(ValueError, match="is not the build's artifact"):
        HARU.check(artifact=STRAZE.artifact)
    with pytest.raises(ValueError, match="build's artifact \\(none\\)"):
        HARU.check(HARU.build(artifact=None))
    with pytest.raises(ValueError, match="sets\\['set_def'\\]"):
        HARU.check(sets={"set_def": SETS["set_max_hp"]})


def test_the_build_is_never_changed() -> None:
    build = HARU.with_value(B, 3, ("33%", "atk")).build()
    before = build.model_dump()
    check_final_stats(build, HARU.hero, artifact=HARU.artifact, sets=SETS)
    assert build.model_dump() == before


# ------------------------------------------------------------------------------------------------ real captures


SCALES = (0.64, 1.0, 1.28)
GOLDEN = {  # capture -> build of the same hero (the Equipment-tab captures show the same builds as Hero Info)
    "heroinfo_haru.webp": HARU,
    "heroinfo_lots.webp": LOTS,
    "heroinfo_ainz.webp": AINZ,
    "heroinfo_straze.webp": STRAZE,
    "equip_haru.webp": HARU,
    "equip_straze.webp": STRAZE,
}


@functools.cache
def _reader() -> Any:
    from e7ac.vision.ocr import RapidOcrReader

    return RapidOcrReader()


@functools.cache
def screen(name: str, scale: float) -> HeroScreenReading:
    """OCR once per capture and scale."""
    import cv2

    from e7ac.vision.hero_screen import parse_hero_screen
    from e7ac.vision.image import load_image

    image = load_image(FIXTURES_DIR / "screenshots" / name)
    if scale != 1.0:
        interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=interpolation)
    return parse_hero_screen(_reader().read(image))


def screen_finals(reading: HeroScreenReading) -> list[float]:
    values = [reading.stats[stat].final for stat in DISPLAYED_STATS]
    assert all(v is not None for v in values), values
    return [v for v in values if v is not None]


def _golden_params(names: Sequence[str]) -> list[Any]:
    return [
        pytest.param(name, scale, marks=pytest.mark.fixtures(f"screenshots/{name}"), id=f"{name}-{scale}")
        for name in names
        for scale in SCALES
    ]


@pytest.mark.parametrize(("name", "scale"), _golden_params(sorted(GOLDEN)))
def test_real_capture_is_reproduced(name: str, scale: float) -> None:
    case = GOLDEN[name]
    reading = screen(name, scale)
    assert reading.level == 60
    report = case.check(case.build(final_stats=final_stats(screen_finals(reading))))
    assert report.applicable and report.ok, report.warnings
    capped = {CC} if case is STRAZE else set()
    assert verdicts(report, "capped") == capped and verdicts(report, "ok") == set(DISPLAYED_STATS) - capped


@pytest.mark.parametrize(("name", "scale"), _golden_params(["heroinfo_lots.webp", "heroinfo_straze.webp"]))
def test_real_capture_catches_injected_gear_errors(name: str, scale: float) -> None:
    case = GOLDEN[name]
    shown = final_stats(screen_finals(screen(name, scale)))
    faulty = case.with_value(W, 1, ("13%", "atk")) if case is LOTS else case.with_value(R, 2, ("58", "atk"))
    report = faulty.check(faulty.build(final_stats=shown))
    assert verdicts(report, "mismatch") == {ATK}


@pytest.mark.parametrize(("name", "scale"), _golden_params(["heroinfo_politis.webp"]))
def test_real_capture_of_a_low_level_hero_is_not_checked(name: str, scale: float) -> None:
    reading = screen(name, scale)
    build = POLITIS.build(level=reading.level, final_stats=final_stats(screen_finals(reading)))
    report = POLITIS.check(build)
    assert reading.level == 5 and not report.applicable and report.reason is not None and "Lv5" in report.reason


@pytest.mark.parametrize(("name", "scale"), _golden_params(["heroinfo_charles.webp"]))
def test_real_capture_without_gear_is_not_checked(name: str, scale: float) -> None:
    reading = screen(name, scale)
    charles = hero("c2027", (1228, 473, 6266, 113, 0.23, 1.5, 0, 0, 0.03))
    build = HeroBuild(
        hero_code="c2027",
        stars=5,
        awakening=0,
        level=reading.level or 1,
        final_stats=final_stats(screen_finals(reading)),
        captured_at=NOW,
        source=BuildSource.OCR,
    )
    report = check_final_stats(build, charles, artifact=None, sets=SETS)
    assert reading.level == 50 and not report.applicable
