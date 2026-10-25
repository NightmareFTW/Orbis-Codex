"""The win chance read with doubt (SPEC D94): never 100%, a range over the opponent builds, the order unchanged."""

from __future__ import annotations

import pytest

from e7ac.arena import compare
from e7ac.domain.codes import Stat
from e7ac.opponents.assumed import Profile
from e7ac.predict.battle import BattleOdds, Combatant, simulate
from e7ac.predict.damage import SkillPower
from e7ac.predict.doubt import BUILDS, EVIDENCE, believe, believed, mix
from e7ac.settings import OpponentProfile, Settings

S1 = {1: SkillPower(rate=1.0, pow=1.0, enhance=0.0)}


def fighter(label: str, side: str, atk: float, speed: float = 200) -> Combatant:
    stats = {Stat.ATK: atk, Stat.HP: 10000.0, Stat.DEF: 300.0, Stat.CRIT_CHANCE: 0.5, Stat.CRIT_DAMAGE: 2.0}
    return Combatant(label, side, stats, speed, S1)


def odds(win: float, runs: int = 1000, low: float | None = None, high: float | None = None) -> BattleOdds:
    return BattleOdds(win, 1 - win, 0.0, 20, runs, low=low, high=high)


def test_a_sure_win_is_not_believed_at_100_percent() -> None:
    assert believe(1.0) == pytest.approx((EVIDENCE + 1) / (EVIDENCE + 2))
    assert believe(1.0) < 0.96
    assert believe(0.0) > 0.04


def test_an_even_fight_stays_even_and_the_correction_never_changes_the_order() -> None:
    assert believe(0.5) == pytest.approx(0.5)
    believed_chances = [believe(c) for c in (0.0, 0.1, 0.35, 0.5, 0.8, 0.97, 1.0)]
    assert believed_chances == sorted(believed_chances)
    assert len(set(believed_chances)) == len(believed_chances)


def test_the_builds_are_weighted_and_the_weights_make_a_whole() -> None:
    assert sum(weight for _, weight in BUILDS) == pytest.approx(1.0)
    assert [profile for profile, _ in BUILDS] == [Profile.TYPICAL, Profile.STRONG, Profile.TOP]


def test_mixing_builds_gives_the_weighted_mean_and_the_range() -> None:
    mixed = mix([(0.5, odds(1.0)), (0.3, odds(0.8)), (0.2, odds(0.2))])
    assert mixed.win == pytest.approx(0.5 + 0.24 + 0.04)
    assert (mixed.low, mixed.high) == (0.2, 1.0)
    assert mixed.runs == 3000
    with pytest.raises(ValueError, match="nothing to mix"):
        mix([])


def test_the_shown_odds_keep_the_simulations_own_share() -> None:
    shown = believed(mix([(0.5, odds(1.0)), (0.5, odds(0.9))]))
    assert shown.raw == pytest.approx(0.95)
    assert shown.win == pytest.approx(believe(0.95))
    assert shown.low == pytest.approx(believe(0.9)) and shown.high == pytest.approx(believe(1.0))


def test_the_margin_is_the_wider_of_the_noise_and_the_spread_over_the_builds() -> None:
    alone = odds(0.5, runs=1000)
    assert alone.margin == pytest.approx(1.96 * (0.25 / 1000) ** 0.5)
    assert odds(0.5, runs=1000, low=0.2, high=0.9).margin == pytest.approx(0.35)
    assert odds(0.5, runs=1000, low=0.499, high=0.501).margin == pytest.approx(alone.margin)


def test_a_team_that_always_wins_is_shown_below_100_percent() -> None:
    simulated = simulate([fighter("a", "you", 6000, speed=250), fighter("b", "them", 1000)], runs=200)
    assert simulated.win == 1.0
    shown = believed(simulated)
    assert shown.win < 0.96 and shown.raw == 1.0
    assert compare.chance_text(shown.win) == f"{shown.win:.0%}"  # not the "99%+" of a sure thing


def test_the_default_opponent_profile_is_the_mixed_one() -> None:
    assert Settings().opponent_profile is OpponentProfile.MIXED
