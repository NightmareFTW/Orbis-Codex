"""Same physical gear piece across a screen reading and a Fribbels save (SPEC D51, D52)."""

from __future__ import annotations

from e7ac.domain.codes import Stat
from e7ac.domain.roster import Gear, GearGrade, GearSlot, StatValue, Substat
from e7ac.roster.pieces import combine, enhance_is_estimate, same_piece

SCREEN = Gear(
    slot=GearSlot.RING,
    set_code="set_speed",
    grade=GearGrade.EPIC,
    item_level=88,
    enhance=14,
    main=StatValue(stat=Stat.ATK_PERCENT, value=0.6),
    substats=(Substat(stat=Stat.SPEED, value=10), Substat(stat=Stat.CRIT_CHANCE, value=0.08)),
    score=61,
)
SAVED = SCREEN.model_copy(
    update={
        "enhance": 12,
        "score": None,
        "external_id": "ingame:42",
        "substats": (
            Substat(stat=Stat.SPEED, value=10, rolls=3, modified=True),
            Substat(stat=Stat.CRIT_CHANCE, value=0.08, rolls=1),
        ),
    }
)


def test_a_derived_plus_n_is_an_estimate_only_below_15_on_game_pieces() -> None:
    assert enhance_is_estimate(SAVED)
    assert not enhance_is_estimate(SCREEN)  # no game id: read on screen
    assert not enhance_is_estimate(SAVED.model_copy(update={"enhance": 15}))
    assert not enhance_is_estimate(SAVED.model_copy(update={"enhance": 13}))  # not a multiple of 3: not derived


def test_readings_that_agree_on_what_both_know_are_one_piece() -> None:
    assert same_piece(SCREEN, SAVED) and same_piece(SAVED, SCREEN)
    assert not same_piece(SAVED.model_copy(update={"enhance": 9}), SCREEN)  # +14 is beyond the estimate's band
    assert not same_piece(SCREEN.model_copy(update={"enhance": 15}), SAVED)
    assert not same_piece(SAVED, SAVED.model_copy(update={"external_id": "ingame:43"}))  # two pieces, same stats
    assert not same_piece(SCREEN, SCREEN.model_copy(update={"item_level": 85}))
    assert same_piece(SCREEN, SCREEN.model_copy(update={"score": 99}))  # the score is derived, never an identity


def test_combine_keeps_what_each_reading_knows() -> None:
    from_screen = combine(SAVED, SCREEN)  # a scan after an import
    from_save = combine(SCREEN, SAVED)  # an import after a scan
    for piece in (from_screen, from_save):
        assert (piece.enhance, piece.score, piece.external_id) == (14, 61, "ingame:42")
        assert [(s.rolls, s.modified) for s in piece.substats] == [(3, True), (1, False)]
