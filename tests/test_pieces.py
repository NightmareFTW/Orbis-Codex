"""Same physical gear piece across a screen reading and a Fribbels save (SPEC D51, D52)."""

from __future__ import annotations

from e7ac.domain.codes import Stat
from e7ac.domain.roster import Gear, GearGrade, GearSlot, StatValue, Substat
from e7ac.roster.pieces import combine, enhance_is_estimate, is_screen_or_manual, same_piece

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


def test_a_fribbels_id_and_a_game_id_can_be_one_piece_but_two_game_ids_cannot() -> None:
    by_hand = SAVED.model_copy(update={"external_id": "fribbels:abc", "enhance": 14})
    assert same_piece(by_hand, SAVED.model_copy(update={"enhance": 14}))  # Fribbels added the game id later
    assert combine(by_hand, SAVED.model_copy(update={"enhance": 14})).external_id == "ingame:42"
    assert combine(SAVED.model_copy(update={"enhance": 14}), by_hand).external_id == "ingame:42"
    assert not same_piece(by_hand, by_hand.model_copy(update={"external_id": "fribbels:abd"}))


def test_a_screen_reading_never_wipes_flags_it_cannot_see() -> None:
    edited = SAVED.model_copy(  # edited in Fribbels: rolls are not taken, the modified flag is
        update={"substats": tuple(s.model_copy(update={"rolls": None}) for s in SAVED.substats), "enhance": 14}
    )
    scanned = combine(edited, SCREEN)
    assert [(s.rolls, s.modified) for s in scanned.substats] == [(None, True), (None, False)]
    reimported = combine(scanned, edited)
    assert reimported.substats == scanned.substats  # a later import changes nothing: stable


def test_pieces_read_on_screen_or_entered_here_are_told_apart() -> None:
    assert is_screen_or_manual(SCREEN)  # a score: read on Hero Info
    assert is_screen_or_manual(SCREEN.model_copy(update={"score": None}))  # no source id: entered by the user
    assert not is_screen_or_manual(SAVED)
    assert is_screen_or_manual(combine(SAVED, SCREEN))  # confirmed on screen: still protected
