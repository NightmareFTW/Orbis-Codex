"""Hypothesis strategies producing *valid* builds according to the documented rules (MECH-GEAR-01/03/08/09/10)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypothesis import assume
from hypothesis import strategies as st

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
from e7ac.roster.validation import ALLOWED_MAIN, FORBIDDEN_SUBS, GEAR_STATS, MIN_SUBS_BY_ENHANCE

SET_CODES = ["set_cri_dmg", "set_cri", "set_speed", "set_max_hp", "set_att", "set_torrent"]


def stat_value(stat: Stat) -> st.SearchStrategy[float]:
    """Rates as fractions (> 1% so that the same value typed as percent is always caught), flats as whole units."""
    if stat.is_rate:
        return st.floats(min_value=0.011, max_value=1.0, allow_nan=False).map(lambda v: round(v, 4))
    return st.integers(min_value=1, max_value=3000).map(float)


_IMPRINT_STATS = [Stat.ATK_PERCENT, Stat.HP_PERCENT, Stat.DEF_PERCENT, Stat.CRIT_CHANCE, Stat.EFFECTIVENESS, Stat.SPEED]
_EE_STATS = [Stat.CRIT_CHANCE, Stat.SPEED, Stat.ATK_PERCENT, Stat.EFFECT_RESISTANCE]


@st.composite
def imprints(draw: st.DrawFn) -> Imprint:
    stat = draw(st.sampled_from(_IMPRINT_STATS))
    grade = draw(st.none() | st.sampled_from(list(ImprintGrade)))
    return Imprint(grade=grade, stat=stat, value=draw(stat_value(stat)))


@st.composite
def exclusive_equipments(draw: st.DrawFn) -> ExclusiveEquipment:
    """Any combination of known/unknown fields, as OCR may give (but never all unknown: that is `None`)."""
    stat = draw(st.none() | st.sampled_from(_EE_STATS))
    value = draw(st.none() | stat_value(stat or Stat.CRIT_CHANCE))
    code = draw(st.none() | st.from_regex(r"ek_c[0-9]{6}_[0-9]{2}", fullmatch=True))
    text = draw(st.none() | st.text(max_size=40))
    assume(any(v is not None for v in (stat, value, code, text)))
    return ExclusiveEquipment(stat=stat, value=value, option_code=code, option_text=text)


def min_subs(enhance: int, grade: GearGrade) -> int:
    for min_enhance, count in MIN_SUBS_BY_ENHANCE:
        if enhance >= min_enhance:
            return count
    return 0 if grade is GearGrade.NORMAL else 1


@st.composite
def gears(draw: st.DrawFn, slot: GearSlot | None = None) -> Gear:
    slot = slot or draw(st.sampled_from(list(GearSlot)))
    main = draw(st.sampled_from(sorted(ALLOWED_MAIN[slot])))
    grade = draw(st.sampled_from(list(GearGrade)))
    enhance = draw(st.integers(0, 15))
    pool = sorted(GEAR_STATS - {main} - FORBIDDEN_SUBS.get(slot, frozenset()))
    count = draw(st.integers(min_subs(enhance, grade), 4))
    sub_stats = draw(st.lists(st.sampled_from(pool), min_size=count, max_size=count, unique=True))
    substats = tuple(
        Substat(
            stat=s,
            value=draw(stat_value(s)),
            rolls=draw(st.none() | st.integers(0, 5)),
            modified=draw(st.booleans()),
            reforged=draw(st.booleans()),
        )
        for s in sub_stats
    )
    return Gear(
        slot=slot,
        set_code=draw(st.sampled_from(SET_CODES)),
        grade=grade,
        item_level=draw(st.sampled_from([71, 85, 88, 90])),
        enhance=enhance,
        main=StatValue(stat=main, value=draw(stat_value(main))),
        substats=substats,
        score=draw(st.none() | st.integers(0, 100)),
        external_id=draw(st.none() | st.text("abcdef0123456789", min_size=8, max_size=8)),
    )


@st.composite
def builds(draw: st.DrawFn) -> HeroBuild:
    slots = draw(st.lists(st.sampled_from(list(GearSlot)), unique=True, max_size=6))
    stars = draw(st.integers(1, 6))
    final = draw(
        st.none()
        | st.builds(
            FinalStats,
            atk=st.integers(0, 6000),
            defense=st.integers(0, 4000),
            hp=st.integers(0, 40000),
            speed=st.integers(0, 350),
            crit_chance=st.floats(0, 1, allow_nan=False).map(lambda v: round(v, 3)),
            crit_damage=st.floats(1.5, 3.6, allow_nan=False).map(lambda v: round(v, 3)),
            effectiveness=st.floats(0, 3, allow_nan=False).map(lambda v: round(v, 3)),
            effect_resistance=st.floats(0, 3, allow_nan=False).map(lambda v: round(v, 3)),
            dual_attack=st.floats(0, 0.5, allow_nan=False).map(lambda v: round(v, 3)),
        )
    )
    return HeroBuild(
        hero_code=draw(st.sampled_from(["c2011", "c1011", "c9001"])),
        stars=stars,
        awakening=draw(st.integers(0, stars)),
        level=draw(st.integers(1, stars * 10)),
        skills=SkillEnhancements(s1=draw(st.integers(0, 5)), s2=draw(st.integers(0, 5)), s3=draw(st.integers(0, 5))),
        imprint=draw(st.none() | imprints()),
        exclusive_equipment=draw(st.none() | exclusive_equipments()),
        artifact=draw(
            st.none()
            | st.builds(ArtifactRef, code=st.sampled_from(["efa22", "efw37", "ef506"]), level=st.integers(0, 30))
        ),
        gear={slot: draw(gears(slot)) for slot in slots},
        final_stats=final,
        cp=draw(st.none() | st.integers(0, 400000)),
        captured_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=draw(st.integers(0, 10**6))),
        source=draw(st.sampled_from(list(BuildSource))),
        confidence=draw(st.dictionaries(st.sampled_from(["atk", "gear.weapon.main"]), st.floats(0, 1))),
        note=draw(st.text(max_size=20)),
    )
