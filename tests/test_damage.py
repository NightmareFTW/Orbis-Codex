"""Expected damage per hit and hits to defeat (MECH-DMG-01/02/05), by hand-computed numbers."""

from __future__ import annotations

import pytest

from e7ac.domain.codes import Stat
from e7ac.predict.damage import SkillPower, expected_hit, hits_to_defeat

ATTACKER = {Stat.ATK: 3000.0, Stat.HP: 10000.0, Stat.DEF: 1000.0, Stat.CRIT_CHANCE: 0.5, Stat.CRIT_DAMAGE: 4.0}
DEFENDER = {Stat.ATK: 1000.0, Stat.HP: 20000.0, Stat.DEF: 600.0, Stat.CRIT_CHANCE: 0.0, Stat.CRIT_DAMAGE: 1.5}


def test_expected_hit_follows_the_documented_formula() -> None:
    skill = SkillPower(rate=1.0, pow=1.0, enhance=0.1, hp_scaling=0.1, penetration=0.5)
    offense = (3000 * 1.0 + 10000 * 0.1) * 1.871 * 1.0 * 1.1  # MECH-DMG-01
    defence = 1 / (1 + 600 * 0.5 / 300)  # MECH-DMG-02/04
    hit_type = 0.5 * 3.5 + 0.5  # crit damage capped at 350% (MECH-DMG-05)
    assert expected_hit(ATTACKER, skill, DEFENDER) == pytest.approx(offense * defence * hit_type)
    assert hits_to_defeat(ATTACKER, skill, DEFENDER) == 3  # 20000 / ~9261 per hit
